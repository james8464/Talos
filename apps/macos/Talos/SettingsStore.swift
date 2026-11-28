import Foundation

@MainActor
final class SettingsStore: ObservableObject {
    private let defaults = UserDefaults.standard

    @Published var localURL: String { didSet { defaults.set(localURL, forKey: "localURL") } }
    @Published var openCodeBase: String { didSet { defaults.set(openCodeBase, forKey: "openCodeBase") } }
    @Published var openCodeModel: String { didSet { defaults.set(openCodeModel, forKey: "openCodeModel") } }
    @Published var openAIBase: String { didSet { defaults.set(openAIBase, forKey: "openAIBase") } }
    @Published var openAIModel: String { didSet { defaults.set(openAIModel, forKey: "openAIModel") } }
    @Published var anthropicBase: String { didSet { defaults.set(anthropicBase, forKey: "anthropicBase") } }
    @Published var anthropicModel: String { didSet { defaults.set(anthropicModel, forKey: "anthropicModel") } }
    @Published var geminiBase: String { didSet { defaults.set(geminiBase, forKey: "geminiBase") } }
    @Published var geminiModel: String { didSet { defaults.set(geminiModel, forKey: "geminiModel") } }

    @Published var speed: ResponseSpeed {
        didSet { defaults.set(speed.rawValue, forKey: "speed") }
    }
    @Published var temperature: Double {
        didSet { defaults.set(temperature, forKey: "temperature") }
    }
    @Published var keepAlive: String {
        didSet { defaults.set(keepAlive, forKey: "keepAlive") }
    }

    @Published private(set) var reasoningByModel: [String: String] = [:]
    @Published private(set) var contextWindowOverrides: [String: Int] = [:]
    @Published private(set) var savedKeys: Set<Provider> = []
    @Published private(set) var mcpServers: [MCPServerConfig] = []

    @Published var approvalPolicy: ApprovalPolicy {
        didSet { defaults.set(approvalPolicy.rawValue, forKey: "approvalPolicy") }
    }
    @Published var sandboxMode: SandboxMode {
        didSet { defaults.set(sandboxMode.rawValue, forKey: "sandboxMode") }
    }
    @Published var networkAccess: Bool {
        didSet { defaults.set(networkAccess, forKey: "networkAccess") }
    }
    @Published var workspacePath: String {
        didSet { defaults.set(workspacePath, forKey: "workspacePath") }
    }
    @Published var maxToolSteps: Int {
        didSet { defaults.set(maxToolSteps, forKey: "maxToolSteps") }
    }
    @Published var autoCompact: Bool {
        didSet { defaults.set(autoCompact, forKey: "autoCompact") }
    }
    @Published var compactionThreshold: Double {
        didSet { defaults.set(compactionThreshold, forKey: "compactionThreshold") }
    }

    @Published var enableShell: Bool { didSet { defaults.set(enableShell, forKey: "enableShell") } }
    @Published var enableFileEdits: Bool { didSet { defaults.set(enableFileEdits, forKey: "enableFileEdits") } }
    @Published var enableComputerUse: Bool { didSet { defaults.set(enableComputerUse, forKey: "enableComputerUse") } }
    @Published var enableWeb: Bool { didSet { defaults.set(enableWeb, forKey: "enableWeb") } }
    @Published var enableBrowser: Bool { didSet { defaults.set(enableBrowser, forKey: "enableBrowser") } }
    @Published var enablePlan: Bool { didSet { defaults.set(enablePlan, forKey: "enablePlan") } }

    @Published var appearance: AppearanceMode {
        didSet { defaults.set(appearance.rawValue, forKey: "appearance") }
    }
    @Published var transcriptFontSize: Double {
        didSet { defaults.set(transcriptFontSize, forKey: "transcriptFontSize") }
    }
    @Published var notifyOnFinish: Bool {
        didSet { defaults.set(notifyOnFinish, forKey: "notifyOnFinish") }
    }
    @Published var completionSound: Bool {
        didSet { defaults.set(completionSound, forKey: "completionSound") }
    }

