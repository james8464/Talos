import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.undoManager) private var undoManager
    @Environment(\.openWindow) private var openWindow
    @Environment(\.layoutDirection) private var layoutDirection
    @State private var draft = ""
    @FocusState private var composerFocused: Bool

    private func registerDeletionUndo(_ session: Session, at index: Int) {
        guard let manager = undoManager ?? NSApp.keyWindow?.undoManager else { return }
        Self.registerDeletionHistory(
            model: model,
            manager: manager,
            session: session,
            index: index,
            restoring: true
        )
    }

    private static func registerDeletionHistory(
        model: AppModel,
        manager: UndoManager,
        session: Session,
        index: Int,
        restoring: Bool
    ) {
        manager.registerUndo(withTarget: model) { target in
            MainActor.assumeIsolated {
                if restoring {
                    target.restoreDeleted(session, at: index)
                } else {
                    target.delete(session.id)
                }
                manager.setActionName("Delete Task")
                registerDeletionHistory(
                    model: target,
                    manager: manager,
                    session: session,
                    index: index,
                    restoring: !restoring
                )
            }
        }
        manager.setActionName("Delete Task")
    }

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
                ContentUnavailableView {
                    if let icon = Brand.icon {
                        Image(nsImage: icon)
                            .resizable()
                            .frame(width: 52, height: 52)
                    } else {
                        Image(systemName: "circle.hexagongrid.fill")
                            .font(.system(size: 46))
                            .foregroundStyle(Brand.bronze)
                    }
                } description: {
                    Text("Choose a task in the sidebar, or start a new one.\nPress ⌘K to search tasks and commands.")
                } actions: {
                    Button("New Task") { model.newSession() }
                        .buttonStyle(.borderedProminent)
                    Button("Review Changes…") { model.showGitPanel = true }
                }
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
                Button {
                    model.showQuickSwitcher = true
                } label: {
                    Label("Quick Switch", systemImage: "magnifyingglass")
                }
                .help("Search tasks and commands (⌘K)")

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
        .onAppear {
            WindowBridge.shared.openWindow = openWindow
        }
        .environment(\.layoutDirection, resolvedLayoutDirection)
        .alert("Request Failed", isPresented: Binding(
            get: { model.error != nil },
            set: { if !$0 { model.error = nil } }
        )) {
            Button("OK", role: .cancel) { model.error = nil }
        } message: {
            Text(model.error ?? "")
        }
        .confirmationDialog(
            "Delete “\(model.deleteCandidate?.title ?? "Task")”?",
            isPresented: Binding(
                get: { model.deleteCandidate != nil },
                set: { if !$0 { model.deleteCandidate = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                if let session = model.deleteCandidate {
                    let index = model.sessions.firstIndex(where: { $0.id == session.id }) ?? 0
                    registerDeletionUndo(session, at: index)
                }
                model.confirmDelete()
            }
            Button("Cancel", role: .cancel) { model.deleteCandidate = nil }
        } message: {
            Text("This removes the task and its messages. This cannot be undone.")
        }
        .sheet(item: $model.pendingApproval) { request in
            ApprovalSheet(request: request) { decision in
                model.resolveApproval(decision)
            }
            .environmentObject(model)
            .environmentObject(model.settings)
            .environment(\.layoutDirection, resolvedLayoutDirection)
        }
        .sheet(isPresented: $model.showRecentDocuments) {
            RecentDocumentsView()
                .environmentObject(model)
                .environment(\.layoutDirection, resolvedLayoutDirection)
        }
        .sheet(isPresented: $model.showHelp) {
            HelpView()
                .environmentObject(model)
                .environment(\.layoutDirection, resolvedLayoutDirection)
        }
        .sheet(isPresented: $model.showQuickSwitcher) {
            QuickSwitcher()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environment(\.layoutDirection, resolvedLayoutDirection)
        }
        .sheet(isPresented: $model.showProjectBrowser) {
            ProjectBrowserView()
                .environmentObject(model)
                .environmentObject(model.settings)
                .environmentObject(model.usage)
        }
        .task {
            if ProcessInfo.processInfo.environment["TALOS_OPEN_GIT_PANEL"] == "1" {
                model.showGitPanel = true
            }
        }
        .sheet(isPresented: $model.showGitPanel) {
            GitPanelView()
                .environmentObject(model)
                .environmentObject(model.settings)
        }
        .sheet(item: $model.renameTarget) { session in
            RenameSheet(session: session) { title in
                model.commitRename(session.id, title: title)
            }
        }
        .sheet(item: $model.editingProvider) { provider in
            ProviderEditor(provider: provider)
                .environmentObject(model)
                .environmentObject(model.settings)
        }
        .sheet(isPresented: $model.showWorktrees) {
            WorktreePanel()
                .environmentObject(model)
        }
        .sheet(isPresented: $model.showWorktreeSheet) {
            NewWorktreeSheet()
                .environmentObject(model)
                .environmentObject(model.settings)
        }
        .sheet(item: $model.mcpElicitation) { request in
            MCPElicitationView(request: request)
                .environmentObject(model)
        }
        .sheet(item: $model.pendingHookTrust) { request in
            HookTrustView(request: request)
                .environmentObject(model)
        }
        .sheet(item: $model.pendingSampling) { request in
            MCPSamplingView(request: request)
                .environmentObject(model)
        }
        .sheet(item: $model.editingMessage) { target in
            MessageEditSheet(target: target)
                .environmentObject(model)
        }
        .sheet(item: $model.activeMCPApp) { app in
            MCPAppView(app: app)
                .environmentObject(model)
        }
    }

    private var resolvedLayoutDirection: LayoutDirection {
        switch ProcessInfo.processInfo.environment["TALOS_LAYOUT_DIRECTION"]?.lowercased() {
        case "rtl": return .rightToLeft
        case "ltr": return .leftToRight
        default:
            let preferred = Locale.preferredLanguages.first ?? "en"
            let language = Locale.Language(identifier: preferred)
            return language.characterDirection == .rightToLeft ? .rightToLeft : layoutDirection
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
            Button("New Worktree…") { model.showWorktreeSheet = true }
            Button("Manage Worktrees…") { model.showWorktrees = true }
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
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.openSettings) private var openSettings

    private struct SessionGroup: Identifiable {
        let id: String
        let title: String
        let sessions: [Session]
    }

    private var groups: [SessionGroup] {
        let sessions = model.filteredSessions
        switch settings.sidebarGrouping {
        case .none:
            guard !sessions.isEmpty else { return [] }
            return [SessionGroup(id: "all", title: String(localized: "Tasks"), sessions: sessions)]
        case .project:
            var buckets: [String: [Session]] = [:]
            for session in sessions {
                let name = session.projectPath.isEmpty
                    ? "No Project"
                    : URL(fileURLWithPath: session.projectPath).lastPathComponent
                buckets[name, default: []].append(session)
            }
            return buckets.keys.sorted().map { name in
                SessionGroup(id: "project-\(name)", title: name, sessions: buckets[name] ?? [])
            }
        case .date:
            let calendar = Calendar.current
            let now = Date()
            let today = calendar.startOfDay(for: now)
            let yesterday = calendar.date(byAdding: .day, value: -1, to: today) ?? today
            let weekAgo = calendar.date(byAdding: .day, value: -7, to: today) ?? today
            var buckets: [String: [Session]] = [:]
            for session in sessions {
                let day = calendar.startOfDay(for: session.updatedAt)
                let key: String
                if day >= today {
                    key = "Today"
                } else if day >= yesterday {
                    key = "Yesterday"
                } else if day >= weekAgo {
                    key = "Previous 7 Days"
                } else {
                    key = "Older"
                }
                buckets[key, default: []].append(session)
            }
            let localizedTitles: [String: String] = [
                "Today": String(localized: "Today"),
                "Yesterday": String(localized: "Yesterday"),
                "Previous 7 Days": String(localized: "Previous 7 Days"),
                "Older": String(localized: "Older"),
            ]
            return ["Today", "Yesterday", "Previous 7 Days", "Older"].compactMap { title in
                guard let rows = buckets[title], !rows.isEmpty else { return nil }
                return SessionGroup(id: title, title: localizedTitles[title] ?? title, sessions: rows)
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(model.currentProject?.name ?? "Talos")
                        .font(.headline)
                        .lineLimit(1)
                    if let branch = model.gitStatus.branch {
                        Label(branch, systemImage: "arrow.triangle.branch")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    } else {
                        Text("AI tools for your Mac")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Menu {
                    Picker("Group by", selection: $settings.sidebarGrouping) {
                        ForEach(SidebarGrouping.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    Divider()
                    Toggle("Show previews", isOn: $settings.sidebarShowPreviews)
                    Toggle("Show message counts", isOn: $settings.sidebarShowCounts)
                    Divider()
                    Toggle("Show archived tasks", isOn: $model.showArchived)
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 13, weight: .medium))
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .help("Customize sidebar")
                .accessibilityLabel("Customize sidebar")

                Button {
                    model.newSession()
                } label: {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 14, weight: .medium))
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .help("New Task (⌘N)")
                .accessibilityLabel("New task")
            }
            .padding(.horizontal, 14)
            .padding(.top, 4)
            .padding(.bottom, 8)

            Button {
                model.newSession()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "plus.circle.fill")
                        .foregroundStyle(Brand.bronze)
                    Text("New Task")
                        .font(.body.weight(.medium))
                    Spacer()
                    Text("⌘N")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .contentShape(Rectangle())
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Brand.bronze.opacity(0.10))
                )
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 10)
            .padding(.bottom, 6)

            List(selection: $model.selectedID) {
                ForEach(groups) { group in
                    Section(group.title) {
                        ForEach(group.sessions) { session in
                            SidebarRow(
                                session: session,
                                isRunning: model.activeRunSessionID == session.id,
                                showsPreview: settings.sidebarShowPreviews,
                                showsCounts: settings.sidebarShowCounts
                            )
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
                }

                if !model.archivedSessions.isEmpty {
                    Section("Archived") {
                        if model.showArchived {
                            ForEach(model.archivedSessions) { session in
                                SidebarRow(
                                    session: session,
                                    isRunning: false,
                                    showsPreview: settings.sidebarShowPreviews,
                                    showsCounts: settings.sidebarShowCounts
                                )
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

            Divider()

            HStack(spacing: 8) {
                Circle()
                    .fill(model.isStreaming ? Color.orange : Brand.bronze.opacity(0.55))
                    .frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 0) {
                    Text(model.choice.id)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Text(model.isStreaming ? "Working…" : model.status)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                Button {
                    openSettings()
                } label: {
                    Image(systemName: "gearshape")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .help("Settings")
                .accessibilityLabel("Open settings")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .navigationTitle("Talos")
    }
}

private struct SidebarRow: View {
    let session: Session
    var isRunning = false
    var showsPreview = true
    var showsCounts = true

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if isRunning {
                    ProgressView()
                        .controlSize(.mini)
                        .accessibilityLabel("Running")
                }
                Text(session.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text(session.updatedAt, style: .relative)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            if showsPreview || showsCounts {
                HStack(spacing: 5) {
                    if showsPreview, !session.preview.isEmpty {
                        Text(session.preview)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    if showsCounts, session.messages.count > 0 {
                        Text("\(session.messages.count)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.tertiary)
                    }
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

private struct HelpView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openSettings) private var openSettings

    private let shortcuts: [(String, String)] = [
        ("New task", "⌘N"),
        ("Quick switch", "⌘K"),
        ("Next / previous task", "⌘] / ⌘["),
        ("Send message", "Return"),
        ("Stop response", "⌘."),
        ("Compact conversation", "⌘⇧K"),
        ("Review changes", "⌘⇧G"),
        ("Choose model", "⌘⇧M"),
        ("Show / hide inspector", "⌘⌥I"),
        ("Settings", "⌘,"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Talos Help", systemImage: "questionmark.circle")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    section(
                        "Getting started",
                        text: "Create a task, choose a model, and describe the outcome. Talos gives the model real tools on this Mac and asks before high-impact actions."
                    )
                    section(
                        "Files and attachments",
                        text: "Attach files with the paperclip or by dragging them onto the composer. Images and PDFs are sent to vision-capable models."
                    )
                    section(
                        "Approvals",
                        text: "Shell commands, edits, terminal input, browser actions, and computer control require approval unless you allow them for the task. Patches show a per-file preview before they apply."
                    )

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Keyboard shortcuts")
                            .font(.headline)
                        ForEach(shortcuts, id: \.0) { pair in
                            HStack {
                                Text(pair.0)
                                    .foregroundStyle(.secondary)
                                Spacer()
                                Text(pair.1)
                                    .font(.system(.callout, design: .monospaced))
                                    .foregroundStyle(.secondary)
                            }
                            .font(.callout)
                        }
                    }

                    section(
                        "Privacy",
                        text: "Sessions, usage, and the semantic index stay on this Mac. Provider keys live in the macOS Keychain. Only prompts and attachments go to the model provider you choose."
                    )
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack {
                Button("Open Settings…") {
                    dismiss()
                    openSettings()
                }
                Spacer()
                Text("Talos 1.0")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(20)
        .frame(width: 560, height: 560)
    }

    private func section(_ title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.headline)
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct QuickSwitcher: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openSettings) private var openSettings
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @State private var query = ""
    @State private var hovered: String?
    @FocusState private var searchFocused: Bool

    private struct PaletteCommand: Identifiable {
        let id: String
        let title: String
        let detail: String
        let symbol: String
        let shortcut: String?
        let action: () -> Void
    }

    private var trimmedQuery: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var sessions: [Session] {
        let base = model.sessions.filter { !$0.archived }
        guard !trimmedQuery.isEmpty else { return Array(base.prefix(4)) }
        return base.filter {
            $0.title.localizedCaseInsensitiveContains(trimmedQuery)
                || $0.preview.localizedCaseInsensitiveContains(trimmedQuery)
        }.prefix(8).map { $0 }
    }

    private var commands: [PaletteCommand] {
        var list: [PaletteCommand] = [
            PaletteCommand(
                id: "new",
                title: String(localized: "New Task"),
                detail: String(localized: "Start a fresh task in this project"),
                symbol: "plus.circle",
                shortcut: "⌘N"
            ) { model.newSession() },
            PaletteCommand(
                id: "changes",
                title: String(localized: "Review Changes"),
                detail: String(localized: "Open diffs, staging, commit, and pull requests"),
                symbol: "arrow.triangle.branch",
                shortcut: "⌘⇧G"
            ) { model.showGitPanel = true },
            PaletteCommand(
                id: "worktrees",
                title: String(localized: "Manage Worktrees"),
                detail: String(localized: "Create, switch, merge, or clean up task branches"),
                symbol: "square.split.2x1",
                shortcut: nil
            ) { model.showWorktrees = true },
            PaletteCommand(
                id: "compact",
                title: String(localized: "Compact Conversation"),
                detail: String(localized: "Summarize older messages to free context"),
                symbol: "arrow.down.right.and.arrow.up.left",
                shortcut: "⌘⇧K"
            ) { model.compactCurrentSession() },
            PaletteCommand(
                id: "inspector",
                title: model.showInspector
                    ? String(localized: "Hide Details Inspector")
                    : String(localized: "Show Details Inspector"),
                detail: String(localized: "Project, model, tools, plan, and token details"),
                symbol: "sidebar.right",
                shortcut: "⌘⌥I"
            ) { model.showInspector.toggle() },
            PaletteCommand(
                id: "settings",
                title: String(localized: "Settings"),
                detail: String(localized: "Models, providers, tools, hooks, MCP, and policy"),
                symbol: "gearshape",
                shortcut: "⌘,"
            ) { openSettings() },
        ]
        if model.isStreaming {
            list.insert(
                PaletteCommand(
                    id: "stop",
                    title: "Stop Response",
                    detail: "Cancel the active run",
                    symbol: "stop.circle",
                    shortcut: "⌘."
                ) { model.stop() },
                at: 0
            )
        }
        guard !trimmedQuery.isEmpty else { return list }
        return list.filter {
            $0.title.localizedCaseInsensitiveContains(trimmedQuery)
                || $0.detail.localizedCaseInsensitiveContains(trimmedQuery)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search tasks and commands", text: $query)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .focused($searchFocused)
                    .onSubmit(openFirst)
                if !query.isEmpty {
                    Button {
                        query = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.tertiary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Clear search")
                }
                Text("esc")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 4))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    if !sessions.isEmpty {
                        sectionTitle("Tasks")
                        ForEach(sessions) { session in
                            Button {
                                model.selectedID = session.id
                                dismiss()
                            } label: {
                                row(
                                    id: "session-\(session.id)",
                                    symbol: "bubble.left.and.text.bubble.right",
                                    title: session.title,
                                    detail: session.preview,
                                    trailing: "\(session.messages.count)"
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    if !commands.isEmpty {
                        sectionTitle("Commands")
                        ForEach(commands) { command in
                            Button {
                                command.action()
                                dismiss()
                            } label: {
                                row(
                                    id: command.id,
                                    symbol: command.symbol,
                                    title: command.title,
                                    detail: command.detail,
                                    trailing: command.shortcut
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    if sessions.isEmpty && commands.isEmpty {
                        ContentUnavailableView.search(text: trimmedQuery)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 24)
                    }
                }
                .padding(8)
            }
            .frame(maxHeight: 500)
        }
        .frame(width: 580)
        .background(reduceTransparency ? AnyShapeStyle(Color(nsColor: .windowBackgroundColor)) : AnyShapeStyle(.ultraThickMaterial))
        .onExitCommand { dismiss() }
        .onAppear {
            DispatchQueue.main.async { searchFocused = true }
        }
    }

    private func sectionTitle(_ title: LocalizedStringKey) -> some View {
        Text(title)
            .textCase(.uppercase)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.tertiary)
            .padding(.horizontal, 10)
            .padding(.top, 8)
            .padding(.bottom, 2)
    }

    private func row(
        id: String,
        symbol: String,
        title: String,
        detail: String,
        trailing: String?
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 24, height: 24)
                .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            if let trailing {
                Text(trailing)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(hovered == id ? Brand.bronze.opacity(0.12) : Color.clear)
        )
        .contentShape(Rectangle())
        .onHover { value in
            if value { hovered = id } else if hovered == id { hovered = nil }
        }
    }

    private func openFirst() {
        if let session = sessions.first {
            model.selectedID = session.id
            dismiss()
        } else if let command = commands.first {
            command.action()
            dismiss()
        }
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
                .frame(width: 24, height: 24)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .foregroundStyle(.secondary)
        .accessibilityLabel("Provider and tool settings")
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

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Providers")
                    .font(.headline)
                Spacer()
                Text("\(settings.savedKeys.count + (model.localModels.isEmpty ? 0 : 1)) ready")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 2) {
                ForEach(Provider.allCases) { provider in
                    ProviderPopoverRow(provider: provider) {
                        model.editingProvider = provider
                        isPresented = false
                    }
                }
            }

            Divider()

            HStack {
                Text(model.choice.display)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer()
                SettingsLink {
                    Text("All Settings…")
                }
            }
        }
        .padding(12)
        .frame(width: 370)
    }

}

private struct ProviderPopoverRow: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let provider: Provider
    var onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 9) {
                Image(systemName: provider.symbol)
                    .frame(width: 18)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(provider.title)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Text(status)
                    .font(.caption)
                    .foregroundStyle(status == "Ready" ? Color.secondary : Color.orange)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, 4)
    }

    private var subtitle: String {
        if provider == .local {
            return model.localModels.isEmpty ? "Ollama · offline" : "Ollama · \(model.localModels.count) models"
        }
        return settings.model(for: provider)
    }

    private var status: String {
        if provider == .local {
            return model.localModels.isEmpty ? "Offline" : "Ready"
        }
        return settings.savedKeys.contains(provider) ? "Ready" : "No key"
    }
}

private struct ComposerView: View {
    @EnvironmentObject private var model: AppModel
    @Binding var draft: String
    var composerFocused: FocusState<Bool>.Binding
    @State private var attachments: [URL] = []
    @State private var importing = false
    @StateObject private var dictation = DictationController()
    @State private var dictationBase = ""
    @State private var dropTargeted = false
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

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
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(alignment: .top, spacing: 8) {
                            ForEach(attachments, id: \.self) { url in
                                ComposerAttachmentTile(url: url) {
                                    attachments.removeAll { $0 == url }
                                }
                            }
                        }
                        .padding(.vertical, 2)
                    }
                    .frame(maxHeight: 92)
                }

                if !slashMatches.isEmpty {
                    ScrollView {
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
                    }
                    .frame(maxHeight: 300)
                    .padding(6)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(Color(nsColor: .textBackgroundColor))
                    )
                }

                TextField("Ask Talos anything…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .lineLimit(1...12)
                    .focused(composerFocused)
                    .onSubmit(send)
                    .accessibilityLabel("Message")

                if let error = dictation.error {
                    HStack(spacing: 6) {
                        Image(systemName: "exclamationmark.triangle")
                        Text(error)
                            .lineLimit(3)
                        Spacer()
                        Button("Open Settings") {
                            DictationController.openSpeechSettings()
                        }
                        .buttonStyle(.borderless)
                    }
                    .font(.caption)
                    .foregroundStyle(.red)
                }

                if let fraction = model.contextUsage.fraction {
                    HStack(spacing: 8) {
                        GeometryReader { geometry in
                            ZStack(alignment: .leading) {
                                Capsule()
                                    .fill(Color(nsColor: .separatorColor).opacity(0.6))
                                Capsule()
                                    .fill(contextColor)
                                    .frame(width: max(2, geometry.size.width * min(fraction, 1)))
                            }
                        }
                        .frame(height: 2)
                        Text(model.contextLabel)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(contextColor)
                            .lineLimit(1)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Context usage \(model.contextLabel)")
                }

                HStack(spacing: 7) {
                    Button {
                        importing = true
                    } label: {
                        Image(systemName: "paperclip")
                            .frame(width: 26, height: 26)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .help("Attach files")
                    .accessibilityLabel("Attach files")

                    Button {
                        toggleDictation()
                    } label: {
                        Image(systemName: dictation.isRecording ? "mic.fill" : "mic")
                            .frame(width: 26, height: 26)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(dictation.isRecording ? Color.red : Color.secondary)
                    .help(dictation.isRecording ? "Stop dictation" : "Dictate a message")
                    .accessibilityLabel(dictation.isRecording ? "Stop dictation" : "Start dictation")

                    ModelPickerButton()
                    Divider().frame(height: 12)
                    ReasoningMenu()
                    SpeedMenu()
                    Spacer(minLength: 4)
                    ProviderButton()
                    sendControl
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(
                        dropTargeted
                            ? Brand.bronze
                            : (composerFocused.wrappedValue ? Brand.bronze.opacity(0.55) : Color(nsColor: .separatorColor)),
                        lineWidth: dropTargeted ? 2 : (composerFocused.wrappedValue ? 1.5 : 1)
                    )
            )
            .shadow(color: reduceTransparency ? .clear : .black.opacity(0.06), radius: 10, y: 4)
            .overlay(alignment: .top) {
                if dropTargeted {
                    Label("Drop files to attach", systemImage: "paperclip")
                        .font(.caption)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.regularMaterial, in: Capsule())
                        .padding(6)
                        .transition(.opacity)
                }
            }
            .dropDestination(for: URL.self) { urls, _ in
                let files = urls.filter { FileManager.default.fileExists(atPath: $0.path) }
                let new = files.filter { !attachments.contains($0) }
                attachments.append(contentsOf: new)
                for url in new { model.recentDocuments.record(url) }
                return !new.isEmpty
            } isTargeted: { value in
                dropTargeted = value
            }
            .padding(.horizontal, 24)
            .padding(.top, 12)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: composerFocused.wrappedValue)

            HStack(spacing: 4) {
                Image(systemName: model.choice.provider == .local ? "desktopcomputer" : "cloud")
                if model.choice.provider == .local {
                    Text("Local · this Mac")
                } else {
                    Text("Cloud · \(model.choice.provider.title)")
                }
                Spacer()
                Text(model.contextLabel)
                    .foregroundStyle(contextColor)
                Text("·")
                Text(LocalizedStringKey(model.isStreaming ? "Return to queue" : "Return to send"))
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
                for url in urls { model.recentDocuments.record(url) }
            }
        }
        .onAppear {
            if attachments.isEmpty,
               let seeded = ProcessInfo.processInfo.environment["TALOS_DEMO_ATTACHMENT"] {
                let urls = seeded
                    .split(separator: "|")
                    .map { URL(fileURLWithPath: String($0)) }
                    .filter { FileManager.default.fileExists(atPath: $0.path) }
                attachments.append(contentsOf: urls)
            }
        }
        .onDisappear {
            dictation.onTranscript = nil
            dictation.cancel()
        }
    }

    private func toggleDictation() {
        if dictation.isRecording {
            dictation.stop()
            return
        }
        dictationBase = draft
        dictation.error = nil
        dictation.onTranscript = { text in
            draft = DictationText.merged(base: dictationBase, transcript: text)
        }
        dictation.start()
    }

    private var sendControl: some View {
        HStack(spacing: 7) {
            if model.isStreaming {
                Button {
                    model.stop()
                } label: {
                    ZStack {
                        Circle()
                            .strokeBorder(Color.secondary.opacity(0.45), lineWidth: 1)
                            .frame(width: 28, height: 28)
                        Image(systemName: "stop.fill")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(.plain)
                .help("Stop (⌘.)")
                .accessibilityLabel("Stop response")
            }

            Button {
                send()
            } label: {
                ZStack {
                    Circle()
                        .fill(canSend ? Brand.bronze : Color.secondary.opacity(0.18))
                        .frame(width: 28, height: 28)
                    Image(systemName: model.isStreaming ? "text.badge.plus" : "arrow.up")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(canSend ? Color.white : Color.secondary)
                }
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .help(model.isStreaming ? "Queue message (Return)" : "Send (Return)")
            .accessibilityLabel(model.isStreaming ? "Queue message" : "Send message")
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
        if dictation.isRecording {
            dictation.onTranscript = nil
            dictation.cancel()
        }
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
                    .accessibilityLabel("Remove queued message")
                }
            }
        }
        .padding(.horizontal, 26)
        .padding(.top, 8)
    }
}

private struct ComposerAttachmentTile: View {
    let url: URL
    var remove: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            ZStack(alignment: .topTrailing) {
                Group {
                    if url.pathExtension.lowercased() == "pdf" {
                        VStack(spacing: 4) {
                            Image(systemName: "doc.richtext")
                                .font(.system(size: 22))
                                .foregroundStyle(Brand.bronze)
                            Text("PDF")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.secondary)
                        }
                        .frame(width: 64, height: 64)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    } else if let image = NSImage(contentsOf: url) {
                        Image(nsImage: image)
                            .resizable()
                            .scaledToFill()
                            .frame(width: 64, height: 64)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    } else {
                        VStack(spacing: 4) {
                            Image(systemName: "doc")
                                .font(.system(size: 22))
                                .foregroundStyle(.secondary)
                            Text(url.pathExtension.uppercased())
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.tertiary)
                        }
                        .frame(width: 64, height: 64)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                }
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(Color(nsColor: .separatorColor).opacity(0.7), lineWidth: 1)
                )

                Button(action: remove) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 13))
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.black.opacity(0.55))
                }
                .buttonStyle(.plain)
                .offset(x: 5, y: -5)
                .help("Remove attachment")
                .accessibilityLabel("Remove \(url.lastPathComponent)")
            }
            .frame(width: 64, height: 64)

            Text(url.lastPathComponent)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(width: 64, alignment: .leading)
        }
        .help(url.path)
    }
}

