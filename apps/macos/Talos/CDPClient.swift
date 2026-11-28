import Foundation

struct CDPTarget: Sendable {
    let id: String
    let title: String
    let url: String
    let type: String
    let webSocketURL: URL
}

struct CDPElementInfo: Sendable {
    let tag: String
    let type: String
    let text: String
    let selector: String
}

struct CDPPageSnapshot: Sendable {
    let title: String
    let url: String
    let text: String
    let elements: [CDPElementInfo]
}

struct CDPResult: Sendable {
    let output: String
    var attachment: String? = nil
}

enum CDPError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

actor CDPClient {
    private let webSocketURL: URL
    private let session: URLSession
    private var task: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var pending: [Int: CheckedContinuation<[String: Any], Error>] = [:]
    private var nextID = 1

    init(webSocketURL: URL, session: URLSession = .shared) {
        self.webSocketURL = webSocketURL
        self.session = session
    }

    func connect() async throws {
        let task = session.webSocketTask(with: webSocketURL)
        self.task = task
        task.resume()
        receiveTask = Task { [weak self] in
            await self?.readLoop()
        }
    }

    func close() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        receiveTask?.cancel()
        receiveTask = nil
        failPending(CDPError.message("CDP connection closed."))
    }

    func send(_ method: String, _ params: [String: Any] = [:]) async throws -> [String: Any] {
        guard let task else {
            throw CDPError.message("Not connected to Chrome.")
        }
        let id = nextID
        nextID += 1
        let payload: [String: Any] = [
            "id": id,
            "method": method,
            "params": params,
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        guard let text = String(data: data, encoding: .utf8) else {
            throw CDPError.message("Could not encode a CDP message.")
        }

        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            Task { [weak self] in
                do {
                    try await task.send(.string(text))
                } catch {
                    await self?.fail(id: id, error: error)
                }
            }
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                await self?.fail(id: id, error: CDPError.message("Chrome did not answer \(method) in time."))
            }
        }
    }

    func evaluate(_ expression: String) async throws -> Any? {
        let response = try await send("Runtime.evaluate", [
            "expression": expression,
            "returnByValue": true,
            "awaitPromise": true,
        ])
        if let exception = response["exceptionDetails"] as? [String: Any] {
            let message = (exception["exception"] as? [String: Any])?["description"] as? String
                ?? (exception["text"] as? String)
                ?? "JavaScript failed."
            throw CDPError.message(message)
        }
        guard let result = response["result"] as? [String: Any] else { return nil }
        if let value = result["value"] { return value }
        return result["description"]
    }

    func snapshot() async throws -> CDPPageSnapshot {
        let script = """
        (() => {
          function cssPath(node) {
            if (!node || node.nodeType !== 1) { return ""; }
            if (node.id) { return "#" + CSS.escape(node.id); }
            const parts = [];
            let el = node;
            while (el && el.nodeType === 1 && parts.length < 6) {
              let part = el.tagName.toLowerCase();
              if (typeof el.className === "string" && el.className.trim()) {
                part += "." + CSS.escape(el.className.trim().split(/\\s+/)[0]);
              }
              const parent = el.parentElement;
              if (parent) {
                const same = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                if (same.length > 1) { part += ":nth-of-type(" + (same.indexOf(el) + 1) + ")"; }
              }
              parts.unshift(part);
              el = parent;
            }
            return parts.join(" > ");
          }
          const nodes = Array.from(document.querySelectorAll(
            'a,button,input,textarea,select,[role="button"],[contenteditable="true"]'
          )).slice(0, 250);
          const elements = nodes.map((el) => ({
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute("type") || "",
            text: (el.innerText || el.value || el.getAttribute("aria-label") ||
                   el.getAttribute("placeholder") || "").trim().slice(0, 200),
            selector: cssPath(el)
          }));
          return {
            title: document.title || "",
            url: location.href,
            text: (document.body ? document.body.innerText : "").slice(0, 18000),
            elements: elements
          };
        })()
        """
        let value = try await evaluate(script)
        guard let object = value as? [String: Any] else {
            throw CDPError.message("Chrome returned an unreadable page snapshot.")
        }
        let elements = (object["elements"] as? [[String: Any]] ?? []).map {
            CDPElementInfo(
                tag: $0["tag"] as? String ?? "",
                type: $0["type"] as? String ?? "",
                text: $0["text"] as? String ?? "",
                selector: $0["selector"] as? String ?? ""
            )
        }
        return CDPPageSnapshot(
            title: object["title"] as? String ?? "",
            url: object["url"] as? String ?? "",
            text: object["text"] as? String ?? "",
            elements: elements
        )
    }

    func navigate(to url: String) async throws -> CDPPageSnapshot {
        _ = try await send("Page.navigate", ["url": url])
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 200_000_000)
            if let state = try? await evaluate("document.readyState") as? String,
               state == "complete" {
                break
            }
        }
        try? await Task.sleep(nanoseconds: 200_000_000)
        return try await snapshot()
    }

    func click(selector: String?, text: String?) async throws -> String {
        let encodedSelector = selector.map(jsonString) ?? "null"
        let encodedText = text.map(jsonString) ?? "null"
        let script = """
        (() => {
          const selector = \(encodedSelector);
          const wanted = \(encodedText);
          let el = selector ? document.querySelector(selector) : null;
          if (!el && wanted) {
            const candidates = Array.from(document.querySelectorAll(
              'a,button,[role="button"],input[type="submit"],input[type="button"]'
            ));
            el = candidates.find((candidate) =>
              (candidate.innerText || candidate.value || "").trim().includes(wanted)
            ) || null;
          }
          if (!el) { return { ok: false, error: "No matching element." }; }
          el.scrollIntoView({ block: "center" });
          el.click();
          return { ok: true, label: (el.innerText || el.value || el.tagName).trim().slice(0, 200) };
        })()
        """
        let value = try await evaluate(script)
        guard let object = value as? [String: Any], object["ok"] as? Bool == true else {
            let message = (value as? [String: Any])?["error"] as? String ?? "Click failed."
            throw CDPError.message(message)
        }
        return "Clicked \(object["label"] as? String ?? "element")."
    }

    func type(selector: String, text: String) async throws -> String {
        let script = """
        (() => {
          const el = document.querySelector(\(jsonString(selector)));
          if (!el) { return { ok: false, error: "No element matched \(selector.replacingOccurrences(of: "\"", with: "\\\""))" }; }
          el.focus();
          el.value = \(jsonString(text));
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return { ok: true, tag: el.tagName.toLowerCase() };
        })()
        """
        let value = try await evaluate(script)
        guard let object = value as? [String: Any], object["ok"] as? Bool == true else {
            let message = (value as? [String: Any])?["error"] as? String ?? "Typing failed."
            throw CDPError.message(message)
        }
        return "Typed \(text.count) characters into \(object["tag"] as? String ?? "element")."
    }

    func screenshot() async throws -> String {
        _ = try? await send("Emulation.setDeviceMetricsOverride", [
            "width": 1280,
            "height": 900,
            "deviceScaleFactor": 1,
            "mobile": false,
        ])
        let response = try await send("Page.captureScreenshot", [
            "format": "png",
            "captureBeyondViewport": false,
        ])
        guard let base64 = response["data"] as? String,
              let data = Data(base64Encoded: base64) else {
            throw CDPError.message("Chrome returned no screenshot data.")
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-cdp-\(UUID().uuidString).png")
        try data.write(to: url)
        return url.path
    }

    private func readLoop() async {
        guard let task else { return }
        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                switch message {
                case .string(let text):
                    handle(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        handle(text)
                    }
                @unknown default:
                    break
                }
            } catch {
                break
            }
        }
        failPending(CDPError.message("Chrome closed the CDP connection."))
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rawID = object["id"] as? NSNumber,
              let continuation = pending.removeValue(forKey: rawID.intValue) else {
            return
        }
        if let error = object["error"] as? [String: Any] {
            let message = error["message"] as? String ?? "CDP error."
            continuation.resume(throwing: CDPError.message(message))
            return
        }
        continuation.resume(returning: object["result"] as? [String: Any] ?? [:])
    }

    private func fail(id: Int, error: Error) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(throwing: error)
    }

    private func failPending(_ error: Error) {
        let continuations = pending.values
        pending.removeAll()
        for continuation in continuations {
            continuation.resume(throwing: error)
        }
    }

    private func jsonString(_ value: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: [value])) ?? Data("[\"\"]".utf8)
        let text = String(decoding: data, as: UTF8.self)
        return String(text.dropFirst().dropLast())
    }
}

