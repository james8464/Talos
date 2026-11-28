import Foundation
import Network

struct DaemonRequest {
    let method: String
    let path: String
    let query: [String: String]
    let headers: [String: String]
    let body: Data
}

struct DaemonResponse {
    let status: Int
    let contentType: String
    let body: Data

    static func json(_ status: Int, _ object: [String: Any]) -> DaemonResponse {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return DaemonResponse(status: status, contentType: "application/json", body: data)
    }

    static func html(_ text: String) -> DaemonResponse {
        DaemonResponse(status: 200, contentType: "text/html; charset=utf-8", body: Data(text.utf8))
    }
}

struct AppServerProxy {
    let endpoint: String
    let token: String

    func rpc(_ method: String, params: [String: Any] = [:]) async throws -> Any? {
        guard let url = URL(string: endpoint + "/rpc") else {
            throw DaemonError.message("The Talos app server URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw DaemonError.message("Talos returned no response.")
        }
        guard http.statusCode == 200 else {
            throw DaemonError.message("Talos app server answered \(http.statusCode).")
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DaemonError.message("Talos returned unreadable JSON.")
        }
        if let error = object["error"] as? [String: Any] {
            throw DaemonError.message(error["message"] as? String ?? "Talos returned an error.")
        }
        return object["result"]
    }

    func dictionary(_ method: String, params: [String: Any] = [:]) async throws -> [String: Any] {
        (try await rpc(method, params: params) as? [String: Any]) ?? [:]
    }
}

enum DaemonError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

@main
struct TalosRemoteDaemon {
    static func main() async {
        let arguments = CommandLine.arguments
        let environment = ProcessInfo.processInfo.environment
        let port = UInt16(argument("--port", in: arguments) ?? environment["TALOS_REMOTE_PORT"] ?? "8798") ?? 8798
        let remoteToken = argument("--token", in: arguments) ?? environment["TALOS_REMOTE_TOKEN"] ?? ""
        let appPort = argument("--app-port", in: arguments) ?? environment["TALOS_APP_SERVER_PORT"] ?? "8790"
        let appToken = argument("--app-token", in: arguments) ?? environment["TALOS_APP_SERVER_TOKEN"] ?? ""
        let bindLAN = arguments.contains("--lan")
        guard !remoteToken.isEmpty else {
            print("TALOS_REMOTE_TOKEN or --token is required.")
            exit(1)
        }
        guard !appToken.isEmpty else {
            print("TALOS_APP_SERVER_TOKEN or --app-token is required.")
            exit(1)
        }
        let proxy = AppServerProxy(endpoint: "http://127.0.0.1:\(appPort)", token: appToken)
        do {
            let server = try DaemonServer(
                port: port,
                token: remoteToken,
                proxy: proxy,
                bindLAN: bindLAN
            )
            try await server.start()
            print("Talos remote daemon listening on http://127.0.0.1:\(port)/?token=\(remoteToken)")
            fflush(stdout)
            while true {
                try await Task.sleep(nanoseconds: 3_600_000_000_000)
            }
        } catch {
            print("Remote daemon failed: \(error.localizedDescription)")
            exit(1)
        }
    }

    private static func argument(_ name: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
            return nil
        }
        return arguments[index + 1]
    }
}

