import SwiftUI
import AppKit
import Combine
import CryptoKit

struct MessageEditTarget: Identifiable {
    let id = UUID()
    let sessionID: UUID
    let messageID: UUID
    let text: String
    let attachments: [String]
}

struct ApprovalRequest: Identifiable {
    let id = UUID()
    let toolName: String
    let summary: String
    let sessionID: UUID?
    var patchPreview: PatchPreview? = nil
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
    @Published var selectedID: UUID? {
        didSet {
            if oldValue != selectedID {
                scheduleProviderContextCount()
            }
        }
    }
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
    @Published var mcpResources: [MCPResource] = []
    @Published var mcpResourceTemplates: [MCPResourceTemplate] = []
    @Published var mcpStatus = "No MCP servers"
    @Published var mcpElicitation: MCPElicitationRequest?
    @Published var activeMCPApp: MCPUIApp?
    private var mcpElicitationContinuation: CheckedContinuation<MCPElicitationResponse, Never>?
    @Published var pendingSampling: MCPSamplingRequest?
    @Published private(set) var samplingApprovedServers: Set<String> = []
    private var samplingContinuation: CheckedContinuation<MCPSamplingApproval, Never>?
    @Published var pendingHookTrust: HookTrustRequest?
    private var hookTrustContinuation: CheckedContinuation<HookTrustDecision, Never>?
    @Published var approvalReview: ApprovalReview?
    @Published var approvalReviewing = false
    @Published var approvalReviewError: String?
    private var approvalReviewTask: Task<Void, Never>?
    @Published var openCodeModels: [OpenCodeModel] = []
    @Published var gitStatus = GitStatus()
    @Published var worktrees: [GitWorktreeInfo] = []
    @Published var showGitPanel = false
    @Published var showWorktrees = false
    @Published var showQuickSwitcher = false
    @Published var showArchived = false
    @Published var renameTarget: Session?
    @Published var pendingDraft: String?
    @Published var editingProvider: Provider?
    @Published var settingsSection: SettingsSection = .general
    @Published private(set) var managedPolicy: ManagedPolicy?
    @Published private(set) var policySource: String?
    @Published private(set) var policySignature = "Not configured"
    @Published private(set) var policyError: String?
    @Published private(set) var cloudTasks: [CloudTask] = []
    @Published private(set) var cloudServerStatus = "Off"
    @Published private(set) var cloudWorkerStatus = "Off"
    @Published private(set) var cloudEndpoint: String?
    @Published private(set) var activeRunSessionID: UUID?
    @Published var editingMessage: MessageEditTarget?
    private var pendingAssistantRetryOf: UUID?
    @Published private(set) var providerContextTokens: Int?
    @Published private(set) var providerContextLabel: String?
    private var providerContextKey: String?
    private var providerContextTask: Task<Void, Never>?
    @Published var showWorktreeSheet = false

    let settings = SettingsStore()
    let usage = UsageStore()
    let projects = ProjectStore()
    let plugins = PluginStore()
    let skills = SkillStore()
    let hooks = HookStore()
    let profiles = ProfileStore()
    let memories = MemoryStore()
    let chronicle = ChronicleStore()
    let mcp = MCPManager()
    let remote = RemoteServer()
    let cloudQueue = CloudTaskQueue()
    let appServer = AppServer()
    private var cloudServer: CloudTaskHTTPServer?
    private var cloudWorker: CloudTaskWorker?
    private let defaults = UserDefaults.standard
    private var runTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()
    private var approvalContinuation: CheckedContinuation<ApprovalDecision, Never>?
    private let openCodeSessionID = UUID().uuidString

