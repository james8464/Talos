import Foundation

struct AgentProfile: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var name: String
    var provider: Provider
    var model: String
    var reasoning: String?
    var sandbox: SandboxMode
    var approval: ApprovalPolicy
    var network: Bool
    var speed: ResponseSpeed
    var enableShell: Bool
    var enableFileEdits: Bool
    var enableComputerUse: Bool
    var enableWeb: Bool
    var enableBrowser: Bool
    var enablePlan: Bool

    var summary: String {
        [
            "\(provider.title) · \(model.isEmpty ? "default model" : model)",
            sandbox.title,
            approval == .never ? "no approvals" : "approvals",
        ].joined(separator: " · ")
    }
}

@MainActor
final class ProfileStore: ObservableObject {
    private let defaults = UserDefaults.standard
    private static let storageKey = "agentProfiles"
    private static let activeKey = "activeProfile"

    @Published private(set) var profiles: [AgentProfile] = []
    @Published private(set) var activeID: UUID?

    init() {
        if let data = defaults.data(forKey: Self.storageKey),
           let decoded = try? JSONDecoder().decode([AgentProfile].self, from: data) {
            profiles = decoded
        }
        if let raw = defaults.string(forKey: Self.activeKey) {
            activeID = UUID(uuidString: raw)
        }
    }

    var active: AgentProfile? {
        profiles.first { $0.id == activeID }
    }

    func upsert(_ profile: AgentProfile) {
        if let index = profiles.firstIndex(where: { $0.id == profile.id }) {
            profiles[index] = profile
        } else {
            profiles.append(profile)
        }
        save()
    }

    func remove(_ profile: AgentProfile) {
        profiles.removeAll { $0.id == profile.id }
        if activeID == profile.id {
            activeID = nil
            defaults.removeObject(forKey: Self.activeKey)
        }
        save()
    }

    func markActive(_ profile: AgentProfile?) {
        activeID = profile?.id
        if let profile {
            defaults.set(profile.id.uuidString, forKey: Self.activeKey)
        } else {
            defaults.removeObject(forKey: Self.activeKey)
        }
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(profiles) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}
