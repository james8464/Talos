import Foundation
import Network
import Combine

struct AppServerMessage: Sendable {
    let id: UUID
    let role: String
    let text: String
    let toolName: String?
    let isStreaming: Bool
    let createdAt: Date
    let attachments: [String]
    let parentID: UUID?
    let revisionOf: UUID?
    let retryOf: UUID?
}

struct AppServerSession: Sendable {
    let id: UUID
    let title: String
    let updatedAt: Date
    let archived: Bool
    let messages: [AppServerMessage]
    let forkedFromSessionID: UUID?
    let forkedFromMessageID: UUID?
}

struct AppServerApproval: Sendable {
    let id: UUID
    let sessionID: UUID?
    let toolName: String
    let summary: String
}

@MainActor
protocol AppServerHost: AnyObject {
    func appServerSessions() -> [AppServerSession]
    func appServerActiveSession() -> UUID?
    func appServerPendingApproval() -> AppServerApproval?
    func appServerCreateSession(title: String?) -> UUID?
    func appServerPrompt(sessionID: UUID, text: String) -> Result<UUID, Error>
    func appServerCancel(sessionID: UUID) -> Bool
    func appServerResolveApproval(id: UUID, decision: String) -> Bool
    func appServerRetryMessage(sessionID: UUID, messageID: UUID) async -> Result<UUID, Error>
    func appServerEditMessage(sessionID: UUID, messageID: UUID, text: String) async -> Result<UUID, Error>
    func appServerForkMessage(sessionID: UUID, messageID: UUID) -> Result<UUID, Error>
    func appServerInfo() -> [String: Any]
}

enum AppServerError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

@MainActor
final class AppServer: ObservableObject {
    private final class SSEClient {
        let id = UUID()
        let connection: NWConnection
        let sessionID: UUID?
        var lastMessageState: [UUID: MessageState] = [:]
        var lastStreamingIDs: Set<UUID> = []

        init(connection: NWConnection, sessionID: UUID?) {
            self.connection = connection
            self.sessionID = sessionID
        }
    }

    private struct MessageState: Equatable {
        let role: String
        let text: String
        let isStreaming: Bool
    }

    weak var host: AppServerHost?

    @Published private(set) var running = false
    @Published private(set) var status = "Off"
    @Published private(set) var endpoint: String?
    private var listener: NWListener?
    private var port: UInt16 = 8790
    private var token = ""
    private var clients: [UUID: SSEClient] = [:]
    private var tickTask: Task<Void, Never>?
    private var lastActiveSession: UUID?
    private var lastApprovalID: UUID?
    private let queue = DispatchQueue(label: "app.talos.appserver")