    init() {
        appServer.host = self
        chronicle.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        let loadedPolicy = ManagedPolicyLoader.load()
        managedPolicy = loadedPolicy.policy
        policySource = loadedPolicy.sourcePath
        policySignature = loadedPolicy.signatureStatus
        policyError = loadedPolicy.error
        if let policy = loadedPolicy.policy {
            settings.applyManagedPolicy(policy)
            if let managedHooks = policy.hooks {
                hooks.applyManaged(managedHooks)
            }
        }

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
        if let policy = loadedPolicy.policy {
            applyManagedSelection(policy)
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
            await refreshCloudTasks()
            await refreshPluginCatalogs()
            startCloudServicesIfNeeded()
            startAppServerIfConfigured()

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
        plugins.reloadCatalogs()
        reloadSkills()
        Task {
            await refreshPluginCatalogs()
            await reloadMCP()
        }
    }

    func refreshPluginCatalogs() async {
        await plugins.loadRemoteCatalog(
            urlString: settings.pluginCatalogURL,
            publicKeyBase64: settings.pluginCatalogPublicKey
        )
    }

    func reloadMCP() async {
        let configured = settings.managedMCPLocked
            ? settings.mcpServers
            : settings.mcpServers + plugins.enabledMCPServers
        await mcp.setElicitationHandler { [weak self] request in
            guard let self else { return .decline }
            return await self.promptForElicitation(request)
        }
        await mcp.setSamplingHandler { [weak self] request in
            guard let self else { return .decline }
            return await self.handleSampling(request)
        }
        guard !configured.isEmpty else {
            mcpTools = []
            mcpResources = []
            mcpResourceTemplates = []
            mcpStatus = "No MCP servers"
            return
        }
        mcpStatus = "Connecting…"
        let result = await mcp.connect(configured)
        mcpTools = result.tools
        mcpResources = result.resources
        mcpResourceTemplates = result.templates
        if result.errors.isEmpty {
            mcpStatus = result.tools.isEmpty && result.resources.isEmpty
                ? "No MCP tools or resources"
                : "\(result.tools.count) tools · \(result.resources.count) resources"
        } else {
            mcpStatus = result.errors.joined(separator: " · ")
        }
    }

    func refreshMCPResources() async {
        let result = await mcp.refreshResources()
        mcpResources = result.0
        mcpResourceTemplates = result.1
        mcpStatus = "\(mcpTools.count) tools · \(result.0.count) resources · \(result.1.count) templates"
    }

    func readMCPResource(uri: String) async -> Result<MCPResourceContent, Error> {
        await mcp.readResource(uri: uri)
    }

    private var lastChronicleChangeCount = -1

    func refreshGit() async {
        gitStatus = await GitService.status(in: projectURL)
        worktrees = await GitService.listWorktrees(in: projectURL)
        let count = gitStatus.files.count
        if settings.chronicleEnabled,
           count >= 3,
           count != lastChronicleChangeCount {
            lastChronicleChangeCount = count
            chronicleRecord(
                .filesChanged,
                summary: "\(count) files changed",
                sessionID: selectedID
            )
        } else if count < 3 {
            lastChronicleChangeCount = count
        }
    }

    private func promptForElicitation(_ request: MCPElicitationRequest) async -> MCPElicitationResponse {
        if let existing = mcpElicitationContinuation {
            existing.resume(returning: .cancel)
            mcpElicitationContinuation = nil
        }
        return await withCheckedContinuation { continuation in
            mcpElicitationContinuation = continuation
            mcpElicitation = request
        }
    }

    func presentMCPApp(for toolName: String) async {
        guard let tool = mcpTools.first(where: { $0.toolName == toolName }),
              let uri = tool.uiResourceUri else { return }
        switch await mcp.readResource(uri: uri) {
        case .success(let content):
            guard let html = content.text, content.isHTML else { return }
            activeMCPApp = MCPUIApp(
                uri: uri,
                serverName: tool.serverName,
                title: tool.name,
                mimeType: content.mimeType ?? "text/html",
                html: html
            )
        case .failure(let error):
            mcpStatus = "App failed: \(error.localizedDescription)"
        }
    }

    func callMCPAppTool(
        _ name: String,
        serverName: String,
        arguments: [String: Any]
    ) async -> Result<String, Error> {
        guard let tool = mcpTools.first(where: {
            $0.serverName == serverName && ($0.name == name || $0.toolName == name)
        }) else {
            return .failure(MCPError.message("No MCP tool named \(name) on \(serverName)."))
        }
        return await mcp.call(toolName: tool.toolName, arguments: arguments)
    }

    func answerElicitation(_ response: MCPElicitationResponse) {
        mcpElicitation = nil
        mcpElicitationContinuation?.resume(returning: response)
        mcpElicitationContinuation = nil
    }

    private func handleSampling(_ request: MCPSamplingRequest) async -> MCPSamplingResult {
        if !samplingApprovedServers.contains(request.serverName) {
            switch await requestSamplingApproval(request) {
            case .deny:
                return .decline
            case .once:
                break
            case .session:
                samplingApprovedServers.insert(request.serverName)
            }
        }

        var messages: [ChatMessage] = []
        if let system = request.systemPrompt?.trimmingCharacters(in: .whitespacesAndNewlines),
           !system.isEmpty {
            messages.append(ChatMessage(role: .system, text: system))
        }
        for message in request.messages {
            let role: Role = message.role == "assistant" ? .assistant : .user
            var attachments: [String] = []
            if role == .user, !message.images.isEmpty {
                attachments = writeSamplingImages(message.images)
            }
            messages.append(ChatMessage(role: role, text: message.text, attachments: attachments))
        }
        guard messages.contains(where: { $0.role == .user || $0.role == .assistant }) else {
            return .failure("The sampling request contained no usable messages.")
        }

        var generation = settings.generation(reasoning: reasoningSelection)
        if let maxTokens = request.maxTokens, maxTokens > 0 {
            generation.maxTokens = min(maxTokens, generation.maxTokens)
        }
        if let temperature = request.temperature {
            generation.temperature = min(max(temperature, 0), 1.5)
        }
        do {
            let first = try await streamCompletion(
                choice: choice,
                messages: messages,
                generation: generation
            )
            if let id = selectedID {
                usage.record(first.1, provider: choice.provider, model: choice.id, sessionID: id)
            }
            var text = first.0.trimmingCharacters(in: .whitespacesAndNewlines)

            // Small sampling budgets can be consumed entirely by reasoning on
            // modern models. Retry once with a larger budget instead of
            // returning an empty message to the server.
            if text.isEmpty,
               let requested = request.maxTokens,
               requested < settings.speed.maxTokens {
                var retry = generation
                retry.maxTokens = min(settings.speed.maxTokens, max(requested * 4, 256))
                if retry.maxTokens > generation.maxTokens {
                    let second = try await streamCompletion(
                        choice: choice,
                        messages: messages,
                        generation: retry
                    )
                    if let id = selectedID {
                        usage.record(second.1, provider: choice.provider, model: choice.id, sessionID: id)
                    }
                    text = second.0.trimmingCharacters(in: .whitespacesAndNewlines)
                }
            }

            guard !text.isEmpty else {
                return .failure("The model returned an empty sampling response.")
            }
            return .accept(text: text, model: choice.id)
        } catch {
            return .failure("Sampling failed: \(error.localizedDescription)")
        }
    }

    private func requestSamplingApproval(_ request: MCPSamplingRequest) async -> MCPSamplingApproval {
        if let existing = samplingContinuation {
            existing.resume(returning: .deny)
            samplingContinuation = nil
        }
        return await withCheckedContinuation { continuation in
            samplingContinuation = continuation
            pendingSampling = request
        }
    }

    func resolveSamplingApproval(_ approval: MCPSamplingApproval) {
        pendingSampling = nil
        samplingContinuation?.resume(returning: approval)
        samplingContinuation = nil
    }

    private func writeSamplingImages(_ images: [MCPSamplingImage]) -> [String] {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-sampling-images", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return images.prefix(4).compactMap { image in
            guard let data = Data(base64Encoded: image.base64) else { return nil }
            let ext: String
            switch image.mediaType.lowercased() {
            case "image/jpeg", "image/jpg": ext = "jpg"
            case "image/gif": ext = "gif"
            case "image/webp": ext = "webp"
            default: ext = "png"
            }
            let url = folder.appendingPathComponent("sample-\(UUID().uuidString).\(ext)")
            do {
                try data.write(to: url)
                return url.path
            } catch {
                return nil
            }
        }
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

    func startCloudServicesIfNeeded() {
        if settings.cloudServerEnabled || settings.cloudRunLocally {
            startCloudServer()
        }
    }

    func setCloudServerEnabled(_ enabled: Bool) {
        settings.cloudServerEnabled = enabled
        if enabled {
            startCloudServer()
        } else {
            settings.cloudRunLocally = false
            stopCloudWorker()
            stopCloudServer()
        }
    }

    func startCloudServer() {
        guard cloudServer == nil else { return }
        do {
            let server = try CloudTaskHTTPServer(
                queue: cloudQueue,
                token: settings.cloudToken,
                port: UInt16(min(max(settings.cloudPort, 1024), 65_535)),
                bindLAN: settings.cloudAllowLAN
            )
            cloudServer = server
            cloudServerStatus = "Starting…"
            Task {
                do {
                    try await server.start()
                    cloudEndpoint = "http://127.0.0.1:\(server.port)"
                    cloudServerStatus = settings.cloudAllowLAN
                        ? "Listening on all interfaces, port \(server.port)"
                        : "Listening at \(cloudEndpoint ?? "")"
                    if settings.cloudRunLocally {
                        startCloudWorker()
                    }
                } catch {
                    server.stop()
                    cloudServer = nil
                    cloudServerStatus = "Failed"
                    self.error = "Cloud task server failed: \(error.localizedDescription)"
                }
            }
        } catch {
            cloudServerStatus = "Failed"
            self.error = "Cloud task server failed: \(error.localizedDescription)"
        }
        Task { await refreshCloudTasks() }
    }

    func stopCloudServer() {
        stopCloudWorker()
        cloudServer?.stop()
        cloudServer = nil
        cloudServerStatus = "Off"
        cloudEndpoint = nil
    }

    func startCloudWorker() {
        guard cloudWorker == nil else { return }
        guard cloudServer != nil,
              let port = cloudServer?.port,
              let baseURL = URL(string: "http://127.0.0.1:\(port)") else {
            error = "Start the cloud task server before the local worker."
            return
        }
        let transport = HTTPCloudTaskTransport(baseURL: baseURL, token: settings.cloudToken)
        let worker = CloudTaskWorker(
            transport: transport,
            executor: { [weak self] task in
                guard let self else {
                    throw CloudTransportError.message("Talos is not available.")
                }
                return try await self.executeCloudTask(task)
            },
            onChange: { [weak self] in
                await self?.refreshCloudTasks()
            }
        )
        cloudWorker = worker
        cloudWorkerStatus = "Running"
        settings.cloudRunLocally = true
        Task { await worker.start() }
    }

    func stopCloudWorker() {
        guard let worker = cloudWorker else { return }
        cloudWorker = nil
        cloudWorkerStatus = "Stopping…"
        settings.cloudRunLocally = false
        Task {
            await worker.stop()
            cloudWorkerStatus = "Off"
        }
    }

    func enqueueCloudTask(_ prompt: String) async -> CloudTask? {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let task = await cloudQueue.enqueue(
            prompt: trimmed,
            workspace: projectURL.path,
            provider: choice.provider.rawValue,
            model: choice.id
        )
        await refreshCloudTasks()
        return task
    }

    func refreshCloudTasks() async {
        cloudTasks = await cloudQueue.list()
    }

    func cancelCloudTask(_ id: UUID) async {
        _ = await cloudQueue.cancel(id)
        await refreshCloudTasks()
    }

    func removeCloudTask(_ id: UUID) async {
        _ = await cloudQueue.remove(id)
        await refreshCloudTasks()
    }

    private func executeCloudTask(_ task: CloudTask) async throws -> CloudTaskExecution {
        let root = URL(fileURLWithPath: task.workspace, isDirectory: true)
        let worktreeResult = await GitService.createWorktree(
            in: root,
            name: "cloud-\(task.id.uuidString.prefix(8))",
            branchPrefix: "cloud/"
        )
        guard case .success(let worktree) = worktreeResult else {
            if case .failure(let error) = worktreeResult {
                throw CloudTransportError.message("Could not create an isolated worktree: \(error.localizedDescription)")
            }
            throw CloudTransportError.message("Could not create an isolated worktree.")
        }

        var generation = settings.generation(reasoning: reasoningSelection)
        generation.allowImages = modelCapabilities.supportsVision
        let runChoice = choice
        let context = ToolContext(
            workspace: worktree.path,
            sandbox: .workspaceWrite,
            network: settings.networkAccess,
            enabledShell: true,
            enabledFileEdits: true,
            enabledComputerUse: false,
            enabledWeb: settings.enableWeb,
            enabledBrowser: false,
            enabledPlan: true,
            skills: skills.enabled,
            mcpTools: []
        )
        let instructions = """
        You are a cloud task worker running in an isolated git worktree.
        Implement the task fully: inspect the repository, edit files, and run relevant tests or builds.
        You may use shell and file tools inside the worktree. Do not ask for approval.
        When you finish, give a concise summary of what changed and which checks passed or failed.
        """
        let engine = SubagentEngine(
            label: task.title,
            task: task.prompt,
            maxSteps: 16,
            context: context,
            projectInstructions: projectInstructions,
            instructions: instructions
        ) { messages in
            let (raw, used) = try await self.streamCompletion(
                choice: runChoice,
                messages: messages,
                generation: generation
            )
            return (raw, used)
        }
        let outcome = try await engine.run()
        let trace = outcome.steps.isEmpty
            ? ""
            : "\n\nTools:\n- " + outcome.steps.joined(separator: "\n- ")
        return CloudTaskExecution(
            result: outcome.summary + trace,
            worktree: worktree.path,
            branch: worktree.branch
        )
    }

    func openWorktree(_ worktree: GitWorktreeInfo) {
        let project = projects.open(path: worktree.path)
        openProject(project)
        showWorktrees = false
    }

    func mergeWorktree(_ worktree: GitWorktreeInfo) async -> Result<String, Error> {
        guard let branch = worktree.branch else {
            return .failure(GitError.message("This worktree has a detached HEAD and cannot be merged by branch name."))
        }
        let result = await GitService.mergeWorktree(in: projectURL, branch: branch)
        await refreshGit()
        return result
    }

    func removeWorktree(
        _ worktree: GitWorktreeInfo,
        force: Bool = false,
        deleteBranch: Bool = true
    ) async -> Result<String, Error> {
        let result = await GitService.removeWorktree(
            in: projectURL,
            path: worktree.path,
            branch: worktree.branch,
            force: force,
            deleteBranch: deleteBranch
        )
        await refreshGit()
        return result
    }

    func createWorktree(name: String) async -> Result<Project, Error> {
        let result = await GitService.createWorktree(
            in: projectURL,
            name: name,
            branchPrefix: settings.branchPrefix
        )
        switch result {
        case .success(let worktree):
            let project = projects.open(path: worktree.path)
            openProject(project)
            await refreshGit()
            return .success(project)
        case .failure(let error):
            return .failure(error)
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
        let messages = currentContextMessages()
        let providerCount = providerContextKey == contextFingerprint(messages)
            ? providerContextTokens
            : nil
        let used = providerCount
            ?? exactTokenCount(messages)
            ?? TokenEstimator.estimate(messages)
        return ContextUsage(used: used, limit: contextLimit)
    }

    var tokenizerLabel: String {
        if let provider = providerTokenProvider {
            let key = contextFingerprint(currentContextMessages())
            if providerContextKey == key, let label = providerContextLabel {
                return label
            }
            return provider.method
        }
        return TokenCounter.label(provider: choice.provider.rawValue, model: choice.id)
    }

    private var providerTokenProvider: TokenCounterProvider? {
        switch choice.provider {
        case .anthropic: return .anthropic
        case .gemini: return .gemini
        case .local: return .ollama
        default: return nil
        }
    }

    private func currentContextMessages() -> [ChatMessage] {
        guard let session = selected else { return [] }
        let system = ChatMessage(role: .system, text: systemPrompt())
        return [system] + session.messages.filter {
            $0.role != .system && !($0.role == .assistant && $0.text.isEmpty)
        }
    }

    private func contextFingerprint(_ messages: [ChatMessage]) -> String {
        var hasher = SHA256()
        hasher.update(data: Data("\(choice.provider.rawValue)|\(choice.id)|".utf8))
        for message in messages {
            hasher.update(data: Data(
                "\(message.role.rawValue)|\(message.text)|\(message.attachments.joined(separator: ","))".utf8
            ))
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    func scheduleProviderContextCount() {
        providerContextTask?.cancel()
        guard settings.exactProviderTokenCounting,
              let provider = providerTokenProvider else {
            providerContextTokens = nil
            providerContextLabel = nil
            providerContextKey = nil
            return
        }
        let messages = currentContextMessages()
        guard !messages.isEmpty,
              messages.allSatisfy({ $0.attachments.isEmpty && $0.attachmentPath == nil }) else {
            providerContextTokens = nil
            providerContextLabel = nil
            providerContextKey = nil
            return
        }
        let key = contextFingerprint(messages)
        if providerContextKey == key, providerContextTokens != nil { return }

        let baseURL: String
        let apiKey: String
        switch provider {
        case .anthropic:
            baseURL = settings.anthropicBase
            apiKey = settings.key(for: .anthropic) ?? ""
        case .gemini:
            baseURL = settings.geminiBase
            apiKey = settings.key(for: .gemini) ?? ""
        case .ollama:
            baseURL = settings.localURL
            apiKey = ""
        }
        let model = choice.id
        let targetChoice = choice
        providerContextTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard let self, !Task.isCancelled else { return }
            let result = await ProviderTokenCounter.count(
                messages: messages,
                provider: provider,
                model: model,
                baseURL: baseURL,
                apiKey: apiKey
            )
            guard !Task.isCancelled, self.choice == targetChoice else { return }
            self.providerContextKey = key
            self.providerContextTokens = result?.count
            self.providerContextLabel = result?.method
        }
    }

    private func exactTokenCount(_ messages: [ChatMessage]) -> Int? {
        guard messages.allSatisfy({ $0.attachments.isEmpty && $0.attachmentPath == nil }) else {
            return nil
        }
        return TokenCounter.count(
            messages.map(\.text),
            provider: choice.provider.rawValue,
            model: choice.id
        )
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
        let id = session.id
        Task { await runSessionHooks(.sessionStart, sessionID: id) }
    }

    func delete(_ id: UUID) {
        sessions.removeAll { $0.id == id }
        if selectedID == id { selectedID = filteredSessions.first?.id ?? sessions.first?.id }
        if sessions.isEmpty { newSession() }
        saveSessions()
    }

    func archive(_ id: UUID) {
        guard let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        Task { await runSessionHooks(.sessionEnd, sessionID: id) }
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
            approvedTools: source.approvedTools,
            forkedFromSessionID: source.id
        )
        fork.updatedAt = Date()
        sessions.insert(fork, at: 0)
        selectedID = fork.id
        saveSessions()
    }

    func editMessage(_ sessionID: UUID, _ messageID: UUID) {
        guard !isStreaming,
              let index = sessions.firstIndex(where: { $0.id == sessionID }),
              let plan = ConversationBranching.edit(sessions[index].messages, messageID: messageID) else {
            return
        }
        editingMessage = MessageEditTarget(
            sessionID: sessionID,
            messageID: messageID,
            text: plan.user.text,
            attachments: plan.user.attachments
        )
    }

    func commitEditMessage(
        _ target: MessageEditTarget,
        text: String
    ) async -> Result<UUID, Error> {
        guard !isStreaming,
              let index = sessions.firstIndex(where: { $0.id == target.sessionID }),
              let plan = ConversationBranching.edit(sessions[index].messages, messageID: target.messageID) else {
            error = "That message can no longer be edited."
            return .failure(AppServerError.message("That message can no longer be edited."))
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return .failure(AppServerError.message("The edited message is empty."))
        }

        let summary = await runHooks(.userPromptSubmit, sessionID: target.sessionID)
        if summary.blocked {
            let message = summary.reason.map { "Blocked by hook: \($0)" } ?? "Blocked by a hook."
            error = message
            return .failure(AppServerError.message(message))
        }
        var prompt = trimmed
        if !summary.contexts.isEmpty {
            prompt += "\n\nHook context:\n" + summary.contexts.joined(separator: "\n")
        }

        sessions[index].messages = plan.prefix
        sessions[index].updatedAt = Date()
        saveSessions()
        let result = startRun(
            sessionID: target.sessionID,
            text: prompt,
            attachments: target.attachments,
            revisionOf: target.messageID,
            parentID: plan.parentID
        )
        if case .failure(let failure) = result {
            error = failure.localizedDescription
            return .failure(failure)
        }
        return result
    }

    func retryMessage(_ sessionID: UUID, _ messageID: UUID) async -> Result<UUID, Error> {
        guard !isStreaming,
              let index = sessions.firstIndex(where: { $0.id == sessionID }),
              let plan = ConversationBranching.retry(sessions[index].messages, messageID: messageID) else {
            let message = "Select an assistant message that follows a user message."
            error = message
            return .failure(AppServerError.message(message))
        }
        let summary = await runHooks(.userPromptSubmit, sessionID: sessionID)
        if summary.blocked {
            let message = summary.reason.map { "Blocked by hook: \($0)" } ?? "Blocked by a hook."
            error = message
            return .failure(AppServerError.message(message))
        }
        var prompt = plan.user.text
        if !summary.contexts.isEmpty {
            prompt += "\n\nHook context:\n" + summary.contexts.joined(separator: "\n")
        }
        sessions[index].messages = plan.prefix
        sessions[index].updatedAt = Date()
        saveSessions()
        let result = startRun(
            sessionID: sessionID,
            text: prompt,
            attachments: plan.user.attachments,
            revisionOf: plan.user.id,
            assistantRetryOf: plan.assistant.id,
            parentID: plan.parentID
        )
        if case .failure(let failure) = result {
            error = failure.localizedDescription
            return .failure(failure)
        }
        return result
    }

    @discardableResult
    func forkMessage(_ sessionID: UUID, _ messageID: UUID) -> Result<UUID, Error> {
        guard let source = sessions.first(where: { $0.id == sessionID }),
              let messages = ConversationBranching.fork(source.messages, messageID: messageID) else {
            return .failure(AppServerError.message("Message not found."))
        }
        let copied = messages.map { message in
            ChatMessage(
                role: message.role,
                text: message.text,
                isStreaming: false,
                model: message.model,
                toolName: message.toolName,
                toolStatus: message.toolStatus,
                attachmentPath: message.attachmentPath,
                attachments: message.attachments,
                parentID: message.parentID,
                revisionOf: message.revisionOf,
                retryOf: message.retryOf
            )
        }
        var fork = Session(
            title: "\(source.title) branch",
            projectPath: source.projectPath,
            messages: copied,
            plan: source.plan,
            approvedTools: source.approvedTools,
            forkedFromSessionID: source.id,
            forkedFromMessageID: messageID
        )
        fork.updatedAt = Date()
        sessions.insert(fork, at: 0)
        selectedID = fork.id
        saveSessions()
        return .success(fork.id)
    }

    func retryLastTurn() {
        guard !isStreaming, let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }),
              let assistant = sessions[index].messages.last(where: { $0.role == .assistant }) else {
            return
        }
        Task { _ = await retryMessage(id, assistant.id) }
    }

