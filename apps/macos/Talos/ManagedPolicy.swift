import Foundation
import CoreFoundation
import CryptoKit

struct ManagedPolicy: Codable, Equatable, Sendable {
    var version: Int?
    var name: String?
    var message: String?

    var provider: Provider?
    var model: String?
    var reasoning: String?

    var sandbox: SandboxMode?
    var approval: ApprovalPolicy?
    var network: Bool?

    var enableShell: Bool?
    var enableFileEdits: Bool?
    var enableComputerUse: Bool?
    var enableWeb: Bool?
    var enableBrowser: Bool?
    var enablePlan: Bool?
    var autoReview: Bool?
    var maxToolSteps: Int?

    /// Present means the managed hook list replaces local hooks and is locked.
    var hooks: [HookDefinition]?
    /// Present means the managed server list replaces local servers and is locked.
    var mcpServers: [MCPServerConfig]?

    var title: String {
        name?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? name!
            : "Managed policy"
    }

    var isModelLocked: Bool {
        provider != nil || model != nil
    }

    var isReasoningLocked: Bool {
        reasoning != nil
    }

    var isHooksLocked: Bool {
        hooks != nil
    }

    var isMCPLocked: Bool {
        mcpServers != nil
    }

    func merged(over base: ManagedPolicy?) -> ManagedPolicy {
        guard let base else { return self }
        var value = base
        if let version { value.version = version }
        if let name { value.name = name }
        if let message { value.message = message }
        if let provider { value.provider = provider }
        if let model { value.model = model }
        if let reasoning { value.reasoning = reasoning }
        if let sandbox { value.sandbox = sandbox }
        if let approval { value.approval = approval }
        if let network { value.network = network }
        if let enableShell { value.enableShell = enableShell }
        if let enableFileEdits { value.enableFileEdits = enableFileEdits }
        if let enableComputerUse { value.enableComputerUse = enableComputerUse }
        if let enableWeb { value.enableWeb = enableWeb }
        if let enableBrowser { value.enableBrowser = enableBrowser }
        if let enablePlan { value.enablePlan = enablePlan }
        if let autoReview { value.autoReview = autoReview }
        if let maxToolSteps { value.maxToolSteps = maxToolSteps }
        if let hooks { value.hooks = hooks }
        if let mcpServers { value.mcpServers = mcpServers }
        return value
    }

    func allows(choice: ModelChoice) -> Bool {
        if let provider, choice.provider != provider { return false }
        if let model, !model.isEmpty, choice.id != model { return false }
        return true
    }

    func enforcedReasoning(_ requested: String?) -> String? {
        reasoning ?? requested
    }

    var lockedSummary: [String] {
        var lines: [String] = []
        if let provider { lines.append("Provider: \(provider.title)") }
        if let model { lines.append("Model: \(model)") }
        if let reasoning { lines.append("Reasoning: \(reasoning)") }
        if let sandbox { lines.append("Sandbox: \(sandbox.title)") }
        if let approval { lines.append("Approvals: \(approval.title)") }
        if let network { lines.append("Network: \(network ? "allowed" : "blocked")") }
        if let enableShell { lines.append("Shell: \(enableShell ? "on" : "off")") }
        if let enableFileEdits { lines.append("File edits: \(enableFileEdits ? "on" : "off")") }
        if let enableComputerUse { lines.append("Computer use: \(enableComputerUse ? "on" : "off")") }
        if let enableWeb { lines.append("Web: \(enableWeb ? "on" : "off")") }
        if let enableBrowser { lines.append("Browser: \(enableBrowser ? "on" : "off")") }
        if let enablePlan { lines.append("Plans: \(enablePlan ? "on" : "off")") }
        if let autoReview { lines.append("Auto-review: \(autoReview ? "on" : "off")") }
        if let maxToolSteps { lines.append("Max tool steps: \(maxToolSteps)") }
        if let hooks { lines.append("Hooks: \(hooks.count) managed hook\(hooks.count == 1 ? "" : "s")") }
        if let mcpServers { lines.append("MCP servers: \(mcpServers.count) managed server\(mcpServers.count == 1 ? "" : "s")") }
        return lines
    }
}

struct ManagedPolicyLoad: Sendable {
    let policy: ManagedPolicy?
    let sourcePath: String?
    let signatureStatus: String
    let error: String?
}

