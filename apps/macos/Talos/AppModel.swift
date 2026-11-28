import SwiftUI
import AppKit

struct ApprovalRequest: Identifiable {
    let id = UUID()
    let toolName: String
    let summary: String
}

struct ContextUsage {
    let used: Int
    let limit: Int?

    var fraction: Double? {
        guard let limit, limit > 0 else { return nil }
        return min(Double(used) / Double(limit), 1.5)
    }

    var label: String {
        guard let limit else { return "Context unknown" }
        return "\(TokenEstimator.format(used)) / \(TokenEstimator.format(limit)) context"
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var sessions: [Session] = []
    @Published var selectedID: UUID?
    @Published var choices: [ModelChoice] = []
    @Published var localModels: [LocalModel] = []
    @Published var choice = ModelChoice(provider: .local, id: Provider.local.defaultModel)
    @Published var status: String = "Connecting…"
    @Published var isStreaming = false
    @Published var error: String?
    @Published var search = ""
    @Published var columnVisibility: NavigationSplitViewVisibility = .all
    @Published var showInspector = true

    @Published var modelCapabilities = ModelCapabilities.none
    @Published var reasoningSelection: String?
    @Published var selectedModelInfo: OllamaModelInfo?
    @Published var pendingApproval: ApprovalRequest?
    @Published var showModelPicker = false
    @Published var showProjectBrowser = false
    @Published var currentProject: Project?
    @Published var mcpTools: [MCPTool] = []
    @Published var mcpStatus = "No MCP servers"
    @Published var gitStatus = GitStatus()
    @Published var showGitPanel = false
    @Published var showArchived = false
    @Published var renameTarget: Session?
    @Published var pendingDraft: String?
    @Published var editingProvider: Provider?
    @Published var settingsSection: SettingsSection = .general

    let settings = SettingsStore()
    let usage = UsageStore()
    let projects = ProjectStore()
    let plugins = PluginStore()
    let skills = SkillStore()
    let mcp = MCPManager()
    let remote = RemoteServer()
    private let defaults = UserDefaults.standard
    private var runTask: Task<Void, Never>?
    private var approvalContinuation: CheckedContinuation<ApprovalDecision, Never>?
    private let openCodeSessionID = UUID().uuidString

    init() {
        if let snapshot = SessionPersistence.load(), !snapshot.sessions.isEmpty {
            sessions = snapshot.sessions
            selectedID = snapshot.selectedID ?? snapshot.sessions.first?.id
            for sessionIndex in sessions.indices {
                for messageIndex in sessions[sessionIndex].messages.indices {
                    sessions[sessionIndex].messages[messageIndex].isStreaming = false
                }
            }
        } else {
            newSession()
        }

        let defaultProject = projects.open(path: settings.workspacePath)
        currentProject = defaultProject
        plugins.reload()
        skills.reload(
            workspace: URL(fileURLWithPath: defaultProject.path, isDirectory: true),
            extraDirectories: plugins.enabledSkillDirectories
        )
        for index in sessions.indices where sessions[index].projectPath.isEmpty {
            sessions[index].projectPath = defaultProject.path
        }
        if let selectedID,
           let selected = sessions.first(where: { $0.id == selectedID }),
           selected.projectPath == defaultProject.path {
            // Keep the restored selection.
        } else if let latest = sessions
            .filter({ $0.projectPath == defaultProject.path })
            .max(by: { $0.updatedAt < $1.updatedAt }) {
            self.selectedID = latest.id
        } else {
            newSession()
        }

        if let raw = defaults.string(forKey: "selectedProvider"),
           let provider = Provider(rawValue: raw),
           let id = defaults.string(forKey: "selectedModel") {
            choice = ModelChoice(provider: provider, id: id)
        }
        remote.onText = { [weak self] text in
            self?.submit(text)
        }
        remote.onStop = { [weak self] in
            self?.stop()
        }
        remote.onCompact = { [weak self] in
            self?.compactCurrentSession()
        }
        remote.statusProvider = { [weak self] in
            guard let self else { return [:] }
            return [
                "project": self.currentProject?.name ?? "Project",
                "model": self.choice.display,
                "streaming": self.isStreaming,
                "queue": self.selected?.queue.count ?? 0,
                "context": self.contextUsage.label,
            ]
        }

        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            self.saveSessions()
            self.remote.stop()
            Task { await self.mcp.stopAll() }
        }

        if !settings.mcpServers.isEmpty || !plugins.enabledMCPServers.isEmpty {
            mcpStatus = "Connecting…"
        }

        Task {
            await refreshChoices()
            await refreshCapabilities()
            await reloadMCP()
            await refreshGit()
        }
    }

    var projectURL: URL {
        URL(
            fileURLWithPath: currentProject?.path ?? settings.workspacePath,
            isDirectory: true
        ).standardizedFileURL
    }

    func reloadSkills() {
        skills.reload(workspace: projectURL, extraDirectories: plugins.enabledSkillDirectories)
    }

    func reloadPlugins() {
        plugins.reload()
        reloadSkills()
        Task { await reloadMCP() }
    }

    func reloadMCP() async {
        let configured = settings.mcpServers + plugins.enabledMCPServers
        guard !configured.isEmpty else {
            mcpTools = []
            mcpStatus = "No MCP servers"
            return
        }
        mcpStatus = "Connecting…"
        let result = await mcp.connect(configured)
        mcpTools = result.tools
        if result.errors.isEmpty {
            mcpStatus = result.tools.isEmpty ? "No MCP tools" : "\(result.tools.count) MCP tools"
        } else {
            mcpStatus = result.errors.joined(separator: " · ")
        }
    }

