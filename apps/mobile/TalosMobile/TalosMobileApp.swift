import SwiftUI

@main
struct TalosMobileApp: App {
    var body: some Scene {
        WindowGroup {
            MobileRootView()
        }
    }
}

struct MobileRootView: View {
    @AppStorage("remoteServer") private var server = "http://127.0.0.1:8798"
    @AppStorage("remoteToken") private var token = ""
    @State private var client: RemoteClient?
    @State private var model = "Talos"
    @State private var sessions: [MobileSessionSummary] = []
    @State private var selectedID: String?
    @State private var messages: [MobileMessage] = []
    @State private var approval: MobileApproval?
    @State private var prompt = ""
    @State private var error: String?
    @State private var connecting = false

    private var isStreaming: Bool {
        messages.contains { $0.isStreaming }
    }

    private var selectedTitle: String {
        sessions.first(where: { $0.id == selectedID })?.title ?? "Talos Remote"
    }

    var body: some View {
        Group {
            if let client {
                sessionView(client)
            } else {
                setupView
            }
        }
        .tint(Color(red: 0.80, green: 0.53, blue: 0.27))
        .onAppear {
            let environment = ProcessInfo.processInfo.environment
            if let envServer = environment["TALOS_REMOTE_SERVER"], !envServer.isEmpty {
                server = envServer
            }
            if let envToken = environment["TALOS_REMOTE_TOKEN"], !envToken.isEmpty {
                token = envToken
            }
            if client == nil, !server.isEmpty, !token.isEmpty {
                connect()
            }
        }
    }

