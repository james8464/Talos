import Foundation

@MainActor
final class SettingsStore: ObservableObject {
    private let defaults = UserDefaults.standard

    @Published var localURL: String { didSet { defaults.set(localURL, forKey: "localURL") } }
    @Published var openAIBase: String { didSet { defaults.set(openAIBase, forKey: "openAIBase") } }
    @Published var openAIModel: String { didSet { defaults.set(openAIModel, forKey: "openAIModel") } }
    @Published var anthropicBase: String { didSet { defaults.set(anthropicBase, forKey: "anthropicBase") } }
    @Published var anthropicModel: String { didSet { defaults.set(anthropicModel, forKey: "anthropicModel") } }
    @Published var geminiBase: String { didSet { defaults.set(geminiBase, forKey: "geminiBase") } }
    @Published var geminiModel: String { didSet { defaults.set(geminiModel, forKey: "geminiModel") } }

    @Published var reasoning: ReasoningEffort {
        didSet { defaults.set(reasoning.rawValue, forKey: "reasoning") }
    }
    @Published var speed: ResponseSpeed {
        didSet { defaults.set(speed.rawValue, forKey: "speed") }
    }
    @Published var temperature: Double {
        didSet { defaults.set(temperature, forKey: "temperature") }
    }
    @Published var keepAlive: String {
        didSet { defaults.set(keepAlive, forKey: "keepAlive") }
    }

    @Published private(set) var savedKeys: Set<Provider> = []

    init() {
        let defaults = UserDefaults.standard
        localURL = defaults.string(forKey: "localURL") ?? Provider.local.defaultBaseURL
        openAIBase = defaults.string(forKey: "openAIBase") ?? Provider.openAI.defaultBaseURL
        openAIModel = defaults.string(forKey: "openAIModel") ?? Provider.openAI.defaultModel
        anthropicBase = defaults.string(forKey: "anthropicBase") ?? Provider.anthropic.defaultBaseURL
        anthropicModel = defaults.string(forKey: "anthropicModel") ?? Provider.anthropic.defaultModel
        geminiBase = defaults.string(forKey: "geminiBase") ?? Provider.gemini.defaultBaseURL
        geminiModel = defaults.string(forKey: "geminiModel") ?? Provider.gemini.defaultModel

        let storedTemperature = defaults.object(forKey: "temperature") as? Double ?? 0.7
        temperature = min(max(storedTemperature, 0), 1.5)
        reasoning = ReasoningEffort(rawValue: defaults.string(forKey: "reasoning") ?? "") ?? .medium
        speed = ResponseSpeed(rawValue: defaults.string(forKey: "speed") ?? "") ?? .balanced
        keepAlive = defaults.string(forKey: "keepAlive") ?? "30m"

        refreshKeys()
    }

    var generation: GenerationSettings {
        GenerationSettings(
            temperature: temperature,
            maxTokens: speed.maxTokens,
            reasoning: reasoning,
            keepAlive: keepAlive
        )
    }

    func baseURL(for provider: Provider) -> String {
        switch provider {
        case .local: return localURL
        case .openAI: return openAIBase
        case .anthropic: return anthropicBase
        case .gemini: return geminiBase
        }
    }

    func model(for provider: Provider) -> String {
        switch provider {
        case .local: return ""
        case .openAI: return openAIModel
        case .anthropic: return anthropicModel
        case .gemini: return geminiModel
        }
    }

    func setModel(_ model: String, for provider: Provider) {
        switch provider {
        case .local: break
        case .openAI: openAIModel = model
        case .anthropic: anthropicModel = model
        case .gemini: geminiModel = model
        }
    }

    func key(for provider: Provider) -> String? {
        guard let account = provider.keyAccount else { return nil }
        return Keychain.read(account: account)
    }

    func saveKey(_ key: String, for provider: Provider) throws {
        guard let account = provider.keyAccount else { return }
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        try Keychain.save(trimmed, account: account)
        refreshKeys()
    }

    func clearKey(for provider: Provider) {
        guard let account = provider.keyAccount else { return }
        Keychain.delete(account: account)
        refreshKeys()
    }

    func refreshKeys() {
        savedKeys = Set(Provider.allCases.filter { provider in
            guard let account = provider.keyAccount else { return false }
            return Keychain.read(account: account)?.isEmpty == false
        })
    }

    func resetGeneration() {
        reasoning = .medium
        speed = .balanced
        temperature = 0.7
        keepAlive = "30m"
    }
}
