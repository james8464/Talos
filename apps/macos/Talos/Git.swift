import Foundation

struct GitChangedFile: Identifiable, Hashable, Sendable {
    let path: String
    let status: String
    let staged: Bool

    var id: String { "\(status)|\(staged)|\(path)" }

    var label: String {
        switch status {
        case "??": return "Untracked"
        case "M": return "Modified"
        case "A": return "Added"
        case "D": return "Deleted"
        case "R": return "Renamed"
        default: return status
        }
    }
}

struct GitStatus: Equatable, Sendable {
    var branch: String?
    var files: [GitChangedFile] = []
    var ahead = 0
    var behind = 0
    var isRepository = false

    var hasChanges: Bool { !files.isEmpty }
}

struct GitWorktree: Sendable {
    let path: String
    let branch: String
}

struct GitWorktreeInfo: Identifiable, Hashable, Sendable {
    let path: String
    let branch: String?
    let head: String
    let isBare: Bool
    let isLocked: Bool
    let isCurrent: Bool

    var id: String { path }

    var name: String {
        URL(fileURLWithPath: path).lastPathComponent
    }

    var branchLabel: String {
        branch ?? "detached"
    }

    var isMain: Bool { isCurrent }
}

enum GitService {
    static func createWorktree(
        in root: URL,
        name: String,
        branchPrefix: String
    ) async -> Result<GitWorktree, Error> {
        let status = await status(in: root)
        guard status.isRepository else {
            return .failure(GitError.message("The project folder is not a Git repository."))
        }
        let slug = name
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let safeName = slug.isEmpty ? "task" : slug
        let branch = branchPrefix + safeName
        let parent = root.deletingLastPathComponent()
        var path = parent.appendingPathComponent(root.lastPathComponent + "-" + safeName, isDirectory: true)
        var suffix = 1
        while FileManager.default.fileExists(atPath: path.path) {
            path = parent.appendingPathComponent(root.lastPathComponent + "-" + safeName + "-\(suffix)", isDirectory: true)
            suffix += 1
        }

        let result = await run([
            "worktree", "add", "-b", branch, path.path,
        ], in: root)
        guard result.code == 0 else {
            return .failure(GitError.message(result.text.isEmpty ? "git worktree add failed." : result.text))
        }
        return .success(GitWorktree(path: path.path, branch: branch))
    }

    static func listWorktrees(in root: URL) async -> [GitWorktreeInfo] {
        let result = await run(["worktree", "list", "--porcelain"], in: root)
        guard result.code == 0 else { return [] }
        let current = root.resolvingSymlinksInPath().standardizedFileURL.path
        var items: [GitWorktreeInfo] = []
        var path: String?
        var head = ""
        var branch: String?
        var bare = false
        var locked = false

        func flush() {
            guard let path else { return }
            let normalized = URL(fileURLWithPath: path)
                .resolvingSymlinksInPath()
                .standardizedFileURL
                .path
            items.append(GitWorktreeInfo(
                path: normalized,
                branch: branch,
                head: head,
                isBare: bare,
                isLocked: locked,
                isCurrent: normalized == current || current.hasPrefix(normalized + "/")
            ))
        }

        for line in result.text.components(separatedBy: "\n") {
            if line.isEmpty {
                flush()
                path = nil
                head = ""
                branch = nil
                bare = false
                locked = false
                continue
            }
            if line.hasPrefix("worktree ") {
                path = String(line.dropFirst("worktree ".count))
            } else if line.hasPrefix("HEAD ") {
                head = String(line.dropFirst("HEAD ".count))
            } else if line.hasPrefix("branch ") {
                let ref = String(line.dropFirst("branch ".count))
                branch = ref.hasPrefix("refs/heads/") ? String(ref.dropFirst("refs/heads/".count)) : ref
            } else if line == "bare" {
                bare = true
            } else if line == "locked" || line.hasPrefix("locked ") {
                locked = true
            }
        }
        flush()
        return items
    }

    static func currentBranch(in root: URL) async -> String? {
        let result = await run(["branch", "--show-current"], in: root)
        let value = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return result.code == 0 && !value.isEmpty ? value : nil
    }

    static func mergeWorktree(
        in root: URL,
        branch: String,
        message: String? = nil
    ) async -> Result<String, Error> {
        let status = await status(in: root)
        guard status.isRepository else {
            return .failure(GitError.message("The project folder is not a Git repository."))
        }
        let trimmed = branch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return .failure(GitError.message("Choose a branch to merge."))
        }
        if let current = await currentBranch(in: root), current == trimmed {
            return .failure(GitError.message("\(trimmed) is already the current branch."))
        }
        guard !status.hasChanges else {
            return .failure(GitError.message(
                "Commit or stash the changes in \(root.lastPathComponent) before merging."
            ))
        }