actor CDPController {
    static let shared = CDPController()

    func perform(
        action: String,
        endpoint: String,
        url: String? = nil,
        expression: String? = nil,
        selector: String? = nil,
        text: String? = nil
    ) async throws -> CDPResult {
        let endpoint = normalized(endpoint)
        switch action {
        case "status":
            let targets = try await Self.targets(endpoint: endpoint)
            let lines = targets.map { "- \($0.type): \($0.title) — \($0.url)" }
            return CDPResult(output: "Chrome debugging endpoint \(endpoint)\n" + lines.joined(separator: "\n"))
        case "read":
            return try await withClient(endpoint: endpoint) { client in
                let snapshot = try await client.snapshot()
                return CDPResult(output: Self.render(snapshot))
            }
        case "navigate":
            guard let url, !url.isEmpty else { throw CDPError.message("navigate requires a url.") }
            return try await withClient(endpoint: endpoint) { client in
                let snapshot = try await client.navigate(to: url)
                return CDPResult(output: "Navigated to \(snapshot.url)\n\n" + Self.render(snapshot))
            }
        case "eval":
            guard let expression, !expression.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw CDPError.message("eval requires an expression.")
            }
            return try await withClient(endpoint: endpoint) { client in
                let value = try await client.evaluate(expression)
                let text = Self.describe(value)
                return CDPResult(output: text)
            }
        case "click":
            return try await withClient(endpoint: endpoint) { client in
                let message = try await client.click(selector: selector, text: text)
                let snapshot = try await client.snapshot()
                return CDPResult(output: message + "\n\n" + Self.render(snapshot))
            }
        case "type":
            guard let selector, !selector.isEmpty, let text else {
                throw CDPError.message("type requires selector and text.")
            }
            return try await withClient(endpoint: endpoint) { client in
                let message = try await client.type(selector: selector, text: text)
                return CDPResult(output: message)
            }
        case "screenshot":
            return try await withClient(endpoint: endpoint) { client in
                let path = try await client.screenshot()
                return CDPResult(output: "Chrome screenshot saved to \(path).", attachment: path)
            }
        default:
            throw CDPError.message("Unknown browser_cdp action \(action).")
        }
    }

    private func withClient<T>(
        endpoint: String,
        _ body: (CDPClient) async throws -> T
    ) async throws -> T {
        let targets = try await Self.targets(endpoint: endpoint)
        guard let target = targets.first(where: { $0.type == "page" }) ?? targets.first else {
            throw CDPError.message("Chrome has no page target. Open a tab or start Chrome with --remote-debugging-port.")
        }
        let client = CDPClient(webSocketURL: target.webSocketURL)
        try await client.connect()
        defer { Task { await client.close() } }
        return try await body(client)
    }

    private func normalized(_ endpoint: String) -> String {
        var value = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty { value = "http://127.0.0.1:9222" }
        if !value.hasPrefix("http://") && !value.hasPrefix("https://") {
            value = "http://" + value
        }
        while value.hasSuffix("/") { value.removeLast() }
        return value
    }

    static func targets(endpoint: String) async throws -> [CDPTarget] {
        guard let url = URL(string: endpoint + "/json/list") else {
            throw CDPError.message("The CDP endpoint is invalid.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw CDPError.message("Chrome is not exposing a debugging endpoint at \(endpoint). Start Chrome with --remote-debugging-port=9222.")
        }
        guard let rows = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw CDPError.message("Chrome returned an unreadable target list.")
        }
        return rows.compactMap { row in
            guard let id = row["id"] as? String,
                  let ws = row["webSocketDebuggerUrl"] as? String,
                  let url = URL(string: ws) else { return nil }
            return CDPTarget(
                id: id,
                title: row["title"] as? String ?? "",
                url: row["url"] as? String ?? "",
                type: row["type"] as? String ?? "",
                webSocketURL: url
            )
        }
    }

    private static func render(_ snapshot: CDPPageSnapshot) -> String {
        let elements = snapshot.elements.enumerated().map { index, element in
            let type = element.type.isEmpty ? "" : " type=\(element.type)"
            return "[\(index)] \(element.tag)\(type) \"\(element.text)\" selector=\(element.selector)"
        }
        .joined(separator: "\n")
        let clipped = snapshot.text.count > 18_000
            ? String(snapshot.text.prefix(18_000)) + "\n…"
            : snapshot.text
        return """
        Chrome page
        Title: \(snapshot.title)
        URL: \(snapshot.url)

        Interactive elements (use click with selector or text, or type with selector):
        \(elements.isEmpty ? "None found." : elements)

        Page text:
        \(clipped.isEmpty ? "No page text." : clipped)
        """
    }

    private static func describe(_ value: Any?) -> String {
        guard let value else { return "undefined" }
        if let string = value as? String { return string }
        if JSONSerialization.isValidJSONObject(value),
           let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) {
            return String(decoding: data, as: UTF8.self)
        }
        return String(describing: value)
    }
}