final class DaemonServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "app.talos.remote.daemon")
    private let token: String
    private let proxy: AppServerProxy
    private let port: UInt16
    private var readyContinuation: CheckedContinuation<Void, Error>?

    init(port: UInt16, token: String, proxy: AppServerProxy, bindLAN: Bool) throws {
        self.port = port
        self.token = token
        self.proxy = proxy
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        let endpointPort = NWEndpoint.Port(rawValue: port) ?? 8798
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
        try await withCheckedThrowingContinuation { continuation in
            readyContinuation = continuation
        }
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
                if buffer.count > 1_000_000 || error != nil {
                    self.respond(connection, .json(400, ["ok": false, "error": "Malformed request"]))
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
            if buffer.count - bodyStart < length {
                self.receive(connection, buffer: buffer)
                return
            }
            let body = Data(buffer[bodyStart..<min(bodyStart + length, buffer.count)])
            let target = requestLine.count > 1 ? requestLine[1] : "/"
            let components = URLComponents(string: "http://localhost" + target)
            let query = Dictionary(uniqueKeysWithValues: (components?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
            let request = DaemonRequest(
                method: requestLine.first ?? "GET",
                path: components?.path ?? target,
                query: query,
                headers: headers,
                body: body
            )
            Task {
                let response = await self.route(request)
                self.respond(connection, response)
            }
        }
    }

    private func authorized(_ request: DaemonRequest) -> Bool {
        if request.query["token"] == token { return true }
        return request.headers["authorization"] == "Bearer \(token)"
    }

    private func route(_ request: DaemonRequest) async -> DaemonResponse {
        guard authorized(request) else {
            return .json(401, ["ok": false, "error": "Missing or invalid remote token"])
        }
        if request.method == "GET", request.path == "/" {
            return .html(Self.html)
        }
        if request.method == "GET", request.path == "/health" {
            return .json(200, ["ok": true, "daemon": "talos-remote", "port": port])
        }
        do {
            switch (request.method, request.path) {
            case ("GET", "/api/state"):
                let info = try await proxy.dictionary("initialize")
                let sessions = (try await proxy.rpc("session.list") as? [[String: Any]]) ?? []
                let approval = try await proxy.rpc("approval.pending")
                return .json(200, [
                    "ok": true,
                    "info": info,
                    "sessions": sessions,
                    "approval": approval ?? NSNull(),
                ])
            case ("POST", "/api/session"):
                let body = decode(request.body)
                let result = try await proxy.dictionary("session.create", params: [
                    "title": (body["title"] as? String) ?? "Remote Task",
                ])
                return .json(200, ["ok": true, "result": result])
            case ("GET", "/api/session"):
                guard let sessionID = request.query["sessionId"] else {
                    return .json(400, ["ok": false, "error": "sessionId is required"])
                }
                let result = try await proxy.dictionary("session.get", params: ["sessionId": sessionID])
                return .json(200, ["ok": true, "session": result])
            case ("POST", "/api/prompt"):
                let body = decode(request.body)
                guard let sessionID = body["sessionId"] as? String,
                      let text = body["text"] as? String else {
                    return .json(400, ["ok": false, "error": "sessionId and text are required"])
                }
                let result = try await proxy.dictionary("session.prompt", params: [
                    "sessionId": sessionID,
                    "text": text,
                ])
                return .json(200, ["ok": true, "result": result])
            case ("POST", "/api/stop"):
                let body = decode(request.body)
                guard let sessionID = body["sessionId"] as? String else {
                    return .json(400, ["ok": false, "error": "sessionId is required"])
                }
                let result = try await proxy.dictionary("session.cancel", params: ["sessionId": sessionID])
                return .json(200, ["ok": true, "result": result])
            case ("GET", "/api/approval"):
                let result = try await proxy.rpc("approval.pending")
                return .json(200, ["ok": true, "approval": result ?? NSNull()])
            case ("POST", "/api/approval"):
                let body = decode(request.body)
                guard let approvalID = body["approvalId"] as? String,
                      let decision = body["decision"] as? String else {
                    return .json(400, ["ok": false, "error": "approvalId and decision are required"])
                }
                let result = try await proxy.dictionary("approval.resolve", params: [
                    "approvalId": approvalID,
                    "decision": decision,
                ])
                return .json(200, ["ok": true, "result": result])
            default:
                return .json(404, ["ok": false, "error": "Unknown path"])
            }
        } catch {
            return .json(502, ["ok": false, "error": error.localizedDescription])
        }
    }

    private func decode(_ data: Data) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    private func respond(_ connection: NWConnection, _ response: DaemonResponse) {
        let reason: String
        switch response.status {
        case 200: reason = "OK"
        case 400: reason = "Bad Request"
        case 401: reason = "Unauthorized"
        case 404: reason = "Not Found"
        case 502: reason = "Bad Gateway"
        default: reason = "Error"
        }
        let header = "HTTP/1.1 \(response.status) \(reason)\r\nContent-Type: \(response.contentType)\r\nContent-Length: \(response.body.count)\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
        var data = Data(header.utf8)
        data.append(response.body)
        connection.send(content: data, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private static let html = #"""
    <!doctype html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
    <title>Talos Remote</title>
    <style>
    :root{color-scheme:light dark;--bronze:#cc8745}
    *{box-sizing:border-box}
    body{margin:0;font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#111;color:#eee}
    header{position:sticky;top:0;background:#181818;border-bottom:1px solid #333;padding:12px 14px;display:flex;gap:10px;align-items:center}
    header h1{font-size:17px;margin:0}
    .pill{margin-left:auto;font-size:11px;padding:4px 8px;border-radius:999px;background:#2a2a2a;color:#aaa}
    main{padding:14px;max-width:820px;margin:auto}
    select,textarea,button,input{font:inherit}
    select,textarea,input{width:100%;border:1px solid #333;background:#1a1a1a;color:#eee;border-radius:9px;padding:9px}
    textarea{min-height:86px;resize:vertical;margin-top:8px}
    .row{display:flex;gap:8px;margin-top:8px}
    button{border:0;border-radius:9px;padding:9px 12px;background:#2a2a2a;color:#eee}
    button.primary{background:var(--bronze);color:#151515;font-weight:600}
    button.stop{background:#9c2f2f}
    #transcript{display:flex;flex-direction:column;gap:10px;margin:14px 0}
    .msg{border-radius:11px;padding:10px 12px;white-space:pre-wrap;word-break:break-word;line-height:1.35}
    .user{background:#2b2117;border:1px solid #4a3522;align-self:flex-end;max-width:90%}
    .assistant{background:#1c1c1c;border:1px solid #333;max-width:95%}
    .tool{background:#151f1a;border:1px solid #2b4436;font:12px ui-monospace,monospace;max-height:180px;overflow:auto}
    .approval{background:#1c1a12;border:1px solid #5c4a1d;border-radius:12px;padding:12px;margin:12px 0}
    .muted{color:#888;font-size:12px}
    </style>
    </head>
    <body>
    <header><h1>Talos Remote</h1><span id="model" class="pill">…</span></header>
    <main>
      <div class="row"><select id="sessions"></select><button id="new">New</button></div>
      <div id="approval"></div>
      <div id="transcript"><div class="muted">Loading…</div></div>
      <textarea id="prompt" placeholder="Message Talos…"></textarea>
      <div class="row"><button id="send" class="primary">Send</button><button id="stop" class="stop">Stop</button><button id="refresh">Refresh</button></div>
      <p class="muted">Talos remote daemon · token-authenticated · local network only unless started with --lan</p>
    </main>
    <script>
    const params = new URLSearchParams(location.search);
    const token = params.get('token') || sessionStorage.getItem('talosRemoteToken') || '';
    if (params.get('token')) sessionStorage.setItem('talosRemoteToken', params.get('token'));
    let sessionId = localStorage.getItem('talosSession') || null;
    let busy = false;
    async function api(path, options={}) {
      const headers = Object.assign({'Authorization':'Bearer '+token}, options.headers||{});
      if (options.body) headers['Content-Type']='application/json';
      const res = await fetch(path, Object.assign({}, options, {headers}));
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP '+res.status));
      return data;
    }
    function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
    async function refresh() {
      try {
        const state = await api('/api/state');
        document.getElementById('model').textContent = (state.info && state.info.model) || 'Talos';
        const sel = document.getElementById('sessions');
        const sessions = state.sessions || [];
        const current = sessionId || (sessions[0] && sessions[0].id);
        sel.innerHTML = sessions.map(s => `<option value="${s.id}" ${s.id===current?'selected':''}>${esc(s.title)}</option>`).join('');
        sessionId = current;
        if (sessionId) localStorage.setItem('talosSession', sessionId);
        renderApproval(state.approval);
        if (sessionId) {
          const detail = await api('/api/session?sessionId='+encodeURIComponent(sessionId));
          renderSession(detail.session);
        }
      } catch (error) {
        document.getElementById('transcript').innerHTML = `<div class="muted">${esc(error.message)}</div>`;
      }
    }
    function renderApproval(a) {
      const box = document.getElementById('approval');
      if (!a || !a.approvalId) { box.innerHTML=''; return; }
      box.innerHTML = `<div class="approval"><strong>Approval needed</strong><div class="muted">${esc(a.toolName)}: ${esc(a.summary)}</div>
        <div class="row"><button class="primary" onclick="resolve('allow')">Allow</button><button onclick="resolve('deny')">Deny</button></div></div>`;
    }
    function renderSession(session) {
      if (!session) return;
      const messages = session.messages || [];
      const box = document.getElementById('transcript');
      box.innerHTML = messages.map(m => {
        const cls = m.role==='user'?'user':(m.role==='assistant'?'assistant':'tool');
        return `<div class="msg ${cls}">${esc(m.text || (m.isStreaming?'…':''))}</div>`;
      }).join('') || '<div class="muted">No messages yet.</div>';
      window.scrollTo(0, document.body.scrollHeight);
    }
    async function send() {
      const text = document.getElementById('prompt').value.trim();
      if (!text || !sessionId) return;
      document.getElementById('prompt').value='';
      try { await api('/api/prompt',{method:'POST',body:JSON.stringify({sessionId,text})}); refresh(); }
      catch(e){ alert(e.message); }
    }
    async function stop() {
      if (!sessionId) return;
      try { await api('/api/stop',{method:'POST',body:JSON.stringify({sessionId})}); refresh(); }
      catch(e){ alert(e.message); }
    }
    async function newSession() {
      try {
        const r = await api('/api/session',{method:'POST',body:JSON.stringify({title:'Remote Task'})});
        sessionId = r.result && r.result.sessionId;
        if (sessionId) localStorage.setItem('talosSession', sessionId);
        refresh();
      } catch(e){ alert(e.message); }
    }
    async function resolve(decision) {
      try {
        const a = await api('/api/approval');
        if (a.approval && a.approval.approvalId) {
          await api('/api/approval',{method:'POST',body:JSON.stringify({approvalId:a.approval.approvalId,decision})});
        }
        refresh();
      } catch(e){ alert(e.message); }
    }
    document.getElementById('send').onclick=send;
    document.getElementById('stop').onclick=stop;
    document.getElementById('new').onclick=newSession;
    document.getElementById('refresh').onclick=refresh;
    document.getElementById('sessions').onchange=(e)=>{sessionId=e.target.value;localStorage.setItem('talosSession',sessionId);refresh();};
    document.getElementById('prompt').addEventListener('keydown',(e)=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')send();});
    refresh();
    setInterval(refresh, 1500);
    </script>
    </body>
    </html>
    """#
}
