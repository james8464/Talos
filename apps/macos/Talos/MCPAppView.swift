import SwiftUI
import WebKit
import AppKit
import TalosToolParser

struct MCPAppView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let app: MCPUIApp

    @State private var reloadToken = 0
    @State private var loading = true
    @State private var loadError: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Label(app.title, systemImage: "rectangle.inset.filled.and.person.filled")
                    .font(.headline)
                Text(app.serverName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Label("Sandboxed web view", systemImage: "lock.shield")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .help("This app runs in an isolated web view. External links open in your browser.")
                    .accessibilityLabel("Sandboxed web view. External links open in your browser.")
                Spacer()
                Button("Reload") {
                    loading = true
                    loadError = nil
                    reloadToken += 1
                }
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(12)
            Divider()

            ZStack {
                MCPWebView(
                    html: app.html,
                    title: app.title,
                    reloadToken: reloadToken
                ) { isNew in
                    if isNew {
                        loading = true
                        loadError = nil
                    } else {
                        loading = false
                    }
                } onFailure: { message in
                    loading = false
                    loadError = message
                } onToolCall: { name, arguments in
                    await model.callMCPAppTool(name, serverName: app.serverName, arguments: arguments)
                }

                if loading {
                    ProgressView("Loading \(app.title)…")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                } else if let loadError {
                    ContentUnavailableView {
                        Label("App Failed to Load", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(loadError)
                    } actions: {
                        Button("Reload") {
                            loading = true
                            self.loadError = nil
                            reloadToken += 1
                        }
                    }
                }
            }
        }
        .frame(width: 860, height: 680)
    }
}

private struct MCPWebView: NSViewRepresentable {
    let html: String
    let title: String
    let reloadToken: Int
    let onStateChange: @MainActor (Bool) -> Void
    let onFailure: @MainActor (String) -> Void
    let onToolCall: @MainActor (String, [String: Any]) async -> Result<String, Error>

    func makeCoordinator() -> Coordinator {
        Coordinator(
            title: title,
            onStateChange: onStateChange,
            onFailure: onFailure,
            onToolCall: onToolCall
        )
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        configuration.allowsAirPlayForMediaPlayback = false
        configuration.preferences.isElementFullscreenEnabled = false

        let controller = WKUserContentController()
        controller.add(context.coordinator, name: "talos")
        controller.addUserScript(WKUserScript(
            source: Self.frameSandboxScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        controller.addUserScript(WKUserScript(
            source: Self.bridgeScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        configuration.userContentController = controller

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsLinkPreview = false
        webView.setAccessibilityLabel("\(title) app, sandboxed web view")
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

    private static let frameSandboxScript = """
    (function () {
        function harden(frame) {
            if (!frame || frame.tagName !== 'IFRAME') { return; }
            try {
                frame.setAttribute('sandbox', 'allow-scripts allow-forms');
            } catch (error) {}
        }
        const observer = new MutationObserver(function (records) {
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (!node || node.nodeType !== 1) { continue; }
                    if (node.tagName === 'IFRAME') { harden(node); }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('iframe').forEach(harden);
                    }
                }
            }
        });
        observer.observe(document, { childList: true, subtree: true });
        document.addEventListener('DOMContentLoaded', function () {
            document.querySelectorAll('iframe').forEach(harden);
        });
    })();
    """

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
    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
        let title: String
        let onStateChange: @MainActor (Bool) -> Void
        let onFailure: @MainActor (String) -> Void
        let onToolCall: @MainActor (String, [String: Any]) async -> Result<String, Error>
        weak var webView: WKWebView?
        var reloadToken = 0

        init(
            title: String,
            onStateChange: @escaping @MainActor (Bool) -> Void,
            onFailure: @escaping @MainActor (String) -> Void,
            onToolCall: @escaping @MainActor (String, [String: Any]) async -> Result<String, Error>
        ) {
            self.title = title
            self.onStateChange = onStateChange
            self.onFailure = onFailure
            self.onToolCall = onToolCall
        }

        nonisolated func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            let frameURL = message.frameInfo.request.url?.absoluteString
            let mainURL = MainActor.assumeIsolated { webView?.url?.absoluteString }
            guard frameURL == nil || frameURL == mainURL else { return }
            guard message.name == "talos",
                  message.frameInfo.isMainFrame,
                  let body = message.body as? [String: Any],
                  body["type"] as? String == "callTool",
                  let id = body["id"] as? NSNumber,
                  let name = body["name"] as? String,
                  !name.isEmpty,
                  name.count <= 128 else {
                return
            }
            let arguments = body["arguments"] as? [String: Any] ?? [:]
            guard let encoded = try? JSONSerialization.data(withJSONObject: arguments),
                  encoded.count <= 256_000 else {
                return
            }
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
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if navigationAction.targetFrame == nil {
                openExternally(url)
                decisionHandler(.cancel)
                return
            }

            switch url.scheme?.lowercased() ?? "" {
            case "about":
                decisionHandler(.allow)
            case "http", "https", "mailto":
                openExternally(url)
                decisionHandler(.cancel)
            default:
                // Block file, data, javascript, and custom schemes from a
                // server-provided app.
                decisionHandler(.cancel)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
        ) {
            decisionHandler(navigationResponse.canShowMIMEType ? .allow : .cancel)
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url {
                openExternally(url)
            }
            return nil
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            onStateChange(true)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            onStateChange(false)
        }

        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            onFailure(error.localizedDescription)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            onFailure(error.localizedDescription)
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptAlertPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping () -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message
            alert.addButton(withTitle: "OK")
            alert.runModal()
            completionHandler()
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptConfirmPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (Bool) -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message
            alert.addButton(withTitle: "OK")
            alert.addButton(withTitle: "Cancel")
            completionHandler(alert.runModal() == .alertFirstButtonReturn)
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptTextInputPanelWithPrompt prompt: String,
            defaultText: String?,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (String?) -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = prompt
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
            field.stringValue = defaultText ?? ""
            alert.accessoryView = field
            alert.addButton(withTitle: "OK")
            alert.addButton(withTitle: "Cancel")
            completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
        }

        func webView(
            _ webView: WKWebView,
            runOpenPanelWith parameters: WKOpenPanelParameters,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping ([URL]?) -> Void
        ) {
            completionHandler(nil)
        }

        private func openExternally(_ url: URL) {
            switch url.scheme?.lowercased() ?? "" {
            case "http", "https", "mailto":
                NSWorkspace.shared.open(url)
            default:
                break
            }
        }
    }
}
