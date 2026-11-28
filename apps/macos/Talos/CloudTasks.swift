import Foundation
import Network

enum CloudTaskStatus: String, Codable, Sendable {
    case queued
    case running
    case succeeded
    case failed
    case cancelled

    var isTerminal: Bool {
        self == .succeeded || self == .failed || self == .cancelled
    }
}

struct CloudTaskLog: Codable, Equatable, Sendable {
    var date = Date()
    var text: String
}

struct CloudTask: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var title: String
    var prompt: String
    var workspace: String
    var provider: String?
    var model: String?
    var status: CloudTaskStatus = .queued
    var createdAt = Date()
    var startedAt: Date?
    var completedAt: Date?
    var worker: String?
    var result: String?
    var error: String?
    var worktree: String?
    var branch: String?
    var logs: [CloudTaskLog] = []
}

struct CloudTaskExecution: Sendable {
    var result: String
    var worktree: String?
    var branch: String?
}

actor CloudTaskQueue {
    private let fileURL: URL?
    private var tasks: [CloudTask] = []

    init(fileURL: URL? = CloudTaskQueue.defaultFileURL()) {
        self.fileURL = fileURL
        guard let fileURL,
              let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([CloudTask].self, from: data) else {
            return
        }
        tasks = decoded
    }

    static func defaultFileURL() -> URL? {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        guard let support else { return nil }
        let folder = support.appendingPathComponent("Talos", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent("cloud-tasks.json")
    }

    func enqueue(
        prompt: String,
        workspace: String,
        title: String? = nil,
        provider: String? = nil,
        model: String? = nil
    ) -> CloudTask {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let task = CloudTask(
            title: title?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
                ? title!
                : String(trimmed.prefix(60)),
            prompt: trimmed,
            workspace: workspace,
            provider: provider,
            model: model
        )
        tasks.append(task)
        save()
        return task
    }

    func list() -> [CloudTask] {
        tasks.sorted { $0.createdAt > $1.createdAt }
    }

    func get(_ id: UUID) -> CloudTask? {
        tasks.first { $0.id == id }
    }

    func claim(worker: String) -> CloudTask? {
        guard let index = tasks
            .enumerated()
            .filter({ $0.element.status == .queued })
            .min(by: { $0.element.createdAt < $1.element.createdAt })?
            .offset else {
            return nil
        }
        tasks[index].status = .running
        tasks[index].worker = worker
        tasks[index].startedAt = Date()
        tasks[index].logs.append(CloudTaskLog(text: "Claimed by \(worker)."))
        save()
        return tasks[index]
    }

    func append(_ id: UUID, text: String) -> Bool {
        guard let index = tasks.firstIndex(where: { $0.id == id }),
              tasks[index].status == .running else { return false }
        tasks[index].logs.append(CloudTaskLog(text: String(text.prefix(4_000))))
        if tasks[index].logs.count > 120 {
            tasks[index].logs.removeFirst(tasks[index].logs.count - 120)
        }
        save()
        return true
    }

    func complete(_ id: UUID, result: String, worktree: String?, branch: String?) -> Bool {
        guard let index = tasks.firstIndex(where: { $0.id == id }),
              tasks[index].status == .running else { return false }
        tasks[index].status = .succeeded
        tasks[index].result = result
        tasks[index].worktree = worktree
        tasks[index].branch = branch
        tasks[index].completedAt = Date()
        tasks[index].logs.append(CloudTaskLog(text: "Completed."))
        save()
        return true
    }

    func fail(_ id: UUID, error: String) -> Bool {
        guard let index = tasks.firstIndex(where: { $0.id == id }),
              tasks[index].status == .running else { return false }
        tasks[index].status = .failed
        tasks[index].error = error
        tasks[index].completedAt = Date()
        tasks[index].logs.append(CloudTaskLog(text: "Failed: \(error)"))
        save()
        return true
    }

    func cancel(_ id: UUID) -> Bool {
        guard let index = tasks.firstIndex(where: { $0.id == id }),
              !tasks[index].status.isTerminal else { return false }
        tasks[index].status = .cancelled
        tasks[index].completedAt = Date()
        tasks[index].logs.append(CloudTaskLog(text: "Cancelled."))
        save()
        return true
    }

    func remove(_ id: UUID) -> Bool {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return false }
        tasks.remove(at: index)
        save()
        return true
    }

    private func save() {
        guard let fileURL, let data = try? JSONEncoder().encode(tasks) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}

struct CloudHTTPRequest: Sendable {
    let method: String
    let path: String
    let headers: [String: String]
    let body: Data
}

struct CloudHTTPResponse: Sendable {
    let status: Int
    let body: Data
    let contentType: String

    static func json(_ status: Int, _ object: Any) -> CloudHTTPResponse {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
            ?? Data("{}".utf8)
        return CloudHTTPResponse(status: status, body: data, contentType: "application/json")
    }

    static func error(_ status: Int, _ message: String) -> CloudHTTPResponse {
        json(status, ["error": message])
    }
}

actor CloudTaskAPI {
    private let queue: CloudTaskQueue
    private let token: String

    init(queue: CloudTaskQueue, token: String) {
        self.queue = queue
        self.token = token
    }

    func handle(_ request: CloudHTTPRequest) async -> CloudHTTPResponse {
        let authorization = request.headers["authorization"] ?? ""
        guard !token.isEmpty, authorization == "Bearer \(token)" else {
            return .error(401, "Missing or invalid bearer token.")
        }
        let parts = request.path.split(separator: "/").map(String.init)
        guard parts.first == "v1" else {
            return .error(404, "Unknown path.")
        }
        let method = request.method.uppercased()

        if parts.count == 2, parts[1] == "tasks" {
            switch method {
            case "GET":
                return .json(200, ["tasks": encode(await queue.list())])
            case "POST":
                guard let body = decode(request.body),
                      let prompt = body["prompt"] as? String,
                      !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    return .error(400, "A prompt is required.")
                }
                guard let workspace = body["workspace"] as? String, !workspace.isEmpty else {
                    return .error(400, "A workspace path is required.")
                }
                let task = await queue.enqueue(
                    prompt: prompt,
                    workspace: workspace,
                    title: body["title"] as? String,
                    provider: body["provider"] as? String,
                    model: body["model"] as? String
                )
                return .json(201, encode(task))
            default:
                return .error(405, "Method not allowed.")
            }
        }

        if parts.count == 3, parts[1] == "tasks", let id = UUID(uuidString: parts[2]) {
            switch method {
            case "GET":
                guard let task = await queue.get(id) else { return .error(404, "Task not found.") }
                return .json(200, encode(task))
            case "DELETE":
                guard await queue.remove(id) else { return .error(404, "Task not found.") }
                return .json(200, ["removed": true])
            default:
                if parts.count == 3 { return .error(405, "Method not allowed.") }
            }
        }

        if parts.count == 4, parts[1] == "tasks", let id = UUID(uuidString: parts[2]), parts[3] == "cancel" {
            guard method == "POST" else { return .error(405, "Method not allowed.") }
            guard await queue.cancel(id) else {
                return .error(409, "Task is already finished or does not exist.")
            }
            return .json(200, encode(await queue.get(id)))
        }

        if parts == ["v1", "worker", "claim"] {
            guard method == "POST" else { return .error(405, "Method not allowed.") }
            let body = decode(request.body) ?? [:]
            let worker = (body["worker"] as? String) ?? "worker"
            guard let task = await queue.claim(worker: worker) else {
                return CloudHTTPResponse(status: 204, body: Data(), contentType: "application/json")
            }
            return .json(200, encode(task))
        }

        if parts.count == 5, parts[1] == "worker", parts[2] == "tasks",
           let id = UUID(uuidString: parts[3]) {
            guard method == "POST" else { return .error(405, "Method not allowed.") }
            let body = decode(request.body) ?? [:]
            switch parts[4] {
            case "events":
                let text = (body["text"] as? String) ?? ""
                guard await queue.append(id, text: text) else {
                    return .error(409, "Task is not running.")
                }
                return .json(200, ["ok": true])
            case "complete":
                let result = (body["result"] as? String) ?? ""
                guard await queue.complete(
                    id,
                    result: result,
                    worktree: body["worktree"] as? String,
                    branch: body["branch"] as? String
                ) else {
                    return .error(409, "Task is not running.")
                }
                return .json(200, encode(await queue.get(id)))
            case "fail":
                let error = (body["error"] as? String) ?? "Task failed."
                guard await queue.fail(id, error: error) else {
                    return .error(409, "Task is not running.")
                }
                return .json(200, encode(await queue.get(id)))
            default:
                return .error(404, "Unknown worker path.")
            }
        }

        return .error(404, "Unknown path.")
    }

    private func decode(_ data: Data) -> [String: Any]? {
        guard !data.isEmpty else { return [:] }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private func encode<T: Encodable>(_ value: T) -> Any {
        guard let data = try? JSONEncoder().encode(value),
              let object = try? JSONSerialization.jsonObject(with: data) else {
            return [:]
        }
        return object
    }
}

final class CloudTaskHTTPServer: @unchecked Sendable {
    let port: UInt16
    private let listener: NWListener
    private let api: CloudTaskAPI
    private let queue = DispatchQueue(label: "app.talos.cloud.server")
    private var readyContinuation: CheckedContinuation<Void, Error>?

    init(
        queue: CloudTaskQueue,
        token: String,
        port: UInt16,
        bindLAN: Bool
    ) throws {
        self.port = port
        self.api = CloudTaskAPI(queue: queue, token: token)
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        let endpointPort = NWEndpoint.Port(rawValue: port) ?? 8788
        if bindLAN {
            listener = try NWListener(using: parameters, on: endpointPort)
        } else {
            parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: endpointPort)
            listener = try NWListener(using: parameters)
        }
    }

    func start() async throws {
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.readyContinuation?.resume()
                self.readyContinuation = nil
            case .failed(let error):
                self.readyContinuation?.resume(throwing: error)
                self.readyContinuation = nil
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            readyContinuation = continuation
        }
    }

    func stop() {
        listener.cancel()
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
            if let headerRange = buffer.range(of: Data("\r\n\r\n".utf8)) {
                let headerData = buffer[..<headerRange.lowerBound]
                let headerText = String(decoding: headerData, as: UTF8.self)
                var lines = headerText.components(separatedBy: "\r\n")
                let requestLine = lines.first?.components(separatedBy: " ") ?? []
                lines.removeFirst()
                var headers: [String: String] = [:]
                for line in lines {
                    guard let colon = line.firstIndex(of: ":") else { continue }
                    let key = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                    let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
                    headers[key] = value
                }
                let length = Int(headers["content-length"] ?? "0") ?? 0
                let bodyStart = headerRange.upperBound
                let available = buffer.count - bodyStart
                if available < length && buffer.count < 2_000_000 {
                    if error == nil { self.receive(connection, buffer: buffer) }
                    return
                }
                let body = buffer[bodyStart..<min(bodyStart + length, buffer.count)]
                let request = CloudHTTPRequest(
                    method: requestLine.first ?? "GET",
                    path: requestLine.count > 1 ? requestLine[1] : "/",
                    headers: headers,
                    body: Data(body)
                )
                Task {
                    let response = await self.api.handle(request)
                    self.respond(connection, response: response)
                }
                return
            }
            if buffer.count > 2_000_000 || error != nil {
                self.respond(connection, response: .error(400, "Malformed request."))
                return
            }
            self.receive(connection, buffer: buffer)
        }
    }

    private func respond(_ connection: NWConnection, response: CloudHTTPResponse) {
        let reason: String
        switch response.status {
        case 200: reason = "OK"
        case 201: reason = "Created"
        case 204: reason = "No Content"
        case 400: reason = "Bad Request"
        case 401: reason = "Unauthorized"
        case 404: reason = "Not Found"
        case 405: reason = "Method Not Allowed"
        case 409: reason = "Conflict"
        default: reason = "Error"
        }
        var header = "HTTP/1.1 \(response.status) \(reason)\r\n"
        header += "Content-Type: \(response.contentType)\r\n"
        header += "Content-Length: \(response.body.count)\r\n"
        header += "Connection: close\r\n\r\n"
        var data = Data(header.utf8)
        data.append(response.body)
        connection.send(content: data, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }
}