    init() {
        let defaults = UserDefaults.standard
        localURL = defaults.string(forKey: "localURL") ?? Provider.local.defaultBaseURL
        openCodeBase = defaults.string(forKey: "openCodeBase") ?? Provider.openCode.defaultBaseURL
        openCodeModel = defaults.string(forKey: "openCodeModel") ?? Provider.openCode.defaultModel
        openAIBase = defaults.string(forKey: "openAIBase") ?? Provider.openAI.defaultBaseURL
        openAIModel = defaults.string(forKey: "openAIModel") ?? Provider.openAI.defaultModel
        anthropicBase = defaults.string(forKey: "anthropicBase") ?? Provider.anthropic.defaultBaseURL
        anthropicModel = defaults.string(forKey: "anthropicModel") ?? Provider.anthropic.defaultModel
        geminiBase = defaults.string(forKey: "geminiBase") ?? Provider.gemini.defaultBaseURL
        geminiModel = defaults.string(forKey: "geminiModel") ?? Provider.gemini.defaultModel

        let storedTemperature = defaults.object(forKey: "temperature") as? Double ?? 0.7
        temperature = min(max(storedTemperature, 0), 1.5)
        speed = ResponseSpeed(rawValue: defaults.string(forKey: "speed") ?? "") ?? .balanced
        keepAlive = defaults.string(forKey: "keepAlive") ?? "30m"

        approvalPolicy = ApprovalPolicy(rawValue: defaults.string(forKey: "approvalPolicy") ?? "") ?? .onRequest
        sandboxMode = SandboxMode(rawValue: defaults.string(forKey: "sandboxMode") ?? "") ?? .workspaceWrite
        networkAccess = defaults.bool(forKey: "networkAccess")
        let home = FileManager.default.homeDirectoryForCurrentUser
        let developer = home.appendingPathComponent("Developer")
        let fallback = FileManager.default.fileExists(atPath: developer.path) ? developer.path : home.path
        workspacePath = defaults.string(forKey: "workspacePath") ?? fallback
        let storedSteps = defaults.object(forKey: "maxToolSteps") as? Int ?? 8
        maxToolSteps = min(max(storedSteps, 1), 24)
        autoCompact = defaults.object(forKey: "autoCompact") as? Bool ?? true
        let storedThreshold = defaults.object(forKey: "compactionThreshold") as? Double ?? 0.8
        compactionThreshold = min(max(storedThreshold, 0.5), 0.95)

        enableShell = defaults.object(forKey: "enableShell") as? Bool ?? true
        enableFileEdits = defaults.object(forKey: "enableFileEdits") as? Bool ?? true
        enableComputerUse = defaults.object(forKey: "enableComputerUse") as? Bool ?? true
        enableWeb = defaults.object(forKey: "enableWeb") as? Bool ?? true
        enableBrowser = defaults.object(forKey: "enableBrowser") as? Bool ?? true
        enablePlan = defaults.object(forKey: "enablePlan") as? Bool ?? true

        appearance = AppearanceMode(rawValue: defaults.string(forKey: "appearance") ?? "") ?? .system
        let storedFont = defaults.object(forKey: "transcriptFontSize") as? Double ?? 14
        transcriptFontSize = min(max(storedFont, 12), 20)
        notifyOnFinish = defaults.bool(forKey: "notifyOnFinish")
        completionSound = defaults.object(forKey: "completionSound") as? Bool ?? true

        if let data = defaults.data(forKey: "reasoningByModel"),
           let decoded = try? JSONDecoder().decode([String: String].self, from: data) {
            reasoningByModel = decoded
        }
        if let data = defaults.data(forKey: "contextWindowOverrides"),
           let decoded = try? JSONDecoder().decode([String: Int].self, from: data) {
            contextWindowOverrides = decoded
        }
        if let data = defaults.data(forKey: "mcpServers"),
           let decoded = try? JSONDecoder().decode([MCPServerConfig].self, from: data) {
            mcpServers = decoded
        }

        refreshKeys()
    }

    func generation(reasoning: String?) -> GenerationSettings {
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
        case .openCode: return openCodeBase
        case .openAI: return openAIBase
        case .anthropic: return anthropicBase
        case .gemini: return geminiBase
        }
    }

    func model(for provider: Provider) -> String {
        switch provider {
        case .local: return ""
        case .openCode: return openCodeModel
        case .openAI: return openAIModel
        case .anthropic: return anthropicModel
        case .gemini: return geminiModel
        }
    }

    func setModel(_ model: String, for provider: Provider) {
        switch provider {
        case .local: break
        case .openCode: openCodeModel = model
        case .openAI: openAIModel = model
        case .anthropic: anthropicModel = model
        case .gemini: geminiModel = model
        }
    }

    func reasoningValue(for choice: ModelChoice) -> String? {
        reasoningByModel[reasoningKey(for: choice)]
    }

    func setReasoning(_ value: String?, for choice: ModelChoice) {
        var next = reasoningByModel
        let key = reasoningKey(for: choice)
        if let value {
            next[key] = value
        } else {
            next.removeValue(forKey: key)
        }
        reasoningByModel = next
        if let data = try? JSONEncoder().encode(next) {
            defaults.set(data, forKey: "reasoningByModel")
        }
    }

    func contextWindowOverride(for choice: ModelChoice) -> Int? {
        contextWindowOverrides[reasoningKey(for: choice)]
    }

    func setContextWindowOverride(_ value: Int?, for choice: ModelChoice) {
        var next = contextWindowOverrides
        let key = reasoningKey(for: choice)
        if let value, value > 0 {
            next[key] = value
        } else {
            next.removeValue(forKey: key)
        }
        contextWindowOverrides = next
        if let data = try? JSONEncoder().encode(next) {
            defaults.set(data, forKey: "contextWindowOverrides")
        }
    }

    func addMCPServer(_ server: MCPServerConfig) {
        mcpServers.append(server)
        saveMCP()
    }

    func removeMCPServer(_ server: MCPServerConfig) {
        mcpServers.removeAll { $0.id == server.id }
        saveMCP()
    }

    func setMCPEnabled(_ server: MCPServerConfig, _ enabled: Bool) {
        guard let index = mcpServers.firstIndex(where: { $0.id == server.id }) else { return }
        mcpServers[index].enabled = enabled
        saveMCP()
    }

    private func saveMCP() {
        guard let data = try? JSONEncoder().encode(mcpServers) else { return }
        defaults.set(data, forKey: "mcpServers")
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
        speed = .balanced
        temperature = 0.7
        keepAlive = "30m"
        reasoningByModel = [:]
        defaults.removeObject(forKey: "reasoningByModel")
    }

    private func reasoningKey(for choice: ModelChoice) -> String {
        "\(choice.provider.rawValue)|\(choice.id)"
    }
}
