import Foundation
import AppKit

enum NativeMessage {
    static func read() -> [String: Any]? {
        guard let header = readExactly(4) else { return nil }
        let length = header.withUnsafeBytes { $0.load(as: UInt32.self) }.littleEndian
        guard length > 0, length <= 5_000_000 else { return nil }
        guard let body = readExactly(Int(length)) else { return nil }
        return (try? JSONSerialization.jsonObject(with: body) as? [String: Any]) ?? nil
    }

    static func write(_ object: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return }
        var length = UInt32(data.count).littleEndian
        let header = withUnsafeBytes(of: &length) { Data($0) }
        FileHandle.standardOutput.write(header)
        FileHandle.standardOutput.write(data)
    }

    private static var buffer = Data()

    private static func readExactly(_ count: Int) -> Data? {
        while buffer.count < count {
            let chunk = FileHandle.standardInput.availableData
            if chunk.isEmpty { return nil }
            buffer.append(chunk)
        }
        let result = Data(buffer.prefix(count))
        buffer.removeFirst(count)
        return result
    }
}

struct BridgeConfig {
    let endpoint: String
    let token: String
    let appSupport: URL

    static func load() -> BridgeConfig {
        let environment = ProcessInfo.processInfo.environment
        let defaults = UserDefaults(suiteName: "app.talos.macos")
        let port = environment["TALOS_APP_SERVER_PORT"]
            ?? String(defaults?.object(forKey: "appServerPort") as? Int ?? 8790)
        let token = environment["TALOS_APP_SERVER_TOKEN"]
            ?? defaults?.string(forKey: "appServerToken")
            ?? ""
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = support.appendingPathComponent("Talos", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return BridgeConfig(
            endpoint: "http://127.0.0.1:\(port)",
            token: token,
            appSupport: folder
        )
    }
}

struct AppServerBridge {
    let config: BridgeConfig

