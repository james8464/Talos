import Foundation

struct UsageRecord: Codable, Identifiable, Equatable {
    var id = UUID()
    var date = Date()
    var provider: Provider
    var model: String
    var promptTokens: Int
    var completionTokens: Int
    var estimated: Bool
    var tokenizer: Bool? = nil
    var sessionID: UUID?

    var total: Int { promptTokens + completionTokens }
}

@MainActor
final class UsageStore: ObservableObject {
    @Published private(set) var records: [UsageRecord] = []

    private var directory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos", isDirectory: true)
    }

    private var fileURL: URL {
        directory.appendingPathComponent("usage.json")
    }

    init() {
        load()
        prune()
    }

    func record(_ usage: TokenUsage, provider: Provider, model: String, sessionID: UUID?) {
        records.append(UsageRecord(
            provider: provider,
            model: model,
            promptTokens: usage.prompt,
            completionTokens: usage.completion,
            estimated: usage.estimated,
            tokenizer: usage.tokenizer,
            sessionID: sessionID
        ))
        prune()
        save()
    }

    func tokens(for sessionID: UUID) -> TokenUsage {
        records
            .filter { $0.sessionID == sessionID }
            .reduce(TokenUsage(prompt: 0, completion: 0, estimated: false)) { total, record in
                TokenUsage(
                    prompt: total.prompt + record.promptTokens,
                    completion: total.completion + record.completionTokens,
                    estimated: total.estimated || record.estimated,
                    tokenizer: (total.tokenizer == true || record.tokenizer == true) ? true : nil
                )
            }
    }

    func dailyTotals(days: Int) -> [Date: Int] {
        let calendar = Calendar.current
        let start = calendar.startOfDay(for: Date()).addingTimeInterval(-Double(days - 1) * 86_400)
        var totals: [Date: Int] = [:]
        for record in records where record.date >= start {
            let day = calendar.startOfDay(for: record.date)
            totals[day, default: 0] += record.total
        }
        return totals
    }

    var totalTokens: Int {
        records.reduce(0) { $0 + $1.total }
    }

    var promptTokens: Int {
        records.reduce(0) { $0 + $1.promptTokens }
    }

    var completionTokens: Int {
        records.reduce(0) { $0 + $1.completionTokens }
    }

    var hasEstimates: Bool {
        records.contains { $0.estimated }
    }

    var modelTotals: [(model: String, tokens: Int)] {
        Dictionary(grouping: records, by: { "\($0.provider.title) · \($0.model)" })
            .map { (model: $0.key, tokens: $0.value.reduce(0) { $0 + $1.total }) }
            .sorted { $0.tokens > $1.tokens }
    }

    func clear() {
        records = []
        save()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([UsageRecord].self, from: data) else {
            return
        }
        records = decoded
    }

    private func prune() {
        let cutoff = Date().addingTimeInterval(-365 * 86_400)
        records.removeAll { $0.date < cutoff }
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(records) else { return }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            // Usage history is best-effort; live counters still work for this session.
        }
    }
}