protocol CloudTaskTransport: Sendable {
    func claim(worker: String) async throws -> CloudTask?
    func status(_ id: UUID) async throws -> CloudTask?
    func log(_ id: UUID, text: String) async throws
    func complete(_ id: UUID, execution: CloudTaskExecution) async throws
    func fail(_ id: UUID, error: String) async throws
}

enum CloudTransportError: LocalizedError {
    case message(String)
    case conflict(String)

    var errorDescription: String? {
        switch self {
        case .message(let value), .conflict(let value): return value
        }
    }
}

struct HTTPCloudTaskTransport: CloudTaskTransport {
    let baseURL: URL
    let token: String

    func claim(worker: String) async throws -> CloudTask? {
        let (status, data) = try await request("POST", "v1/worker/claim", body: ["worker": worker])
        if status == 204 { return nil }
        return try decodeTask(status: status, data: data)
    }

    func status(_ id: UUID) async throws -> CloudTask? {
        let (status, data) = try await request("GET", "v1/tasks/\(id.uuidString)")
        if status == 404 { return nil }
        return try decodeTask(status: status, data: data)
    }

    func log(_ id: UUID, text: String) async throws {
        _ = try await request("POST", "v1/worker/tasks/\(id.uuidString)/events", body: ["text": text])
    }