        let title = message?.trimmingCharacters(in: .whitespacesAndNewlines)
        let arguments = ["merge", "--no-ff", trimmed, "-m", (title?.isEmpty == false ? title! : "Merge branch '\(trimmed)'")]
        let result = await run(arguments, in: root)
        guard result.code == 0 else {
            _ = await run(["merge", "--abort"], in: root)
            let text = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failure(GitError.message(text.isEmpty ? "Merge failed." : text))
        }
        return .success(result.text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    static func removeWorktree(
        in root: URL,
        path: String,
        branch: String?,
        force: Bool = false,
        deleteBranch: Bool = false
    ) async -> Result<String, Error> {
        let target = URL(fileURLWithPath: path).standardizedFileURL
        let rootPath = root.standardizedFileURL.path
        guard target.path != rootPath else {
            return .failure(GitError.message("The main worktree cannot be removed."))
        }

        var arguments = ["worktree", "remove"]
        if force { arguments.append("--force") }
        arguments.append(target.path)
        let removal = await run(arguments, in: root)
        guard removal.code == 0 else {
            let text = removal.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failure(GitError.message(text.isEmpty ? "git worktree remove failed." : text))
        }
        _ = await run(["worktree", "prune"], in: root)

        var notes = ["Removed worktree \(target.lastPathComponent)."]
        if let branch, !branch.isEmpty {
            let deletion = await run(["branch", force ? "-D" : "-d", branch], in: root)
            if deletion.code == 0 {
                notes.append("Deleted branch \(branch).")
            } else {
                let reason = deletion.text.trimmingCharacters(in: .whitespacesAndNewlines)
                notes.append("Kept branch \(branch): \(reason.isEmpty ? "it is not fully merged." : reason)")
            }
        }
        return .success(notes.joined(separator: "\n"))
    }

    static func status(in root: URL) async -> GitStatus {
        let result = await run(["status", "--porcelain=v1", "-b"], in: root)
        guard result.code == 0 else { return GitStatus() }
        var status = GitStatus(isRepository: true)
        for (index, line) in result.text.components(separatedBy: "\n").enumerated() {
            if index == 0, line.hasPrefix("## ") {
                let branchInfo = String(line.dropFirst(3))
                if let range = branchInfo.range(of: "...") {
                    status.branch = String(branchInfo[..<range.lowerBound])
                } else if let range = branchInfo.range(of: " [") {
                    status.branch = String(branchInfo[..<range.lowerBound])
                } else {
                    status.branch = branchInfo
                }
                if let ahead = regexInt("ahead (\\d+)", in: branchInfo) { status.ahead = ahead }
                if let behind = regexInt("behind (\\d+)", in: branchInfo) { status.behind = behind }
                continue
            }
            guard line.count >= 3 else { continue }
            let code = String(line.prefix(2))
            let path = String(line.dropFirst(3))
            let staged = code.first != " " && code.first != "?"
            status.files.append(GitChangedFile(path: path, status: code.trimmingCharacters(in: .whitespaces), staged: staged))
        }
        return status
    }

    static func diff(in root: URL, path: String? = nil) async -> String {
        var arguments = ["diff", "--no-ext-diff"]
        if let path { arguments.append(contentsOf: ["--", path]) }
        let unstaged = await run(arguments, in: root)
        var argumentsStaged = ["diff", "--cached", "--no-ext-diff"]
        if let path { argumentsStaged.append(contentsOf: ["--", path]) }
        let staged = await run(argumentsStaged, in: root)
        let combined = [staged.text, unstaged.text]
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .joined(separator: "\n")
        if combined.isEmpty {
            return "No diff for this file. It may be untracked."
        }
        return combined
    }

    static func stageAll(in root: URL) async -> Result<Void, Error> {
        let result = await run(["add", "-A"], in: root)
        if result.code == 0 { return .success(()) }
        return .failure(GitError.message(result.text.isEmpty ? "git add failed." : result.text))
    }

    static func commit(in root: URL, message: String) async -> Result<String, Error> {
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .failure(GitError.message("A commit message is required.")) }
        let result = await run(["commit", "-m", trimmed], in: root)
        if result.code == 0 { return .success(result.text) }
        return .failure(GitError.message(result.text.isEmpty ? "git commit failed." : result.text))
    }

    private static func regexInt(_ pattern: String, in text: String) -> Int? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..<text.endIndex, in: text)),
              let range = Range(match.range(at: 1), in: text) else {
            return nil
        }
        return Int(text[range])
    }

    private struct ProcessResult: Sendable {
        let code: Int32
        let text: String
    }

    private static func run(_ arguments: [String], in root: URL) async -> ProcessResult {
        await Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            process.arguments = arguments
            process.currentDirectoryURL = root
            process.environment = ProcessInfo.processInfo.environment.merging(["PWD": root.path]) { _, new in new }
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
            } catch {
                return ProcessResult(code: -1, text: error.localizedDescription)
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return ProcessResult(
                code: process.terminationStatus,
                text: String(decoding: data, as: UTF8.self)
            )
        }.value
    }
}

enum GitError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}
