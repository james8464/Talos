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

enum GitService {
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
