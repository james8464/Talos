import SwiftUI
import AppKit
import UniformTypeIdentifiers

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
                .inspectorColumnWidth(min: 230, ideal: 270, max: 340)
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                ProjectMenu()
            }
            ToolbarItem(placement: .navigation) {
                Button {
                    model.newSession()
                } label: {
                    Label("New Task", systemImage: "square.and.pencil")
                }
                .help("New Task (⌘N)")
            }
            ToolbarItemGroup(placement: .primaryAction) {
                GitButton()
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
        .sheet(item: $model.pendingApproval) { request in
            ApprovalSheet(request: request) { decision in
                model.resolveApproval(decision)
            }
        }
        .sheet(isPresented: $model.showProjectBrowser) {
            ProjectBrowserView()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environmentObject(model.usage)
        }
        .sheet(isPresented: $model.showGitPanel) {
            GitPanelView()
                .environmentObject(model)
        }
        .sheet(item: $model.renameTarget) { session in
            RenameSheet(session: session) { title in
                model.commitRename(session.id, title: title)
            }
        }
    }
}

private struct ProjectMenu: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Menu {
            Section("Projects") {
                ForEach(model.projects.sorted) { project in
                    Button {
                        model.openProject(project)
                    } label: {
                        if project.path == model.currentProject?.path {
                            Label(project.name, systemImage: "checkmark")
                        } else {
                            Text(project.name)
                        }
                    }
                }
            }
            Divider()
            Button("Open Folder…") { model.chooseProject() }
            Button("Browse Files…") { model.showProjectBrowser = true }
            Button("Review Changes…") { model.showGitPanel = true }
            Divider()
            Button("Reveal in Finder") {
                NSWorkspace.shared.activateFileViewerSelecting([model.projectURL])
            }
        } label: {
            Label(model.currentProject?.name ?? "Project", systemImage: "folder")
        }
        .help(model.projectURL.path)
    }
}