private struct AttachmentPill: View {
    let url: URL
    var remove: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 5) {
            if url.pathExtension.lowercased() == "pdf" {
                Image(systemName: "doc.richtext")
            } else if let image = NSImage(contentsOf: url) {
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
                .accessibilityLabel("Remove attachment")
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    let session: Session
    @Binding var draft: String
    var composerFocused: FocusState<Bool>.Binding

    var body: some View {
        VStack(spacing: 0) {
            if !session.plan.isEmpty {
                PlanStrip(plan: session.plan)
            }
            transcript
            if model.isStreaming {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Working…")
                        .font(.caption.weight(.medium))
                    Text(model.choice.id)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Spacer()
                    Text(model.contextLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Button("Stop") {
                        model.stop()
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                    .accessibilityLabel("Stop response")
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 7)
                .background(reduceTransparency ? AnyShapeStyle(Color(nsColor: .controlBackgroundColor)) : AnyShapeStyle(.thinMaterial))
                Divider()
            }
            AmbientSuggestionBar()
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
                        EmptyTask { suggestion in
                            draft = suggestion
                            composerFocused.wrappedValue = true
                        }
                    }
                    ForEach(visibleMessages) { message in
                        MessageRow(
                            message: message,
                            onEdit: message.role == .user ? {
                                model.editMessage(session.id, message.id)
                            } : nil,
                            onRetry: message.role == .assistant ? {
                                Task { _ = await model.retryMessage(session.id, message.id) }
                            } : nil,
                            onFork: {
                                model.forkMessage(session.id, message.id)
                            }
                        )
                        .id(message.id)
                    }
                }
                .frame(maxWidth: 760, alignment: .leading)
                .padding(.horizontal, 32)
                .padding(.vertical, 28)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .background {
                if session.messages.isEmpty {
                    RadialGradient(
                        colors: [Brand.bronze.opacity(0.12), Brand.bronze.opacity(0.03), .clear],
                        center: UnitPoint(x: 0.12, y: 0.04),
                        startRadius: 4,
                        endRadius: 560
                    )
                }
            }
            .onChange(of: session.messages.last?.text) {
                guard let id = session.messages.last?.id else { return }
                if reduceMotion {
                    proxy.scrollTo(id, anchor: .bottom)
                } else {
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
    var onFork: (() -> Void)? = nil
    @State private var hovering = false

    var body: some View {
        Group {
            if message.role == .tool {
                ToolCard(message: message)
            } else if message.role == .user {
                userRow
            } else {
                assistantRow
            }
        }
        .onHover { hovering = $0 }
        .contextMenu {
            if let onEdit {
                Button("Edit and Resend…") { onEdit() }
            }
            if let onRetry {
                Button("Retry This Message") { onRetry() }
            }
            if let onFork {
                Button("Fork From Here") { onFork() }
            }
            if !message.text.isEmpty {
                Button("Copy") { copy() }
            }
        }
    }

    private var userRow: some View {
        HStack(alignment: .top, spacing: 0) {
            Spacer(minLength: 70)
            VStack(alignment: .trailing, spacing: 4) {
                HStack(spacing: 6) {
                    if hovering, !message.text.isEmpty {
                        Button {
                            copy()
                        } label: {
                            Image(systemName: "doc.on.doc")
                                .font(.system(size: 10))
                        }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.tertiary)
                        .help("Copy")
                        .accessibilityLabel("Copy message")
                    }
                    Text("You")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
                messageText
                    .padding(.horizontal, 13)
                    .padding(.vertical, 9)
                    .background(
                        Brand.bronze.opacity(0.14),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                    )
                attachments(alignment: .trailing)
            }
        }
    }

    private var assistantRow: some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                Circle()
                    .fill(Brand.bronze.opacity(0.12))
                Image(systemName: "sparkles")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Brand.bronze)
            }
            .frame(width: 26, height: 26)
            .padding(.top, 1)

            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(roleTitle)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    if message.revisionOf != nil {
                        Text("edited branch")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    if message.retryOf != nil {
                        Text("retry branch")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    if hovering, !message.text.isEmpty {
                        Button {
                            copy()
                        } label: {
                            Image(systemName: "doc.on.doc")
                                .font(.system(size: 10))
                        }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.tertiary)
                        .help("Copy")
                        .accessibilityLabel("Copy message")
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
                    messageText
                }

                attachments(alignment: .leading)
            }
        }
    }

    private var messageText: some View {
        Text(attributedText)
            .font(.system(size: settings.transcriptFontSize))
            .lineSpacing(2.5)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel("\(roleTitle) said: \(message.text)")
    }

    @ViewBuilder
    private func attachments(alignment: HorizontalAlignment) -> some View {
        if !message.attachments.isEmpty {
            HStack(spacing: 6) {
                ForEach(message.attachments, id: \.self) { path in
                    AttachmentPill(url: URL(fileURLWithPath: path))
                }
            }
        }
    }

    private var roleTitle: String {
        message.role == .user ? String(localized: "You") : (message.model ?? String(localized: "Assistant"))
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
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 22, height: 22)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                Text(message.toolName ?? "Tool")
                    .font(.callout.weight(.semibold))
                Spacer()
                Label(statusTitle, systemImage: statusSymbol)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(statusColor)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(statusColor.opacity(0.12), in: Capsule())
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
                        .environment(\.layoutDirection, .leftToRight)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                        .padding(.top, 4)
                } label: {
                    Text(expanded ? "Hide output" : "Show output")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(11)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.8), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(message.toolName ?? "Tool") \(statusTitle)")
    }

    private var status: ToolStatus { message.toolStatus ?? .succeeded }

    private var statusTitle: String {
        switch status {
        case .succeeded: return String(localized: "Done")
        case .failed: return String(localized: "Failed")
        case .denied: return String(localized: "Denied")
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
        case "browser": return "globe"
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

private struct PlanStrip: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    let plan: [PlanStep]
    @State private var expanded = false

    private var completed: Int { plan.filter { $0.status == .completed }.count }
    private var current: PlanStep? {
        plan.first(where: { $0.status == .inProgress }) ?? plan.first(where: { $0.status == .pending })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "checklist")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Brand.bronze)
                Text("Plan")
                    .font(.caption.weight(.semibold))
                Text("\(completed)/\(plan.count)")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                if !expanded, let current {
                    Text("·")
                        .foregroundStyle(.tertiary)
                    Text(current.title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    if reduceMotion {
                        expanded.toggle()
                    } else {
                        withAnimation(.easeInOut(duration: 0.16)) {
                            expanded.toggle()
                        }
                    }
                } label: {
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 20, height: 20)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(expanded ? "Collapse plan" : "Expand plan")
            }

            ProgressView(value: Double(completed), total: Double(max(plan.count, 1)))
                .progressViewStyle(.linear)
                .tint(Brand.bronze)

            if expanded {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(plan) { step in
                        HStack(alignment: .firstTextBaseline, spacing: 7) {
                            Image(systemName: step.status.symbol)
                                .font(.system(size: 10))
                                .foregroundStyle(color(for: step.status))
                            Text(step.title)
                                .font(.caption)
                                .strikethrough(step.status == .completed)
                                .foregroundStyle(step.status == .completed ? .secondary : .primary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(.top, 3)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(reduceTransparency ? AnyShapeStyle(Color(nsColor: .controlBackgroundColor)) : AnyShapeStyle(.thinMaterial))
        }
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color(nsColor: .separatorColor).opacity(0.6), lineWidth: 1)
        )
        .padding(.horizontal, 24)
        .padding(.top, 10)
    }

    private func color(for status: PlanStatus) -> Color {
        switch status {
        case .completed: return .green
        case .inProgress: return Brand.bronze
        case .pending: return .secondary
        }
    }
}