struct ManagedPreferencesResult: Sendable {
    let policy: ManagedPolicy?
    let domain: String
    let error: String?
}

enum ManagedPolicyLoader {
    static let managedDomain = "app.talos.macos"

    static var candidatePaths: [String] {
        var paths: [String] = []
        if let override = ProcessInfo.processInfo.environment["TALOS_POLICY_FILE"], !override.isEmpty {
            paths.append(override)
        }
        paths.append("/Library/Application Support/Talos/policy.json")
        paths.append("/etc/talos/policy.json")
        let home = FileManager.default.homeDirectoryForCurrentUser
        paths.append(home.appendingPathComponent("Library/Application Support/Talos/policy.json").path)
        paths.append(home.appendingPathComponent(".config/talos/policy.json").path)
        var seen = Set<String>()
        return paths.filter { seen.insert($0).inserted }
    }

    static func load() -> ManagedPolicyLoad {
        var fileResult = ManagedPolicyLoad(
            policy: nil,
            sourcePath: nil,
            signatureStatus: String(localized: "Not configured"),
            error: nil
        )
        for path in candidatePaths where FileManager.default.fileExists(atPath: path) {
            fileResult = load(url: URL(fileURLWithPath: path))
            break
        }

        let preferences = loadManagedPreferences()
        guard let preferences else { return fileResult }
        if let error = preferences.error {
            return ManagedPolicyLoad(
                policy: nil,
                sourcePath: "Managed preferences: \(preferences.domain)",
                signatureStatus: String(localized: "Managed preferences (MDM)"),
                error: error
            )
        }
        guard let managed = preferences.policy else { return fileResult }

        let merged = managed.merged(over: fileResult.policy)
        let source: String
        if let fileSource = fileResult.sourcePath {
            source = "\(fileSource) + managed preferences: \(preferences.domain)"
        } else {
            source = "Managed preferences: \(preferences.domain)"
        }
        let signature: String
        switch (fileResult.policy != nil, fileResult.signatureStatus) {
        case (true, _) where fileResult.signatureStatus.contains("Signed"):
            signature = "Signed (Ed25519) + MDM managed preferences"
        case (true, _):
            signature = "Local file (unsigned) + MDM managed preferences"
        default:
            signature = "Managed preferences (MDM)"
        }
        return ManagedPolicyLoad(
            policy: merged,
            sourcePath: source,
            signatureStatus: signature,
            error: nil
        )
    }