private struct GitButton: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Button {
            model.showGitPanel = true
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.triangle.branch")
                if let branch = model.gitStatus.branch {
                    Text(branch)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .frame(maxWidth: 100, alignment: .leading)
                } else {
                    Text("Changes")
                }
                if !model.gitStatus.files.isEmpty {
                    Text("\(model.gitStatus.files.count)")
                        .font(.caption2.monospacedDigit())
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(Brand.bronze.opacity(0.25)))
                }
            }
            .font(.caption)
        }
        .buttonStyle(.borderless)
        .help("Review working tree changes")
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
                            Button("Rename…") { model.beginRename(session.id) }
                            Button("Fork") { model.fork(session.id) }
                            Button("Archive") { model.archive(session.id) }
                            Divider()
                            Button("Delete", role: .destructive) {
                                model.delete(session.id)
                            }
                        }
                }
            }

            if !model.archivedSessions.isEmpty {
                Section("Archived") {
                    if model.showArchived {
                        ForEach(model.archivedSessions) { session in
                            SidebarRow(session: session)
                                .tag(session.id)
                                .opacity(0.7)
                                .contextMenu {
                                    Button("Unarchive") { model.unarchive(session.id) }
                                    Button("Rename…") { model.beginRename(session.id) }
                                    Button("Fork") { model.fork(session.id) }
                                    Divider()
                                    Button("Delete", role: .destructive) {
                                        model.delete(session.id)
                                    }
                                }
                        }
                    } else {
                        Button("Show \(model.archivedSessions.count) Archived") {
                            model.showArchived = true
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    if model.showArchived {
                        Button("Hide Archived") {
                            model.showArchived = false
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .searchable(text: $model.search, placement: .sidebar, prompt: "Search Tasks")
        .overlay {
            if model.filteredSessions.isEmpty && model.archivedSessions.isEmpty {
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

    var body: some View {
        Button {
            model.showModelPicker.toggle()
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
        .popover(isPresented: $model.showModelPicker, arrowEdge: .bottom) {
            ModelPickerPopover(isPresented: $model.showModelPicker)
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
    @EnvironmentObject private var model: AppModel

    @ViewBuilder
    var body: some View {
        if model.modelCapabilities.reasoningOptions.isEmpty {
            EmptyView()
        } else {
            Menu {
                Section("Reasoning") {
                    ForEach(model.modelCapabilities.reasoningOptions) { option in
                        Button {
                            model.setReasoning(option.id)
                        } label: {
                            if option.id == model.reasoningSelection {
                                Label(option.title, systemImage: "checkmark")
                            } else {
                                Text(option.title)
                            }
                        }
                    }
                }
                Divider()
                Text(selectedDetail)
            } label: {
                Label(model.reasoningTitle, systemImage: "brain")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("Reasoning — \(selectedDetail)")
        }
    }

    private var selectedDetail: String {
        model.modelCapabilities.reasoningOptions
            .first(where: { $0.id == model.reasoningSelection })?
            .detail ?? model.modelCapabilities.summary
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
        .help("Provider and tool settings")
    }
}

private struct ProviderPopover: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Binding var isPresented: Bool

    private var enabledTools: Int {
        [settings.enableShell, settings.enableFileEdits, settings.enableComputerUse,
         settings.enableWeb, settings.enablePlan].filter { $0 }.count
    }

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

            LabeledContent("Reasoning") {
                Text(model.reasoningTitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            LabeledContent("Tools") {
                Text("\(enabledTools) enabled")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(model.choice.provider.privacyNote)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider()

            HStack {
                SettingsLink {
                    Text("Settings…")
                }
                Spacer()
                Button("Refresh") {
                    Task {
                        await model.refreshChoices()
                        await model.refreshCapabilities()
                    }
                }
            }
        }
        .padding(14)
        .frame(width: 360)
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
    @State private var attachments: [URL] = []
    @State private var importing = false

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            if let session = model.selected, !session.queue.isEmpty {
                QueueStrip(session: session)
            }

            VStack(spacing: 8) {
                if !attachments.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(attachments, id: \.self) { url in
                            AttachmentPill(url: url) {
                                attachments.removeAll { $0 == url }
                            }
                        }
                        Spacer()
                    }
                }

                if !slashMatches.isEmpty {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(slashMatches) { command in
                            Button {
                                draft = ""
                                model.submit(command.id)
                            } label: {
                                HStack(spacing: 8) {
                                    Text(command.id)
                                        .font(.system(.callout, design: .monospaced))
                                    Text(command.detail)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .padding(.vertical, 2)
                        }
                    }
                    .padding(6)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(Color(nsColor: .textBackgroundColor))
                    )
                }

                TextField("Send a message…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .lineLimit(1...12)
                    .focused(composerFocused)
                    .onSubmit(send)
                    .accessibilityLabel("Message")

                HStack(spacing: 7) {
                    Button {
                        importing = true
                    } label: {
                        Image(systemName: "paperclip")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .help("Attach files")

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
                Text(model.contextLabel)
                    .foregroundStyle(contextColor)
                Text("·")
                Text(model.isStreaming ? "Return to queue" : "Return to send")
                Text("·")
                Text("/ for commands")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 26)
            .padding(.vertical, 7)
        }
        .background(.bar)
        .fileImporter(
            isPresented: $importing,
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { result in
            if case .success(let urls) = result {
                attachments.append(contentsOf: urls)
            }
        }
    }

    private var sendControl: some View {
        HStack(spacing: 5) {
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
            }

            Button {
                send()
            } label: {
                Image(systemName: model.isStreaming ? "text.badge.plus" : "arrow.up.circle.fill")
                    .font(.system(size: 22))
            }
            .buttonStyle(.borderless)
            .foregroundStyle(canSend ? Brand.bronze : Color.secondary)
            .disabled(!canSend)
            .help(model.isStreaming ? "Queue message (Return)" : "Send (Return)")
        }
    }

    private var slashMatches: [SlashCommand] {
        guard draft.hasPrefix("/"), !draft.contains("\n"), !draft.contains(" ") else { return [] }
        if draft == "/" { return SlashCommands.all }
        return SlashCommands.all.filter { $0.id.hasPrefix(draft.lowercased()) }
    }

    private var contextColor: Color {
        guard let fraction = model.contextUsage.fraction else { return .secondary }
        if fraction >= 0.9 { return .red }
        if fraction >= 0.75 { return .orange }
        return .secondary
    }

    private func send() {
        guard canSend else { return }
        let text = draft
        let paths = attachments.map(\.path)
        draft = ""
        attachments = []
        model.submit(text, attachments: paths)
    }
}

private struct QueueStrip: View {
    @EnvironmentObject private var model: AppModel
    let session: Session

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Label("Queued", systemImage: "clock.arrow.circlepath")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Clear") { model.clearQueue() }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
            ForEach(session.queue) { item in
                HStack(spacing: 6) {
                    Text(item.text)
                        .font(.callout)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Button {
                        model.removeQueued(item.id)
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .help("Remove queued message")
                }
            }
        }
        .padding(.horizontal, 26)
        .padding(.top, 8)
    }
}

private struct AttachmentPill: View {
    let url: URL
    var remove: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 5) {
            if let image = NSImage(contentsOf: url) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 22, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 3))
            } else {
                Image(systemName: "doc")
            }
            Text(url.lastPathComponent)
                .lineLimit(1)
                .font(.caption)
            if let remove {
                Button {
                    remove()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
        )
        .help(url.path)
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
        .onChange(of: model.pendingDraft) {
            guard let text = model.pendingDraft else { return }
            draft = text
            model.pendingDraft = nil
            composerFocused.wrappedValue = true
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    if session.messages.isEmpty {
                        EmptyTask()
                    }
                    ForEach(visibleMessages) { message in
                        MessageRow(
                            message: message,
                            onEdit: message.role == .user ? {
                                model.editMessage(session.id, message.id)
                            } : nil,
                            onRetry: message.role == .assistant ? {
                                model.retryLastTurn()
                            } : nil
                        )
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

    private var visibleMessages: [ChatMessage] {
        session.messages.filter { message in
            guard message.role != .system else { return false }
            if message.role == .assistant && message.text.isEmpty && !message.isStreaming {
                return false
            }
            return true
        }
    }
}

private struct MessageRow: View {
    @EnvironmentObject private var settings: SettingsStore
    let message: ChatMessage
    var onEdit: (() -> Void)? = nil
    var onRetry: (() -> Void)? = nil
    @State private var hovering = false

    var body: some View {
        if message.role == .tool {
            ToolCard(message: message)
        } else {
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
                        .font(.system(size: settings.transcriptFontSize))
                        .lineSpacing(2)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if !message.attachments.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(message.attachments, id: \.self) { path in
                            AttachmentPill(url: URL(fileURLWithPath: path))
                        }
                    }
                }
            }
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .contextMenu {
                if let onEdit {
                    Button("Edit and Resend") { onEdit() }
                }
                if let onRetry {
                    Button("Retry") { onRetry() }
                }
                if !message.text.isEmpty {
                    Button("Copy") { copy() }
                }
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

private struct ToolCard: View {
    @EnvironmentObject private var settings: SettingsStore
    let message: ChatMessage
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 7) {
                Image(systemName: symbol)
                    .foregroundStyle(.secondary)
                Text(message.toolName ?? "Tool")
                    .font(.callout.weight(.medium))
                Spacer()
                Label(statusTitle, systemImage: statusSymbol)
                    .font(.caption)
                    .foregroundStyle(statusColor)
            }

            if let path = message.attachmentPath {
                AttachmentPreview(path: path)
            }

            if !message.text.isEmpty {
                DisclosureGroup(isExpanded: $expanded) {
                    Text(message.text)
                        .font(.system(size: max(11, settings.transcriptFontSize - 1), design: .monospaced))
                        .lineSpacing(1)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 6)
                } label: {
                    Text(expanded ? "Hide output" : "Show output")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color(nsColor: .separatorColor), lineWidth: 1)
        )
    }

    private var status: ToolStatus { message.toolStatus ?? .succeeded }

    private var statusTitle: String {
        switch status {
        case .succeeded: return "Done"
        case .failed: return "Failed"
        case .denied: return "Denied"
        }
    }

    private var statusSymbol: String {
        switch status {
        case .succeeded: return "checkmark.circle.fill"
        case .failed: return "xmark.octagon.fill"
        case .denied: return "hand.raised.fill"
        }
    }

    private var statusColor: Color {
        switch status {
        case .succeeded: return .green
        case .failed: return .red
        case .denied: return .orange
        }
    }

    private var symbol: String {
        switch message.toolName {
        case "shell": return "terminal"
        case "read_file": return "doc.text"
        case "write_file": return "square.and.pencil"
        case "apply_patch": return "doc.badge.gearshape"
        case "computer": return "cursorarrow.rays"
        case "web_fetch", "web_search": return "safari"
        case "view_image": return "photo"
        case "update_plan": return "checklist"
        default: return "wrench.and.screwdriver"
        }
    }
}

private struct AttachmentPreview: View {
    let path: String

    private var url: URL { URL(fileURLWithPath: path) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let image = NSImage(contentsOf: url) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 240)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .onTapGesture { NSWorkspace.shared.open(url) }
            }

            HStack(spacing: 8) {
                Image(systemName: imageSymbol)
                Text(url.lastPathComponent)
                    .font(.caption)
                    .lineLimit(1)
                Spacer()
                Button("Open") { NSWorkspace.shared.open(url) }
                    .buttonStyle(.borderless)
                Button("Reveal") {
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
                .buttonStyle(.borderless)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private var imageSymbol: String {
        NSImage(contentsOf: url) == nil ? "doc" : "photo"
    }
}

private struct EmptyTask: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Group {
                    if let icon = Brand.icon {
                        Image(nsImage: icon)
                            .resizable()
                    } else {
                        Image(systemName: "circle.hexagongrid.fill")
                            .resizable()
                            .foregroundStyle(Brand.bronze)
                    }
                }
                .frame(width: 40, height: 40)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Talos")
                        .font(.title3.weight(.semibold))
                    Text("Local and hosted models, one workspace")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text("Choose a model below, then ask a question or describe what you want to build. Talos can run shell commands, edit files, search the web, and use the desktop with your approval.")
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: 560, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 36)
    }
}

private struct RenameSheet: View {
    let session: Session
    let save: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var title: String

    init(session: Session, save: @escaping (String) -> Void) {
        self.session = session
        self.save = save
        _title = State(initialValue: session.title)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rename Task")
                .font(.headline)
            TextField("Task name", text: $title)
                .textFieldStyle(.roundedBorder)
                .onSubmit(submit)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Save", action: submit)
                    .keyboardShortcut(.defaultAction)
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 380)
    }

    private func submit() {
        save(title)
        dismiss()
    }
}

private struct ApprovalSheet: View {
    let request: ApprovalRequest
    let resolve: (ApprovalDecision) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Allow \(request.toolName)?", systemImage: "exclamationmark.shield")
                .font(.headline)
            Text(request.summary)
                .font(.callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            Text("This tool can affect files or the desktop. Talos will run it only after you allow it.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("Deny", role: .cancel) { resolve(.deny) }
                Spacer()
                Button("Allow Once") { resolve(.allowOnce) }
                Button("Always Allow for This Task") { resolve(.allowForTask) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 500)
        .interactiveDismissDisabled()
    }
}

private struct Inspector: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var usage: UsageStore

    var body: some View {
        Form {
            Section("Project") {
                LabeledContent("Name", value: model.currentProject?.name ?? "Project")
                LabeledContent("Folder") {
                    Text(model.projectURL.path)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .foregroundStyle(.secondary)
                }
                if let branch = model.gitStatus.branch {
                    LabeledContent("Branch", value: branch)
                }
                LabeledContent("Changes", value: model.gitStatus.files.isEmpty ? "Clean" : "\(model.gitStatus.files.count)")
            }

            Section("Model") {
                LabeledContent("Provider", value: model.choice.provider.title)
                LabeledContent("Model", value: model.choice.id)
                LabeledContent("Reasoning", value: model.reasoningTitle)
                LabeledContent("Context", value: model.contextUsage.label)
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
                Text(model.modelCapabilities.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Tools") {
                toolRow("Shell", symbol: "terminal", enabled: settings.enableShell)
                toolRow("File edits", symbol: "doc.badge.gearshape", enabled: settings.enableFileEdits)
                toolRow("Computer use", symbol: "cursorarrow.rays", enabled: settings.enableComputerUse)
                toolRow("Web", symbol: "safari", enabled: settings.enableWeb)
                toolRow("Plan", symbol: "checklist", enabled: settings.enablePlan)
                LabeledContent("Workspace") {
                    Text(settings.workspacePath)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .foregroundStyle(.secondary)
                }
                LabeledContent("Sandbox", value: settings.sandboxMode.title)
                LabeledContent("Approvals", value: settings.approvalPolicy == .onRequest ? "On request" : "Never")
            }

            if let plan = model.selected?.plan, !plan.isEmpty {
                Section("Plan") {
                    ForEach(plan) { step in
                        Label {
                            Text(step.title)
                                .strikethrough(step.status == .completed)
                        } icon: {
                            Image(systemName: step.status.symbol)
                                .foregroundStyle(step.status == .completed ? .green : .secondary)
                        }
                    }
                }
            }

            Section("Task") {
                LabeledContent("Messages", value: "\(model.selected?.messages.count ?? 0)")
                LabeledContent("State", value: model.isStreaming ? "Running" : "Idle")
                if let session = model.selected {
                    LabeledContent("Tokens", value: TokenEstimator.format(usage.tokens(for: session.id).total))
                    LabeledContent("Created") {
                        Text(session.createdAt, format: .dateTime.month().day().hour().minute())
                            .foregroundStyle(.secondary)
                    }
                }
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

    private func toolRow(_ name: String, symbol: String, enabled: Bool) -> some View {
        LabeledContent {
            Text(enabled ? "Enabled" : "Off")
                .foregroundStyle(.secondary)
        } label: {
            Label(name, systemImage: symbol)
        }
    }
}
