import SwiftUI
import AppKit

struct AppServerSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Server") {
                Toggle("Enable the local app server", isOn: Binding(
                    get: { settings.appServerEnabled },
                    set: { model.setAppServerEnabled($0) }
                ))
                .accessibilityLabel("Enable the local app server")
                TextField("Port", value: $settings.appServerPort, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("App server port")
                    .disabled(settings.appServerEnabled)
                Toggle("Allow connections from the local network", isOn: $settings.appServerAllowLAN)
                    .accessibilityLabel("Allow connections from the local network")
                    .disabled(settings.appServerEnabled)

                AppServerStatusView(server: model.appServer)

                HStack {
                    Text(settings.appServerToken)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Copy Token") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(settings.appServerToken, forType: .string)
                    }
                }
                Text("Headless clients authenticate with Authorization: Bearer <token> or ?token=. The server binds to localhost unless LAN access is enabled. Set TALOS_APP_SERVER=1 to start it at launch for automation.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("JSON-RPC methods") {
                code("POST /rpc")
                code("initialize, ping")
                code("session.create, session.list, session.get")
                code("session.prompt, session.cancel")
                code("approval.pending, approval.resolve")
                Text("Responses are JSON-RPC 2.0. Start a run with session.prompt and watch GET /events?sessionId=… for SSE notifications.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Example") {
                Text("""
                curl -s http://127.0.0.1:8790/rpc \\
                  -H 'Authorization: Bearer <token>' \\
                  -H 'Content-Type: application/json' \\
                  -d '{"jsonrpc":"2.0","id":1,"method":"session.create","params":{"title":"From a client"}}'
                """)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
            }
        }
        .formStyle(.grouped)
        .padding()
    }

    private func code(_ text: String) -> some View {
        Text(text)
            .font(.system(.caption, design: .monospaced))
            .textSelection(.enabled)
    }
}

private struct AppServerStatusView: View {
    @ObservedObject var server: AppServer

    var body: some View {
        LabeledContent("Status", value: server.status)
        if let endpoint = server.endpoint {
            LabeledContent("Endpoint") {
                Text(endpoint)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
    }
}
