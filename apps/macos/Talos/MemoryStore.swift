import Foundation

enum MemoryKind: String, Codable, CaseIterable, Identifiable, Sendable {
    case memory
    case goal

    var id: String { rawValue }
    var title: String { self == .memory ? "Memory" : "Goal" }
    var symbol: String { self == .memory ? "brain" : "target" }
}

enum GoalStatus: String, Codable, CaseIterable, Identifiable, Sendable {
    case active
    case completed

    var id: String { rawValue }
    var title: String { self == .active ? "Active" : "Completed" }
}

struct MemoryItem: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var kind: MemoryKind
    var text: String
    var enabled = true
    var goalStatus: GoalStatus? = nil
    var createdAt = Date()
    var updatedAt = Date()

    var isActiveGoal: Bool {
        kind == .goal && goalStatus == .active
    }
}

@MainActor
final class MemoryStore: ObservableObject {
    @Published private(set) var items: [MemoryItem] = []

    private let fileURL: URL
    private let defaults = UserDefaults.standard

    init(fileURL: URL = MemoryStore.defaultFileURL()) {
        self.fileURL = fileURL
        load()
    }

    nonisolated static func defaultFileURL() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = support.appendingPathComponent("Talos", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent("memories.json")
    }

    var memories: [MemoryItem] {
        items.filter { $0.kind == .memory }
    }

    var goals: [MemoryItem] {
        items.filter { $0.kind == .goal }
    }

    var activeGoals: [MemoryItem] {
        items.filter(\.isActiveGoal)
    }

    var contextSection: String? {
        let memories = items
            .filter { $0.enabled && $0.kind == .memory }
            .map(\.text)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        let goals = items
            .filter { $0.enabled && $0.isActiveGoal }
            .map(\.text)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !memories.isEmpty || !goals.isEmpty else { return nil }

        var lines = [
            "Persistent memory (managed by the user and Talos; apply when relevant, do not repeat verbatim):",
        ]
        if !memories.isEmpty {
            lines.append("Memories:")
            lines.append(contentsOf: memories.map { "- \($0)" })
        }
        if !goals.isEmpty {
            lines.append("Active goals:")
            lines.append(contentsOf: goals.map { "- \($0)" })
        }
        var section = lines.joined(separator: "\n")
        if section.count > 8_000 {
            section = String(section.prefix(8_000)) + "\n… memory truncated …"
        }
        return section
    }

    @discardableResult
    func add(kind: MemoryKind, text: String, goalStatus: GoalStatus? = nil) -> MemoryItem? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let item = MemoryItem(
            kind: kind,
            text: trimmed,
            goalStatus: kind == .goal ? (goalStatus ?? .active) : nil
        )
        items.append(item)
        save()
        return item
    }

    func update(_ item: MemoryItem) {
        guard let index = items.firstIndex(where: { $0.id == item.id }) else { return }
        var updated = item
        updated.text = updated.text.trimmingCharacters(in: .whitespacesAndNewlines)
        updated.updatedAt = Date()
        if updated.kind == .memory {
            updated.goalStatus = nil
        } else if updated.goalStatus == nil {
            updated.goalStatus = .active
        }
        items[index] = updated
        save()
    }

    func setEnabled(_ item: MemoryItem, _ enabled: Bool) {
        guard let index = items.firstIndex(where: { $0.id == item.id }) else { return }
        items[index].enabled = enabled
        items[index].updatedAt = Date()
        save()
    }

    func setGoalStatus(_ item: MemoryItem, _ status: GoalStatus) {
        guard let index = items.firstIndex(where: { $0.id == item.id }),
              items[index].kind == .goal else { return }
        items[index].goalStatus = status
        items[index].updatedAt = Date()
        save()
    }

    func remove(_ item: MemoryItem) {
        items.removeAll { $0.id == item.id }
        save()
    }

    func clearCompletedGoals() {
        items.removeAll { $0.kind == .goal && $0.goalStatus == .completed }
        save()
    }

    func reload() {
        load()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([MemoryItem].self, from: data) else {
            items = []
            return
        }
        items = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(items) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