    private var setupView: some View {
        NavigationStack {
            Form {
                Section("Talos daemon") {
                    TextField("http://192.168.1.10:8798", text: $server)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .accessibilityIdentifier("daemonURLField")
                    SecureField("Remote token", text: $token)
                        .textInputAutocapitalization(.never)
                        .accessibilityIdentifier("daemonTokenField")
                }

                Section {
                    Button(connecting ? "Connecting…" : "Connect") {
                        connect()
                    }
                    .accessibilityIdentifier("connectButton")
                    .disabled(connecting || server.isEmpty || token.isEmpty)
                } footer: {
                    Text("Run TalosRemoteDaemon on the Mac, then open this app on the same network. The daemon token is separate from the Talos app-server token.")
                }

                if let error {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Talos Remote")
        }
    }

    private func sessionView(_ client: RemoteClient) -> some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Menu {
                        ForEach(sessions) { session in
                            Button {
                                selectedID = session.id
                            } label: {
                                if session.id == selectedID {
                                    Label(session.title, systemImage: "checkmark")
                                } else {
                                    Text(session.title)
                                }
                            }
                        }
                        Divider()
                        Button("New Task") { createSession(client) }
                    } label: {
                        Label(selectedTitle, systemImage: "rectangle.stack")
                            .lineLimit(1)
                    }
                    Spacer()
                    Text(model)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .accessibilityIdentifier("modelLabel")
                    Button {
                        clientDisconnect()
                    } label: {
                        Image(systemName: "xmark.circle")
                    }
                    .accessibilityLabel("Disconnect")
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)

                if let approval {
                    approvalCard(approval, client: client)
                }

                if let error {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .padding(.bottom, 6)
                }

                transcript

                composer(client)
            }
            .navigationTitle("Talos Remote")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await refresh(client) }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        createSession(client)
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityIdentifier("newSessionButton")
                }
            }
            .task(id: pollingKey) {
                while !Task.isCancelled, self.client != nil {
                    await refresh(client)
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                }
            }
        }
    }

    private var pollingKey: String {
        "\(client != nil)|\(selectedID ?? "")"
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if messages.isEmpty {
                        Text("No messages yet. Send a prompt below.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.top, 24)
                    }
                    ForEach(messages) { message in
                        bubble(message)
                            .id(message.id)
                    }
                }
                .padding(14)
            }
            .background(Color(.systemGroupedBackground))
            .onChange(of: messages.last?.text) {
                if let id = messages.last?.id {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func bubble(_ message: MobileMessage) -> some View {
        HStack {
            if message.role == "user" { Spacer(minLength: 42) }
            VStack(alignment: .leading, spacing: 4) {
                if message.role == "tool" {
                    Text(message.text)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                } else {
                    Text(message.text.isEmpty && message.isStreaming ? "Thinking…" : message.text)
                        .textSelection(.enabled)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(background(for: message.role), in: RoundedRectangle(cornerRadius: 13))
            .foregroundStyle(message.role == "user" ? .white : .primary)
            if message.role != "user" { Spacer(minLength: 42) }
        }
    }

    private func background(for role: String) -> Color {
        switch role {
        case "user": return Color(red: 0.80, green: 0.53, blue: 0.27)
        case "tool": return Color(.secondarySystemGroupedBackground)
        default: return Color(.secondarySystemGroupedBackground)
        }
    }

    private func approvalCard(_ approval: MobileApproval, client: RemoteClient) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Approval needed", systemImage: "exclamationmark.shield")
                .font(.callout.weight(.semibold))
            Text("\(approval.toolName): \(approval.summary)")
                .font(.caption)
                .lineLimit(4)
            HStack {
                Button("Deny", role: .destructive) {
                    resolve(approval, decision: "deny", client: client)
                }
                .accessibilityIdentifier("denyButton")
                Button("Allow") {
                    resolve(approval, decision: "allow", client: client)
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("allowButton")
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal, 14)
        .padding(.bottom, 8)
    }

    private func composer(_ client: RemoteClient) -> some View {
        VStack(spacing: 8) {
            TextField("Message Talos…", text: $prompt, axis: .vertical)
                .lineLimit(1...5)
                .accessibilityIdentifier("promptField")
                .padding(10)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 10))
            HStack {
                if isStreaming {
                    Button("Stop", role: .destructive) {
                        stop(client)
                    }
                    .accessibilityIdentifier("stopButton")
                }
                Spacer()
                Button("Send") {
                    send(client)
                }
                .accessibilityIdentifier("sendButton")
                .buttonStyle(.borderedProminent)
                .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isStreaming || selectedID == nil)
            }
        }
        .padding(12)
        .background(.bar)
    }

    private func connect() {
        guard let created = RemoteClient(server: server, token: token) else {
            error = "Enter a daemon URL and token."
            return
        }
        connecting = true
        error = nil
        Task {
            do {
                let state = try await created.state()
                await MainActor.run {
                    client = created
                    model = state.model
                    sessions = state.sessions
                    selectedID = state.sessions.first?.id
                    approval = state.approval
                    connecting = false
                    if let id = selectedID {
                        Task { await refresh(created, sessionID: id) }
                    }
                }
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    connecting = false
                }
            }
        }
    }

    private func clientDisconnect() {
        client = nil
        sessions = []
        selectedID = nil
        messages = []
        approval = nil
    }

    private func refresh(_ client: RemoteClient?, sessionID: String? = nil) async {
        guard let client else { return }
        do {
            let state = try await client.state()
            var target = sessionID ?? selectedID ?? state.sessions.first?.id
            if target == nil, let first = state.sessions.first?.id {
                target = first
            }
            var detail: MobileSessionDetail?
            if let target {
                detail = try await client.session(target)
            }
            await MainActor.run {
                model = state.model
                sessions = state.sessions
                approval = state.approval
                if let target {
                    selectedID = target
                    messages = detail?.messages ?? []
                }
                error = nil
            }
        } catch {
            await MainActor.run {
                self.error = error.localizedDescription
            }
        }
    }

    private func createSession(_ client: RemoteClient) {
        Task {
            do {
                let id = try await client.createSession(title: "Remote Task")
                await MainActor.run {
                    selectedID = id
                    messages = []
                }
                await refresh(client, sessionID: id)
            } catch {
                await MainActor.run { self.error = error.localizedDescription }
            }
        }
    }

    private func send(_ client: RemoteClient) {
        guard let selectedID else { return }
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        prompt = ""
        Task {
            do {
                try await client.prompt(sessionID: selectedID, text: text)
                await refresh(client, sessionID: selectedID)
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    self.prompt = text
                }
            }
        }
    }

    private func stop(_ client: RemoteClient) {
        guard let selectedID else { return }
        Task {
            do {
                _ = try await client.stop(sessionID: selectedID)
                await refresh(client, sessionID: selectedID)
            } catch {
                await MainActor.run { self.error = error.localizedDescription }
            }
        }
    }

    private func resolve(_ approval: MobileApproval, decision: String, client: RemoteClient) {
        Task {
            do {
                _ = try await client.resolveApproval(id: approval.approvalId, decision: decision)
                await refresh(client)
            } catch {
                await MainActor.run { self.error = error.localizedDescription }
            }
        }
    }
}
