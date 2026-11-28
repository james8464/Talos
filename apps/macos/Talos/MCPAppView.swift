import SwiftUI
import WebKit
import AppKit

struct MCPAppView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let app: MCPUIApp

    @State private var reloadToken = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Label(app.title, systemImage: "rectangle.inset.filled.and.person.filled")
                    .font(.headline)
                Text(app.serverName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Reload") { reloadToken += 1 }
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)
            Divider()
            MCPWebView(
                html: app.html,
                title: app.title,
                reloadToken: reloadToken
            ) { name, arguments in
                await model.callMCPAppTool(name, serverName: app.serverName, arguments: arguments)
            }
        }
        .frame(width: 860, height: 680)
    }
}

private struct MCPWebView: NSViewRepresentable {
    let html: String
    let title: String
    let reloadToken: Int
    let onToolCall: @MainActor (String, [String: Any]) async -> Result<String, Error>

    func makeCoordinator() -> Coordinator {
        Coordinator(title: title, onToolCall: onToolCall)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let controller = WKUserContentController()
        controller.add(context.coordinator, name: "talos")
        controller.addUserScript(WKUserScript(
            source: Self.bridgeScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        configuration.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        context.coordinator.webView = webView
        context.coordinator.reloadToken = reloadToken
        webView.loadHTMLString(html, baseURL: nil)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.reloadToken != reloadToken else { return }
        context.coordinator.reloadToken = reloadToken
        webView.loadHTMLString(html, baseURL: nil)
    }

    private static let bridgeScript = """
    window.talos = {
        _seq: 0,
        _pending: {},
        callTool(name, args) {
            return new Promise((resolve, reject) => {
                const id = ++this._seq;
                this._pending[id] = { resolve: resolve, reject: reject };
                window.webkit.messageHandlers.talos.postMessage({
                    type: "callTool", id: id, name: name, arguments: args || {}
                });
            });
        }
    };
    window.__talosResolve = function(id, payload) {
        const entry = window.talos._pending[id];
        if (!entry) { return; }
        delete window.talos._pending[id];
        if (payload && payload.ok) {
            entry.resolve(payload.result);
        } else {
            entry.reject(new Error((payload && payload.error) || "Tool call failed"));
        }
    };
    """

    @MainActor
    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        let title: String
        let onToolCall: @MainActor (String, [String: Any]) async -> Result<String, Error>
        weak var webView: WKWebView?
        var reloadToken = 0

        init(title: String, onToolCall: @escaping @MainActor (String, [String: Any]) async -> Result<String, Error>) {
            self.title = title
            self.onToolCall = onToolCall
        }

        nonisolated func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard let body = message.body as? [String: Any],
                  body["type"] as? String == "callTool",
                  let id = body["id"] as? NSNumber,
                  let name = body["name"] as? String else {
                return
            }
            let arguments = body["arguments"] as? [String: Any] ?? [:]
            Task { @MainActor in
                let result = await onToolCall(name, arguments)
                let payload: [String: Any]
                switch result {
                case .success(let text):
                    payload = ["ok": true, "result": text]
                case .failure(let error):
                    payload = ["ok": false, "error": error.localizedDescription]
                }
                guard let data = try? JSONSerialization.data(withJSONObject: payload),
                      let json = String(data: data, encoding: .utf8) else { return }
                try? await webView?.evaluateJavaScript("window.__talosResolve(\(id.intValue), \(json));")
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if let url = navigationAction.request.url,
               let scheme = url.scheme?.lowercased(),
               scheme == "http" || scheme == "https" {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }
    }
}
