import Foundation
import Network
import AppKit

@MainActor
final class RemoteServer: ObservableObject {
    @Published private(set) var running = false
    @Published private(set) var status = "Off"
    @Published private(set) var url: String?
    @Published var enabled: Bool {
        didSet {
            defaults.set(enabled, forKey: "remoteEnabled")
            enabled ? start() : stop()
        }
    }
    @Published var port: Int {
        didSet {
            port = min(max(port, 1024), 65535)
            defaults.set(port, forKey: "remotePort")
            if running { restart() }
        }
    }
    @Published var token: String {
        didSet { defaults.set(token, forKey: "remoteToken") }
    }

    var onText: ((String) -> Void)?
    var onStop: (() -> Void)?
    var onCompact: (() -> Void)?
    var statusProvider: (() -> [String: Any])?

    private let defaults = UserDefaults.standard
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "app.talos.remote")

    init() {
        let storedPort = defaults.object(forKey: "remotePort") as? Int ?? 8787
        port = min(max(storedPort, 1024), 65535)
        enabled = defaults.bool(forKey: "remoteEnabled")
        if let stored = defaults.string(forKey: "remoteToken"), !stored.isEmpty {
            token = stored
        } else {
            token = Self.randomToken()
            defaults.set(token, forKey: "remoteToken")
        }
        if enabled {
            start()
        }
    }

    func start() {
        guard listener == nil else { return }
        do {
            let parameters = NWParameters.tcp
            parameters.allowLocalEndpointReuse = true
            guard let nwPort = NWEndpoint.Port(rawValue: UInt16(port)) else {
                status = "Invalid port"
                return
            }
            let listener = try NWListener(using: parameters, on: nwPort)
            listener.stateUpdateHandler = { [weak self] state in
                Task { @MainActor in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        self.running = true
                        self.status = "Serving on \(self.address)"
                        self.url = "http://\(self.address):\(self.port)/?token=\(self.token)"
                    case .failed(let error):
                        self.running = false
                        self.status = error.localizedDescription
                        self.url = nil
                        self.listener?.cancel()
                        self.listener = nil
                    case .cancelled:
                        self.running = false
                        self.status = "Off"
                        self.url = nil
                    default:
                        break
                    }
                }
            }
            listener.newConnectionHandler = { [weak self] connection in
                Task { @MainActor in
                    self?.handle(connection)
                }
            }
            self.listener = listener
            listener.start(queue: queue)
            status = "Starting…"
        } catch {
            status = error.localizedDescription
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
        running = false
        status = "Off"
        url = nil
    }

    func setEnabled(_ value: Bool) {
        enabled = value
    }

    func restart() {
        stop()
        start()
    }

    func regenerateToken() {
        token = Self.randomToken()
        if running { restart() }
    }

    private var address: String {
        Host.current().addresses.first { address in
            address.contains(".") && !address.hasPrefix("127.") && address != "0.0.0.0"
        } ?? "127.0.0.1"
    }

    private func handle(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 128 * 1024) { [weak self] data, _, complete, error in
            var buffer = buffer
            if let data { buffer.append(data) }

            if let headerRange = buffer.range(of: Data("\r\n\r\n".utf8)),
               let header = String(data: buffer[..<headerRange.lowerBound], encoding: .utf8) {
                let length = Self.contentLength(header)
                let bodyStart = headerRange.upperBound
                if buffer.count - bodyStart >= length {
                    let body = buffer[bodyStart..<bodyStart + length]
                    Task { @MainActor in
                        self?.route(connection: connection, header: header, body: Data(body))
                    }
                    return
                }
            }

            if complete || error != nil {
                connection.cancel()
                return
            }
            Task { @MainActor in
                self?.receive(connection, buffer: buffer)
            }
        }
    }

    private func route(connection: NWConnection, header: String, body: Data) {
        let lines = header.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else {
            respond(connection, status: 400, contentType: "text/plain", body: "Bad request")
            return
        }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else {
            respond(connection, status: 400, contentType: "text/plain", body: "Bad request")
            return
        }
        let method = String(parts[0])
        let target = String(parts[1])
        let components = URLComponents(string: "http://localhost\(target)")
        let path = components?.path ?? "/"
        let queryToken = components?.queryItems?.first(where: { $0.name == "token" })?.value
        let auth = queryToken
            ?? lines.first(where: { $0.lowercased().hasPrefix("authorization: bearer ") })
                .map { String($0.dropFirst("authorization: bearer ".count)) }
        let authorized = auth == token

        if method == "GET" && path == "/" {
            respond(connection, status: 200, contentType: "text/html; charset=utf-8", body: page)
            return
        }
        guard authorized else {
            respond(connection, status: 401, contentType: "application/json", body: "{\"error\":\"unauthorized\"}")
            return
        }

        switch (method, path) {
        case ("GET", "/api/status"):
            let status = statusProvider?() ?? [:]
            respond(connection, status: 200, contentType: "application/json", body: json(status))
        case ("POST", "/api/send"):
            guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let text = object["text"] as? String,
                  !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                respond(connection, status: 400, contentType: "application/json", body: "{\"error\":\"text required\"}")
                return
            }
            onText?(text)
            respond(connection, status: 202, contentType: "application/json", body: "{\"ok\":true}")
        case ("POST", "/api/stop"):
            onStop?()
            respond(connection, status: 200, contentType: "application/json", body: "{\"ok\":true}")
        case ("POST", "/api/compact"):
            onCompact?()
            respond(connection, status: 200, contentType: "application/json", body: "{\"ok\":true}")
        default:
            respond(connection, status: 404, contentType: "text/plain", body: "Not found")
        }
    }

    private func respond(_ connection: NWConnection, status: Int, contentType: String, body: String) {
        let data = Data(body.utf8)
        let header = "HTTP/1.1 \(status) \(status == 200 ? "OK" : status == 401 ? "Unauthorized" : status == 202 ? "Accepted" : "Error")\r\n"
            + "Content-Type: \(contentType)\r\n"
            + "Content-Length: \(data.count)\r\n"
            + "Connection: close\r\n"
            + "Access-Control-Allow-Origin: *\r\n"
            + "\r\n"
        var response = Data(header.utf8)
        response.append(data)
        connection.send(content: response, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private var page: String {
        """
        <!doctype html>
        <html><head><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Talos</title>
        <style>
        :root { color-scheme: dark; }
        body { margin:0; background:#15171a; color:#eee; font:16px -apple-system,system-ui,sans-serif; }
        main { max-width: 680px; margin: 0 auto; padding: 24px; }
        h1 { font-size: 24px; margin: 0 0 4px; color:#d48c45; }
        .sub { color:#999; font-size:13px; margin-bottom:20px; }
        .card { background:#1d2024; border:1px solid #30343a; border-radius:12px; padding:14px; margin:12px 0; }
        textarea { width:100%; min-height:120px; box-sizing:border-box; background:#111317; color:#eee; border:1px solid #34383f; border-radius:8px; padding:10px; font:15px inherit; }
        button { background:#b87333; color:#fff; border:0; border-radius:8px; padding:10px 16px; font-weight:600; margin-right:8px; }
        button.secondary { background:#2c3036; color:#ddd; }
        pre { white-space:pre-wrap; word-break:break-word; color:#bbb; font-size:13px; }
        </style></head>
        <body><main>
        <h1>Talos</h1><div class="sub">Remote task control</div>
        <div class="card" id="status">Connecting…</div>
        <div class="card">
          <textarea id="text" placeholder="Send a message to the current task…"></textarea>
          <div style="margin-top:10px">
            <button onclick="send()">Send</button>
            <button class="secondary" onclick="stopTask()">Stop</button>
            <button class="secondary" onclick="compact()">Compact</button>
          </div>
        </div>
        </main>
        <script>
        const token = new URLSearchParams(location.search).get('token') || '';
        const api = (path) => path + '?token=' + encodeURIComponent(token);
        async function refresh() {
          try {
            const r = await fetch(api('/api/status'));
            const d = await r.json();
            document.getElementById('status').innerHTML =
              '<b>' + (d.project || 'Project') + '</b><br>' +
              (d.model || '') + '<br>' +
              'State: ' + (d.streaming ? 'Working' : 'Idle') + '<br>' +
              'Queue: ' + (d.queue || 0) + '<br>' +
              (d.context || '');
          } catch (e) { document.getElementById('status').textContent = 'Offline'; }
        }
        async function send() {
          const text = document.getElementById('text').value;
          if (!text.trim()) return;
          await fetch(api('/api/send'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text}) });
          document.getElementById('text').value = '';
          refresh();
        }
        async function stopTask() { await fetch(api('/api/stop'), {method:'POST'}); refresh(); }
        async function compact() { await fetch(api('/api/compact'), {method:'POST'}); refresh(); }
        refresh(); setInterval(refresh, 2500);
        </script></body></html>
        """
    }

    private func json(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let string = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return string
    }

    private static func contentLength(_ header: String) -> Int {
        for line in header.components(separatedBy: "\r\n") {
            let parts = line.split(separator: ":", maxSplits: 1).map(String.init)
            if parts.count == 2, parts[0].lowercased() == "content-length" {
                return Int(parts[1].trimmingCharacters(in: .whitespaces)) ?? 0
            }
        }
        return 0
    }

    private static func randomToken() -> String {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyz0123456789")
        return String((0..<28).map { _ in alphabet.randomElement()! })
    }
}