    func refreshGit() async {
        gitStatus = await GitService.status(in: projectURL)
    }

    func openProject(_ project: Project) {
        currentProject = projects.open(path: project.path)
        settings.workspacePath = project.path
        reloadSkills()
        Task { await refreshGit() }
        if let latest = sessions
            .filter({ $0.projectPath == project.path })
            .max(by: { $0.updatedAt < $1.updatedAt }) {
            selectedID = latest.id
        } else {
            newSession()
        }
        saveSessions()
    }

    func chooseProject() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = projectURL
        if panel.runModal() == .OK, let url = panel.url {
            let project = projects.open(path: url.path)
            openProject(project)
        }
    }

    var filteredSessions: [Session] {
        let projectPath = currentProject?.path ?? settings.workspacePath
        let scoped = sessions.filter { $0.projectPath == projectPath && !$0.archived }
        guard !search.isEmpty else { return scoped }
        return scoped.filter {
            $0.title.localizedCaseInsensitiveContains(search) ||
            $0.preview.localizedCaseInsensitiveContains(search)
        }
    }

    var archivedSessions: [Session] {
        let projectPath = currentProject?.path ?? settings.workspacePath
        return sessions.filter { $0.projectPath == projectPath && $0.archived }
    }

    var selected: Session? {
        get { sessions.first { $0.id == selectedID } }
        set {
            guard let newValue, let index = sessions.firstIndex(where: { $0.id == newValue.id }) else { return }
            sessions[index] = newValue
        }
    }

    var reasoningTitle: String {
        guard let reasoningSelection,
              let option = modelCapabilities.reasoningOptions.first(where: { $0.id == reasoningSelection }) else {
            return "Not exposed"
        }
        return option.title
    }

    var contextLimit: Int? {
        settings.contextWindowOverride(for: choice) ?? modelCapabilities.contextWindow
    }

    var contextUsage: ContextUsage {
        guard let session = selected else {
            return ContextUsage(used: 0, limit: contextLimit)
        }
        let system = ChatMessage(role: .system, text: systemPrompt())
        let messages = [system] + session.messages.filter {
            $0.role != .system && !($0.role == .assistant && $0.text.isEmpty)
        }
        return ContextUsage(used: TokenEstimator.estimate(messages), limit: contextLimit)
    }

    var contextLabel: String {
        contextUsage.label
    }

    func newSession() {
        var session = Session(title: "New Task")
        session.projectPath = currentProject?.path ?? settings.workspacePath
        sessions.insert(session, at: 0)
        selectedID = session.id
        saveSessions()
    }

    func delete(_ id: UUID) {
        sessions.removeAll { $0.id == id }
        if selectedID == id { selectedID = filteredSessions.first?.id ?? sessions.first?.id }
        if sessions.isEmpty { newSession() }
        saveSessions()
    }

    func archive(_ id: UUID) {
        guard let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].archived = true
        sessions[index].updatedAt = Date()
        if selectedID == id {
            selectedID = filteredSessions.first?.id
        }
        saveSessions()
    }

    func unarchive(_ id: UUID) {
        guard let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].archived = false
        sessions[index].updatedAt = Date()
        selectedID = id
        saveSessions()
    }

    func beginRename(_ id: UUID) {
        renameTarget = sessions.first { $0.id == id }
    }

    func commitRename(_ id: UUID, title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].title = trimmed
        sessions[index].updatedAt = Date()
        renameTarget = nil
        saveSessions()
    }

    func fork(_ id: UUID) {
        guard let source = sessions.first(where: { $0.id == id }) else { return }
        let copiedMessages = source.messages.map { message in
            ChatMessage(
                role: message.role,
                text: message.text,
                isStreaming: false,
                model: message.model,
                toolName: message.toolName,
                toolStatus: message.toolStatus,
                attachmentPath: message.attachmentPath,
                attachments: message.attachments
            )
        }
        var fork = Session(
            title: "\(source.title) fork",
            projectPath: source.projectPath,
            messages: copiedMessages,
            plan: source.plan,
            approvedTools: source.approvedTools
        )
        fork.updatedAt = Date()
        sessions.insert(fork, at: 0)
        selectedID = fork.id
        saveSessions()
    }

    func editMessage(_ sessionID: UUID, _ messageID: UUID) {
        guard !isStreaming,
              let index = sessions.firstIndex(where: { $0.id == sessionID }),
              let messageIndex = sessions[index].messages.firstIndex(where: { $0.id == messageID }),
              sessions[index].messages[messageIndex].role == .user else { return }
        let text = sessions[index].messages[messageIndex].text
        sessions[index].messages.removeSubrange(messageIndex...)
        sessions[index].updatedAt = Date()
        pendingDraft = text
        saveSessions()
    }

    func retryLastTurn() {
        guard !isStreaming, let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        guard let userIndex = sessions[index].messages.lastIndex(where: { $0.role == .user }) else { return }
        let text = sessions[index].messages[userIndex].text
        let attachments = sessions[index].messages[userIndex].attachments
        sessions[index].messages.removeSubrange(userIndex...)
        sessions[index].updatedAt = Date()
        saveSessions()
        send(text, attachments: attachments)
    }

    func clearHistory() {
        sessions = []
        newSession()
    }

    func choose(_ choice: ModelChoice) {
        self.choice = choice
        defaults.set(choice.provider.rawValue, forKey: "selectedProvider")
        defaults.set(choice.id, forKey: "selectedModel")
        Task { await refreshCapabilities() }
    }

    func setReasoning(_ value: String?) {
        reasoningSelection = value
        settings.setReasoning(value, for: choice)
    }

    func refreshChoices() async {
        var next: [ModelChoice] = []
        var localError: String?

        do {
            let client = OllamaClient(baseURL: URL(string: settings.localURL) ?? URL(string: Provider.local.defaultBaseURL)!)
            let models = try await client.models()
            localModels = models.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
            next += localModels.map { ModelChoice(provider: .local, id: $0.name) }
        } catch {
            localModels = []
            localError = "Local server offline"
        }

        settings.refreshKeys()
        for provider in [Provider.openCode, .openAI, .anthropic, .gemini] where settings.savedKeys.contains(provider) {
            let model = settings.model(for: provider)
            if !model.isEmpty {
                next.append(ModelChoice(provider: provider, id: model))
            }
        }

        choices = next

        if !next.contains(choice) {
            if let firstLocal = localModels.first {
                choose(ModelChoice(provider: .local, id: firstLocal.name))
            } else if let first = next.first {
                choose(first)
            }
        }

        let cloudCount = next.filter { $0.provider != .local }.count
        if let localError {
            status = cloudCount > 0 ? "\(localError) · \(cloudCount) cloud ready" : localError
        } else {
            let localCount = localModels.count
            status = "\(localCount) local" + (cloudCount > 0 ? " · \(cloudCount) cloud" : "")
        }

        await refreshCapabilities()
    }

    func refreshCapabilities() async {
        let target = choice
        var info: OllamaModelInfo?
        if target.provider == .local {
            do {
                info = try await OllamaClient(
                    baseURL: URL(string: settings.localURL) ?? URL(string: Provider.local.defaultBaseURL)!
                ).show(model: target.id)
            } catch {
                info = nil
            }
        }
        guard choice == target else { return }

        selectedModelInfo = info
        let capabilities = ModelCatalog.capabilities(for: target, ollama: info)
        modelCapabilities = capabilities

        if let current = reasoningSelection,
           capabilities.reasoningOptions.contains(where: { $0.id == current }) {
            return
        }
        if let stored = settings.reasoningValue(for: target),
           capabilities.reasoningOptions.contains(where: { $0.id == stored }) {
            reasoningSelection = stored
            return
        }
        reasoningSelection = capabilities.defaultReasoning
    }

    func send(_ text: String, attachments: [String] = []) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isStreaming, let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        guard choices.contains(choice) else {
            error = "Choose a model before sending."
            return
        }
        if choice.provider != .local, settings.key(for: choice.provider) == nil {
            error = "Add an API key for \(choice.provider.title) in Settings."
            return
        }
        error = nil

        let isFirstMessage = !sessions[index].messages.contains { $0.role == .user }
        if isFirstMessage {
            sessions[index].title = String(trimmed.prefix(48))
        }
        sessions[index].messages.append(
            ChatMessage(role: .user, text: trimmed, attachments: attachments)
        )
        sessions[index].updatedAt = Date()
        isStreaming = true

        let runChoice = choice
        runTask = Task { [weak self] in
            await self?.runLoop(sessionID: id, choice: runChoice)
        }
    }

    func submit(_ text: String, attachments: [String] = []) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if trimmed.hasPrefix("/") {
            handleCommand(trimmed)
        } else if isStreaming {
            enqueue(trimmed, attachments: attachments)
        } else {
            send(trimmed, attachments: attachments)
        }
    }

    func enqueue(_ text: String, attachments: [String] = []) {
        guard let id = selectedID, let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].queue.append(QueuedPrompt(text: text, attachments: attachments))
        sessions[index].updatedAt = Date()
        saveSessions()
    }

    func removeQueued(_ id: UUID) {
        for index in sessions.indices {
            sessions[index].queue.removeAll { $0.id == id }
        }
        saveSessions()
    }

    func clearQueue() {
        guard let id = selectedID, let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].queue.removeAll()
        saveSessions()
    }

    private func startNextQueued(_ sessionID: UUID) {
        guard !isStreaming,
              let index = sessions.firstIndex(where: { $0.id == sessionID }),
              !sessions[index].queue.isEmpty else { return }
        let next = sessions[index].queue.removeFirst()
        saveSessions()
        send(next.text, attachments: next.attachments)
    }

    func clearCurrentSession() {
        guard !isStreaming, let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].messages = []
        sessions[index].plan = []
        sessions[index].updatedAt = Date()
        saveSessions()
    }

    func compactCurrentSession() {
        guard !isStreaming, let id = selectedID else {
            error = "Stop the current response before compacting."
            return
        }
        let runChoice = choice
        isStreaming = true
        runTask = Task { [weak self] in
            guard let self else { return }
            await compactSession(sessionID: id, choice: runChoice)
            isStreaming = false
            runTask = nil
            saveSessions()
        }
    }

    private func handleCommand(_ text: String) {
        let parts = text.split(separator: " ", maxSplits: 1).map(String.init)
        let command = parts.first?.lowercased() ?? ""
        let remainder = parts.count > 1 ? parts[1] : ""
        switch command {
        case "/new":
            newSession()
        case "/clear":
            clearCurrentSession()
        case "/compact":
            compactCurrentSession()
        case "/stop":
            stop()
        case "/model":
            showModelPicker = true
        case "/settings", "/config":
            openSettings(.general)
        case "/tools", "/permissions":
            openSettings(.tools)
        case "/providers":
            openSettings(.providers)
        case "/models":
            openSettings(.models)
        case "/queue":
            if remainder.isEmpty {
                appendCommandMessage(queueSummary())
            } else {
                enqueue(remainder)
            }
        case "/retry":
            retryLastTurn()
        case "/fork":
            if let id = selectedID { fork(id) }
        case "/archive":
            if let id = selectedID { archive(id) }
        case "/rename", "/title":
            if remainder.isEmpty {
                appendCommandMessage("Usage: /rename New task name")
            } else if let id = selectedID {
                commitRename(id, title: remainder)
            }
        case "/context":
            appendCommandMessage(contextSummary())
        case "/todos":
            appendCommandMessage(todosSummary())
        case "/cost":
            appendCommandMessage(usageSummary())
        case "/diff":
            showGitPanel = true
        case "/review":
            reviewChanges(focus: .general)
        case "/security-review":
            reviewChanges(focus: .security)
        case "/rewind":
            rewindLastTurn()
        case "/export":
            exportSession()
        case "/doctor":
            Task {
                appendCommandMessage(await doctorReport())
            }
        case "/init":
            initializeAgentsFile()
        case "/resume":
            resumeSummary()
        case "/memory", "/instructions":
            showProjectMemory()
        case "/browser":
            let result = BrowserController.shared.read()
            appendCommandMessage(result.output)
        case "/add-dir":
            appendCommandMessage("Use Projects → Open Folder… to switch the workspace. Additional writable roots are not implemented yet.")
        case "/status":
            appendCommandMessage(statusSummary())
        case "/remote":
            if let url = remote.url {
                appendCommandMessage("Phone control:\n\(url)\n\nOpen it on a device on the same network. The token in the URL is required.")
            } else {
                appendCommandMessage("Phone control is off. Enable it in Settings → Tools.")
            }
        case "/plugins":
            if plugins.plugins.isEmpty {
                appendCommandMessage("No plugins found. Add a plugin.json under \(plugins.directory.path).")
            } else {
                appendCommandMessage("Plugins:\n" + plugins.plugins.map {
                    "- \($0.name)\($0.description.isEmpty ? "" : ": \($0.description)")"
                }.joined(separator: "\n"))
            }
        case "/mcp":
            if mcpTools.isEmpty {
                appendCommandMessage("No MCP tools. \(mcpStatus). Add a stdio server in Settings → MCP.")
            } else {
                appendCommandMessage("MCP status: \(mcpStatus)\n" + mcpTools.map {
                    "- \($0.toolName)\($0.description.isEmpty ? "" : ": \($0.description)")"
                }.joined(separator: "\n"))
            }
        case "/skills":
            if skills.skills.isEmpty {
                appendCommandMessage("No skills found. Add a folder with SKILL.md under \(skills.directory.path).")
            } else {
                appendCommandMessage("Skills:\n" + skills.skills.map {
                    "- \($0.name)\($0.description.isEmpty ? "" : ": \($0.description)")"
                }.joined(separator: "\n"))
            }
        case "/usage":
            appendCommandMessage(usageSummary())
        case "/help", "/":
            appendCommandMessage(
                "Commands:\n" + SlashCommands.all.map { "\($0.id) — \($0.detail)" }.joined(separator: "\n")
            )
        default:
            appendCommandMessage("Unknown command \(command). Type /help for the list.")
        }
    }

    private var projectInstructions: String? {
        let paths = [
            projectURL.appendingPathComponent("AGENTS.md"),
            projectURL.appendingPathComponent(".talos/AGENTS.md"),
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
                .appendingPathComponent("Talos/AGENTS.md"),
        ].compactMap { $0 }
        for path in paths {
            guard let text = try? String(contentsOf: path, encoding: .utf8),
                  !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                continue
            }
            let clipped = text.count > 30_000 ? String(text.prefix(30_000)) + "\n…" : text
            return "Project instructions from \(path.path):\n\(clipped)"
        }
        return nil
    }

    private func systemPrompt() -> String {
        var prompt = ToolPrompt.system(context: toolContext())
        if let projectInstructions {
            prompt += "\n\n" + projectInstructions
        }
        return prompt
    }

    private func contextSummary() -> String {
        let usage = contextUsage
        let messageTokens = TokenEstimator.estimate(
            (selected?.messages ?? []).filter { $0.role != .system }
        )
        let systemTokens = TokenEstimator.estimate(systemPrompt())
        var lines = [
            "Context: \(usage.label)",
            "Messages: \(TokenEstimator.format(messageTokens)) tokens",
            "System and tools: \(TokenEstimator.format(systemTokens)) tokens",
            "Skills: \(skills.enabled.count)",
            "MCP tools: \(mcpTools.count)",
        ]
        if let fraction = usage.fraction {
            lines.append("Window used: \(Int(fraction * 100))%")
        }
        lines.append("Automatic compaction: \(settings.autoCompact ? "on at \(Int(settings.compactionThreshold * 100))%" : "off")")
        return lines.joined(separator: "\n")
    }

    private func todosSummary() -> String {
        guard let plan = selected?.plan, !plan.isEmpty else {
            return "No plan yet. Ask the model to use update_plan."
        }
        return "Plan:\n" + plan.map {
            "\($0.status == .completed ? "✓" : $0.status == .inProgress ? "→" : "·") \($0.title)"
        }.joined(separator: "\n")
    }

    private enum ReviewFocus {
        case general
        case security
    }

    private func reviewChanges(focus: ReviewFocus) {
        guard !isStreaming else {
            error = "Stop the current response before starting a review."
            return
        }
        Task {
            let diff = await GitService.diff(in: projectURL)
            guard !diff.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !diff.hasPrefix("No diff") else {
                appendCommandMessage("No working tree changes to review.")
                return
            }
            let instruction = focus == .security
                ? "Perform a security review of the current working tree changes. Look for injection, unsafe shell or file handling, secret exposure, authorization mistakes, and data leaks. Be concrete and point to files."
                : "Review the current working tree changes for correctness, regressions, and missing tests. Be concrete and point to files."
            let prompt = """
            \(instruction)

            ```diff
            \(String(diff.prefix(30_000)))
            ```
            """
            send(prompt)
        }
    }

    private func rewindLastTurn() {
        guard !isStreaming, let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        guard let userIndex = sessions[index].messages.lastIndex(where: { $0.role == .user }) else {
            appendCommandMessage("Nothing to rewind.")
            return
        }
        sessions[index].messages.removeSubrange(userIndex...)
        sessions[index].updatedAt = Date()
        saveSessions()
        appendCommandMessage("Rewound the last turn.")
    }

    private func exportSession() {
        guard let session = selected else { return }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let slug = session.title
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let name = ".talos/exports/\(slug.isEmpty ? "task" : slug)-\(formatter.string(from: Date())).md"
        var lines = [
            "# \(session.title)",
            "",
            "- Project: \(projectURL.path)",
            "- Created: \(session.createdAt.formatted())",
            "",
        ]
        for message in session.messages where message.role != .system {
            switch message.role {
            case .user:
                lines.append("## User\n\n\(message.text)\n")
            case .assistant:
                lines.append("## \(message.model ?? "Assistant")\n\n\(message.text)\n")
            case .tool:
                lines.append("### Tool · \(message.toolName ?? "tool")\n\n```\n\(message.text)\n```\n")
            case .system:
                break
            }
        }
        let content = lines.joined(separator: "\n")
        do {
            let url = try DocumentWriter.create(name: name, format: "md", content: content, in: projectURL)
            appendCommandMessage("Exported to \(url.path)")
        } catch {
            appendCommandMessage("Export failed: \(error.localizedDescription)")
        }
    }

    private func doctorReport() async -> String {
        let git = await GitService.status(in: projectURL)
        var lines = [
            "Talos Doctor",
            "Workspace: \(projectURL.path)",
            "Ollama: \(status)",
            "Selected: \(choice.display) · reasoning \(reasoningTitle)",
            "Context: \(contextUsage.label)",
            "MCP: \(mcpStatus) · \(mcpTools.count) tools",
            "Skills: \(skills.enabled.count) enabled",
            "Plugins: \(plugins.enabled.count) enabled",
            "Git: \(git.branch ?? "no repository") · \(git.files.count) changed",
            "Accessibility: \(ToolPermission.accessibility ? "granted" : "missing")",
            "Screen Recording: \(ToolPermission.screenRecording ? "granted" : "missing")",
            "Sandbox: \(settings.sandboxMode.title) · network \(settings.networkAccess ? "on" : "off")",
            "Approvals: \(settings.approvalPolicy == .onRequest ? "on request" : "never")",
            "History: \(sessions.count) tasks · \(TokenEstimator.format(usage.totalTokens)) tokens",
        ]
        for provider in Provider.allCases where provider != .local {
            let key = settings.savedKeys.contains(provider) ? "key saved" : "no key"
            lines.append("\(provider.title): \(settings.model(for: provider)) · \(key)")
        }
        return lines.joined(separator: "\n")
    }

    private func showProjectMemory() {
        let target = projectURL.appendingPathComponent("AGENTS.md")
        guard FileManager.default.fileExists(atPath: target.path) else {
            appendCommandMessage("No AGENTS.md in this project. Run /init to create one.")
            return
        }
        NSWorkspace.shared.open(target)
        appendCommandMessage("Opened \(target.path). Talos includes this file in the system prompt.")
    }

    private func initializeAgentsFile() {
        let target = projectURL.appendingPathComponent("AGENTS.md")
        guard !FileManager.default.fileExists(atPath: target.path) else {
            appendCommandMessage("AGENTS.md already exists at \(target.path).")
            return
        }
        let content = """
        # \(currentProject?.name ?? "Project")

        ## Commands

        - Build:
        - Test:
        - Lint:

        ## Conventions

        - Keep changes scoped to the request.
        - Run the focused checks for changed behavior.
        """
        do {
            let url = try DocumentWriter.create(name: "AGENTS", format: "md", content: content, in: projectURL)
            appendCommandMessage("Created \(url.path). Fill in the project-specific commands.")
        } catch {
            appendCommandMessage("Could not create AGENTS.md: \(error.localizedDescription)")
        }
    }

    private func resumeSummary() {
        let recent = filteredSessions.prefix(10)
        guard !recent.isEmpty else {
            appendCommandMessage("No tasks in this project yet.")
            return
        }
        appendCommandMessage("Recent tasks:\n" + recent.map {
            "- \($0.title)\($0.archived ? " (archived)" : "")"
        }.joined(separator: "\n") + "\n\nSelect one in the sidebar to resume it.")
    }

    private func queueSummary() -> String {
        guard let session = selected else { return "No task selected." }
        guard !session.queue.isEmpty else { return "Queue is empty." }
        return "Queued:\n" + session.queue.map { "• \($0.text)" }.joined(separator: "\n")
    }

    private func statusSummary() -> String {
        let queue = selected?.queue.count ?? 0
        return """
        Model: \(choice.display)
        Reasoning: \(reasoningTitle)
        Context: \(contextUsage.label)
        Workspace: \(settings.workspacePath)
        Sandbox: \(settings.sandboxMode.title)
        Approvals: \(settings.approvalPolicy == .onRequest ? "On request" : "Never")
        Skills: \(skills.enabled.count)
        Plugins: \(plugins.enabled.count)
        MCP tools: \(mcpTools.count)
        Phone control: \(remote.running ? "on" : "off")
        Queue: \(queue)
        """
    }

    private func usageSummary() -> String {
        guard let id = selectedID else { return "No task selected." }
        let sessionUsage = usage.tokens(for: id)
        let qualifier = sessionUsage.estimated ? "estimated" : "reported"
        return """
        This task: \(TokenEstimator.format(sessionUsage.total)) tokens (\(TokenEstimator.format(sessionUsage.prompt)) prompt, \(TokenEstimator.format(sessionUsage.completion)) completion, \(qualifier)).
        All tasks: \(TokenEstimator.format(usage.totalTokens)) tokens.
        """
    }

    private func appendCommandMessage(_ text: String) {
        guard let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].messages.append(
            ChatMessage(role: .assistant, text: text, model: "Command")
        )
        sessions[index].updatedAt = Date()
        saveSessions()
    }

    func openSettings(_ section: SettingsSection = .general) {
        settingsSection = section
        if !NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil) {
            NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil)
        }
    }

    func stop() {
        resolveApproval(.deny)
        runTask?.cancel()
        runTask = nil
        if let id = selectedID,
           let index = sessions.firstIndex(where: { $0.id == id }),
           var last = sessions[index].messages.last,
           last.isStreaming,
           last.text.isEmpty {
            last.text = "Stopped."
            last.isStreaming = false
            sessions[index].messages[sessions[index].messages.count - 1] = last
        }
        isStreaming = false
        saveSessions()
    }

    func resolveApproval(_ decision: ApprovalDecision) {
        pendingApproval = nil
        let continuation = approvalContinuation
        approvalContinuation = nil
        continuation?.resume(returning: decision)
    }

    func saveSessions() {
        SessionPersistence.save(sessions: sessions, selectedID: selectedID)
    }

    private func runLoop(sessionID: UUID, choice runChoice: ModelChoice) async {
        defer {
            let wasCancelled = Task.isCancelled
            isStreaming = false
            runTask = nil
            saveSessions()
            if !wasCancelled {
                playCompletionFeedback()
                startNextQueued(sessionID)
            }
            Task { await refreshGit() }
        }

        for _ in 0..<settings.maxToolSteps {
            if Task.isCancelled { return }
            guard var index = sessions.firstIndex(where: { $0.id == sessionID }) else { return }

            if settings.autoCompact,
               let limit = contextLimit,
               sessions[index].messages.count > 4,
               Double(contextTokens(for: sessions[index])) > Double(limit) * settings.compactionThreshold {
                await compactSession(sessionID: sessionID, choice: runChoice)
                guard let refreshed = sessions.firstIndex(where: { $0.id == sessionID }) else { return }
                index = refreshed
            }

            let history = sessions[index].messages
            let assistantID = UUID()
            sessions[index].messages.append(
                ChatMessage(id: assistantID, role: .assistant, text: "", isStreaming: true, model: runChoice.display)
            )
            sessions[index].updatedAt = Date()

            do {
                let raw = try await streamModel(
                    choice: runChoice,
                    history: history,
                    assistantID: assistantID,
                    sessionID: sessionID
                )
                finishMessage(sessionID, assistantID)
                guard let parsed = ToolParser.extract(from: raw) else {
                    break
                }
                setMessageText(sessionID, assistantID, parsed.cleaned)

                let approved = await requestApproval(for: parsed.call, sessionID: sessionID)
                guard approved else {
                    appendToolMessage(sessionID, call: parsed.call, result: .denied("Denied by the user."))
                    continue
                }

                let result = await executeTool(parsed.call, sessionID: sessionID)
                appendToolMessage(sessionID, call: parsed.call, result: result)
            } catch {
                if !Task.isCancelled {
                    setMessageText(sessionID, assistantID, "Request failed. \(error.localizedDescription)")
                    finishMessage(sessionID, assistantID)
                    self.error = error.localizedDescription
                }
                return
            }
        }
    }

    private func streamModel(
        choice: ModelChoice,
        history: [ChatMessage],
        assistantID: UUID,
        sessionID: UUID
    ) async throws -> String {
        let system = ChatMessage(role: .system, text: systemPrompt())
        let normalized = history
            .filter { $0.role != .system && !($0.role == .assistant && $0.text.isEmpty) }
            .map { message in
                if message.role == .tool {
                    var attachments = message.attachments
                    if let path = message.attachmentPath, !attachments.contains(path) {
                        attachments.append(path)
                    }
                    return ChatMessage(
                        role: .tool,
                        text: "Tool result (\(message.toolName ?? "tool")):\n\(message.text)",
                        toolName: message.toolName,
                        toolStatus: message.toolStatus,
                        attachmentPath: message.attachmentPath,
                        attachments: attachments
                    )
                }
                if message.role == .user, !message.attachments.isEmpty {
                    return ChatMessage(
                        role: .user,
                        text: message.text + "\n\nAttached files:\n" + message.attachments.map { "- \($0)" }.joined(separator: "\n"),
                        attachments: message.attachments
                    )
                }
                return message
            }
        let turns = [system] + normalized
        var generation = settings.generation(reasoning: reasoningSelection)
        generation.allowImages = modelCapabilities.supportsVision

        let (raw, reportedUsage) = try await streamCompletion(
            choice: choice,
            messages: turns,
            generation: generation
        ) { chunk in
            self.appendChunk(chunk, to: assistantID, sessionID: sessionID)
        }

        usage.record(
            reportedUsage,
            provider: choice.provider,
            model: choice.id,
            sessionID: sessionID
        )
        return raw
    }

    private func streamCompletion(
        choice: ModelChoice,
        messages: [ChatMessage],
        generation: GenerationSettings,
        onChunk: ((String) -> Void)? = nil
    ) async throws -> (String, TokenUsage) {
        let stream: AsyncThrowingStream<ModelStreamEvent, Error>
        switch choice.provider {
        case .local:
            stream = OllamaClient(baseURL: URL(string: settings.localURL) ?? URL(string: Provider.local.defaultBaseURL)!)
                .stream(model: choice.id, messages: messages, settings: generation)
        case .openCode:
            stream = OpenAICompatibleClient(
                baseURL: settings.openCodeBase,
                apiKey: settings.key(for: .openCode) ?? "",
                usesCompletionTokens: false,
                sendsTemperature: true,
                sendsReasoningEffort: false,
                sendsImages: modelCapabilities.supportsVision,
                isOpenCode: true,
                openCodeSession: openCodeSessionID
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text, attachments: $0.attachments) },
                settings: generation
            )
        case .openAI:
            stream = OpenAICompatibleClient(
                baseURL: settings.openAIBase,
                apiKey: settings.key(for: .openAI) ?? "",
                usesCompletionTokens: true,
                sendsTemperature: false,
                sendsReasoningEffort: true,
                sendsImages: modelCapabilities.supportsVision
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text, attachments: $0.attachments) },
                settings: generation
            )
        case .gemini:
            stream = OpenAICompatibleClient(
                baseURL: settings.geminiBase,
                apiKey: settings.key(for: .gemini) ?? "",
                usesCompletionTokens: false,
                sendsTemperature: true,
                sendsReasoningEffort: false,
                sendsImages: modelCapabilities.supportsVision
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text, attachments: $0.attachments) },
                settings: generation
            )
        case .anthropic:
            stream = AnthropicClient(
                baseURL: settings.anthropicBase,
                apiKey: settings.key(for: .anthropic) ?? "",
                sendsImages: modelCapabilities.supportsVision
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text, attachments: $0.attachments) },
                settings: generation
            )
        }

        var raw = ""
        var reportedUsage: TokenUsage?
        for try await event in stream {
            if Task.isCancelled { break }
            switch event {
            case .text(let chunk):
                raw += chunk
                onChunk?(chunk)
            case .usage(let usage):
                reportedUsage = usage
            }
        }
        return (raw, reportedUsage ?? TokenEstimator.estimatedUsage(prompt: messages, completion: raw))
    }

    private func contextTokens(for session: Session) -> Int {
        let system = ChatMessage(role: .system, text: systemPrompt())
        let messages = [system] + session.messages.filter {
            $0.role != .system && !($0.role == .assistant && $0.text.isEmpty)
        }
        return TokenEstimator.estimate(messages)
    }

    private func compactSession(sessionID: UUID, choice: ModelChoice) async {
        guard let index = sessions.firstIndex(where: { $0.id == sessionID }),
              sessions[index].messages.count > 2 else {
            return
        }

        let limit = settings.contextWindowOverride(for: choice) ?? modelCapabilities.contextWindow
        let maxCharacters = Int(Double(limit ?? 40_000) * 0.5 * 4)
        let transcript = sessions[index].messages
            .filter { $0.role != .system && !($0.role == .assistant && $0.text.isEmpty) }
            .map { message -> String in
                if message.role == .tool {
                    return "Tool result (\(message.toolName ?? "tool")):\n\(message.text)"
                }
                return "\(message.role == .user ? "User" : "Assistant"):\n\(message.text)"
            }
            .joined(separator: "\n\n")
        let clipped = transcript.count > maxCharacters
            ? String(transcript.suffix(maxCharacters))
            : transcript

        let prompt = [
            ChatMessage(
                role: .system,
                text: "Summarize a coding conversation so work can continue after older messages are removed. Preserve decisions, file paths, commands, tool results, errors, and open tasks. Return only the summary."
            ),
            ChatMessage(role: .user, text: "Conversation to summarize:\n\n\(clipped)"),
        ]

        do {
            let (summary, used) = try await streamCompletion(
                choice: choice,
                messages: prompt,
                generation: settings.generation(reasoning: nil)
            )
            usage.record(used, provider: choice.provider, model: choice.id, sessionID: sessionID)
            let cleaned = summary.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !cleaned.isEmpty else {
                error = "Compaction returned an empty summary."
                return
            }
            let retained = Array(sessions[index].messages.suffix(2))
            sessions[index].messages = [
                ChatMessage(role: .assistant, text: "Earlier conversation summary:\n\(cleaned)", model: "Context summary"),
            ] + retained
            sessions[index].updatedAt = Date()
            saveSessions()
        } catch {
            self.error = "Compaction failed: \(error.localizedDescription)"
        }
    }

    private func requestApproval(for call: ToolCall, sessionID: UUID) async -> Bool {
        guard settings.approvalPolicy == .onRequest, ToolRunner.requiresApproval(call) else {
            return true
        }
        if let index = sessions.firstIndex(where: { $0.id == sessionID }),
           sessions[index].approvedTools.contains(call.name) {
            return true
        }
        let decision = await withCheckedContinuation { continuation in
            if let existing = approvalContinuation {
                existing.resume(returning: .deny)
            }
            approvalContinuation = continuation
            pendingApproval = ApprovalRequest(toolName: call.name, summary: call.summary)
        }
        switch decision {
        case .deny:
            return false
        case .allowOnce:
            return true
        case .allowForTask:
            if let index = sessions.firstIndex(where: { $0.id == sessionID }),
               !sessions[index].approvedTools.contains(call.name) {
                sessions[index].approvedTools.append(call.name)
                saveSessions()
            }
            return true
        }
    }

    private func executeTool(_ call: ToolCall, sessionID: UUID) async -> ToolResult {
        if call.name == "update_plan" {
            return updatePlan(call, sessionID: sessionID)
        }
        if call.name.hasPrefix("mcp__") {
            let arguments = call.arguments.mapValues(\.anyValue)
            switch await mcp.call(toolName: call.name, arguments: arguments) {
            case .success(let output):
                return .ok(output)
            case .failure(let error):
                return .fail(error.localizedDescription)
            }
        }
        return await ToolRunner(context: toolContext()).run(call)
    }

    private func updatePlan(_ call: ToolCall, sessionID: UUID) -> ToolResult {
        guard settings.enablePlan else { return .fail("Plan updates are disabled in Settings.") }
        guard let rawSteps = call.array("steps") else {
            return .fail("update_plan requires a steps array.")
        }
        var steps: [PlanStep] = []
        for raw in rawSteps {
            guard let object = raw.object, let title = object["title"]?.string, !title.isEmpty else {
                continue
            }
            let rawStatus = object["status"]?.string ?? "pending"
            let status: PlanStatus
            switch rawStatus.lowercased() {
            case "in_progress", "in-progress", "active":
                status = .inProgress
            case "completed", "done":
                status = .completed
            default:
                status = .pending
            }
            steps.append(PlanStep(title: title, status: status))
        }
        guard !steps.isEmpty else { return .fail("No valid plan steps were provided.") }
        if let index = sessions.firstIndex(where: { $0.id == sessionID }) {
            sessions[index].plan = steps
            sessions[index].updatedAt = Date()
        }
        return .ok("Plan updated with \(steps.count) steps.")
    }

    private func toolContext() -> ToolContext {
        let workspace = projectURL.path
        return ToolContext(
            workspace: workspace,
            sandbox: settings.sandboxMode,
            network: settings.networkAccess,
            enabledShell: settings.enableShell,
            enabledFileEdits: settings.enableFileEdits,
            enabledComputerUse: settings.enableComputerUse,
            enabledWeb: settings.enableWeb,
            enabledBrowser: settings.enableBrowser,
            enabledPlan: settings.enablePlan,
            skills: skills.enabled,
            mcpTools: mcpTools
        )
    }

    private func appendChunk(_ chunk: String, to messageID: UUID, sessionID: UUID) {
        guard let sessionIndex = sessions.firstIndex(where: { $0.id == sessionID }),
              let messageIndex = sessions[sessionIndex].messages.firstIndex(where: { $0.id == messageID }) else {
            return
        }
        sessions[sessionIndex].messages[messageIndex].text += chunk
        sessions[sessionIndex].updatedAt = Date()
    }

    private func setMessageText(_ sessionID: UUID, _ messageID: UUID, _ text: String) {
        guard let sessionIndex = sessions.firstIndex(where: { $0.id == sessionID }),
              let messageIndex = sessions[sessionIndex].messages.firstIndex(where: { $0.id == messageID }) else {
            return
        }
        sessions[sessionIndex].messages[messageIndex].text = text
    }

    private func finishMessage(_ sessionID: UUID, _ messageID: UUID) {
        guard let sessionIndex = sessions.firstIndex(where: { $0.id == sessionID }),
              let messageIndex = sessions[sessionIndex].messages.firstIndex(where: { $0.id == messageID }) else {
            return
        }
        sessions[sessionIndex].messages[messageIndex].isStreaming = false
        sessions[sessionIndex].updatedAt = Date()
    }

    private func appendToolMessage(_ sessionID: UUID, call: ToolCall, result: ToolResult) {
        guard let index = sessions.firstIndex(where: { $0.id == sessionID }) else { return }
        sessions[index].messages.append(
            ChatMessage(
                role: .tool,
                text: result.output,
                toolName: call.name,
                toolStatus: result.status,
                attachmentPath: result.attachmentPath
            )
        )
        sessions[index].updatedAt = Date()
    }

    private func playCompletionFeedback() {
        if settings.completionSound {
            NSSound(named: "Glass")?.play()
        }
        guard settings.notifyOnFinish, !NSApp.isActive else { return }
        guard let session = selected else { return }
        NotificationManager.shared.notify(
            title: session.title,
            body: session.preview.isEmpty ? "Task finished." : session.preview
        )
    }
}