    func clearHistory() {
        sessions = []
        newSession()
    }

    func choose(_ choice: ModelChoice) {
        if let policy = managedPolicy, !policy.allows(choice: choice) {
            return
        }
        self.choice = choice
        if choice.provider != .local {
            settings.setModel(choice.id, for: choice.provider)
        }
        defaults.set(choice.provider.rawValue, forKey: "selectedProvider")
        defaults.set(choice.id, forKey: "selectedModel")
        Task { await refreshCapabilities() }
    }

    func setReasoning(_ value: String?) {
        if let policy = managedPolicy, policy.reasoning != nil {
            let forced = policy.enforcedReasoning(value)
            reasoningSelection = forced
            settings.setReasoning(forced, for: choice)
            return
        }
        reasoningSelection = value
        settings.setReasoning(value, for: choice)
    }

    private func applyManagedSelection(_ policy: ManagedPolicy) {
        if let provider = policy.provider {
            var id = policy.model ?? ""
            if id.isEmpty {
                id = choice.provider == provider ? choice.id : settings.model(for: provider)
            }
            if !id.isEmpty {
                choice = ModelChoice(provider: provider, id: id)
            }
        } else if let model = policy.model, !model.isEmpty {
            choice = ModelChoice(provider: choice.provider, id: model)
        }
        if let reasoning = policy.reasoning {
            reasoningSelection = reasoning
            settings.setReasoning(reasoning, for: choice)
        }
    }