    static func loadManagedPreferences(domain: String? = nil) -> ManagedPreferencesResult? {
        let environment = ProcessInfo.processInfo.environment
        if let simulatedDomain = environment["TALOS_MANAGED_PREFS_DOMAIN"],
           !simulatedDomain.isEmpty {
            let values = UserDefaults(suiteName: simulatedDomain)?.dictionaryRepresentation() ?? [:]
            return decodeManagedPreferences(values, domain: simulatedDomain)
        }
        if let file = environment["TALOS_MANAGED_PREFS_FILE"], !file.isEmpty {
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: file)),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return ManagedPreferencesResult(
                    policy: nil,
                    domain: file,
                    error: "The managed preferences file is not a valid property list or JSON dictionary."
                )
            }
            return decodeManagedPreferences(object, domain: file)
        }

        let resolvedDomain = domain ?? managedDomain
        let scopes: [(CFString, CFString)] = [
            (kCFPreferencesAnyUser, kCFPreferencesCurrentHost),
            (kCFPreferencesAnyUser, kCFPreferencesAnyHost),
        ]
        for (user, host) in scopes {
            if let values = CFPreferencesCopyMultiple(
                nil,
                resolvedDomain as CFString,
                user,
                host
            ) as? [String: Any], !values.isEmpty {
                return decodeManagedPreferences(values, domain: resolvedDomain)
            }
        }
        return nil
    }

    private static func decodeManagedPreferences(
        _ values: [String: Any],
        domain: String
    ) -> ManagedPreferencesResult? {
        let wrappedKeys = ["ManagedPolicy", "Policy", "policy"]
        for key in wrappedKeys {
            guard let raw = values[key] else { continue }
            let data: Data?
            if let dictionary = raw as? [String: Any] {
                data = try? JSONSerialization.data(withJSONObject: dictionary)
            } else if let text = raw as? String {
                data = text.data(using: .utf8)
            } else if let value = raw as? Data {
                data = value
            } else {
                data = nil
            }
            guard let data else {
                return ManagedPreferencesResult(
                    policy: nil,
                    domain: domain,
                    error: "Managed policy in \(domain) is not a dictionary or JSON string."
                )
            }
            guard let policy = try? JSONDecoder().decode(ManagedPolicy.self, from: data) else {
                return ManagedPreferencesResult(
                    policy: nil,
                    domain: domain,
                    error: "Managed policy in \(domain) does not match the policy schema."
                )
            }
            return ManagedPreferencesResult(policy: policy, domain: domain, error: nil)
        }

        let knownKeys: Set<String> = [
            "version", "name", "message", "provider", "model", "reasoning",
            "sandbox", "approval", "network", "enableShell", "enableFileEdits",
            "enableComputerUse", "enableWeb", "enableBrowser", "enablePlan",
            "autoReview", "maxToolSteps", "hooks", "mcpServers",
        ]
        let filtered = values.filter { knownKeys.contains($0.key) }
        guard !filtered.isEmpty else { return nil }
        guard let data = try? JSONSerialization.data(withJSONObject: filtered),
              let policy = try? JSONDecoder().decode(ManagedPolicy.self, from: data) else {
            return ManagedPreferencesResult(
                policy: nil,
                domain: domain,
                error: "Managed preferences in \(domain) do not match the policy schema."
            )
        }
        return ManagedPreferencesResult(policy: policy, domain: domain, error: nil)
    }

    static func load(url: URL) -> ManagedPolicyLoad {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            return ManagedPolicyLoad(
                policy: nil,
                sourcePath: url.path,
                signatureStatus: String(localized: "Unreadable"),
                error: "Could not read \(url.path): \(error.localizedDescription)"
            )
        }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return managedError(url, "The policy file is not valid JSON.")
        }

        if let payload = object["payload"] as? String, let signature = object["signature"] as? String {
            guard let payloadData = decodeBase64(payload),
                  let signatureData = decodeBase64(signature) else {
                return managedError(url, "The signed policy envelope is not valid base64.")
            }
            guard let publicKey = publicKey(for: url) else {
                return managedError(url, "The policy is signed but no public key was found. Provide policy.pub next to it or TALOS_POLICY_PUBLIC_KEY.")
            }
            guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
                  key.isValidSignature(signatureData, for: payloadData) else {
                return managedError(url, "The policy signature is invalid. The policy was not applied.")
            }
            guard let policy = try? JSONDecoder().decode(ManagedPolicy.self, from: payloadData) else {
                return managedError(url, "The signed policy payload is not a valid managed policy.")
            }
            return ManagedPolicyLoad(
                policy: policy,
                sourcePath: url.path,
                signatureStatus: String(localized: "Signed (Ed25519)"),
                error: nil
            )
        }

        guard let policy = try? JSONDecoder().decode(ManagedPolicy.self, from: data) else {
            return managedError(url, "The policy file does not match the managed policy schema.")
        }
        return ManagedPolicyLoad(
            policy: policy,
            sourcePath: url.path,
            signatureStatus: String(localized: "Local file (unsigned)"),
            error: nil
        )
    }

    private static func managedError(_ url: URL, _ message: String) -> ManagedPolicyLoad {
        ManagedPolicyLoad(
            policy: nil,
            sourcePath: url.path,
            signatureStatus: String(localized: "Rejected"),
            error: message
        )
    }

    private static func publicKey(for policyURL: URL) -> Data? {
        if let raw = ProcessInfo.processInfo.environment["TALOS_POLICY_PUBLIC_KEY"],
           let data = decodeBase64(raw.trimmingCharacters(in: .whitespacesAndNewlines)) {
            return data
        }
        let file = policyURL.deletingLastPathComponent().appendingPathComponent("policy.pub")
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        return decodeBase64(text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private static func decodeBase64(_ value: String) -> Data? {
        let normalized = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let padded = normalized + String(repeating: "=", count: (4 - normalized.count % 4) % 4)
        return Data(base64Encoded: padded)
    }
}
