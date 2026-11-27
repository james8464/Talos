import SwiftUI

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

    let settings = SettingsStore()
    private let defaults = UserDefaults.standard
    private var runTask: Task<Void, Never>?

    init() {
        newSession()
        if let raw = defaults.string(forKey: "selectedProvider"),
           let provider = Provider(rawValue: raw),
           let id = defaults.string(forKey: "selectedModel") {
            choice = ModelChoice(provider: provider, id: id)
        }
        Task { await refreshChoices() }
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

    func newSession() {
        let session = Session(title: "New Task")
        sessions.insert(session, at: 0)
        selectedID = session.id
    }

    func delete(_ id: UUID) {
        sessions.removeAll { $0.id == id }
        if selectedID == id { selectedID = sessions.first?.id }
    }

    func choose(_ choice: ModelChoice) {
        self.choice = choice
        defaults.set(choice.provider.rawValue, forKey: "selectedProvider")
        defaults.set(choice.id, forKey: "selectedModel")
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
    }

    func send(_ text: String) {
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
        sessions[index].messages.append(ChatMessage(role: .user, text: trimmed))
        let history = sessions[index].messages.filter { $0.role != .system }
        sessions[index].messages.append(
            ChatMessage(role: .assistant, text: "", isStreaming: true, model: choice.display)
        )
        isStreaming = true

        let generation = settings.generation
        let stream: AsyncThrowingStream<String, Error>
        switch choice.provider {
        case .local:
            stream = OllamaClient(baseURL: URL(string: settings.localURL) ?? URL(string: Provider.local.defaultBaseURL)!)
                .stream(model: choice.id, messages: history, settings: generation)
        case .openAI:
            stream = OpenAICompatibleClient(
                baseURL: settings.openAIBase,
                apiKey: settings.key(for: .openAI) ?? "",
                usesCompletionTokens: true,
                sendsTemperature: false,
                sendsReasoningEffort: true
            ).stream(
                model: choice.id,
                messages: history.map { ChatTurn(role: $0.role, text: $0.text) },
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
                messages: history.map { ChatTurn(role: $0.role, text: $0.text) },
                settings: generation
            )
        case .anthropic:
            stream = AnthropicClient(
                baseURL: settings.anthropicBase,
                apiKey: settings.key(for: .anthropic) ?? ""
            ).stream(
                model: choice.id,
                messages: history.map { ChatTurn(role: $0.role, text: $0.text) },
                settings: generation
            )
        }

        runTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in stream {
                    if Task.isCancelled { break }
                    append(chunk, to: id)
                }
            } catch {
                if !Task.isCancelled {
                    self.error = error.localizedDescription
                    fail(id, message: error.localizedDescription)
                }
            }
            finish(id)
        }
    }

    func stop() {
        if let id = selectedID,
           let index = sessions.firstIndex(where: { $0.id == id }),
           var last = sessions[index].messages.last,
           last.isStreaming,
           last.text.isEmpty {
            last.text = "Stopped."
            last.isStreaming = false
            sessions[index].messages[sessions[index].messages.count - 1] = last
        }
        runTask?.cancel()
        runTask = nil
        isStreaming = false
    }

    private func append(_ chunk: String, to id: UUID) {
        guard let index = sessions.firstIndex(where: { $0.id == id }),
              var last = sessions[index].messages.last else { return }
        last.text += chunk
        sessions[index].messages[sessions[index].messages.count - 1] = last
    }

    private func fail(_ id: UUID, message: String) {
        guard let index = sessions.firstIndex(where: { $0.id == id }),
              var last = sessions[index].messages.last,
              last.isStreaming else { return }
        if last.text.isEmpty {
            last.text = "Request failed. \(message)"
        }
        last.isStreaming = false
        sessions[index].messages[sessions[index].messages.count - 1] = last
    }

    private func finish(_ id: UUID) {
        if let index = sessions.firstIndex(where: { $0.id == id }),
           var last = sessions[index].messages.last {
            if last.text.isEmpty {
                last.text = "No response was returned."
            }
            last.isStreaming = false
            sessions[index].messages[sessions[index].messages.count - 1] = last
        }
        isStreaming = false
        runTask = nil
    }
}