    func reloadPolicy() {
        let loaded = ManagedPolicyLoader.load()
        managedPolicy = loaded.policy
        policySource = loaded.sourcePath
        policySignature = loaded.signatureStatus
        policyError = loaded.error
        if let policy = loaded.policy {
            settings.applyManagedPolicy(policy)
            if let managedHooks = policy.hooks {
                hooks.applyManaged(managedHooks)
            } else {
                hooks.clearManaged()
            }
            applyManagedSelection(policy)
        } else {
            settings.clearManagedPolicy()
            hooks.clearManaged()
        }
        Task {
            await refreshChoices()
            await reloadMCP()
            await refreshCapabilities()
        }
    }

    func captureProfile(name: String = "Profile") -> AgentProfile {
        AgentProfile(
            name: name,
            provider: choice.provider,
            model: choice.id,
            reasoning: reasoningSelection,
            sandbox: settings.sandboxMode,
            approval: settings.approvalPolicy,
            network: settings.networkAccess,
            speed: settings.speed,
            enableShell: settings.enableShell,
            enableFileEdits: settings.enableFileEdits,
            enableComputerUse: settings.enableComputerUse,
            enableWeb: settings.enableWeb,
            enableBrowser: settings.enableBrowser,
            enablePlan: settings.enablePlan
        )
    }

    func applyProfile(_ profile: AgentProfile) {
        if !profile.model.isEmpty {
            let target = ModelChoice(provider: profile.provider, id: profile.model)
            if !choices.contains(target) {
                choices.append(target)
            }
            choose(target)
        }
        settings.sandboxMode = profile.sandbox
        settings.approvalPolicy = profile.approval
        settings.networkAccess = profile.network
        settings.speed = profile.speed
        settings.enableShell = profile.enableShell
        settings.enableFileEdits = profile.enableFileEdits
        settings.enableComputerUse = profile.enableComputerUse
        settings.enableWeb = profile.enableWeb
        settings.enableBrowser = profile.enableBrowser
        settings.enablePlan = profile.enablePlan
        if let reasoning = profile.reasoning, !reasoning.isEmpty {
            reasoningSelection = reasoning
            settings.setReasoning(reasoning, for: choice)
        } else {
            reasoningSelection = nil
            settings.setReasoning(nil, for: choice)
        }
        profiles.markActive(profile)
        if let policy = managedPolicy {
            applyManagedSelection(policy)
        }
        Task { await refreshCapabilities() }
    }

    func clearActiveProfile() {
        profiles.markActive(nil)
    }