    func complete(_ id: UUID, execution: CloudTaskExecution) async throws {
        var body: [String: Any] = ["result": execution.result]
        if let worktree = execution.worktree { body["worktree"] = worktree }
        if let branch = execution.branch { body["branch"] = branch }
        let (status, data) = try await request("POST", "v1/worker/tasks/\(id.uuidString)/complete", body: body)
        if status == 409 {
            throw CloudTransportError.conflict("Task was cancelled or is no longer running.")
        }
        _ = try decodeTask(status: status, data: data)
    }

    func fail(_ id: UUID, error: String) async throws {
        _ = try await request("POST", "v1/worker/tasks/\(id.uuidString)/fail", body: ["error": error])
    }

    private func request(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> (Int, Data) {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw CloudTransportError.message("Invalid cloud task URL.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw CloudTransportError.message("No response from the cloud task server.")
        }
        if http.statusCode == 401 {
            throw CloudTransportError.message("The cloud task token was rejected.")
        }
        return (http.statusCode, data)
    }

    private func decodeTask(status: Int, data: Data) throws -> CloudTask {
        guard (200..<300).contains(status) else {
            let text = String(decoding: data, as: UTF8.self)
            throw CloudTransportError.message("Cloud task server answered \(status). \(text)")
        }
        return try JSONDecoder().decode(CloudTask.self, from: data)
    }
}

actor CloudTaskWorker {
    let id: String
    private let transport: any CloudTaskTransport
    private let executor: @Sendable (CloudTask) async throws -> CloudTaskExecution
    private let onChange: (@Sendable () async -> Void)?
    private var loopTask: Task<Void, Never>?
    private var lastErrorStorage: String?

    init(
        id: String = "talos-local-worker",
        transport: any CloudTaskTransport,
        executor: @escaping @Sendable (CloudTask) async throws -> CloudTaskExecution,
        onChange: (@Sendable () async -> Void)? = nil
    ) {
        self.id = id
        self.transport = transport
        self.executor = executor
        self.onChange = onChange
    }

    var lastError: String? { lastErrorStorage }

    func start(interval: TimeInterval = 1.0) {
        guard loopTask == nil else { return }
        loopTask = Task { [weak self] in
            await self?.runLoop(interval: interval)
        }
    }

    func stop() async {
        loopTask?.cancel()
        await loopTask?.value
        loopTask = nil
    }

    private func runLoop(interval: TimeInterval) async {
        while !Task.isCancelled {
            do {
                if let task = try await transport.claim(worker: id) {
                    await onChange?()
                    try? await transport.log(task.id, text: "Worker \(id) started.")
                    if let current = try await transport.status(task.id), current.status == .cancelled {
                        await onChange?()
                        continue
                    }
                    do {
                        let execution = try await executor(task)
                        if let current = try await transport.status(task.id), current.status == .cancelled {
                            await onChange?()
                            continue
                        }
                        try await transport.complete(task.id, execution: execution)
                    } catch {
                        try? await transport.fail(task.id, error: error.localizedDescription)
                    }
                    await onChange?()
                    continue
                }
                lastErrorStorage = nil
            } catch {
                lastErrorStorage = error.localizedDescription
            }
            try? await Task.sleep(nanoseconds: UInt64(max(interval, 0.2) * 1_000_000_000))
        }
    }
}
