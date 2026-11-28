import Foundation

struct TerminalResult: Sendable {
    let output: String
    let exitCode: Int?
    let timedOut: Bool
}

enum TerminalError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

/// A persistent interactive shell backed by a pseudo-terminal.
///
/// The shell is launched through `/usr/bin/script`, which allocates a PTY for
/// zsh. A temporary `ZDOTDIR` supplies a quiet prompt so command output can be
/// separated with sentinel markers while keeping working directory and
/// environment changes between calls.
actor TerminalSession {
    let workspace: URL
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private var buffer = ""
    private var started = false
    private var closed = false
    private var busy = false
    private var nextMarker = 1
    private var startupDirectory: URL?

    init(workspace: URL) {
        self.workspace = workspace
    }

    func start() async throws {
        guard !started else { return }
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-pty-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        startupDirectory = directory
        let rc = """
        PROMPT=''
        RPROMPT=''
        PROMPT_EOL_MARK=''
        unsetopt prompt_sp 2>/dev/null
        unsetopt zle 2>/dev/null
        stty -echo 2>/dev/null
        """
        try rc.write(to: directory.appendingPathComponent(".zshrc"), atomically: true, encoding: .utf8)

        process.executableURL = URL(fileURLWithPath: "/usr/bin/script")
        process.arguments = ["-q", "/dev/null", "/bin/zsh", "-i"]
        process.currentDirectoryURL = workspace
        var environment = ProcessInfo.processInfo.environment
        environment["ZDOTDIR"] = directory.path
        environment["TERM"] = "dumb"
        environment["PROMPT"] = ""
        environment["PS1"] = ""
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = output
        try process.run()
        started = true

        let handle = output.fileHandleForReading
        Task.detached(priority: .utility) { [weak self] in
            while true {
                let data = handle.availableData
                if data.isEmpty { break }
                await self?.append(data)
            }
            await self?.markClosed()
        }

        // Wait for the shell to source its rc file and answer a probe.
        try write("printf '__TALOS_READY__\\n'\n")
        for _ in 0..<80 {
            try await Task.sleep(nanoseconds: 100_000_000)
            if buffer.contains("__TALOS_READY__") {
                buffer = ""
                // Re-apply quiet terminal modes once zsh has finished initialising.
                try write("stty -echo 2>/dev/null\n")
                try await Task.sleep(nanoseconds: 300_000_000)
                buffer = ""
                return
            }
            if closed {
                throw TerminalError.message("The interactive shell exited during startup.")
            }
        }
        throw TerminalError.message("The interactive shell did not become ready.")
    }

    func run(_ command: String, timeout: Double) async throws -> TerminalResult {
        guard started, !closed else {
            throw TerminalError.message("The terminal is not running.")
        }
        guard !busy else {
            throw TerminalError.message("A terminal command is already running. Send Ctrl-C to interrupt it first.")
        }
        busy = true
        defer { busy = false }

        let marker = "__TALOS_DONE_\(nextMarker)__"
        nextMarker += 1
        buffer = ""
        try write(command + "\n")
        try write("printf '\\n\(marker):%s\\n' $?\n")

        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let result = extract(marker: marker) {
                return result
            }
            if closed {
                return TerminalResult(output: Self.clean(buffer), exitCode: nil, timedOut: false)
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        return TerminalResult(output: Self.clean(buffer), exitCode: nil, timedOut: true)
    }

    func send(_ text: String) throws {
        guard started, !closed else {
            throw TerminalError.message("The terminal is not running.")
        }
        try write(text + "\n")
    }

    func read() -> String {
        let text = Self.clean(buffer)
        buffer = ""
        return text
    }

    func interrupt() throws {
        guard started, !closed else { return }
        try write("\u{03}")
    }

    func close() {
        guard started, !closed else { return }
        closed = true
        try? write("exit\n")
        if process.isRunning {
            DispatchQueue.global().asyncAfter(deadline: .now() + 1) { [process] in
                if process.isRunning { process.terminate() }
            }
        }
        if let startupDirectory {
            try? FileManager.default.removeItem(at: startupDirectory)
        }
    }

    private func extract(marker: String) -> TerminalResult? {
        let normalized = buffer.replacingOccurrences(of: "\r\n", with: "\n")
        guard let markerRange = normalized.range(of: marker + ":") else { return nil }
        let afterMarker = normalized[markerRange.upperBound...]
        guard let newline = afterMarker.firstIndex(of: "\n") else { return nil }
        let codeText = afterMarker[..<newline].trimmingCharacters(in: .whitespacesAndNewlines)
        var end = markerRange.lowerBound
        while end > normalized.startIndex {
            let previous = normalized.index(before: end)
            if normalized[previous] == "\n" {
                end = previous
            } else {
                break
            }
        }
        let text = String(normalized[normalized.startIndex..<end])
        let result = TerminalResult(
            output: Self.clean(text),
            exitCode: Int(codeText) ?? 0,
            timedOut: false
        )
        buffer = String(normalized[normalized.index(after: newline)...])
        return result
    }

    private func append(_ data: Data) {
        buffer += String(decoding: data, as: UTF8.self)
        if buffer.count > 4_000_000 {
            buffer = String(buffer.suffix(2_000_000))
        }
    }

    private func markClosed() {
        closed = true
    }

    private func write(_ text: String) throws {
        try input.fileHandleForWriting.write(contentsOf: Data(text.utf8))
    }

    static func clean(_ text: String) -> String {
        var value = text
        let patterns = [
            "\u{001B}\\[[0-9;?]*[A-Za-z]",
            "\u{001B}\\][^\u{0007}]*\u{0007}",
            "\u{001B}[=>]",
        ]
        for pattern in patterns {
            if let regex = try? NSRegularExpression(pattern: pattern) {
                let range = NSRange(value.startIndex..<value.endIndex, in: value)
                value = regex.stringByReplacingMatches(in: value, range: range, withTemplate: "")
            }
        }
        value = value.replacingOccurrences(of: "\r\n", with: "\n")
        value = value.replacingOccurrences(of: "\r", with: "")
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

actor TerminalManager {
    static let shared = TerminalManager()

    private var sessions: [String: TerminalSession] = [:]

    func session(for workspace: String) async throws -> TerminalSession {
        let key = URL(fileURLWithPath: workspace).standardizedFileURL.path
        if let existing = sessions[key] {
            return existing
        }
        let session = TerminalSession(workspace: URL(fileURLWithPath: key, isDirectory: true))
        try await session.start()
        sessions[key] = session
        return session
    }

    func close(for workspace: String) async {
        let key = URL(fileURLWithPath: workspace).standardizedFileURL.path
        if let session = sessions.removeValue(forKey: key) {
            await session.close()
        }
    }

    func closeAll() async {
        for session in sessions.values {
            await session.close()
        }
        sessions.removeAll()
    }
}
