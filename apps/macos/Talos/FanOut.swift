import Foundation

struct FanOutTask: Identifiable, Sendable {
    let id = UUID()
    let label: String
    let task: String
}

enum FanOutStatus: String, Sendable {
    case succeeded
    case failed
}

struct FanOutResult: Identifiable, Sendable {
    let id: UUID
    let label: String
    let task: String
    let status: FanOutStatus
    let summary: String
    let steps: [String]
    let usage: TokenUsage
    let duration: TimeInterval
    let error: String?
}

struct FanOutOutcome: Sendable {
    let results: [FanOutResult]
    let concurrency: Int
    let duration: TimeInterval

    var succeeded: Int {
        results.filter { $0.status == .succeeded }.count
    }

    var report: String {
        var lines = [
            "Fan-out: \(results.count) tasks · \(succeeded) succeeded · concurrency \(concurrency) · \(String(format: "%.1f", duration))s",
        ]
        for (index, result) in results.enumerated() {
            lines.append("")
            let timing = String(format: "%.1f", result.duration)
            lines.append("[\(index + 1)] \(result.label) — \(result.status.rawValue) · \(timing)s · \(result.steps.count) step\(result.steps.count == 1 ? "" : "s")")
            if !result.steps.isEmpty {
                lines.append("Tools: " + result.steps.joined(separator: "; "))
            }
            if let error = result.error {
                lines.append("Error: \(error)")
            } else {
                lines.append(result.summary.isEmpty ? "No report." : result.summary)
            }
        }
        let joined = lines.joined(separator: "\n")
        return joined.count > 30_000 ? String(joined.prefix(30_000)) + "\n…" : joined
    }
}

/// Runs several read-only child tasks with bounded concurrency.
///
/// Each task gets its own `SubagentEngine`, so message histories, tool traces,
/// and reports never cross between tasks. Only the provider closure is shared.
@MainActor
struct FanOutEngine {
    let tasks: [FanOutTask]
    let maxConcurrency: Int
    let maxSteps: Int
    let context: ToolContext
    let projectInstructions: String?
    let complete: @MainActor ([ChatMessage]) async throws -> (String, TokenUsage)

    func run() async -> FanOutOutcome {
        let started = Date()
        guard !tasks.isEmpty else {
            return FanOutOutcome(results: [], concurrency: maxConcurrency, duration: 0)
        }
        let limit = min(max(1, maxConcurrency), tasks.count)
        var running: [Int: Task<FanOutResult, Never>] = [:]
        var next = 0

        for index in 0..<limit {
            let task = tasks[index]
            running[index] = Task { @MainActor in
                await self.runTask(task)
            }
        }
        next = limit

        var results: [(index: Int, result: FanOutResult)] = []
        while !running.isEmpty {
            let index = running.keys.min() ?? 0
            guard let job = running.removeValue(forKey: index) else { break }
            results.append((index, await job.value))
            if next < tasks.count {
                let task = tasks[next]
                running[next] = Task { @MainActor in
                    await self.runTask(task)
                }
                next += 1
            }
        }

        return FanOutOutcome(
            results: results.sorted { $0.index < $1.index }.map(\.result),
            concurrency: limit,
            duration: Date().timeIntervalSince(started)
        )
    }

    private func runTask(_ task: FanOutTask) async -> FanOutResult {
        let started = Date()
        do {
            let outcome = try await SubagentEngine(
                label: task.label,
                task: task.task,
                maxSteps: maxSteps,
                context: context,
                projectInstructions: projectInstructions,
                complete: complete
            ).run()
            return FanOutResult(
                id: task.id,
                label: task.label,
                task: task.task,
                status: .succeeded,
                summary: outcome.summary,
                steps: outcome.steps,
                usage: outcome.usage,
                duration: Date().timeIntervalSince(started),
                error: nil
            )
        } catch {
            return FanOutResult(
                id: task.id,
                label: task.label,
                task: task.task,
                status: .failed,
                summary: "",
                steps: [],
                usage: TokenUsage(prompt: 0, completion: 0, estimated: false),
                duration: Date().timeIntervalSince(started),
                error: error.localizedDescription
            )
        }
    }
}
