import SwiftUI
import AppKit

struct WorktreePanel: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var busyID: String?
    @State private var notice: String?
    @State private var error: String?
    @State private var pendingMerge: GitWorktreeInfo?
    @State private var pendingRemoval: GitWorktreeInfo?
    @State private var forceRemoval = false

    private var others: [GitWorktreeInfo] {
        model.worktrees.filter { !$0.isCurrent }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Worktrees", systemImage: "arrow.triangle.branch")
                    .font(.title3.weight(.semibold))
                Spacer()
                if let branch = model.gitStatus.branch {
                    Text("Current branch: \(branch)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Button("Refresh") {
                    Task { await model.refreshGit() }
                }
            }

            if model.worktrees.isEmpty {
                Text("No Git worktrees found. Open a Git repository to manage branches.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(model.worktrees) { worktree in
                            row(worktree)
                            Divider()
                        }
                    }
                }
                .frame(maxHeight: 360)
            }

            if let notice {
                Text(notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }

            HStack {
                Button("New Worktree…") {
                    model.showWorktrees = false
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                        model.showWorktreeSheet = true
                    }
                }
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 700, height: 500)
        .onAppear {
            Task { await model.refreshGit() }
        }
        .confirmationDialog(
            "Merge this branch?",
            isPresented: Binding(
                get: { pendingMerge != nil },
                set: { if !$0 { pendingMerge = nil } }
            ),
            presenting: pendingMerge
        ) { worktree in
            Button("Merge \(worktree.branchLabel)") {
                merge(worktree)
            }
            Button("Cancel", role: .cancel) { pendingMerge = nil }
        } message: { worktree in
            Text("Talos merges \(worktree.branchLabel) into the current branch of \(model.projectURL.lastPathComponent). The working tree must be clean.")
        }
        .confirmationDialog(
            forceRemoval ? "Force remove this worktree?" : "Remove this worktree?",
            isPresented: Binding(
                get: { pendingRemoval != nil },
                set: { if !$0 { pendingRemoval = nil } }
            ),
            presenting: pendingRemoval
        ) { worktree in
            Button(forceRemoval ? "Force Remove" : "Remove", role: .destructive) {
                remove(worktree, force: forceRemoval)
            }
            Button("Cancel", role: .cancel) { pendingRemoval = nil }
        } message: { worktree in
            Text(forceRemoval
                ? "Uncommitted changes in \(worktree.name) will be discarded."
                : "The worktree folder is removed. Its branch is deleted only if it is fully merged.")
        }
    }

    @ViewBuilder
    private func row(_ worktree: GitWorktreeInfo) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: worktree.isCurrent ? "folder.fill" : "arrow.triangle.branch")
                .foregroundStyle(worktree.isCurrent ? Color.accentColor : .secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(worktree.name)
                        .font(.body.weight(.medium))
                    Text(worktree.branchLabel)
                        .font(.caption)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.quaternary.opacity(0.6), in: Capsule())
                    if worktree.isCurrent {
                        Text("This project")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    if worktree.isLocked {
                        Text("locked")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                    }
                }
                Text(worktree.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            if busyID == worktree.id {
                ProgressView()
                    .controlSize(.small)
            } else {
                if !worktree.isCurrent {
                    Button("Switch") { model.openWorktree(worktree) }
                    Button("Merge") { pendingMerge = worktree }
                    Button("Clean Up") {
                        forceRemoval = false
                        pendingRemoval = worktree
                    }
                    Menu {
                        Button("Reveal in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting([
                                URL(fileURLWithPath: worktree.path)
                            ])
                        }
                        Button("Force Remove…", role: .destructive) {
                            forceRemoval = true
                            pendingRemoval = worktree
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                }
            }
        }
        .padding(.vertical, 8)
    }

    private func merge(_ worktree: GitWorktreeInfo) {
        busyID = worktree.id
        notice = nil
        error = nil
        Task {
            switch await model.mergeWorktree(worktree) {
            case .success(let text):
                notice = text.isEmpty ? "Merged \(worktree.branchLabel)." : text
            case .failure(let failure):
                error = failure.localizedDescription
            }
            busyID = nil
            pendingMerge = nil
        }
    }

    private func remove(_ worktree: GitWorktreeInfo, force: Bool) {
        busyID = worktree.id
        notice = nil
        error = nil
        Task {
            switch await model.removeWorktree(worktree, force: force, deleteBranch: true) {
            case .success(let text):
                notice = text
            case .failure(let failure):
                error = failure.localizedDescription
            }
            busyID = nil
            pendingRemoval = nil
            forceRemoval = false
        }
    }
}