    private func runHooks(
        _ event: HookEvent,
        tool: ToolCall? = nil,
        toolResult: ToolResult? = nil,
        sessionID: UUID
    ) async -> HookSummary {
        var summary = HookSummary()
        for hook in hooks.matching(event: event, toolName: tool?.name) {
            if !hook.trusted {
                switch await requestHookTrust(hook, event: event) {
                case .skip:
                    hooks.record(hook, event: event, status: "Skipped (not trusted)", output: hook.command)
                    continue
                case .alwaysTrust:
                    hooks.setTrusted(hook, true)
                case .runOnce:
                    break
                }
            }
            let outcome = await HookRunner.run(
                hook,
                event: event,
                workspace: projectURL,
                sessionID: sessionID,
                tool: tool,
                toolResult: toolResult?.output
            )
            let status: String
            if outcome.blocked {
                status = "Blocked"
            } else if outcome.timedOut {
                status = "Timed out"
            } else if outcome.exitCode != 0 {
                status = "Exit \(outcome.exitCode)"
            } else {
                status = "OK"
            }
            hooks.record(hook, event: event, status: status, output: outcome.output)
            if let context = outcome.context, !context.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                summary.contexts.append(context)
            }
            if outcome.blocked {
                summary.blocked = true
                summary.reason = outcome.blockReason
                break
            }
        }
        return summary
    }

    private func runSessionHooks(_ event: HookEvent, sessionID: UUID) async {
        let summary = await runHooks(event, sessionID: sessionID)
        guard !summary.contexts.isEmpty,
              let index = sessions.firstIndex(where: { $0.id == sessionID }) else { return }
        sessions[index].messages.append(
            ChatMessage(
                role: .assistant,
                text: "Hook context:\n" + summary.contexts.joined(separator: "\n"),
                model: "Hook"
            )
        )
        sessions[index].updatedAt = Date()
        saveSessions()
    }

    private func requestHookTrust(_ hook: HookDefinition, event: HookEvent) async -> HookTrustDecision {
        if let existing = hookTrustContinuation {
            existing.resume(returning: .skip)
            hookTrustContinuation = nil
        }
        return await withCheckedContinuation { continuation in
            hookTrustContinuation = continuation
            pendingHookTrust = HookTrustRequest(hook: hook, event: event)
        }
    }

    func resolveHookTrust(_ decision: HookTrustDecision) {
        pendingHookTrust = nil
        hookTrustContinuation?.resume(returning: decision)
        hookTrustContinuation = nil
    }

    private func submitWithHooks(_ text: String, attachments: [String] = []) async {
        let summary = await runHooks(.userPromptSubmit, sessionID: selectedID ?? UUID())
        if summary.blocked {
            error = summary.reason.map { "Blocked by hook: \($0)" } ?? "Blocked by a hook."
            return
        }
        var prompt = text
        if !summary.contexts.isEmpty {
            prompt += "\n\nHook context:\n" + summary.contexts.joined(separator: "\n")
        }
        send(prompt, attachments: attachments)
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

        if settings.savedKeys.contains(.openCode), let key = settings.key(for: .openCode) {
            do {
                openCodeModels = try await OpenCodeCatalog.fetch(token: key)
                    .filter(\.isRouted)
                next += openCodeModels.map { ModelChoice(provider: .openCode, id: $0.id) }
            } catch {
                openCodeModels = []
                let model = settings.openCodeModel
                if !model.isEmpty {
                    next.append(ModelChoice(provider: .openCode, id: model))
                }
            }
        } else {
            openCodeModels = []
        }

        for provider in [Provider.openAI, .anthropic, .gemini] where settings.savedKeys.contains(provider) {
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
        defer { scheduleProviderContextCount() }
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
        let capabilities = ModelCatalog.capabilities(
            for: target,
            ollama: info,
            openCode: openCodeModels.first { $0.id == target.id }
        ).applyingVisionOverride(settings.visionOverride(for: target))
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
        guard let id = selectedID else { return }
        let result = startRun(sessionID: id, text: text, attachments: attachments)
        if case .failure(let error) = result {
            self.error = error.localizedDescription
        }
    }

    @discardableResult
    func startRun(
        sessionID: UUID,
        text: String,
        attachments: [String] = [],
        revisionOf: UUID? = nil,
        assistantRetryOf: UUID? = nil,
        parentID: UUID? = nil
    ) -> Result<UUID, Error> {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return .failure(AppServerError.message("A prompt is required."))
        }
        guard !isStreaming else {
            return .failure(AppServerError.message("A run is already active."))
        }
        guard let index = sessions.firstIndex(where: { $0.id == sessionID }) else {
            return .failure(AppServerError.message("Session not found."))
        }
        guard choices.contains(choice) else {
            return .failure(AppServerError.message("Choose a model before sending."))
        }
        if choice.provider != .local, settings.key(for: choice.provider) == nil {
            return .failure(AppServerError.message("Add an API key for \(choice.provider.title) in Settings."))
        }
        error = nil

        let isFirstMessage = !sessions[index].messages.contains { $0.role == .user }
        if isFirstMessage,
           sessions[index].title == "New Task" || sessions[index].title.isEmpty {
            sessions[index].title = String(trimmed.prefix(48))
        }
        let message = ChatMessage(
            role: .user,
            text: trimmed,
            attachments: attachments,
            parentID: parentID ?? sessions[index].messages.last?.id,
            revisionOf: revisionOf
        )
        sessions[index].messages.append(message)
        pendingAssistantRetryOf = assistantRetryOf
        chronicleRecord(
            .prompt,
            summary: String(trimmed.prefix(180)),
            sessionID: sessionID
        )
        sessions[index].updatedAt = Date()
        isStreaming = true
        activeRunSessionID = sessionID

        let runChoice = choice
        runTask = Task { [weak self] in
            await self?.runLoop(sessionID: sessionID, choice: runChoice)
        }
        return .success(message.id)
    }

    func cancelRun(sessionID: UUID) -> Bool {
        guard isStreaming, activeRunSessionID == sessionID else { return false }
        runTask?.cancel()
        runTask = nil
        isStreaming = false
        activeRunSessionID = nil
        if let index = sessions.firstIndex(where: { $0.id == sessionID }),
           var last = sessions[index].messages.last,
           last.isStreaming {
            last.text = last.text.isEmpty ? "Stopped." : last.text
            last.isStreaming = false
            sessions[index].messages[sessions[index].messages.count - 1] = last
        }
        saveSessions()
        return true
    }

    func setAppServerEnabled(_ enabled: Bool) {
        settings.appServerEnabled = enabled
        if enabled {
            startAppServer()
        } else {
            appServer.stop()
        }
    }

    func startAppServer() {
        appServer.start(
            port: settings.appServerPort,
            token: settings.appServerToken,
            bindLAN: settings.appServerAllowLAN
        )
    }

    func startAppServerIfConfigured() {
        let environment = ProcessInfo.processInfo.environment
        if environment["TALOS_APP_SERVER"] == "1" {
            let port = Int(environment["TALOS_APP_SERVER_PORT"] ?? "") ?? settings.appServerPort
            let token = environment["TALOS_APP_SERVER_TOKEN"] ?? settings.appServerToken
            appServer.start(port: port, token: token, bindLAN: false)
        } else if settings.appServerEnabled {
            startAppServer()
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
            Task { await submitWithHooks(trimmed, attachments: attachments) }
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
        Task { await submitWithHooks(next.text, attachments: next.attachments) }
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
            if activeRunSessionID == id {
                activeRunSessionID = nil
            }
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
        case "/profiles":
            openSettings(.profiles)
        case "/policy":
            openSettings(.policy)
        case "/cloud", "/tasks":
            openSettings(.cloud)
        case "/app-server", "/appserver":
            openSettings(.appServer)
        case "/marketplace", "/catalog":
            openSettings(.plugins)
        case "/hooks":
            openSettings(.hooks)
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
        case "/worktree":
            if remainder.isEmpty {
                showWorktreeSheet = true
            } else {
                Task {
                    switch await createWorktree(name: remainder) {
                    case .success(let project):
                        appendCommandMessage("Opened worktree \(project.path).")
                    case .failure(let error):
                        appendCommandMessage("Worktree failed: \(error.localizedDescription)")
                    }
                }
            }
        case "/worktrees":
            Task { await refreshGit() }
            showWorktrees = true
        case "/merge":
            guard !remainder.isEmpty else {
                appendCommandMessage("Usage: /merge talos/branch-name")
                break
            }
            guard !isStreaming else {
                appendCommandMessage("Stop the current response before merging.")
                break
            }
            Task {
                switch await GitService.mergeWorktree(in: projectURL, branch: remainder) {
                case .success(let text):
                    await refreshGit()
                    appendCommandMessage(text.isEmpty ? "Merged \(remainder)." : text)
                case .failure(let error):
                    appendCommandMessage("Merge failed: \(error.localizedDescription)")
                }
            }
        case "/pr", "/pull-request":
            showGitPanel = true
        case "/diff":
            showGitPanel = true
        case "/review":
            reviewChanges(focus: .general)
        case "/fanout":
            let parts = remainder
                .components(separatedBy: CharacterSet.newlines)
                .flatMap { $0.components(separatedBy: "|") }
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            guard parts.count >= 2 else {
                appendCommandMessage("Usage: /fanout task one | task two | task three")
                break
            }
            guard !isStreaming, let id = selectedID else {
                appendCommandMessage("Stop the current response before starting a fan-out.")
                break
            }
            Task {
                isStreaming = true
                let result = await runFanout(
                    ToolCall(name: "fanout", arguments: [
                        "tasks": .array(parts.map { .object(["task": .string($0)]) }),
                    ]),
                    sessionID: id
                )
                isStreaming = false
                appendCommandMessage(result.output)
            }
        case "/subagent", "/delegate":
            guard !remainder.isEmpty else {
                appendCommandMessage("Usage: /subagent Investigate how authentication is wired, then report the files involved.")
                break
            }
            guard !isStreaming, let id = selectedID else {
                appendCommandMessage("Stop the current response before starting a subagent.")
                break
            }
            Task {
                isStreaming = true
                let result = await runSubagent(
                    ToolCall(name: "subagent", arguments: ["task": .string(remainder)]),
                    sessionID: id
                )
                isStreaming = false
                appendCommandMessage(result.output)
            }
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
        case "/memory":
            openSettings(.memory)
        case "/chronicle", "/suggestions":
            openSettings(.chronicle)
        case "/instructions":
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

    private func chronicleRecord(
        _ kind: ChronicleEventKind,
        summary: String,
        sessionID: UUID? = nil
    ) {
        guard settings.chronicleEnabled else { return }
        chronicle.record(
            kind,
            summary: summary,
            sessionID: sessionID,
            generateSuggestions: settings.chronicleSuggestionsEnabled
        )
    }

    func performSuggestion(_ suggestion: AmbientSuggestion) {
        chronicle.markActedOn(suggestion)
        switch suggestion.action {
        case .openMemory:
            openSettings(.memory)
        case .reviewChanges:
            showGitPanel = true
        case .compact:
            compactCurrentSession()
        case .retry:
            retryLastTurn()
        case .draft:
            if let prompt = suggestion.prompt, !prompt.isEmpty {
                pendingDraft = prompt
            }
        }
    }

    private func systemPrompt() -> String {
        var prompt = ToolPrompt.system(context: toolContext())
        if let memory = memories.contextSection {
            prompt += "\n\n" + memory
        }
        if let projectInstructions {
            prompt += "\n\n" + projectInstructions
        }
        return prompt
    }

    private func contextSummary() -> String {
        let usage = contextUsage
        let history = (selected?.messages ?? []).filter { $0.role != .system }
        let messageTokens = exactTokenCount(history) ?? TokenEstimator.estimate(history)
        let systemTokens = TokenCounter.count(
            systemPrompt(),
            provider: choice.provider.rawValue,
            model: choice.id
        ) ?? TokenEstimator.estimate(systemPrompt())
        var lines = [
            "Context: \(usage.label)",
            "Messages: \(TokenEstimator.format(messageTokens)) tokens",
            "System and tools: \(TokenEstimator.format(systemTokens)) tokens",
            "Skills: \(skills.enabled.count)",
            "MCP tools: \(mcpTools.count)",
            "Token counting: \(tokenizerLabel)",
            providerContextTokens.map { "Provider count: \($0.formatted()) tokens" } ?? "Provider count: pending",
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
            await submitWithHooks(prompt)
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
            "MCP: \(mcpStatus) · \(mcpTools.count) tools · \(mcpResources.count) resources",
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
        Profile: \(profiles.active?.name ?? "None")
        Memories: \(memories.memories.count) · Goals: \(memories.activeGoals.count) active
        Chronicle: \(chronicle.activeSuggestions.count) suggestion(s)
        Policy: \(managedPolicy?.title ?? "None")
        Hooks: \(hooks.enabled.count) enabled
        Auto-review: \(settings.autoReviewTools ? "On" : "Off")
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
        let qualifier = sessionUsage.estimated
            ? "estimated"
            : (sessionUsage.tokenizer == true ? "tokenized" : "reported")
        return """
        This task: \(TokenEstimator.format(sessionUsage.total)) tokens (\(TokenEstimator.format(sessionUsage.prompt)) prompt, \(TokenEstimator.format(sessionUsage.completion)) completion, \(qualifier)).
        All tasks: \(TokenEstimator.format(usage.totalTokens)) tokens.
        """
    }

    private func appendCommandMessage(_ text: String) {
        guard let id = selectedID,
              let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        sessions[index].messages.append(
            ChatMessage(
                role: .assistant,
                text: text,
                model: "Command",
                parentID: sessions[index].messages.last?.id
            )
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
        activeRunSessionID = nil
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
        approvalReviewTask?.cancel()
        approvalReviewTask = nil
        approvalReviewing = false
        pendingApproval = nil
        let continuation = approvalContinuation
        approvalContinuation = nil
        continuation?.resume(returning: decision)
    }

    func saveSessions() {
        SessionPersistence.save(sessions: sessions, selectedID: selectedID)
    }

    private func runLoop(sessionID: UUID, choice runChoice: ModelChoice) async {
        var runFailed = false
        var runError: String?
        defer {
            let wasCancelled = Task.isCancelled
            isStreaming = false
            if activeRunSessionID == sessionID {
                activeRunSessionID = nil
            }
            runTask = nil
            saveSessions()
            if !wasCancelled {
                playCompletionFeedback()
                startNextQueued(sessionID)
            }
            if !wasCancelled {
                chronicleRecord(
                    runFailed ? .runFailed : .runCompleted,
                    summary: runFailed
                        ? (runError ?? "The run failed.")
                        : "Completed \(runChoice.display)",
                    sessionID: sessionID
                )
            }
            scheduleProviderContextCount()
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
                ChatMessage(
                    id: assistantID,
                    role: .assistant,
                    text: "",
                    isStreaming: true,
                    model: runChoice.display,
                    parentID: sessions[index].messages.last?.id,
                    retryOf: pendingAssistantRetryOf
                )
            )
            pendingAssistantRetryOf = nil
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

                let pre = await runHooks(.preToolUse, tool: parsed.call, sessionID: sessionID)
                if pre.blocked {
                    appendToolMessage(
                        sessionID,
                        call: parsed.call,
                        result: .denied("Blocked by hook: \(pre.reason ?? "no reason given").")
                    )
                    continue
                }

                let approved = await requestApproval(for: parsed.call, sessionID: sessionID)
                guard approved else {
                    appendToolMessage(sessionID, call: parsed.call, result: .denied("Denied by the user."))
                    continue
                }

                var result = await executeTool(parsed.call, sessionID: sessionID)
                if result.status == .failed {
                    chronicleRecord(
                        .toolFailed,
                        summary: "\(parsed.call.name): \(result.output.prefix(160))",
                        sessionID: sessionID
                    )
                }
                let post = await runHooks(
                    .postToolUse,
                    tool: parsed.call,
                    toolResult: result,
                    sessionID: sessionID
                )
                let contexts = pre.contexts + post.contexts
                if !contexts.isEmpty {
                    result = ToolResult(
                        status: result.status,
                        output: result.output + "\n\nHook context:\n" + contexts.joined(separator: "\n"),
                        attachmentPath: result.attachmentPath
                    )
                }
                if parsed.call.name.hasPrefix("mcp__"), result.status == .succeeded {
                    await presentMCPApp(for: parsed.call.name)
                }
                appendToolMessage(sessionID, call: parsed.call, result: result)
            } catch {
                if !Task.isCancelled {
                    runFailed = true
                    runError = error.localizedDescription
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
                    var text = message.text + "\n\nAttached files:\n" + message.attachments.map { "- \($0)" }.joined(separator: "\n")
                    let pdfText = PDFLoader.extractedText(paths: message.attachments)
                    if !pdfText.isEmpty {
                        text += "\n\nPDF text:\n" + pdfText
                    }
                    return ChatMessage(
                        role: .user,
                        text: text,
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
            let metadata = openCodeModels.first { $0.id == choice.id }
            let chatTurns = messages.map { ChatTurn(role: $0.role, text: $0.text, attachments: $0.attachments) }
            switch metadata?.recommendedAPI {
            case "responses":
                stream = OpenAIResponsesClient(
                    baseURL: metadata?.responsesBaseURL ?? "https://opencode.ai/inference/go/openai/v1",
                    apiKey: settings.key(for: .openCode) ?? "",
                    session: openCodeSessionID
                ).stream(model: choice.id, messages: chatTurns, settings: generation)
            case "messages":
                var base = metadata?.messagesBaseURL ?? "https://opencode.ai/inference/go/anthropic/v1"
                if base.hasSuffix("/v1") { base.removeLast(3) }
                stream = AnthropicClient(
                    baseURL: base,
                    apiKey: settings.key(for: .openCode) ?? "",
                    sendsImages: modelCapabilities.supportsVision,
                    isOpenCode: true,
                    openCodeSession: openCodeSessionID
                ).stream(model: choice.id, messages: chatTurns, settings: generation)
            default:
                stream = OpenAICompatibleClient(
                    baseURL: metadata?.chatBaseURL ?? settings.openCodeBase,
                    apiKey: settings.key(for: .openCode) ?? "",
                    usesCompletionTokens: false,
                    sendsTemperature: true,
                    sendsReasoningEffort: true,
                    sendsImages: modelCapabilities.supportsVision,
                    isOpenCode: true,
                    openCodeSession: openCodeSessionID
                ).stream(model: choice.id, messages: chatTurns, settings: generation)
            }
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
        if let reportedUsage {
            return (raw, reportedUsage)
        }
        if messages.allSatisfy({ $0.attachments.isEmpty && $0.attachmentPath == nil }),
           let promptCount = TokenCounter.count(
               messages.map(\.text),
               provider: choice.provider.rawValue,
               model: choice.id
           ),
           let completionCount = TokenCounter.count(
               raw,
               provider: choice.provider.rawValue,
               model: choice.id
           ) {
            return (raw, TokenUsage(
                prompt: promptCount,
                completion: completionCount,
                estimated: false,
                tokenizer: true
            ))
        }
        return (raw, TokenEstimator.estimatedUsage(prompt: messages, completion: raw))
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

    private func generateApprovalReview(
        _ call: ToolCall,
        sessionID: UUID,
        choice reviewChoice: ModelChoice
    ) async {
        let diff = await GitService.diff(in: projectURL)
        if Task.isCancelled { return }
        var generation = settings.generation(reasoning: reasoningSelection)
        generation.allowImages = modelCapabilities.supportsVision
        let reviewerContext = ToolContext(
            workspace: projectURL.path,
            sandbox: .readOnly,
            network: false,
            enabledShell: false,
            enabledFileEdits: settings.enableFileEdits,
            enabledComputerUse: false,
            enabledWeb: false,
            enabledBrowser: false,
            enabledPlan: false,
            skills: [],
            mcpTools: [],
            readOnly: true
        )
        let reviewer = ApprovalReviewer(
            model: reviewChoice.id,
            maxSteps: 4,
            context: reviewerContext,
            projectInstructions: projectInstructions
        ) { messages in
            let (raw, used) = try await self.streamCompletion(
                choice: reviewChoice,
                messages: messages,
                generation: generation
            )
            self.usage.record(used, provider: reviewChoice.provider, model: reviewChoice.id, sessionID: sessionID)
            return (raw, used)
        }
        do {
            let review = try await reviewer.review(call: call, diff: diff)
            guard !Task.isCancelled else { return }
            approvalReview = review
            approvalReviewing = false
        } catch {
            guard !Task.isCancelled else { return }
            approvalReviewError = error.localizedDescription
            approvalReviewing = false
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
        approvalReview = nil
        approvalReviewError = nil
        approvalReviewTask?.cancel()
        approvalReviewing = settings.autoReviewTools
        if settings.autoReviewTools {
            let reviewChoice = choice
            approvalReviewTask = Task { [weak self] in
                guard let self else { return }
                await self.generateApprovalReview(call, sessionID: sessionID, choice: reviewChoice)
            }
        }

        let decision = await withCheckedContinuation { continuation in
            if let existing = approvalContinuation {
                existing.resume(returning: .deny)
            }
            approvalContinuation = continuation
            pendingApproval = ApprovalRequest(
                toolName: call.name,
                summary: call.summary,
                sessionID: sessionID,
                patchPreview: call.name == "apply_patch"
                    ? call.string("patch").map(PatchPreview.parse)
                    : nil
            )
        }
        approvalReviewTask?.cancel()
        approvalReviewTask = nil
        approvalReviewing = false
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
        if call.name == "remember" {
            return remember(call)
        }
        if call.name == "update_goal" {
            return updateGoal(call)
        }
        if call.name == "subagent" {
            return await runSubagent(call, sessionID: sessionID)
        }
        if call.name == "fanout" {
            return await runFanout(call, sessionID: sessionID)
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

    private func remember(_ call: ToolCall) -> ToolResult {
        let text = (call.string("text") ?? call.string("memory") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            return .fail("remember requires text.")
        }
        guard let item = memories.add(kind: .memory, text: text) else {
            return .fail("Could not save the memory.")
        }
        return .ok("Saved memory: \(item.text)")
    }

    private func updateGoal(_ call: ToolCall) -> ToolResult {
        let text = (call.string("text") ?? call.string("goal") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            return .fail("update_goal requires text.")
        }
        let rawStatus = (call.string("status") ?? "active").lowercased()
        if rawStatus == "remove" || rawStatus == "delete" {
            if let existing = memories.goals.first(where: {
                $0.text.caseInsensitiveCompare(text) == .orderedSame
            }) {
                memories.remove(existing)
                return .ok("Removed goal: \(existing.text)")
            }
            return .fail("No matching goal.")
        }
        let status: GoalStatus = rawStatus == "completed" || rawStatus == "done"
            ? .completed
            : .active
        if let existing = memories.goals.first(where: {
            $0.text.caseInsensitiveCompare(text) == .orderedSame
        }) {
            memories.setGoalStatus(existing, status)
            return .ok("Goal is now \(status.title.lowercased()): \(existing.text)")
        }
        guard let item = memories.add(kind: .goal, text: text, goalStatus: status) else {
            return .fail("Could not save the goal.")
        }
        return .ok("Saved \(status.title.lowercased()) goal: \(item.text)")
    }

    private func runSubagent(
        _ call: ToolCall,
        sessionID: UUID,
        depth: Int = 0
    ) async -> ToolResult {
        let maxDepth = 2
        let task = (call.string("task") ?? call.string("prompt") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !task.isEmpty else {
            return .fail("subagent requires a task.")
        }
        let label = call.string("label")?.trimmingCharacters(in: .whitespacesAndNewlines)
        let maxSteps = min(max(call.int("max_steps") ?? 8, 1), 16)
        let runChoice = choice
        var generation = settings.generation(reasoning: reasoningSelection)
        generation.allowImages = modelCapabilities.supportsVision

        let context = ToolContext(
            workspace: projectURL.path,
            sandbox: .readOnly,
            network: settings.networkAccess,
            enabledShell: false,
            enabledFileEdits: settings.enableFileEdits,
            enabledComputerUse: false,
            enabledWeb: settings.enableWeb,
            enabledBrowser: false,
            enabledPlan: false,
            skills: skills.enabled,
            mcpTools: [],
            readOnly: true,
            allowNestedAgents: depth < maxDepth
        )

        let engine = SubagentEngine(
            label: label?.isEmpty == false ? label! : String(task.prefix(40)),
            task: task,
            maxSteps: maxSteps,
            context: context,
            projectInstructions: projectInstructions,
            depth: depth,
            maxDepth: maxDepth,
            nestedRunner: { [weak self] nestedCall, nextDepth in
                guard let self else {
                    return .fail("Talos is not available for nested delegation.")
                }
                return await self.runSubagent(
                    nestedCall,
                    sessionID: sessionID,
                    depth: nextDepth
                )
            }
        ) { messages in
            let (raw, used) = try await self.streamCompletion(
                choice: runChoice,
                messages: messages,
                generation: generation
            )
            self.usage.record(used, provider: runChoice.provider, model: runChoice.id, sessionID: sessionID)
            return (raw, used)
        }

        do {
            let outcome = try await engine.run()
            return .ok(outcome.report)
        } catch {
            return .fail("Subagent failed: \(error.localizedDescription)")
        }
    }

    private func runFanout(_ call: ToolCall, sessionID: UUID) async -> ToolResult {
        let rawTasks = call.array("tasks") ?? []
        var tasks: [FanOutTask] = []
        for raw in rawTasks {
            if let text = raw.string?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                tasks.append(FanOutTask(label: String(text.prefix(40)), task: text))
            } else if let object = raw.object,
                      let text = object["task"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                      !text.isEmpty {
                let label = object["label"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines)
                tasks.append(FanOutTask(
                    label: label?.isEmpty == false ? label! : String(text.prefix(40)),
                    task: text
                ))
            }
        }
        guard !tasks.isEmpty else {
            return .fail("fanout requires a non-empty tasks array.")
        }
        let concurrency = min(max(call.int("max_concurrency") ?? 3, 1), 6)
        let maxSteps = min(max(call.int("max_steps") ?? 6, 1), 12)
        let runChoice = choice
        var generation = settings.generation(reasoning: reasoningSelection)
        generation.allowImages = modelCapabilities.supportsVision
        let context = ToolContext(
            workspace: projectURL.path,
            sandbox: .readOnly,
            network: settings.networkAccess,
            enabledShell: false,
            enabledFileEdits: settings.enableFileEdits,
            enabledComputerUse: false,
            enabledWeb: settings.enableWeb,
            enabledBrowser: false,
            enabledPlan: false,
            skills: skills.enabled,
            mcpTools: [],
            readOnly: true
        )
        let engine = FanOutEngine(
            tasks: tasks,
            maxConcurrency: concurrency,
            maxSteps: maxSteps,
            context: context,
            projectInstructions: projectInstructions
        ) { messages in
            let (raw, used) = try await self.streamCompletion(
                choice: runChoice,
                messages: messages,
                generation: generation
            )
            self.usage.record(used, provider: runChoice.provider, model: runChoice.id, sessionID: sessionID)
            return (raw, used)
        }
        let outcome = await engine.run()
        return .ok(outcome.report)
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
            mcpTools: mcpTools,
            cdpEndpoint: settings.browserCDPEndpoint,
            enabledLSP: settings.enableLSP,
            enabledSemanticIndex: settings.enableSemanticIndex
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
                attachmentPath: result.attachmentPath,
                parentID: sessions[index].messages.last?.id
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


extension AppModel: AppServerHost {
    func appServerSessions() -> [AppServerSession] {
        sessions.map { session in
            AppServerSession(
                id: session.id,
                title: session.title,
                updatedAt: session.updatedAt,
                archived: session.archived,
                messages: session.messages.map { message in
                    AppServerMessage(
                        id: message.id,
                        role: message.role.rawValue,
                        text: message.text,
                        toolName: message.toolName,
                        isStreaming: message.isStreaming,
                        createdAt: message.createdAt,
                        attachments: message.attachments,
                        parentID: message.parentID,
                        revisionOf: message.revisionOf,
                        retryOf: message.retryOf
                    )
                },
                forkedFromSessionID: session.forkedFromSessionID,
                forkedFromMessageID: session.forkedFromMessageID
            )
        }
    }

    func appServerActiveSession() -> UUID? {
        activeRunSessionID
    }

    func appServerPendingApproval() -> AppServerApproval? {
        guard let approval = pendingApproval else { return nil }
        return AppServerApproval(
            id: approval.id,
            sessionID: approval.sessionID,
            toolName: approval.toolName,
            summary: approval.summary
        )
    }

    func appServerCreateSession(title: String?) -> UUID? {
        var session = Session(title: title?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? title!
            : "App Server Task")
        session.projectPath = currentProject?.path ?? settings.workspacePath
        sessions.insert(session, at: 0)
        selectedID = session.id
        saveSessions()
        return session.id
    }

    func appServerPrompt(sessionID: UUID, text: String) -> Result<UUID, Error> {
        startRun(sessionID: sessionID, text: text)
    }

    func appServerCancel(sessionID: UUID) -> Bool {
        cancelRun(sessionID: sessionID)
    }

    func appServerRetryMessage(sessionID: UUID, messageID: UUID) async -> Result<UUID, Error> {
        await retryMessage(sessionID, messageID)
    }

    func appServerEditMessage(sessionID: UUID, messageID: UUID, text: String) async -> Result<UUID, Error> {
        guard let message = sessions.first(where: { $0.id == sessionID })?
            .messages.first(where: { $0.id == messageID }) else {
            return .failure(AppServerError.message("Message not found."))
        }
        return await commitEditMessage(
            MessageEditTarget(
                sessionID: sessionID,
                messageID: messageID,
                text: message.text,
                attachments: message.attachments
            ),
            text: text
        )
    }

    func appServerForkMessage(sessionID: UUID, messageID: UUID) -> Result<UUID, Error> {
        forkMessage(sessionID, messageID)
    }

    func appServerResolveApproval(id: UUID, decision: String) -> Bool {
        guard pendingApproval?.id == id else { return false }
        let resolved: ApprovalDecision
        switch decision.lowercased() {
        case "allow", "allowonce", "once", "approve":
            resolved = .allowOnce
        case "allowforall", "allowfortask", "always", "trust":
            resolved = .allowForTask
        case "deny", "reject", "cancel":
            resolved = .deny
        default:
            return false
        }
        resolveApproval(resolved)
        return true
    }

    func auditDocument() -> [String: Any] {
        AuditExport.document(
            sessions: sessions,
            usage: usage.records,
            hooks: hooks.log,
            workspace: projectURL.path,
            model: choice.display,
            approvalPolicy: settings.approvalPolicy.rawValue,
            sandbox: settings.sandboxMode.rawValue,
            network: settings.networkAccess,
            managedPolicy: settings.managed != nil
        )
    }

    func appServerAuditLog() -> [String: Any] {
        auditDocument()
    }

    func appServerInfo() -> [String: Any] {
        [
            "server": "talos",
            "protocol": "json-rpc/sse",
            "version": "1.0",
            "model": choice.display,
            "workspace": projectURL.path,
            "streaming": isStreaming,
            "sessionId": activeRunSessionID?.uuidString ?? NSNull(),
            "approvalPolicy": settings.approvalPolicy.rawValue,
            "sandbox": settings.sandboxMode.rawValue,
            "tokenCounting": tokenizerLabel,
            "contextTokens": providerContextTokens ?? NSNull(),
        ]
    }
}
