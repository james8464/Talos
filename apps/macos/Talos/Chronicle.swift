import Foundation

enum ChronicleEventKind: String, Codable, CaseIterable, Sendable {
    case prompt
    case runCompleted
    case runFailed
    case toolFailed
    case compaction
    case filesChanged

    var title: String {
        switch self {
        case .prompt: return "Prompt"
        case .runCompleted: return "Run completed"
        case .runFailed: return "Run failed"
        case .toolFailed: return "Tool failed"
        case .compaction: return "Compaction"
        case .filesChanged: return "Files changed"
        }
    }

    var symbol: String {
        switch self {
        case .prompt: return "text.bubble"
        case .runCompleted: return "checkmark.circle"
        case .runFailed: return "xmark.octagon"
        case .toolFailed: return "wrench.and.screwdriver"
        case .compaction: return "arrow.down.right.and.arrow.up.left"
        case .filesChanged: return "doc.badge.clock"
        }
    }
}

struct ChronicleEvent: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var date = Date()
    var kind: ChronicleEventKind
    var summary: String
    var sessionID: UUID?
}

enum ChronicleAction: String, Codable, CaseIterable, Sendable {
    case openMemory
    case reviewChanges
    case compact
    case retry
    case draft

    var title: String {
        switch self {
        case .openMemory: return "Open Memory"
        case .reviewChanges: return "Review Changes"
        case .compact: return "Compact"
        case .retry: return "Retry"
        case .draft: return "Use Prompt"
        }
    }
}

struct AmbientSuggestion: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var title: String
    var detail: String
    var action: ChronicleAction
    var prompt: String? = nil
    var createdAt = Date()
    var dismissed = false
    var actedOn = false
}

@MainActor
final class ChronicleStore: ObservableObject {
    @Published private(set) var events: [ChronicleEvent] = []
    @Published private(set) var suggestions: [AmbientSuggestion] = []

    private let baseDirectory: URL
    private let historyURL: URL
    private let suggestionsURL: URL
    private let maxEvents = 500

    init(baseDirectory: URL = ChronicleStore.defaultBaseDirectory()) {
        self.baseDirectory = baseDirectory
        self.historyURL = baseDirectory.appendingPathComponent("chronicle-events.json")
        self.suggestionsURL = baseDirectory.appendingPathComponent("chronicle-suggestions.json")
        load()
    }

    nonisolated static func defaultBaseDirectory() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = support.appendingPathComponent("Talos", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    var activeSuggestions: [AmbientSuggestion] {
        suggestions
            .filter { !$0.dismissed && !$0.actedOn }
            .sorted { $0.createdAt > $1.createdAt }
    }

    func record(
        _ kind: ChronicleEventKind,
        summary: String,
        sessionID: UUID? = nil,
        generateSuggestions: Bool
    ) {
        let trimmed = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        events.insert(ChronicleEvent(
            kind: kind,
            summary: String(trimmed.prefix(240)),
            sessionID: sessionID
        ), at: 0)
        if events.count > maxEvents {
            events.removeLast(events.count - maxEvents)
        }
        saveHistory()
        if generateSuggestions {
            generate()
        }
    }

    func generateSuggestions() {
        generate()
    }

    func dismiss(_ suggestion: AmbientSuggestion) {
        guard let index = suggestions.firstIndex(where: { $0.id == suggestion.id }) else { return }
        suggestions[index].dismissed = true
        saveSuggestions()
    }

    func markActedOn(_ suggestion: AmbientSuggestion) {
        guard let index = suggestions.firstIndex(where: { $0.id == suggestion.id }) else { return }
        suggestions[index].actedOn = true
        saveSuggestions()
    }

    func clearHistory() {
        events = []
        saveHistory()
    }

    func clearSuggestions() {
        suggestions = []
        saveSuggestions()
    }

    private func generate(now: Date = Date()) {
        let windowStart = now.addingTimeInterval(-7_200)
        let recent = events.filter { $0.date >= windowStart }
        let failures = recent.filter { $0.kind == .runFailed || $0.kind == .toolFailed }
        if failures.count >= 2 {
            add(
                title: "Review recent failures",
                detail: "\(failures.count) failures were recorded in the last two hours. Inspect the errors or retry the last turn.",
                action: .retry,
                now: now
            )
        }
        let completed = recent.filter { $0.kind == .runCompleted }
        if completed.count >= 2 {
            add(
                title: "Save what matters",
                detail: "Several tasks completed. Save a durable memory or goal so the next task starts with context.",
                action: .openMemory,
                now: now
            )
        }
        if recent.contains(where: { $0.kind == .filesChanged }) {
            add(
                title: "Review working tree changes",
                detail: "Files changed recently. Review the diff before the next task.",
                action: .reviewChanges,
                now: now
            )
        }
    }

    private func add(
        title: String,
        detail: String,
        action: ChronicleAction,
        prompt: String? = nil,
        now: Date
    ) {
        guard activeSuggestions.count < 3 else { return }
        guard !suggestions.contains(where: {
            $0.title == title && now.timeIntervalSince($0.createdAt) < 1_800
        }) else { return }
        suggestions.insert(AmbientSuggestion(
            title: title,
            detail: detail,
            action: action,
            prompt: prompt,
            createdAt: now
        ), at: 0)
        saveSuggestions()
    }

    private func load() {
        if let data = try? Data(contentsOf: historyURL),
           let decoded = try? JSONDecoder().decode([ChronicleEvent].self, from: data) {
            events = decoded
        }
        if let data = try? Data(contentsOf: suggestionsURL),
           let decoded = try? JSONDecoder().decode([AmbientSuggestion].self, from: data) {
            suggestions = decoded
        }
    }

    private func saveHistory() {
        guard let data = try? JSONEncoder().encode(events) else { return }
        try? data.write(to: historyURL, options: .atomic)
    }

    private func saveSuggestions() {
        guard let data = try? JSONEncoder().encode(suggestions) else { return }
        try? data.write(to: suggestionsURL, options: .atomic)
    }
}
