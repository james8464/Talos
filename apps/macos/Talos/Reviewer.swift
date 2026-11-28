import Foundation
import TalosToolParser

enum ReviewVerdict: String, Codable, Sendable {
    case allow
    case caution
    case deny

    var title: String {
        switch self {
        case .allow: return "Looks safe"
        case .caution: return "Use caution"
        case .deny: return "High risk"
        }
    }

    var symbol: String {
        switch self {
        case .allow: return "checkmark.shield"
        case .caution: return "exclamationmark.shield"
        case .deny: return "xmark.shield"
        }
    }
}

struct ApprovalReview: Equatable, Sendable {
    var verdict: ReviewVerdict
    var summary: String
    var reasons: [String]
    var risk: String
    var model: String
    var failed: Bool = false
}

@MainActor
struct ApprovalReviewer {
    let model: String
    let maxSteps: Int
    let context: ToolContext
    let projectInstructions: String?
    let complete: @MainActor ([ChatMessage]) async throws -> (String, TokenUsage)

    func review(call: ToolCall, diff: String) async throws -> ApprovalReview {
        let engine = SubagentEngine(
            label: "approval-review",
            task: task(call: call, diff: diff),
            maxSteps: maxSteps,
            context: context,
            projectInstructions: projectInstructions,
            complete: complete
        )
        let outcome = try await engine.run()
        return parse(outcome.summary, model: model)
    }

    private func task(call: ToolCall, diff: String) -> String {
        let arguments = (try? JSONSerialization.data(
            withJSONObject: call.arguments.mapValues(\.anyValue),
            options: [.prettyPrinted, .sortedKeys]
        )).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let clippedArguments = clip(arguments, limit: 12_000)
        let changes = diff.hasPrefix("No diff")
            ? "No working tree changes."
            : clip(diff, limit: 20_000)
        return """
        Review this high-impact tool call before the user approves it.

        Tool: \(call.name)
        Arguments:
        \(clippedArguments)

        Working tree diff:
        \(changes)

        Check for destructive or irreversible operations, data loss, secret or credential exposure,
        network exfiltration, writes outside the workspace, privilege changes, force pushes,
        history rewrites, and commands that do not match the user's request.
        Inspect files with read_file or search_files if that would make the review more accurate.

        Reply with only a JSON object and no prose:
        {"verdict":"allow|caution|deny","summary":"one sentence","reasons":["concrete reason","..."],"risk":"low|medium|high"}
        """
    }

    private func clip(_ text: String, limit: Int) -> String {
        text.count > limit ? String(text.prefix(limit)) + "\n…" : text
    }

    private func parse(_ raw: String, model: String) -> ApprovalReview {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let start = trimmed.firstIndex(of: "{"),
              let end = trimmed.lastIndex(of: "}"),
              start < end,
              let data = String(trimmed[start...end]).data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ApprovalReview(
                verdict: .caution,
                summary: "The reviewer did not return a readable recommendation.",
                reasons: [clip(trimmed, limit: 500)],
                risk: "unknown",
                model: model,
                failed: true
            )
        }
        let rawVerdict = ((object["verdict"] as? String)
            ?? (object["recommendation"] as? String)
            ?? (object["decision"] as? String)
            ?? "").lowercased()
        let verdict: ReviewVerdict
        if ["allow", "approve", "approved", "safe", "pass"].contains(rawVerdict) {
            verdict = .allow
        } else if ["deny", "block", "reject", "unsafe", "danger"].contains(rawVerdict) {
            verdict = .deny
        } else {
            verdict = .caution
        }
        let reasons = (object["reasons"] as? [String])
            ?? (object["concerns"] as? [String])
            ?? [(object["reason"] as? String)].compactMap { $0 }
        return ApprovalReview(
            verdict: verdict,
            summary: (object["summary"] as? String)
                ?? (object["reason"] as? String)
                ?? verdict.title,
            reasons: reasons.filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty },
            risk: (object["risk"] as? String) ?? (object["severity"] as? String) ?? "unknown",
            model: model
        )
    }
}
