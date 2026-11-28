import Foundation

struct MobileSessionSummary: Identifiable, Sendable {
    let id: String
    let title: String
    let updatedAt: String
    let messageCount: Int
}

struct MobileMessage: Identifiable, Sendable {
    let id: String
    let role: String
    let text: String
    let isStreaming: Bool
}

struct MobileApproval: Sendable {
    let approvalId: String
    let sessionID: String?
    let toolName: String
    let summary: String
}

struct MobileDaemonState: Sendable {
    let model: String
    let sessions: [MobileSessionSummary]
    let approval: MobileApproval?
}

struct MobileSessionDetail: Sendable {
    let title: String
    let messages: [MobileMessage]
    let forkedFromSessionID: String?
}

enum RemoteClientError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

struct RemoteClient: Sendable {
    let baseURL: URL
    let token: String

    init?(server: String, token: String) {
        var value = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !token.isEmpty else { return nil }
        if !value.hasPrefix("http://") && !value.hasPrefix("https://") {
            value = "http://" + value
        }
        while value.hasSuffix("/") { value.removeLast() }
        guard let url = URL(string: value) else { return nil }
        self.baseURL = url
        self.token = token
    }

    func state() async throws -> MobileDaemonState {
        let object = try await get(["/api/state"])
        let info = object["info"] as? [String: Any] ?? [:]
        let rows = object["sessions"] as? [[String: Any]] ?? []
        let approvalObject = object["approval"] as? [String: Any]
        return MobileDaemonState(
            model: info["model"] as? String ?? "Talos",
            sessions: rows.compactMap { row in
                guard let id = row["id"] as? String else { return nil }
                return MobileSessionSummary(
                    id: id,
                    title: row["title"] as? String ?? "Task",
                    updatedAt: row["updatedAt"] as? String ?? "",
                    messageCount: row["messageCount"] as? Int ?? 0
                )
            },
            approval: approvalObject.flatMap(Self.approval)
        )
    }

    func createSession(title: String) async throws -> String {
        let object = try await post(["/api/session"], body: ["title": title])
        guard let result = object["result"] as? [String: Any],
              let id = result["sessionId"] as? String else {
            throw RemoteClientError.message("Talos did not return a session.")
        }
        return id
    }

    func session(_ id: String) async throws -> MobileSessionDetail {
        let object = try await get(["/api/session"], query: ["sessionId": id])
        let session = object["session"] as? [String: Any] ?? [:]
        let messages = (session["messages"] as? [[String: Any]] ?? []).compactMap { row -> MobileMessage? in
            guard let messageID = row["id"] as? String,
                  let role = row["role"] as? String else { return nil }
            return MobileMessage(
                id: messageID,
                role: role,
                text: row["text"] as? String ?? "",
                isStreaming: row["isStreaming"] as? Bool ?? false
            )
        }
        return MobileSessionDetail(
            title: session["title"] as? String ?? "Task",
            messages: messages,
            forkedFromSessionID: session["forkedFromSessionID"] as? String
        )
    }

    func prompt(sessionID: String, text: String) async throws {
        _ = try await post(["/api/prompt"], body: ["sessionId": sessionID, "text": text])
    }

    func stop(sessionID: String) async throws -> Bool {
        let object = try await post(["/api/stop"], body: ["sessionId": sessionID])
        return (object["result"] as? [String: Any])?["cancelled"] as? Bool ?? false
    }

    func resolveApproval(id: String, decision: String) async throws -> Bool {
        let object = try await post(["/api/approval"], body: ["approvalId": id, "decision": decision])
        return (object["result"] as? [String: Any])?["resolved"] as? Bool ?? false
    }

    private func get(_ path: [String], query: [String: String] = [:]) async throws -> [String: Any] {
        try await request(path, method: "GET", query: query, body: nil)
    }

    private func post(_ path: [String], body: [String: Any]) async throws -> [String: Any] {
        try await request(path, method: "POST", query: [:], body: body)
    }

    private func request(
        _ path: [String],
        method: String,
        query: [String: String],
        body: [String: Any]?
    ) async throws -> [String: Any] {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        components?.path = path.joined(separator: "/")
        if !query.isEmpty {
            components?.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components?.url else {
            throw RemoteClientError.message("The daemon URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw RemoteClientError.message("No response from the Talos daemon.")
        }
        let object = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        guard http.statusCode == 200, object["ok"] as? Bool == true else {
            throw RemoteClientError.message(
                object["error"] as? String ?? "Talos daemon answered \(http.statusCode)."
            )
        }
        return object
    }

    private static func approval(_ object: [String: Any]) -> MobileApproval? {
        guard let id = object["approvalId"] as? String, !id.isEmpty else { return nil }
        return MobileApproval(
            approvalId: id,
            sessionID: object["sessionId"] as? String,
            toolName: object["toolName"] as? String ?? "tool",
            summary: object["summary"] as? String ?? ""
        )
    }
}