    func health() async -> Bool {
        guard !config.token.isEmpty,
              let url = URL(string: config.endpoint + "/health") else { return false }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3
        request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    func rpc(_ method: String, params: [String: Any] = [:]) async throws -> [String: Any] {
        guard !config.token.isEmpty,
              let url = URL(string: config.endpoint + "/rpc") else {
            throw BridgeError.message("The Talos app server is not configured. Enable Settings → App Server.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BridgeError.message("No response from the Talos app server.")
        }
        guard http.statusCode == 200 else {
            throw BridgeError.message("Talos app server answered \(http.statusCode).")
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw BridgeError.message("Talos returned an unreadable response.")
        }
        if let error = object["error"] as? [String: Any] {
            throw BridgeError.message(error["message"] as? String ?? "Talos returned an error.")
        }
        return object["result"] as? [String: Any] ?? [:]
    }
}

enum BridgeError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

@main
struct TalosNativeHost {
    static func main() async {
        let config = BridgeConfig.load()
        let bridge = AppServerBridge(config: config)
        while true {
            guard let message = NativeMessage.read() else { break }
            let response = await handle(message, config: config, bridge: bridge)
            NativeMessage.write(response)
        }
    }

    static func handle(
        _ message: [String: Any],
        config: BridgeConfig,
        bridge: AppServerBridge
    ) async -> [String: Any] {
        let action = (message["action"] as? String) ?? "ping"
        let params = message["params"] as? [String: Any] ?? [:]
        switch action {
        case "ping":
            return [
                "ok": true,
                "app": "Talos",
                "version": "1.0",
                "serverRunning": await bridge.health(),
                "endpoint": config.endpoint,
            ]
        case "page":
            return savePage(params, config: config)
        case "context":
            return loadPage(config: config)
        case "ask":
            return await ask(params, bridge: bridge)
        case "status":
            return await status(params, bridge: bridge)
        case "cancel":
            return await cancel(params, bridge: bridge)
        case "open":
            return openApp()
        default:
            return ["ok": false, "error": "Unknown action \(action)."]
        }
    }

    static func savePage(_ params: [String: Any], config: BridgeConfig) -> [String: Any] {
        var context: [String: Any] = [
            "url": params["url"] as? String ?? "",
            "title": params["title"] as? String ?? "",
            "text": String((params["text"] as? String ?? "").prefix(40_000)),
            "selection": String((params["selection"] as? String ?? "").prefix(8_000)),
            "savedAt": ISO8601DateFormatter().string(from: Date()),
        ]
        let folder = config.appSupport.appendingPathComponent("browser", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent("page-context.json")
        guard let data = try? JSONSerialization.data(withJSONObject: context, options: [.prettyPrinted, .sortedKeys]),
              (try? data.write(to: file, options: .atomic)) != nil else {
            return ["ok": false, "error": "Could not save page context."]
        }
        context["ok"] = true
        context["path"] = file.path
        return context
    }

    static func loadPage(config: BridgeConfig) -> [String: Any] {
        let file = config.appSupport.appendingPathComponent("browser/page-context.json")
        guard let data = try? Data(contentsOf: file),
              var object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["ok": false, "error": "No page context has been saved yet."]
        }
        object["ok"] = true
        return object
    }

    static func ask(_ params: [String: Any], bridge: AppServerBridge) async -> [String: Any] {
        let request = (params["prompt"] as? String ?? "Summarize this page.").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !request.isEmpty else {
            return ["ok": false, "error": "A prompt is required."]
        }
        let title = params["title"] as? String ?? "Browser page"
        let url = params["url"] as? String ?? ""
        let pageText = String((params["text"] as? String ?? "").prefix(12_000))
        let selection = params["selection"] as? String ?? ""
        var prompt = """
        Request from the Talos browser extension:
        \(request)

        Page: \(title)
        URL: \(url)
        """
        if !selection.isEmpty {
            prompt += "\n\nSelected text:\n\(selection)"
        }
        if !pageText.isEmpty {
            prompt += "\n\nPage text:\n\(pageText)"
        }
        do {
            let created = try await bridge.rpc("session.create", params: [
                "title": "Browser: \(String(title.prefix(60)))",
            ])
            guard let sessionID = created["sessionId"] as? String else {
                return ["ok": false, "error": "Talos did not return a session."]
            }
            let run = try await bridge.rpc("session.prompt", params: [
                "sessionId": sessionID,
                "text": prompt,
            ])
            return [
                "ok": true,
                "sessionId": sessionID,
                "runId": run["runId"] ?? "",
            ]
        } catch {
            return ["ok": false, "error": error.localizedDescription]
        }
    }

    static func status(_ params: [String: Any], bridge: AppServerBridge) async -> [String: Any] {
        guard let sessionID = params["sessionId"] as? String else {
            return ["ok": false, "error": "sessionId is required."]
        }
        do {
            let session = try await bridge.rpc("session.get", params: ["sessionId": sessionID])
            let messages = session["messages"] as? [[String: Any]] ?? []
            let assistants = messages.filter { $0["role"] as? String == "assistant" }
            let last = assistants.last
            let streaming = last?["isStreaming"] as? Bool ?? false
            return [
                "ok": true,
                "sessionId": sessionID,
                "title": session["title"] ?? "",
                "streaming": streaming,
                "lastAssistant": last?["text"] ?? "",
                "messageCount": messages.count,
            ]
        } catch {
            return ["ok": false, "error": error.localizedDescription]
        }
    }

    static func cancel(_ params: [String: Any], bridge: AppServerBridge) async -> [String: Any] {
        guard let sessionID = params["sessionId"] as? String else {
            return ["ok": false, "error": "sessionId is required."]
        }
        do {
            let result = try await bridge.rpc("session.cancel", params: ["sessionId": sessionID])
            return ["ok": true, "cancelled": result["cancelled"] ?? false]
        } catch {
            return ["ok": false, "error": error.localizedDescription]
        }
    }

    static func openApp() -> [String: Any] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-b", "app.talos.macos"]
        do {
            try process.run()
            return ["ok": true, "opened": true]
        } catch {
            return ["ok": false, "error": error.localizedDescription]
        }
    }
}
