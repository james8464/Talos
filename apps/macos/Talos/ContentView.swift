import SwiftUI
import AppKit

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var draft = ""
    @FocusState private var composerFocused: Bool

    var body: some View {
        NavigationSplitView(columnVisibility: $model.columnVisibility) {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 220, ideal: 250, max: 300)
        } detail: {
            if let session = model.selected {
                TaskView(session: session, draft: $draft, composerFocused: $composerFocused)
                    .navigationTitle(session.title)
                    .navigationSubtitle(model.isStreaming ? "Working…" : model.choice.display)
            } else {
                ContentUnavailableView(
                    "No Task Selected",
                    systemImage: "square.stack.3d.up",
                    description: Text("Choose a task in the sidebar or create a new one.")
                )
            }
        }
        .inspector(isPresented: $model.showInspector) {
            Inspector()
                .inspectorColumnWidth(min: 220, ideal: 260, max: 320)
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button {
                    model.newSession()
                } label: {
                    Label("New Task", systemImage: "square.and.pencil")
                }
                .help("New Task (⌘N)")
            }
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    model.stop()
                } label: {
                    Label("Stop", systemImage: "stop.circle")
                }
                .disabled(!model.isStreaming)
                .help("Stop (⌘.)")

                Button {
                    model.showInspector.toggle()
                } label: {
                    Label("Details", systemImage: "sidebar.right")
                }
                .help("Show or hide details (⌘⌥I)")
            }
        }
        .alert("Request Failed", isPresented: Binding(
            get: { model.error != nil },
            set: { if !$0 { model.error = nil } }
        )) {
            Button("OK", role: .cancel) { model.error = nil }
        } message: {
            Text(model.error ?? "")
        }
    }
}

private struct Sidebar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        List(selection: $model.selectedID) {
            Section("Tasks") {
                ForEach(model.filteredSessions) { session in
                    SidebarRow(session: session)
                        .tag(session.id)
                        .contextMenu {
                            Button("Delete", role: .destructive) {
                                model.delete(session.id)
                            }
                        }
                }
            }
        }
        .listStyle(.sidebar)
        .searchable(text: $model.search, placement: .sidebar, prompt: "Search Tasks")
        .overlay {
            if model.filteredSessions.isEmpty {
                if model.search.isEmpty {
                    ContentUnavailableView(
                        "No Tasks",
                        systemImage: "square.and.pencil",
                        description: Text("Press ⌘N to start one.")
                    )
                } else {
                    ContentUnavailableView.search(text: model.search)
                }
            }
        }
        .navigationTitle("Talos")
    }
}

private struct SidebarRow: View {
    let session: Session

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(session.title)
                    .font(.body)
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text(session.createdAt, style: .relative)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text(session.preview)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}

private struct ModelPickerButton: View {
    @EnvironmentObject private var model: AppModel
    @State private var showing = false

    var body: some View {
        Button {
            showing.toggle()
        } label: {
            HStack(spacing: 5) {
                Image(systemName: model.choice.provider.symbol)
                    .imageScale(.small)
                Text(model.choice.id)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: 190, alignment: .leading)
                Image(systemName: "chevron.down")
                    .font(.system(size: 8, weight: .semibold))
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .buttonStyle(.borderless)
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            ModelPickerPopover(isPresented: $showing)
        }
        .help("Model — \(model.choice.display)")
    }
}

private struct ModelPickerPopover: View {
    @EnvironmentObject private var model: AppModel
    @Binding var isPresented: Bool
    @State private var query = ""
    @State private var refreshing = false

