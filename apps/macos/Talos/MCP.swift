import Foundation

struct MCPServerConfig: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var name: String
    var command: String
    var args: [String] = []
    var env: [String: String] = [:]
    var enabled = true
}

struct MCPTool: Identifiable, Hashable, Sendable {
    let serverID: UUID
    let serverName: String
    let name: String
    let description: String
    let inputSchema: String

    var id: String { "\(serverID.uuidString)|\(name)" }

    var toolName: String {
        "mcp__\(serverName.normalizedToolComponent)__\(name.normalizedToolComponent)"
    }

    var promptDescription: String {
        let schema = inputSchema.count > 600 ? String(inputSchema.prefix(600)) + "…" : inputSchema
        return "\(description.isEmpty ? name : description) Arguments: \(schema)"
    }
}

private extension String {
    var normalizedToolComponent: String {
        let allowed = CharacterSet.alphanumerics
        let scalars = lowercased().unicodeScalars.map { scalar -> Character in
            allowed.contains(scalar) ? Character(String(scalar)) : "_"
        }
        return String(scalars)
    }
}

enum MCPError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

actor MCPClient {
    private let config: MCPServerConfig
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var reader: Task<Void, Never>?
    private var pending: [Int: CheckedContinuation<[String: Any], Error>] = [:]
    private var nextID = 1
    private var stopped = false

    init(config: MCPServerConfig) {
        self.config = config
    }

    deinit {
        process?.terminate()
    }

    func start() async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.command)
        process.arguments = config.args
        process.environment = ProcessInfo.processInfo.environment.merging(config.env) { _, new in new }

        let inputPipe = Pipe()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        do {
            try process.run()
        } catch {
            throw MCPError.message("Could not start \(config.name): \(error.localizedDescription)")
        }
        self.process = process
        self.input = inputPipe.fileHandleForWriting
        self.output = outputPipe.fileHandleForReading
        reader = Task { [weak self] in
            await self?.readLoop()
        }

        let response = try await request("initialize", params: [
            "protocolVersion": "2024-11-05",
            "capabilities": [:],
            "clientInfo": ["name": "Talos", "version": "0.3"],
        ])
        guard response["result"] is [String: Any] else {
            throw MCPError.message("\(config.name) rejected the MCP initialize handshake.")
        }
        try sendNotification("notifications/initialized")
    }

    func listTools() async throws -> [MCPTool] {
        let response = try await request("tools/list")
        guard let result = response["result"] as? [String: Any],
              let rawTools = result["tools"] as? [[String: Any]] else {
            throw MCPError.message("\(config.name) did not return a tools list.")
        }
        return rawTools.compactMap { raw in
            guard let name = raw["name"] as? String else { return nil }
            let description = raw["description"] as? String ?? ""
            let schemaObject = raw["inputSchema"] as? [String: Any] ?? [:]
            let schemaData = try? JSONSerialization.data(withJSONObject: schemaObject, options: [.sortedKeys])
            let schema = schemaData.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
            return MCPTool(
                serverID: config.id,
                serverName: config.name,
                name: name,
                description: description,
                inputSchema: schema
            )
        }
    }

    func callTool(name: String, arguments: [String: Any]) async throws -> String {
        let response = try await request("tools/call", params: [
            "name": name,
            "arguments": arguments,
        ])
        guard let result = response["result"] as? [String: Any] else {
            throw MCPError.message("No result from \(config.name).")
        }
        if let isError = result["isError"] as? Bool, isError {
            throw MCPError.message(renderContent(result["content"]))
        }
        return renderContent(result["content"])
    }

    func stop() {
        stopped = true
        reader?.cancel()
        reader = nil
        process?.terminate()
        process = nil
        input = nil
        output = nil
        failPending(MCPError.message("MCP server \(config.name) stopped."))
    }

    private func renderContent(_ raw: Any?) -> String {
        guard let items = raw as? [[String: Any]] else {
            return String(describing: raw ?? "No content")
        }
        return items.compactMap { item -> String? in
            if let text = item["text"] as? String { return text }
            if let resource = item["resource"] as? [String: Any],
               let text = resource["text"] as? String { return text }
            return nil
        }.joined(separator: "\n")
    }

    private func request(_ method: String, params: [String: Any]? = nil) async throws -> [String: Any] {
        if stopped { throw MCPError.message("MCP server \(config.name) is stopped.") }
        let id = nextID
        nextID += 1
        let message: [String: Any] = [
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params ?? [:],
        ]
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try send(message)
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
                return
            }
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                await self?.timeout(id)
            }
        }
    }

    private func timeout(_ id: Int) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(throwing: MCPError.message("\(config.name) did not answer in time."))
    }

    private func sendNotification(_ method: String) throws {
        try send(["jsonrpc": "2.0", "method": method])
    }

    private func send(_ object: [String: Any]) throws {
        guard let input else {
            throw MCPError.message("\(config.name) is not running.")
        }
        let data = try JSONSerialization.data(withJSONObject: object)
        input.write(data)
        input.write(Data([0x0A]))
    }

    private func readLoop() async {
        guard let output else { return }
        do {
            for try await line in output.bytes.lines {
                guard !line.isEmpty, let data = line.data(using: .utf8) else { continue }
                guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                if let rawID = object["id"] as? NSNumber {
                    let id = rawID.intValue
                    if let continuation = pending.removeValue(forKey: id) {
                        if let error = object["error"] as? [String: Any] {
                            let message = error["message"] as? String ?? "MCP error"
                            continuation.resume(throwing: MCPError.message(message))
                        } else {
                            continuation.resume(returning: object)
                        }
                    }
                }
            }
        } catch {
            // The process ended or the pipe closed.
        }
        failPending(MCPError.message("MCP server \(config.name) closed its output."))
    }

    private func failPending(_ error: Error) {
        let continuations = pending.values
        pending.removeAll()
        for continuation in continuations {
            continuation.resume(throwing: error)
        }
    }
}

actor MCPManager {
    private var clients: [UUID: MCPClient] = [:]
    private(set) var tools: [MCPTool] = []

    func connect(_ configs: [MCPServerConfig]) async -> (tools: [MCPTool], errors: [String]) {
        for client in clients.values {
            await client.stop()
        }
        clients.removeAll()

        var discovered: [MCPTool] = []
        var errors: [String] = []
        for config in configs where config.enabled && !config.command.isEmpty {
            let client = MCPClient(config: config)
            do {
                try await client.start()
                let serverTools = try await client.listTools()
                clients[config.id] = client
                discovered.append(contentsOf: serverTools)
            } catch {
                errors.append("\(config.name): \(error.localizedDescription)")
                await client.stop()
            }
        }
        tools = discovered
        return (discovered, errors)
    }

    func call(toolName: String, arguments: [String: Any]) async -> Result<String, Error> {
        guard let tool = tools.first(where: { $0.toolName == toolName }),
              let client = clients[tool.serverID] else {
            return .failure(MCPError.message("No MCP client is connected for \(toolName)."))
        }
        do {
            return .success(try await client.callTool(name: tool.name, arguments: arguments))
        } catch {
            return .failure(error)
        }
    }

    func stopAll() async {
        for client in clients.values {
            await client.stop()
        }
        clients.removeAll()
        tools = []
    }
}
