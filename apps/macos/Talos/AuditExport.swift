import Foundation

enum AuditExport {
    static let schema = "talos.audit.v1"

    static func document(
        sessions: [Session],
        usage: [UsageRecord],
        hooks: [HookLogEntry],
        workspace: String,
        model: String,
        approvalPolicy: String,
        sandbox: String,
        network: Bool,
        managedPolicy: Bool
    ) -> [String: Any] {
        let formatter = ISO8601DateFormatter()
        let sessionRows: [[String: Any]] = sessions
            .sorted { $0.updatedAt > $1.updatedAt }
            .map { session in
                let tokens = usage
                    .filter { $0.sessionID == session.id }
                    .reduce(0) { $0 + $1.total }
                return [
                    "id": session.id.uuidString,
                    "title": session.title,
                    "projectPath": session.projectPath,
                    "createdAt": formatter.string(from: session.createdAt),
                    "updatedAt": formatter.string(from: session.updatedAt),
                    "archived": session.archived,
                    "messageCount": session.messages.count,
                    "forkedFromSessionID": session.forkedFromSessionID?.uuidString ?? NSNull(),
                    "approvedTools": session.approvedTools,
                    "tokens": tokens,
                ]
            }
        let usageRows: [[String: Any]] = usage.map { record in
            [
                "date": formatter.string(from: record.date),
                "provider": record.provider.rawValue,
                "model": record.model,
                "promptTokens": record.promptTokens,
                "completionTokens": record.completionTokens,
                "estimated": record.estimated,
                "tokenizer": record.tokenizer ?? false,
                "sessionID": record.sessionID?.uuidString ?? NSNull(),
            ]
        }
        let hookRows: [[String: Any]] = hooks.map { entry in
            [
                "date": formatter.string(from: entry.date),
                "hook": entry.hookName,
                "event": entry.event.rawValue,
                "status": entry.status,
                "output": String(entry.output.prefix(4_000)),
            ]
        }
        return [
            "schema": schema,
            "generatedAt": formatter.string(from: Date()),
            "app": ["name": "Talos", "version": "1.0"],
            "workspace": workspace,
            "model": model,
            "policy": [
                "approvalPolicy": approvalPolicy,
                "sandbox": sandbox,
                "network": network,
                "managed": managedPolicy,
            ],
            "sessions": sessionRows,
            "usage": usageRows,
            "hooks": hookRows,
        ]
    }

    static func data(_ document: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
    }
}