    func start(port: Int, token: String, bindLAN: Bool) {
        stop()
        guard !token.isEmpty else {
            status = "Token is empty"
            return
        }
        self.port = UInt16(min(max(port, 1024), 65_535))
        self.token = token
        do {
            let parameters = NWParameters.tcp
            parameters.allowLocalEndpointReuse = true
            let endpointPort = NWEndpoint.Port(rawValue: self.port) ?? 8790
            if bindLAN {
                listener = try NWListener(using: parameters, on: endpointPort)
            } else {
                parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: endpointPort)
                listener = try NWListener(using: parameters)
            }
            listener?.stateUpdateHandler = { [weak self] state in
                Task { @MainActor in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        self.running = true
                        self.endpoint = "http://127.0.0.1:\(self.port)"
                        self.status = bindLAN
                            ? "Listening on all interfaces, port \(self.port)"
                            : "Listening at \(self.endpoint ?? "")"
                    case .failed(let error):
                        self.running = false
                        self.status = "Failed: \(error.localizedDescription)"
                        self.endpoint = nil
                    case .cancelled:
                        self.running = false
                        self.status = "Off"
                        self.endpoint = nil
                    default:
                        break
                    }
                }
            }
            listener?.newConnectionHandler = { [weak self] connection in
                Task { @MainActor in
                    self?.accept(connection)
                }
            }
            listener?.start(queue: queue)
            status = "Starting…"
            lastActiveSession = host?.appServerActiveSession()
            lastApprovalID = host?.appServerPendingApproval()?.id
        } catch {
            status = "Failed: \(error.localizedDescription)"
            listener = nil
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
        running = false
        status = "Off"
        endpoint = nil
        for client in clients.values {
            client.connection.cancel()
        }
        clients.removeAll()
        tickTask?.cancel()
        tickTask = nil
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, _, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            guard let headerRange = buffer.range(of: Data("\r\n\r\n".utf8)) else {
                if buffer.count > 2_000_000 || error != nil {
                    self.respond(connection, status: 400, body: ["error": "Malformed request"])
                } else {
                    self.receive(connection, buffer: buffer)
                }
                return
            }
            let headerText = String(decoding: buffer[..<headerRange.lowerBound], as: UTF8.self)
            var lines = headerText.components(separatedBy: "\r\n")
            let requestLine = lines.first?.components(separatedBy: " ") ?? []
            lines.removeFirst()
            var headers: [String: String] = [:]
            for line in lines {
                guard let colon = line.firstIndex(of: ":") else { continue }
                headers[line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()] =
                    line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            }
            let length = Int(headers["content-length"] ?? "0") ?? 0
            let bodyStart = headerRange.upperBound
            let available = buffer.count - bodyStart
            if available < length && buffer.count < 2_000_000 {
                self.receive(connection, buffer: buffer)
                return
            }
            let body = Data(buffer[bodyStart..<min(bodyStart + length, buffer.count)])
            let method = requestLine.first ?? "GET"
            let target = requestLine.count > 1 ? requestLine[1] : "/"
            Task { @MainActor in
                await self.route(method: method, target: target, headers: headers, body: body, connection: connection)
            }
        }
    }

    private func route(
        method: String,
        target: String,
        headers: [String: String],
        body: Data,
        connection: NWConnection
    ) async {
        let components = URLComponents(string: "http://localhost" + target)
        let path = components?.path ?? target
        let query = Dictionary(
            uniqueKeysWithValues: (components?.queryItems ?? []).map { ($0.name, $0.value ?? "") }
        )
        let authorization = headers["authorization"] ?? ""
        let authorized = authorization == "Bearer \(token)" || query["token"] == token
        guard authorized else {
            respond(connection, status: 401, body: ["error": "Missing or invalid bearer token"])
            return
        }

        if path == "/health" {
            respond(connection, status: 200, body: [
                "ok": true,
                "server": "talos-app-server",
                "endpoint": endpoint ?? "",
            ])
            return
        }

        if path == "/events", method == "GET" {
            let sessionID = query["sessionId"].flatMap(UUID.init(uuidString:))
            openSSE(connection, sessionID: sessionID)
            return
        }

        guard path == "/rpc", method == "POST" else {
            respond(connection, status: 404, body: ["error": "Unknown path"])
            return
        }
        guard let request = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let rpcMethod = request["method"] as? String else {
            respond(connection, status: 200, body: rpcError(id: nil, code: -32700, message: "Parse error"))
            return
        }
        let id = request["id"]
        let params = request["params"] as? [String: Any] ?? [:]
        let result = await handleRPC(method: rpcMethod, params: params)
        let response: [String: Any]
        if let error = result.error {
            response = rpcError(id: id, code: -32000, message: error)
        } else {
            response = ["jsonrpc": "2.0", "id": id ?? NSNull(), "result": result.value ?? [:]]
        }
        respond(connection, status: 200, body: response)
    }

    private func handleRPC(
        method: String,
        params: [String: Any]
    ) async -> (value: Any?, error: String?) {
        guard let host else { return (nil, "The Talos host is not attached.") }
        switch method {
        case "initialize", "ping":
            return (host.appServerInfo(), nil)
        case "session.create":
            guard let id = host.appServerCreateSession(title: params["title"] as? String) else {
                return (nil, "Could not create a session.")
            }
            return (["sessionId": id.uuidString], nil)
        case "session.list":
            return (host.appServerSessions().map(sessionSummary), nil)
        case "session.get":
            guard let raw = params["sessionId"] as? String,
                  let id = UUID(uuidString: raw),
                  let session = host.appServerSessions().first(where: { $0.id == id }) else {
                return (nil, "Session not found.")
            }
            return (sessionDetail(session), nil)
        case "session.prompt":
            guard let raw = params["sessionId"] as? String,
                  let id = UUID(uuidString: raw) else {
                return (nil, "sessionId is required.")
            }
            guard let text = params["text"] as? String,
                  !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return (nil, "text is required.")
            }
            switch host.appServerPrompt(sessionID: id, text: text) {
            case .success(let runID):
                return (["sessionId": id.uuidString, "runId": runID.uuidString], nil)
            case .failure(let error):
                return (nil, error.localizedDescription)
            }
        case "session.cancel":
            guard let raw = params["sessionId"] as? String,
                  let id = UUID(uuidString: raw) else {
                return (nil, "sessionId is required.")
            }
            return (["cancelled": host.appServerCancel(sessionID: id)], nil)
        case "approval.pending":
            guard let approval = host.appServerPendingApproval() else { return (NSNull(), nil) }
            return ([
                "approvalId": approval.id.uuidString,
                "sessionId": approval.sessionID?.uuidString ?? NSNull(),
                "toolName": approval.toolName,
                "summary": approval.summary,
            ], nil)
        case "message.retry":
            guard let sessionID = params["sessionId"] as? String,
                  let sessionUUID = UUID(uuidString: sessionID),
                  let messageID = params["messageId"] as? String,
                  let messageUUID = UUID(uuidString: messageID) else {
                return (nil, "sessionId and messageId are required.")
            }
            switch await host.appServerRetryMessage(sessionID: sessionUUID, messageID: messageUUID) {
            case .success(let runID):
                return (["sessionId": sessionID, "runId": runID.uuidString], nil)
            case .failure(let error):
                return (nil, error.localizedDescription)
            }
        case "message.edit":
            guard let sessionID = params["sessionId"] as? String,
                  let sessionUUID = UUID(uuidString: sessionID),
                  let messageID = params["messageId"] as? String,
                  let messageUUID = UUID(uuidString: messageID),
                  let text = params["text"] as? String else {
                return (nil, "sessionId, messageId, and text are required.")
            }
            switch await host.appServerEditMessage(sessionID: sessionUUID, messageID: messageUUID, text: text) {
            case .success(let runID):
                return (["sessionId": sessionID, "runId": runID.uuidString], nil)
            case .failure(let error):
                return (nil, error.localizedDescription)
            }
        case "message.fork":
            guard let sessionID = params["sessionId"] as? String,
                  let sessionUUID = UUID(uuidString: sessionID),
                  let messageID = params["messageId"] as? String,
                  let messageUUID = UUID(uuidString: messageID) else {
                return (nil, "sessionId and messageId are required.")
            }
            switch host.appServerForkMessage(sessionID: sessionUUID, messageID: messageUUID) {
            case .success(let newID):
                return (["sessionId": newID.uuidString], nil)
            case .failure(let error):
                return (nil, error.localizedDescription)
            }
        case "approval.resolve":
            guard let raw = params["approvalId"] as? String,
                  let id = UUID(uuidString: raw),
                  let decision = params["decision"] as? String else {
                return (nil, "approvalId and decision are required.")
            }
            let resolved = host.appServerResolveApproval(id: id, decision: decision)
            return (["resolved": resolved], resolved ? nil : "Approval not found or decision invalid.")
        default:
            return (nil, "Unknown method \(method).")
        }
    }

    private func sessionSummary(_ session: AppServerSession) -> [String: Any] {
        [
            "id": session.id.uuidString,
            "title": session.title,
            "updatedAt": ISO8601DateFormatter().string(from: session.updatedAt),
            "archived": session.archived,
            "messageCount": session.messages.count,
        ]
    }

    private func sessionDetail(_ session: AppServerSession) -> [String: Any] {
        var detail = sessionSummary(session)
        detail["forkedFromSessionID"] = session.forkedFromSessionID?.uuidString ?? NSNull()
        detail["forkedFromMessageID"] = session.forkedFromMessageID?.uuidString ?? NSNull()
        detail["messages"] = session.messages.map { message in
            [
                "id": message.id.uuidString,
                "role": message.role,
                "text": message.text,
                "toolName": message.toolName ?? NSNull(),
                "isStreaming": message.isStreaming,
                "createdAt": ISO8601DateFormatter().string(from: message.createdAt),
                "attachments": message.attachments,
                "parentID": message.parentID?.uuidString ?? NSNull(),
                "revisionOf": message.revisionOf?.uuidString ?? NSNull(),
                "retryOf": message.retryOf?.uuidString ?? NSNull(),
            ]
        }
        return detail
    }

    private func openSSE(_ connection: NWConnection, sessionID: UUID?) {
        let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n"
        connection.send(content: Data(header.utf8), completion: .contentProcessed { _ in })
        let client = SSEClient(connection: connection, sessionID: sessionID)
        clients[client.id] = client
        emit(to: client, event: "ready", payload: [
            "server": "talos-app-server",
            "sessionId": sessionID?.uuidString ?? NSNull(),
        ])
        if let sessionID,
           let session = host?.appServerSessions().first(where: { $0.id == sessionID }) {
            client.lastMessageState = Dictionary(
                uniqueKeysWithValues: session.messages.map {
                    ($0.id, MessageState(role: $0.role, text: $0.text, isStreaming: $0.isStreaming))
                }
            )
            client.lastStreamingIDs = Set(session.messages.filter(\.isStreaming).map(\.id))
            emit(to: client, event: "session.snapshot", payload: sessionDetail(session))
        }
        connection.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                switch state {
                case .failed, .cancelled:
                    self?.clients.removeValue(forKey: client.id)
                    self?.stopTickerIfIdle()
                default:
                    break
                }
            }
        }
        startTickerIfNeeded()
    }

    private func startTickerIfNeeded() {
        guard tickTask == nil, !clients.isEmpty else { return }
        tickTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                self?.tick()
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    private func stopTickerIfIdle() {
        guard clients.isEmpty else { return }
        tickTask?.cancel()
        tickTask = nil
    }

    private func tick() {
        guard let host else { return }
        let sessions = host.appServerSessions()
        let active = host.appServerActiveSession()

        if active != lastActiveSession {
            if let active,
               let session = sessions.first(where: { $0.id == active }) {
                emit(event: "run.started", payload: ["sessionId": active.uuidString, "title": session.title])
            } else if let previous = lastActiveSession {
                emit(event: "run.finished", payload: ["sessionId": previous.uuidString])
            }
            lastActiveSession = active
        }

        let approval = host.appServerPendingApproval()
        if approval?.id != lastApprovalID {
            if let approval {
                emit(event: "approval.requested", payload: [
                    "approvalId": approval.id.uuidString,
                    "sessionId": approval.sessionID?.uuidString ?? NSNull(),
                    "toolName": approval.toolName,
                    "summary": approval.summary,
                ])
            } else if let previous = lastApprovalID {
                emit(event: "approval.resolved", payload: ["approvalId": previous.uuidString])
            }
            lastApprovalID = approval?.id
        }

        for client in clients.values {
            let relevant = sessions.filter { client.sessionID == nil || $0.id == client.sessionID }
            for session in relevant {
                for message in session.messages {
                    let state = MessageState(role: message.role, text: message.text, isStreaming: message.isStreaming)
                    if let old = client.lastMessageState[message.id] {
                        if message.text != old.text {
                            if message.text.hasPrefix(old.text) && !old.text.isEmpty {
                                emit(to: client, event: "message.delta", payload: [
                                    "sessionId": session.id.uuidString,
                                    "messageId": message.id.uuidString,
                                    "delta": String(message.text.dropFirst(old.text.count)),
                                ])
                            } else if old.text.isEmpty {
                                emit(to: client, event: "message.delta", payload: [
                                    "sessionId": session.id.uuidString,
                                    "messageId": message.id.uuidString,
                                    "delta": message.text,
                                ])
                            } else {
                                emit(to: client, event: "message.updated", payload: [
                                    "sessionId": session.id.uuidString,
                                    "messageId": message.id.uuidString,
                                    "text": message.text,
                                ])
                            }
                        }
                        if old.isStreaming && !message.isStreaming {
                            emit(to: client, event: "message.completed", payload: [
                                "sessionId": session.id.uuidString,
                                "messageId": message.id.uuidString,
                                "text": message.text,
                            ])
                        }
                    } else {
                        emit(to: client, event: message.role == "tool" ? "tool.result" : "message.created", payload: [
                            "sessionId": session.id.uuidString,
                            "messageId": message.id.uuidString,
                            "role": message.role,
                            "text": message.text,
                            "toolName": message.toolName ?? NSNull(),
                        ])
                    }
                    client.lastMessageState[message.id] = state
                    if message.isStreaming {
                        client.lastStreamingIDs.insert(message.id)
                    } else {
                        client.lastStreamingIDs.remove(message.id)
                    }
                }
            }
        }
    }

    private func emit(to client: SSEClient, event: String, payload: [String: Any]) {
        let notification: [String: Any] = [
            "jsonrpc": "2.0",
            "method": event,
            "params": payload,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: notification),
              let json = String(data: data, encoding: .utf8) else { return }
        let message = "event: \(event)\ndata: \(json)\n\n"
        client.connection.send(content: Data(message.utf8), completion: .contentProcessed { _ in })
    }

    private func emit(event: String, payload: [String: Any]) {
        for client in clients.values {
            if let sessionID = client.sessionID,
               let eventSession = payload["sessionId"] as? String,
               eventSession != sessionID.uuidString {
                continue
            }
            emit(to: client, event: event, payload: payload)
        }
    }

    private func rpcError(id: Any?, code: Int, message: String) -> [String: Any] {
        [
            "jsonrpc": "2.0",
            "id": id ?? NSNull(),
            "error": ["code": code, "message": message],
        ]
    }

    private func respond(_ connection: NWConnection, status: Int, body: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])) ?? Data("{}".utf8)
        let reason: String
        switch status {
        case 200: reason = "OK"
        case 400: reason = "Bad Request"
        case 401: reason = "Unauthorized"
        case 404: reason = "Not Found"
        default: reason = "Error"
        }
        let header = "HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\nContent-Length: \(data.count)\r\nConnection: close\r\n\r\n"
        var message = Data(header.utf8)
        message.append(data)
        connection.send(content: message, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }
}