    private var filtered: [ModelChoice] {
        guard !query.isEmpty else { return model.choices }
        return model.choices.filter { $0.display.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Model")
                        .font(.headline)
                    Text(model.status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Task {
                        refreshing = true
                        await model.refreshChoices()
                        refreshing = false
                    }
                } label: {
                    if refreshing {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                .buttonStyle(.borderless)
                .help("Refresh models")
            }
            .padding(12)

            if model.choices.count > 7 {
                TextField("Search models", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
            }

            if filtered.isEmpty {
                ContentUnavailableView(
                    "No Models",
                    systemImage: "cpu",
                    description: Text("Start Ollama or add a provider key in Settings.")
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(Provider.allCases) { provider in
                        let items = filtered.filter { $0.provider == provider }
                        if !items.isEmpty {
                            Section(provider.title) {
                                ForEach(items) { choice in
                                    modelRow(choice)
                                }
                            }
                        }
                    }
                }
                .listStyle(.inset)
            }

            Divider()

            HStack {
                SettingsLink {
                    Text("Model Settings…")
                }
                Spacer()
                Text("\(model.choices.count) available")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(10)
        }
        .frame(width: 390, height: 480)
    }

    private func modelRow(_ choice: ModelChoice) -> some View {
        Button {
            model.choose(choice)
            isPresented = false
        } label: {
            HStack(spacing: 8) {
                Image(systemName: choice.provider.symbol)
                    .frame(width: 16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(choice.id)
                        .lineLimit(1)
                    Text(detail(for: choice))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                if choice == model.choice {
                    Image(systemName: "checkmark")
                        .foregroundStyle(.tint)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, 2)
    }

    private func detail(for choice: ModelChoice) -> String {
        if choice.provider == .local,
           let found = model.localModels.first(where: { $0.name == choice.id }) {
            return found.detail.isEmpty ? "On this Mac" : found.detail
        }
        return choice.provider.title
    }
}

private struct ReasoningMenu: View {
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Menu {
            Section("Reasoning effort") {
                ForEach(ReasoningEffort.allCases) { effort in
                    Button {
                        settings.reasoning = effort
                    } label: {
                        if effort == settings.reasoning {
                            Label(effort.title, systemImage: "checkmark")
                        } else {
                            Text(effort.title)
                        }
                    }
                }
            }
            Divider()
            Text(settings.reasoning.summary)
        } label: {
            Label(settings.reasoning.title, systemImage: "brain")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Reasoning effort — \(settings.reasoning.summary)")
    }
}

private struct SpeedMenu: View {
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Menu {
            Section("Response speed") {
                ForEach(ResponseSpeed.allCases) { speed in
                    Button {
                        settings.speed = speed
                    } label: {
                        if speed == settings.speed {
                            Label(speed.title, systemImage: "checkmark")
                        } else {
                            Text(speed.title)
                        }
                    }
                }
            }
            Divider()
            Text(settings.speed.summary)
        } label: {
            Label(settings.speed.title, systemImage: "speedometer")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Response speed — \(settings.speed.summary)")
    }
}

private struct ProviderButton: View {
    @EnvironmentObject private var model: AppModel
    @State private var showing = false

    var body: some View {
        Button {
            showing.toggle()
        } label: {
            Image(systemName: model.choice.provider == .local ? "server.rack" : "key")
        }
        .buttonStyle(.borderless)
        .foregroundStyle(.secondary)
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            ProviderPopover(isPresented: $showing)
        }
        .help("Provider settings")
    }
}

private struct ProviderPopover: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Binding var isPresented: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(model.choice.provider.title, systemImage: model.choice.provider.symbol)
                    .font(.headline)
                Spacer()
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            LabeledContent("Endpoint") {
                Text(settings.baseURL(for: model.choice.provider))
                    .font(.caption)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }

            if model.choice.provider != .local {
                LabeledContent("API key") {
                    Text(settings.savedKeys.contains(model.choice.provider) ? "Stored in Keychain" : "Not set")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Text(model.choice.provider.privacyNote)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider()

            HStack {
                SettingsLink {
                    Text("Provider Settings…")
                }
                Spacer()
                Button("Refresh") {
                    Task { await model.refreshChoices() }
                }
            }
        }
        .padding(14)
        .frame(width: 350)
    }

    private var status: String {
        if model.choice.provider == .local {
            return model.localModels.isEmpty ? "Offline" : "\(model.localModels.count) models"
        }
        return settings.savedKeys.contains(model.choice.provider) ? "Ready" : "Key missing"
    }
}

private struct ComposerView: View {
    @EnvironmentObject private var model: AppModel
    @Binding var draft: String
    var composerFocused: FocusState<Bool>.Binding

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !model.isStreaming
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) {
                TextField("Send a message…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .lineLimit(1...12)
                    .focused(composerFocused)
                    .onSubmit(send)
                    .accessibilityLabel("Message")

                HStack(spacing: 7) {
                    ModelPickerButton()
                    Divider().frame(height: 12)
                    ReasoningMenu()
                    SpeedMenu()
                    Spacer(minLength: 4)
                    ProviderButton()
                    sendControl
                }
            }
            .padding(10)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
            )
            .padding(.horizontal, 24)
            .padding(.top, 12)

            HStack(spacing: 4) {
                Image(systemName: model.choice.provider == .local ? "desktopcomputer" : "cloud")
                Text(model.choice.provider == .local ? "Local · this Mac" : "Cloud · \(model.choice.provider.title)")
                Spacer()
                Text("Return to send")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 26)
            .padding(.vertical, 7)
        }
        .background(.bar)
    }

    @ViewBuilder
    private var sendControl: some View {
        if model.isStreaming {
            Button {
                model.stop()
            } label: {
                Image(systemName: "stop.circle.fill")
                    .font(.system(size: 22))
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.secondary)
            .help("Stop (⌘.)")
        } else {
            Button {
                send()
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 22))
            }
            .buttonStyle(.borderless)
            .foregroundStyle(canSend ? Color.accentColor : Color.secondary)
            .disabled(!canSend)
            .help("Send (Return)")
        }
    }

    private func send() {
        guard canSend else { return }
        let text = draft
        draft = ""
        model.send(text)
    }
}

