import Foundation

struct SubagentOutcome {
    let label: String
    let task: String
    let summary: String
    let steps: [String]
    let usage: TokenUsage
    let hitStepLimit: Bool

    var report: String {
        var lines = [
            "Subagent: \(label)",
            "Task: \(task)",
            "Steps: \(steps.count)",
        ]
        if !steps.isEmpty {
            lines.append("Tool calls:")
            lines.append(contentsOf: steps.map { "- \($0)" })
        }
        lines.append("")
        lines.append(hitStepLimit ? "The subagent hit its step limit. Partial report:" : "Report:")
        lines.append(summary.isEmpty ? "No report was produced." : summary)
        return lines.joined(separator: "\n")
    }
}

/// Runs a delegated task in a fresh, read-only context.
///
/// The engine owns the tool loop but not the provider: `complete` streams one
/// assistant reply for a message history, so the same engine works with every
/// provider and can be exercised with a scripted completion.
@MainActor
struct SubagentEngine {
    let label: String
    let task: String
    let maxSteps: Int
    let context: ToolContext
    let projectInstructions: String?
    var instructions: String? = nil
    let complete: @MainActor ([ChatMessage]) async throws -> (String, TokenUsage)

    func run() async throws -> SubagentOutcome {
        var messages: [ChatMessage] = [
            ChatMessage(role: .system, text: systemPrompt),
            ChatMessage(role: .user, text: task),
        ]
        var steps: [String] = []
        var usage = TokenUsage(prompt: 0, completion: 0, estimated: false)
        let runner = ToolRunner(context: context)
        let limit = max(1, maxSteps)

        for _ in 0..<limit {
            let (raw, used) = try await complete(messages)
            usage = TokenUsage(
                prompt: usage.prompt + used.prompt,
                completion: usage.completion + used.completion,
                estimated: usage.estimated || used.estimated
            )

            guard let parsed = ToolParser.extract(from: raw) else {
                return SubagentOutcome(
                    label: label,
                    task: task,
                    summary: raw.trimmingCharacters(in: .whitespacesAndNewlines),
                    steps: steps,
                    usage: usage,
                    hitStepLimit: false
                )
            }

            messages.append(ChatMessage(
                role: .assistant,
                text: parsed.cleaned.isEmpty ? "Calling \(parsed.call.name)." : parsed.cleaned
            ))
            let result = await runner.run(parsed.call)
            steps.append("\(parsed.call.name): \(result.status.rawValue) — \(parsed.call.summary)")
            let clipped = result.output.count > 12_000
                ? String(result.output.prefix(12_000)) + "\n…"
                : result.output
            messages.append(ChatMessage(
                role: .tool,
                text: "Tool result (\(parsed.call.name)):\n\(clipped)",
                toolName: parsed.call.name,
                toolStatus: result.status
            ))
        }

        return SubagentOutcome(
            label: label,
            task: task,
            summary: "Stopped after \(limit) steps without a final answer. Narrow the task or raise max_steps.",
            steps: steps,
            usage: usage,
            hitStepLimit: true
        )
    }

    private var systemPrompt: String {
        var prompt = ToolPrompt.system(context: context)
        if let instructions {
            prompt += "\n\n" + instructions
        } else {
            prompt += "\n\n" + """
            You are a subagent. The parent agent delegated one focused task to you.
            You share the workspace but not the parent's conversation.
            You are read-only: inspect files, search, and use the web when it is enabled. You cannot edit files or run shell commands.
            Do not ask for approval. When you have enough evidence, answer with a concise report: findings, exact file paths, and anything the parent should verify.
            """
        }
        if let projectInstructions {
            prompt += "\n\n" + projectInstructions
        }
        return prompt
    }
}
