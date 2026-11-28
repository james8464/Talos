import Foundation
import TalosToolParser

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
    var depth: Int = 0
    var maxDepth: Int = 2
    var nestedRunner: (@MainActor (ToolCall, Int) async -> ToolResult)? = nil
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

            let parsed = ToolParser.extractAll(from: raw)
            guard !parsed.calls.isEmpty else {
                return SubagentOutcome(
                    label: label,
                    task: task,
                    summary: parsed.cleaned.isEmpty
                        ? raw.trimmingCharacters(in: .whitespacesAndNewlines)
                        : parsed.cleaned,
                    steps: steps,
                    usage: usage,
                    hitStepLimit: false
                )
            }

            let names = parsed.calls.map(\.name).joined(separator: ", ")
            messages.append(ChatMessage(
                role: .assistant,
                text: parsed.cleaned.isEmpty ? "Calling \(names)." : parsed.cleaned
            ))
            for call in parsed.calls.prefix(8) {
                let result: ToolResult
                if call.name == "subagent" {
                    if let nestedRunner, depth < maxDepth {
                        result = await nestedRunner(call, depth + 1)
                    } else {
                        result = .fail("Nested subagents are blocked at the depth limit (depth \(depth), limit \(maxDepth)).")
                    }
                } else {
                    result = await runner.run(call)
                }
                steps.append("\(call.name): \(result.status.rawValue) — \(result.output.prefix(120))")
                let clipped = result.output.count > 12_000
                    ? String(result.output.prefix(12_000)) + "\n…"
                    : result.output
                messages.append(ChatMessage(
                    role: .tool,
                    text: "Tool result (\(call.name)):\n\(clipped)",
                    toolName: call.name,
                    toolStatus: result.status
                ))
            }
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
            if depth < maxDepth {
                prompt += "\nYou may delegate focused research to one more subagent. It is also read-only."
            } else {
                prompt += "\nYou are at the subagent depth limit and cannot spawn another subagent."
            }
        }
        if let projectInstructions {
            prompt += "\n\n" + projectInstructions
        }
        return prompt
    }
}