private struct TaskView: View {
    @EnvironmentObject private var model: AppModel
    let session: Session
    @Binding var draft: String
    var composerFocused: FocusState<Bool>.Binding

    var body: some View {
        VStack(spacing: 0) {
            transcript
            Divider()
            ComposerView(draft: $draft, composerFocused: composerFocused)
        }
        .background(Color(nsColor: .textBackgroundColor))
        .onAppear { composerFocused.wrappedValue = true }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    if session.messages.isEmpty {
                        EmptyTask()
                    }
                    ForEach(session.messages.filter { $0.role != .system }) { message in
                        MessageRow(message: message)
                            .id(message.id)
                    }
                }
                .frame(maxWidth: 760, alignment: .leading)
                .padding(.horizontal, 32)
                .padding(.vertical, 28)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: session.messages.last?.text) {
                if let id = session.messages.last?.id {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo(id, anchor: .bottom)
                    }
                }
            }
        }
    }
}

private struct MessageRow: View {
    let message: ChatMessage
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(roleTitle)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer()
                if !message.text.isEmpty {
                    Button {
                        copy()
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .opacity(hovering ? 1 : 0)
                    .help("Copy")
                }
            }

            if message.text.isEmpty && message.isStreaming {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Thinking…")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            } else {
                Text(attributedText)
                    .font(.body)
                    .lineSpacing(2)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .contextMenu {
            if !message.text.isEmpty {
                Button("Copy") { copy() }
            }
        }
    }

    private var roleTitle: String {
        message.role == .user ? "You" : (message.model ?? "Assistant")
    }

    private var attributedText: AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: message.text, options: options)) ?? AttributedString(message.text)
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(message.text, forType: .string)
    }
}

private struct EmptyTask: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("New Task")
                .font(.title2.weight(.semibold))
            Text("Choose a model below, then ask a question or describe what you want to build.")
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: 520, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 36)
    }
}

private struct Inspector: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        Form {
            Section("Model") {
                LabeledContent("Provider", value: model.choice.provider.title)
                LabeledContent("Model", value: model.choice.id)
                LabeledContent("Reasoning", value: settings.reasoning.title)
                LabeledContent("Speed", value: settings.speed.title)
                LabeledContent("Endpoint") {
                    Text(model.choice.provider == .local ? "This Mac" : "Cloud")
                        .foregroundStyle(.secondary)
                }
                if model.choice.provider != .local {
                    LabeledContent("API key") {
                        Text(settings.savedKeys.contains(model.choice.provider) ? "Stored in Keychain" : "Missing")
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section("Task") {
                LabeledContent("Messages", value: "\(model.selected?.messages.count ?? 0)")
                LabeledContent("State", value: model.isStreaming ? "Running" : "Idle")
                if let session = model.selected {
                    LabeledContent("Created") {
                        Text(session.createdAt, format: .dateTime.month().day().hour().minute())
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section("Tools") {
                capability("Chat", symbol: "bubble.left.and.bubble.right")
                capability("Shell", symbol: "terminal", available: false)
                capability("Apply Patch", symbol: "doc.badge.gearshape", available: false)
                capability("Computer Use", symbol: "cursorarrow.rays", available: false)
                capability("Browser", symbol: "safari", available: false)
            }

            Section("Privacy") {
                Text(model.choice.provider.privacyNote)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Details")
    }

    private func capability(_ name: String, symbol: String, available: Bool = true) -> some View {
        LabeledContent {
            Text(available ? "Ready" : "Not yet")
                .foregroundStyle(.secondary)
        } label: {
            Label(name, systemImage: symbol)
        }
    }
}