private struct EmptyTask: View {
    @EnvironmentObject private var model: AppModel
    let onSuggestion: (String) -> Void
    @State private var hoveredSuggestion: UUID?

    private struct Suggestion: Identifiable {
        let id = UUID()
        let symbol: String
        let title: String
        let detail: String
        let prompt: String
    }

    private let suggestions = [
        Suggestion(
            symbol: "sparkles",
            title: String(localized: "Explore the codebase"),
            detail: String(localized: "Map the architecture, entry points, and key files"),
            prompt: "Explore this repository and explain its architecture, entry points, and the most important files."
        ),
        Suggestion(
            symbol: "ladybug",
            title: String(localized: "Fix a failing test"),
            detail: String(localized: "Run the tests, find the failure, and patch it"),
            prompt: "Run the test suite, find the first failure, fix it, and rerun the tests."
        ),
        Suggestion(
            symbol: "arrow.triangle.pull",
            title: String(localized: "Review my changes"),
            detail: String(localized: "Read the diff and flag bugs and missing tests"),
            prompt: "Review my uncommitted changes for bugs, regressions, and missing tests."
        ),
        Suggestion(
            symbol: "wand.and.stars",
            title: String(localized: "Automate a workflow"),
            detail: String(localized: "Use shell, files, browser, and MCP tools"),
            prompt: "Look at this project and propose one workflow worth automating, then implement it."
        ),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            VStack(alignment: .leading, spacing: 12) {
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
                .frame(width: 52, height: 52)

                Text("What would you like to do?")
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))

