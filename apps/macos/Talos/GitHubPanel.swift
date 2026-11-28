import SwiftUI
import AppKit

struct GitHubPanel: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss

    let initialPath: String?

    @State private var repository: GitHubRepository?
    @State private var branch = ""
    @State private var defaultBranch = "main"
    @State private var pullRequest: GitHubPullRequest?
    @State private var tokenAvailable = false
    @State private var title = ""
    @State private var prBody = ""
    @State private var draft = false
    @State private var reviewPath = ""
    @State private var reviewLine = 1
    @State private var reviewBody = ""
    @State private var reviewEvent = "COMMENT"
    @State private var reviewSummary = ""
    @State private var busy = false
    @State private var notice: String?
    @State private var error: String?

    private var service: GitHubService {
        GitHubService(
            root: model.projectURL,
            apiBase: settings.githubAPIBase,
            token: GitHubService.resolveToken() ?? ""
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("GitHub Pull Request", systemImage: "arrow.triangle.pull")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Refresh") { refresh() }
                    .disabled(busy)
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            Form {
                Section("Repository") {
                    LabeledContent("Remote", value: repository?.fullName ?? String(localized: "Detecting…"))
                    LabeledContent("Branch", value: branch.isEmpty ? "—" : branch)
                    LabeledContent("Default base", value: defaultBranch)
                    LabeledContent("Authentication") {
                        Text(LocalizedStringKey(tokenAvailable ? "gh CLI token" : "Not available"))
                    }
                    if !tokenAvailable {
                        Text("Run `gh auth login` or set GITHUB_TOKEN before creating pull requests.")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                    TextField("GitHub API base", text: $settings.githubAPIBase)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.caption, design: .monospaced))
                        .accessibilityLabel("GitHub API base URL")
                }

                if let pullRequest {
                    Section("Open pull request") {
                        LabeledContent("#\(pullRequest.number)", value: pullRequest.title)
                        LabeledContent("Base", value: pullRequest.base)
                        HStack {
                            Button("Open in Browser") {
                                if let url = URL(string: pullRequest.htmlURL) {
                                    NSWorkspace.shared.open(url)
                                }
                            }
                            Spacer()
                            Text(pullRequest.htmlURL)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }

                    Section("Review comment") {
                        TextField("File path", text: $reviewPath)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(.caption, design: .monospaced))
                            .accessibilityLabel("Review file path")
                        Stepper("Line \(reviewLine)", value: $reviewLine, in: 1...100_000)
                        TextEditor(text: $reviewBody)
                            .font(.body)
                            .frame(minHeight: 64)
                            .accessibilityLabel("Review comment body")
                        Button("Add Review Comment") { addComment() }
                            .disabled(busy || reviewPath.isEmpty || reviewBody.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }

                    Section("Submit review") {
                        Picker("Decision", selection: $reviewEvent) {
                            Text("Comment").tag("COMMENT")
                            Text("Approve").tag("APPROVE")
                            Text("Request changes").tag("REQUEST_CHANGES")
                        }
                        TextEditor(text: $reviewSummary)
                            .font(.body)
                            .frame(minHeight: 54)
                            .accessibilityLabel("Review summary")
                        Button("Submit Review") { submitReview() }
                            .disabled(busy)
                    }
                } else {
                    Section("Create pull request") {
                        TextField("Title", text: $title)
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Pull request title")
                        TextEditor(text: $prBody)
                            .font(.body)
                            .frame(minHeight: 70)
                            .accessibilityLabel("Pull request description")
                        TextField("Base branch", text: $defaultBranch)
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Base branch")
                        Toggle("Draft", isOn: $draft)
                            .accessibilityLabel("Draft pull request")
                        HStack {
                            Button("Push Branch") { push() }
                                .disabled(busy || branch.isEmpty)
                            Spacer()
                            Button("Create Pull Request") { create() }
                                .disabled(busy || title.isEmpty || branch.isEmpty || repository == nil)
                        }
                    }
                }

                if let notice {
                    Section {
                        Text(notice)
                            .font(.caption)
                            .foregroundStyle(.green)
                            .textSelection(.enabled)
                    }
                }
                if let error {
                    Section {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .textSelection(.enabled)
                    }
                }
            }
            .formStyle(.grouped)
        }
        .padding(16)
        .frame(width: 640, height: 720)
        .task { refresh() }
    }

    private func refresh() {
        busy = true
        Task {
            let repo = await GitHubService.repository(in: model.projectURL)
            let current = await GitHubService.currentBranch(in: model.projectURL) ?? ""
            let token = GitHubService.resolveToken()
            var base = "main"
            var open: [GitHubPullRequest] = []
            if let repo, token != nil {
                let api = GitHubService(
                    root: model.projectURL,
                    apiBase: settings.githubAPIBase,
                    token: token!
                )
                base = (try? await api.defaultBranch(repository: repo)) ?? "main"
                open = (try? await api.openPullRequests(repository: repo, branch: current)) ?? []
            }
            await MainActor.run {
                repository = repo
                branch = current
                tokenAvailable = token != nil
                defaultBranch = base
                pullRequest = open.first
                if title.isEmpty {
                    title = current.replacingOccurrences(of: "-", with: " ")
                }
                if reviewPath.isEmpty {
                    reviewPath = initialPath ?? model.gitStatus.files.first?.path ?? ""
                }
                busy = false
                error = repo == nil ? "No GitHub origin remote was found." : nil
            }
        }
    }

    private func push() {
        busy = true
        error = nil
        Task {
            switch await GitHubService.push(in: model.projectURL, branch: branch) {
            case .success(let output):
                notice = output.isEmpty ? "Pushed \(branch)." : output
            case .failure(let failure):
                error = failure.localizedDescription
            }
            busy = false
            refresh()
        }
    }

    private func create() {
        guard let repository else { return }
        busy = true
        error = nil
        notice = nil
        Task {
            do {
                let created = try await service.createPullRequest(
                    repository: repository,
                    title: title,
                    body: prBody,
                    head: branch,
                    base: defaultBranch,
                    draft: draft
                )
                await MainActor.run {
                    pullRequest = created
                    notice = "Created pull request #\(created.number)."
                    busy = false
                }
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    busy = false
                }
            }
        }
    }

    private func addComment() {
        guard let repository, let pullRequest else { return }
        busy = true
        error = nil
        Task {
            guard let commit = await GitHubService.headSHA(in: model.projectURL) else {
                await MainActor.run {
                    error = "Could not read HEAD."
                    busy = false
                }
                return
            }
            do {
                let url = try await service.addReviewComment(
                    repository: repository,
                    number: pullRequest.number,
                    commitID: commit,
                    comment: GitHubReviewCommentInput(path: reviewPath, line: reviewLine, body: reviewBody)
                )
                await MainActor.run {
                    notice = url
                    reviewBody = ""
                    busy = false
                }
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    busy = false
                }
            }
        }
    }

    private func submitReview() {
        guard let repository, let pullRequest else { return }
        busy = true
        error = nil
        Task {
            guard let commit = await GitHubService.headSHA(in: model.projectURL) else {
                await MainActor.run {
                    error = "Could not read HEAD."
                    busy = false
                }
                return
            }
            var comments: [GitHubReviewCommentInput] = []
            if !reviewPath.isEmpty,
               !reviewBody.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                comments = [GitHubReviewCommentInput(path: reviewPath, line: reviewLine, body: reviewBody)]
            }
            do {
                let url = try await service.submitReview(
                    repository: repository,
                    number: pullRequest.number,
                    event: reviewEvent,
                    body: reviewSummary,
                    comments: comments,
                    commitID: commit
                )
                await MainActor.run {
                    notice = url
                    reviewSummary = ""
                    reviewBody = ""
                    busy = false
                }
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    busy = false
                }
            }
        }
    }
}
