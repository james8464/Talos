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

    let settings = SettingsStore()
    let usage = UsageStore()
    private let defaults = UserDefaults.standard
    private var runTask: Task<Void, Never>?
    private var approvalContinuation: CheckedContinuation<ApprovalDecision, Never>?

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

        if let raw = defaults.string(forKey: "selectedProvider"),
           let provider = Provider(rawValue: raw),
           let id = defaults.string(forKey: "selectedModel") {
            choice = ModelChoice(provider: provider, id: id)
        }
        Task {
            await refreshChoices()
            await refreshCapabilities()
        }
    }

    var filteredSessions: [Session] {
        guard !search.isEmpty else { return sessions }
        return sessions.filter {
            $0.title.localizedCaseInsensitiveContains(search) ||
            $0.preview.localizedCaseInsensitiveContains(search)
        }
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
        let system = ChatMessage(role: .system, text: ToolPrompt.system(context: toolContext()))
        let messages = [system] + session.messages.filter {
            $0.role != .system && !($0.role == .assistant && $0.text.isEmpty)
        }
        return ContextUsage(used: TokenEstimator.estimate(messages), limit: contextLimit)
    }

    var contextLabel: String {
        contextUsage.label
    }

    func newSession() {
        let session = Session(title: "New Task")
        sessions.insert(session, at: 0)
        selectedID = session.id
        saveSessions()
    }

    func delete(_ id: UUID) {
        sessions.removeAll { $0.id == id }
        if selectedID == id { selectedID = sessions.first?.id }
        if sessions.isEmpty { newSession() }
        saveSessions()
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
        case "/settings", "/tools":
            openSettings()
        case "/queue":
            if remainder.isEmpty {
                appendCommandMessage(queueSummary())
            } else {
                enqueue(remainder)
            }
        case "/status":
            appendCommandMessage(statusSummary())
        case "/usage":
            appendCommandMessage(usageSummary())
        case "/help", "/":
            appendCommandMessage("""
            Commands:
            /new — start a new task
            /clear — clear this task's messages
            /compact — summarize earlier messages now
            /stop — stop the current response
            /model — open the model picker
            /queue [text] — show or add to the queue
            /status — show model, context, and tool state
            /settings — open Settings
            /usage — show token usage
            /help — show this list
            """)
        default:
            appendCommandMessage("Unknown command \(command). Type /help for the list.")
        }
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

    private func openSettings() {
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
        let context = toolContext()
        let system = ChatMessage(role: .system, text: ToolPrompt.system(context: context))
        let normalized = history
            .filter { $0.role != .system && !($0.role == .assistant && $0.text.isEmpty) }
            .map { message in
                if message.role == .tool {
                    return ChatMessage(
                        role: .tool,
                        text: "Tool result (\(message.toolName ?? "tool")):\n\(message.text)",
                        toolName: message.toolName,
                        toolStatus: message.toolStatus
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
        let generation = settings.generation(reasoning: reasoningSelection)

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
        case .openAI:
            stream = OpenAICompatibleClient(
                baseURL: settings.openAIBase,
                apiKey: settings.key(for: .openAI) ?? "",
                usesCompletionTokens: true,
                sendsTemperature: false,
                sendsReasoningEffort: true
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text) },
                settings: generation
            )
        case .gemini:
            stream = OpenAICompatibleClient(
                baseURL: settings.geminiBase,
                apiKey: settings.key(for: .gemini) ?? "",
                usesCompletionTokens: false,
                sendsTemperature: true,
                sendsReasoningEffort: false
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text) },
                settings: generation
            )
        case .anthropic:
            stream = AnthropicClient(
                baseURL: settings.anthropicBase,
                apiKey: settings.key(for: .anthropic) ?? ""
            ).stream(
                model: choice.id,
                messages: messages.map { ChatTurn(role: $0.role, text: $0.text) },
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
        let system = ChatMessage(role: .system, text: ToolPrompt.system(context: toolContext()))
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
        let workspace = settings.workspacePath.isEmpty
            ? FileManager.default.homeDirectoryForCurrentUser.path
            : settings.workspacePath
        return ToolContext(
            workspace: workspace,
            sandbox: settings.sandboxMode,
            network: settings.networkAccess,
            enabledShell: settings.enableShell,
            enabledFileEdits: settings.enableFileEdits,
            enabledComputerUse: settings.enableComputerUse,
            enabledWeb: settings.enableWeb,
            enabledPlan: settings.enablePlan
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
