import SwiftUI
import AppKit

struct GitPanelView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss

    @State private var showGitHub = false
    @State private var showDiagnostics = false
    @State private var selected: GitChangedFile?
    @State private var diff = ""
    @State private var commitMessage = ""
    @State private var busy = false
    @State private var notice: String?

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                HStack {
                    Image(systemName: "arrow.triangle.branch")
                        .foregroundStyle(.secondary)
                    Text(model.gitStatus.branch ?? String(localized: "No repository"))
                        .font(.headline)
                        .lineLimit(1)
                    Spacer()
                    if !model.gitStatus.files.isEmpty {
                        Text("\(model.gitStatus.files.count)")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(12)

                Divider()

                if model.gitStatus.files.isEmpty {
                    ContentUnavailableView(
                        "No Changes",
                        systemImage: "checkmark.circle",
                        description: Text("The working tree is clean.")
                    )
                } else {
                    List(selection: $selected) {
                        ForEach(model.gitStatus.files) { file in
                            HStack(spacing: 7) {
                                Text(file.status)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(file.staged ? .green : .orange)
                                    .frame(width: 20, alignment: .leading)
                                Text(file.path)
                                    .font(.system(.callout, design: .monospaced))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            .tag(file)
                        }
                    }
                    .listStyle(.inset)
                }

                Divider()

                HStack {
                    Button("Stage All") { stageAll() }
                        .disabled(model.gitStatus.files.isEmpty || busy)
                    Spacer()
                    Button("Refresh") { refresh() }
                        .disabled(busy)
                }
                .padding(10)
            }
            .frame(minWidth: 300, idealWidth: 350)

            VStack(spacing: 0) {
                HStack {
                    Text(selected?.path ?? String(localized: "Diff"))
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    if let selected {
                        Text(selected.label)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(10)

                Divider()

                if diff.isEmpty {
                    ContentUnavailableView(
                        "Select a File",
                        systemImage: "doc.text.magnifyingglass",
                        description: Text("Choose a changed file to inspect its diff.")
                    )
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(diff.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
                                Text(line.isEmpty ? " " : line)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(color(for: line))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .padding(10)
                    }
                }

                Divider()

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        TextField("Commit message", text: $commitMessage)
                            .textFieldStyle(.roundedBorder)
                        Button("Commit") { commit() }
                            .disabled(commitMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
                    }
                    if let notice {
                        Text(notice)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(10)
            }
            .frame(minWidth: 460)
        }
        .navigationTitle("Changes")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("Diagnostics") { showDiagnostics = true }
            }
            ToolbarItem(placement: .primaryAction) {
                Button("Pull Request…") { showGitHub = true }
            }
            ToolbarItem(placement: .cancellationAction) {
                Button("Done") { dismiss() }
            }
        }
        .sheet(isPresented: $showDiagnostics) {
            DiagnosticsPanel()
                .environmentObject(model)
        }
        .sheet(isPresented: $showGitHub) {
            GitHubPanel(initialPath: selected?.path)
                .environmentObject(model)
                .environmentObject(settings)
        }
        .onAppear {
            refresh()
            if ProcessInfo.processInfo.environment["TALOS_OPEN_GITHUB_PANEL"] == "1" {
                showGitHub = true
            }
            if ProcessInfo.processInfo.environment["TALOS_OPEN_DIAGNOSTICS"] == "1" {
                showDiagnostics = true
            }
        }
        .onChange(of: selected) { _, file in
            guard let file else {
                diff = ""
                return
            }
            loadDiff(file)
        }
        .frame(minWidth: 860, minHeight: 520)
    }

    private func refresh() {
        busy = true
        Task {
            await model.refreshGit()
            if let selected,
               !model.gitStatus.files.contains(where: { $0.id == selected.id }) {
                self.selected = nil
                diff = ""
            }
            busy = false
        }
    }

    private func loadDiff(_ file: GitChangedFile) {
        Task {
            diff = await GitService.diff(in: model.projectURL, path: file.path)
        }
    }

    private func stageAll() {
        busy = true
        Task {
            switch await GitService.stageAll(in: model.projectURL) {
            case .success:
                notice = "Staged all changes."
                await model.refreshGit()
            case .failure(let error):
                notice = error.localizedDescription
            }
            busy = false
        }
    }

    private func commit() {
        busy = true
        Task {
            switch await GitService.commit(in: model.projectURL, message: commitMessage) {
            case .success(let output):
                notice = output.isEmpty ? "Committed." : output
                commitMessage = ""
                await model.refreshGit()
                if let selected {
                    loadDiff(selected)
                }
            case .failure(let error):
                notice = error.localizedDescription
            }
            busy = false
        }
    }

    private func color(for line: String) -> Color {
        if line.hasPrefix("+++") || line.hasPrefix("---") {
            return .secondary
        }
        if line.hasPrefix("+") { return .green }
        if line.hasPrefix("-") { return .red }
        if line.hasPrefix("@@") { return .blue }
        if line.hasPrefix("diff ") || line.hasPrefix("index ") {
            return .secondary
        }
        return .primary
    }
}
