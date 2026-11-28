import Foundation

struct GitHubRepository: Sendable {
    let owner: String
    let name: String

    var fullName: String { "\(owner)/\(name)" }
}

struct GitHubPullRequest: Identifiable, Sendable {
    let id: Int
    let number: Int
    let title: String
    let htmlURL: String
    let state: String
    let head: String
    let base: String
    let draft: Bool
}

struct GitHubReviewCommentInput: Sendable {
    let path: String
    let line: Int
    let body: String
}

enum GitHubError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

struct GitHubService: Sendable {
    let root: URL
    let apiBase: URL
    let token: String

    init(root: URL, apiBase: String, token: String) {
        self.root = root
        self.token = token
        self.apiBase = URL(string: apiBase) ?? URL(string: "https://api.github.com")!
    }

    static func resolveToken() -> String? {
        let environment = ProcessInfo.processInfo.environment
        for key in ["GITHUB_TOKEN", "GH_TOKEN"] {
            if let value = environment[key], !value.isEmpty { return value }
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [
            home.appendingPathComponent(".local/bin/gh").path,
            "/opt/homebrew/bin/gh",
            "/usr/local/bin/gh",
            "/usr/bin/gh",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            if let token = runToken(executable: path) { return token }
        }
        return nil
    }

    private static func runToken(executable: String) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = ["auth", "token"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        let token = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }

    static func repository(in root: URL) async -> GitHubRepository? {
        guard let remote = await git(["remote", "get-url", "origin"], in: root) else { return nil }
        return parseRemote(remote)
    }

    static func parseRemote(_ remote: String) -> GitHubRepository? {
        let value = remote.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("git@") {
            let parts = value.split(separator: ":", maxSplits: 1)
            guard parts.count == 2 else { return nil }
            return repoPath(String(parts[1]))
        }
        if let url = URL(string: value), let host = url.host, host.contains("github") {
            return repoPath(url.path)
        }
        if value.contains("github.com/") {
            return repoPath(value)
        }
        return nil
    }

    private static func repoPath(_ raw: String) -> GitHubRepository? {
        var path = raw
        if let range = path.range(of: "github.com/") {
            path = String(path[range.upperBound...])
        }
        path = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if path.hasSuffix(".git") { path.removeLast(4) }
        let parts = path.split(separator: "/")
        guard parts.count >= 2 else { return nil }
        return GitHubRepository(owner: String(parts[0]), name: String(parts[1]))
    }

    static func currentBranch(in root: URL) async -> String? {
        await git(["branch", "--show-current"], in: root)
    }

    static func headSHA(in root: URL) async -> String? {
        await git(["rev-parse", "HEAD"], in: root)
    }

    static func push(in root: URL, branch: String) async -> Result<String, Error> {
        guard let result = await gitResult(["push", "-u", "origin", branch], in: root) else {
            return .failure(GitHubError.message("Could not run git."))
        }
        guard result.code == 0 else {
            return .failure(GitHubError.message(result.text.isEmpty ? "git push failed." : result.text))
        }
        return .success(result.text)
    }

    func defaultBranch(repository: GitHubRepository) async throws -> String {
        let response = try await request(
            "GET",
            path: "/repos/\(repository.owner)/\(repository.name)"
        )
        return response["default_branch"] as? String ?? "main"
    }

    func openPullRequests(repository: GitHubRepository, branch: String) async throws -> [GitHubPullRequest] {
        let response = try await requestAny(
            "GET",
            path: "/repos/\(repository.owner)/\(repository.name)/pulls",
            query: [
                "state": "open",
                "head": "\(repository.owner):\(branch)",
            ]
        )
        let rows = response as? [[String: Any]] ?? []
        return rows.compactMap(parsePullRequest)
    }

    func createPullRequest(
        repository: GitHubRepository,
        title: String,
        body: String,
        head: String,
        base: String,
        draft: Bool
    ) async throws -> GitHubPullRequest {
        let response = try await request(
            "POST",
            path: "/repos/\(repository.owner)/\(repository.name)/pulls",
            body: [
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
            ]
        )
        guard let pr = parsePullRequest(response) else {
            throw GitHubError.message("GitHub returned an unreadable pull request.")
        }
        return pr
    }

    func addReviewComment(
        repository: GitHubRepository,
        number: Int,
        commitID: String,
        comment: GitHubReviewCommentInput
    ) async throws -> String {
        let response = try await request(
            "POST",
            path: "/repos/\(repository.owner)/\(repository.name)/pulls/\(number)/comments",
            body: [
                "body": comment.body,
                "commit_id": commitID,
                "path": comment.path,
                "line": comment.line,
                "side": "RIGHT",
            ]
        )
        return response["html_url"] as? String ?? "Review comment added."
    }

    func submitReview(
        repository: GitHubRepository,
        number: Int,
        event: String,
        body: String,
        comments: [GitHubReviewCommentInput],
        commitID: String
    ) async throws -> String {
        let payloadComments = comments.map { comment in
            [
                "path": comment.path,
                "line": comment.line,
                "side": "RIGHT",
                "body": comment.body,
            ] as [String: Any]
        }
        let response = try await request(
            "POST",
            path: "/repos/\(repository.owner)/\(repository.name)/pulls/\(number)/reviews",
            body: [
                "commit_id": commitID,
                "event": event,
                "body": body,
                "comments": payloadComments,
            ]
        )
        return response["html_url"] as? String ?? "Review submitted."
    }

    private func request(
        _ method: String,
        path: String,
        body: [String: Any]? = nil
    ) async throws -> [String: Any] {
        let any = try await requestAny(method, path: path, query: [:], body: body)
        guard let object = any as? [String: Any] else {
            throw GitHubError.message("GitHub returned an unreadable response.")
        }
        return object
    }

    private func requestAny(
        _ method: String,
        path: String,
        query: [String: String] = [:],
        body: [String: Any]? = nil
    ) async throws -> Any {
        var components = URLComponents(url: apiBase, resolvingAgainstBaseURL: false)
        components?.path = path
        if !query.isEmpty {
            components?.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components?.url else {
            throw GitHubError.message("The GitHub API URL is invalid.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        request.setValue("Talos", forHTTPHeaderField: "User-Agent")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw GitHubError.message("GitHub returned no response.")
        }
        guard (200..<300).contains(http.statusCode) else {
            let object = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            throw GitHubError.message(Self.describe(error: object, status: http.statusCode))
        }
        return (try? JSONSerialization.jsonObject(with: data)) ?? [:]
    }

    static func describe(error object: [String: Any], status: Int) -> String {
        let base = object["message"] as? String ?? "GitHub answered \(status)."
        let details = (object["errors"] as? [[String: Any]] ?? []).compactMap { row -> String? in
            if let message = row["message"] as? String { return message }
            if let code = row["code"] as? String { return code }
            return nil
        }
        guard !details.isEmpty else { return base }
        return base + ": " + details.joined(separator: "; ")
    }

    private func parsePullRequest(_ row: [String: Any]) -> GitHubPullRequest? {
        guard let number = row["number"] as? Int else { return nil }
        return GitHubPullRequest(
            id: number,
            number: number,
            title: row["title"] as? String ?? "Pull request",
            htmlURL: row["html_url"] as? String ?? "",
            state: row["state"] as? String ?? "open",
            head: ((row["head"] as? [String: Any])?["ref"] as? String) ?? "",
            base: ((row["base"] as? [String: Any])?["ref"] as? String) ?? "",
            draft: row["draft"] as? Bool ?? false
        )
    }

    private static func git(_ arguments: [String], in root: URL) async -> String? {
        guard let result = await gitResult(arguments, in: root), result.code == 0 else { return nil }
        let text = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    private static func gitResult(
        _ arguments: [String],
        in root: URL
    ) async -> (code: Int32, text: String)? {
        await Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            process.arguments = arguments
            process.currentDirectoryURL = root
            process.environment = ProcessInfo.processInfo.environment.merging(["PWD": root.path]) { _, new in new }
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do { try process.run() } catch { return nil }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, String(decoding: data, as: UTF8.self))
        }.value
    }
}