                Text("Talos gives any model real tools on this Mac: shell, a persistent terminal, files, patches, browser, LSP, MCP, subagents, and the desktop.")
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 560, alignment: .leading)

                HStack(spacing: 6) {
                    capability("terminal")
                    capability("folder")
                    capability("globe")
                    capability("point.3.connected.trianglepath.dotted")
                    capability("person.2")
                    Spacer()
                }
                .padding(.top, 2)
            }

            LazyVGrid(
                columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)],
                spacing: 12
            ) {
                ForEach(suggestions) { suggestion in
                    Button {
                        onSuggestion(suggestion.prompt)
                    } label: {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: suggestion.symbol)
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(Brand.bronze)
                                .frame(width: 30, height: 30)
                                .background(Brand.bronze.opacity(0.12), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(suggestion.title)
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundStyle(.primary)
                                Text(suggestion.detail)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .multilineTextAlignment(.leading)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, minHeight: 74, alignment: .topLeading)
                        .background(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(
                                    hoveredSuggestion == suggestion.id
                                        ? Brand.bronze.opacity(0.08)
                                        : Color(nsColor: .controlBackgroundColor)
                                )
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(
                                    hoveredSuggestion == suggestion.id
                                        ? Brand.bronze.opacity(0.45)
                                        : Color(nsColor: .separatorColor).opacity(0.7),
                                    lineWidth: 1
                                )
                        )
                        .contentShape(Rectangle())
                        .onHover { value in
                            hoveredSuggestion = value ? suggestion.id : nil
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(suggestion.title). \(suggestion.detail)")
                }
            }
            .frame(maxWidth: 620)

            HStack(spacing: 8) {
                Label(model.currentProject?.name ?? "Workspace", systemImage: "folder")
                Text("·")
                Text(model.choice.display)
                Text("·")
                Text("⌘N new task · / for commands")
            }
            .font(.caption)
            .foregroundStyle(.tertiary)
            .lineLimit(1)
        }
        .frame(maxWidth: 680, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.top, 44)
        .padding(.bottom, 20)
    }

    private func capability(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(.secondary)
            .frame(width: 22, height: 20)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 5, style: .continuous))
    }
}

private struct NewWorktreeSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = "task"
    @State private var busy = false
    @State private var error: String?

    private var slug: String {
        let value = name
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return value.isEmpty ? "task" : value
    }

    private var branch: String { settings.branchPrefix + slug }

    private var path: String {
        model.projectURL
            .deletingLastPathComponent()
            .appendingPathComponent(model.projectURL.lastPathComponent + "-" + slug)
            .path
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New Worktree")
                .font(.title3.weight(.semibold))
            Text("Create an isolated checkout for this task without disturbing the current working tree.")
                .font(.callout)
                .foregroundStyle(.secondary)

            TextField("Worktree name", text: $name)
                .textFieldStyle(.roundedBorder)

            LabeledContent("Branch", value: branch)
            LabeledContent("Folder") {
                Text(path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .lineLimit(2)
            }

            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button(busy ? "Creating…" : "Create Worktree") { create() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(busy)
            }
        }
        .padding(20)
        .frame(width: 540)
    }

    private func create() {
        busy = true
        error = nil
        Task {
            switch await model.createWorktree(name: name) {
            case .success:
                dismiss()
            case .failure(let failure):
                error = failure.localizedDescription
            }
            busy = false
        }
    }
}

private struct MessageEditSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let target: MessageEditTarget
    @State private var text: String
    @State private var busy = false
    @State private var error: String?

    init(target: MessageEditTarget) {
        self.target = target
        _text = State(initialValue: target.text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Edit and Resend")
                .font(.title3.weight(.semibold))
            Text("The selected message and everything after it will be replaced by a new branch.")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.body)
                .frame(minHeight: 110)
            if !target.attachments.isEmpty {
                Text("\(target.attachments.count) attachment(s) kept")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button(busy ? "Sending…" : "Save and Resend") {
                    busy = true
                    error = nil
                    Task {
                        let result = await model.commitEditMessage(target, text: text)
                        busy = false
                        switch result {
                        case .success:
                            dismiss()
                        case .failure(let failure):
                            error = failure.localizedDescription
                        }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(busy || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 620, height: 320)
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
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let request: ApprovalRequest
    let resolve: (ApprovalDecision) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Allow \(request.toolName)?", systemImage: "exclamationmark.shield")
                .font(.headline)
            Text(request.summary)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)

            if let preview = request.patchPreview {
                patchPreviewSection(preview)
            }

            reviewSection

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
        .frame(width: request.patchPreview == nil ? 540 : 760)
        .interactiveDismissDisabled()
    }

    @ViewBuilder
    private func patchPreviewSection(_ preview: PatchPreview) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Patch preview", systemImage: "doc.on.doc")
                    .font(.callout.weight(.semibold))
                Spacer()
                Text(preview.summary)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            if preview.files.isEmpty {
                Text("Talos could not parse this patch into files. Review the summary above before allowing it.")
                    .font(.caption)
                    .foregroundStyle(.orange)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(preview.files) { file in
                            patchFileSection(file)
                        }
                    }
                    .padding(8)
                }
                .frame(maxHeight: 340)
                .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    @ViewBuilder
    private func patchFileSection(_ file: PatchFilePreview) -> some View {
        let rendered = file.previewHunks()
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(file.path)
                    .font(.system(.caption, design: .monospaced).weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                if file.isNew {
                    Text("new")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.green)
                }
                if file.isDeleted {
                    Text("deleted")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.red)
                }
                Spacer()
                Text("+\(file.added) −\(file.removed)")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            if file.isBinary {
                Text("Binary file — contents are not shown.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            ForEach(rendered.hunks) { hunk in
                Text(hunk.header)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                ForEach(hunk.lines) { line in
                    Text(prefix(for: line.kind) + line.text)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(line.kind == .context ? .secondary : .primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 4)
                        .background(lineBackground(for: line.kind))
                }
            }
            if rendered.truncated {
                Text("Preview truncated; the full patch will be applied.")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private func prefix(for kind: PatchLinePreview.Kind) -> String {
        switch kind {
        case .addition: return "+ "
        case .removal: return "− "
        case .context: return "  "
        }
    }

    private func lineBackground(for kind: PatchLinePreview.Kind) -> Color {
        switch kind {
        case .addition: return .green.opacity(0.12)
        case .removal: return .red.opacity(0.12)
        case .context: return .clear
        }
    }

    @ViewBuilder
    private var reviewSection: some View {
        if model.approvalReviewing {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Reviewing this action in a separate read-only context…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        } else if let review = model.approvalReview {
            VStack(alignment: .leading, spacing: 6) {
                Label(review.verdict.title, systemImage: review.verdict.symbol)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(color(for: review.verdict))
                Text(review.summary)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                if !review.reasons.isEmpty {
                    ForEach(review.reasons.prefix(5), id: \.self) { reason in
                        Text("• \(reason)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Text("Risk: \(review.risk) · reviewed by \(review.model)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(color(for: review.verdict).opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
        } else if let error = model.approvalReviewError {
            Text("Automatic review unavailable: \(error)")
                .font(.caption)
                .foregroundStyle(.orange)
        } else if !settings.autoReviewTools {
            Text("Automatic review is off.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private func color(for verdict: ReviewVerdict) -> Color {
        switch verdict {
        case .allow: return .green
        case .caution: return .orange
        case .deny: return .red
        }
    }
}

private struct Inspector: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var usage: UsageStore
    @State private var indexStatus: SemanticIndexStatus?
    @State private var indexBusy = false

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
                LabeledContent("Changes") {
                    Text(model.gitStatus.files.isEmpty ? String(localized: "Clean") : "\(model.gitStatus.files.count)")
                }
            }

            Section("Model") {
                LabeledContent("Provider", value: model.choice.provider.title)
                LabeledContent("Model", value: model.choice.id)
                LabeledContent("Reasoning", value: model.reasoningTitle)
                LabeledContent("Vision") {
                    Text(LocalizedStringKey(model.modelCapabilities.supportsVision ? "Images" : "Text only"))
                }
                LabeledContent("Context", value: model.contextUsage.label)
                LabeledContent("Token counting", value: model.tokenizerLabel)
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
                toolRow("Browser", symbol: "globe", enabled: settings.enableBrowser)
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
                LabeledContent("Approvals") {
                    Text(LocalizedStringKey(settings.approvalPolicy == .onRequest ? "On request" : "Never"))
                }
            }

            Section("Index") {
                if let status = indexStatus {
                    LabeledContent("Semantic search") {
                        Text(status.chunks == 0
                             ? "Not built"
                             : "\(status.chunks) chunks · \(status.files) files")
                            .foregroundStyle(.secondary)
                    }
                    LabeledContent("Cached vectors", value: "\(status.cachedFiles) file\(status.cachedFiles == 1 ? "" : "s")")
                    if status.isStale {
                        Text("Stale — rebuild to include recent edits.")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                } else {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Checking index…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Button(indexBusy ? "Rebuilding…" : "Rebuild Index") {
                    rebuildIndex()
                }
                .disabled(indexBusy)
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
                LabeledContent("State") {
                    Text(LocalizedStringKey(model.isStreaming ? "Running" : "Idle"))
                }
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
        .task(id: model.projectURL) {
            indexStatus = await SemanticIndex.shared.status(workspace: model.projectURL)
        }
    }

    private func rebuildIndex() {
        indexBusy = true
        Task {
            indexStatus = try? await SemanticIndex.shared.rebuild(workspace: model.projectURL)
            indexBusy = false
        }
    }

    private func toolRow(_ name: String, symbol: String, enabled: Bool) -> some View {
        LabeledContent {
            Text(enabled ? String(localized: "Enabled") : String(localized: "Off"))
                .foregroundStyle(.secondary)
        } label: {
            Label(LocalizedStringKey(name), systemImage: symbol)
        }
    }
}
