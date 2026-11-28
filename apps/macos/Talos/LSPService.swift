import Foundation

struct LSPDiagnostic: Sendable {
    let path: String
    let line: Int
    let column: Int
    let endLine: Int
    let endColumn: Int
    let severity: Int
    let message: String
    let source: String?
    let code: String?

    var severityLabel: String {
        switch severity {
        case 1: return "error"
        case 2: return "warning"
        case 3: return "info"
        default: return "hint"
        }
    }
}

struct LSPDocumentSymbol: Sendable {
    let name: String
    let kind: String
    let line: Int
    let column: Int
    let detail: String?
}

struct LSPCodeActionSummary: Sendable {
    let index: Int
    let title: String
    let kind: String?
    let hasEdit: Bool
}

struct LSPApplyResult: Sendable {
    let title: String
    let changedFiles: [String]
    let editCount: Int
}

enum LSPError: LocalizedError {
    case message(String)
    case unsupported(String)
    case timeout(String)

    var errorDescription: String? {
        switch self {
        case .message(let value), .unsupported(let value), .timeout(let value):
            return value
        }
    }
}

enum LSPLanguage: String, Sendable {
    case swift
    case c
    case cpp
    case objectivec
    case typescript
    case javascript
    case python
    case go
    case rust
    case json

    static func detect(path: String) -> LSPLanguage? {
        switch URL(fileURLWithPath: path).pathExtension.lowercased() {
        case "swift": return .swift
        case "c", "h": return .c
        case "cc", "cpp", "cxx", "hpp", "hh": return .cpp
        case "m", "mm": return .objectivec
        case "ts", "tsx": return .typescript
        case "js", "jsx": return .javascript
        case "py", "pyi": return .python
        case "go": return .go
        case "rs": return .rust
        case "json", "jsonc": return .json
        default: return nil
        }
    }

    var identifier: String { rawValue }

    var command: [String] {
        switch self {
        case .swift:
            return ["sourcekit-lsp"]
        case .c, .cpp, .objectivec:
            return ["clangd", "--background-index=false"]
        case .typescript, .javascript:
            return ["typescript-language-server", "--stdio"]
        case .python:
            return ["pyright-langserver", "--stdio"]
        case .go:
            return ["gopls"]
        case .rust:
            return ["rust-analyzer"]
        case .json:
            return ["vscode-json-language-server", "--stdio"]
        }
    }

    var extensions: [String] {
        switch self {
        case .swift: return ["swift"]
        case .c: return ["c", "h"]
        case .cpp: return ["cc", "cpp", "cxx", "hpp", "hh"]
        case .objectivec: return ["m", "mm"]
        case .typescript: return ["ts", "tsx"]
        case .javascript: return ["js", "jsx"]
        case .python: return ["py", "pyi"]
        case .go: return ["go"]
        case .rust: return ["rs"]
        case .json: return ["json", "jsonc"]
        }
    }
}

actor LSPServer {
    let language: LSPLanguage
    let root: URL
    private let executable: URL
    private let arguments: [String]
    private let process = Process()
    private let stdinPipe = Pipe()
    private let stdoutPipe = Pipe()
    private let stderrPipe = Pipe()

    private var buffer = Data()
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<Any, Error>] = [:]
    private var diagnosticsByURI: [String: [[String: Any]]] = [:]
    private var waiters: [String: [CheckedContinuation<Void, Never>]] = [:]
    private var opened: Set<String> = []
    private var versions: [String: Int] = [:]
    private var closed = false

    init(language: LSPLanguage, root: URL, executable: URL, arguments: [String]) {
        self.language = language
        self.root = root
        self.executable = executable
        self.arguments = arguments
    }

    var key: String { "\(language.identifier)|\(root.path)" }

    func start() async throws {
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = root
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PWD": root.path,
        ]) { _, new in new }
        do {
            try process.run()
        } catch {
            throw LSPError.message("Could not start \(executable.lastPathComponent): \(error.localizedDescription)")
        }
        let errorHandle = stderrPipe.fileHandleForReading
        Task.detached(priority: .utility) {
            let handle = errorHandle
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }

            }
        }
        Task.detached(priority: .utility) { [weak self] in
            guard let self else { return }
            let handle = self.stdoutPipe.fileHandleForReading
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                await self.receive(chunk)
            }
            await self.markClosed()
        }
        _ = try? await request("initialize", [
            "processId": ProcessInfo.processInfo.processIdentifier,
            "clientInfo": ["name": "Talos", "version": "1.0"],
            "rootUri": root.absoluteString,
            "workspaceFolders": [["uri": root.absoluteString, "name": root.lastPathComponent]],
            "capabilities": [
                "textDocument": [
                    "synchronization": ["didSave": true, "dynamicRegistration": false],
                    "publishDiagnostics": ["relatedInformation": true],
                    "documentSymbol": ["hierarchicalDocumentSymbolSupport": true],
                    "codeAction": ["codeActionLiteralSupport": ["codeActionKind": ["valueSet": ["quickfix", "refactor", "refactor.extract", "refactor.inline", "source"]]]],
                    "formatting": ["dynamicRegistration": false],
                ],
                "workspace": ["workspaceFolders": true, "applyEdit": false],
            ],
        ], timeout: 30)
        notify("initialized", [:])
    }

    func shutdown() {
        guard !closed else { return }
        closed = true
        notify("shutdown", NSNull())
        notify("exit", NSNull())
        try? stdinPipe.fileHandleForWriting.close()
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [process] in
            if process.isRunning { process.terminate() }
        }
    }

    func open(path: String) async throws {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        let uri = url.absoluteString
        if opened.contains(uri) { return }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            throw LSPError.message("Could not read \(path) as UTF-8.")
        }
        opened.insert(uri)
        versions[uri] = 1
        notify("textDocument/didOpen", [
            "textDocument": [
                "uri": uri,
                "languageId": language.identifier,
                "version": 1,
                "text": text,
            ],
        ])
    }

    func refresh(path: String) {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        let uri = url.absoluteString
        guard opened.contains(uri),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return }
        let version = (versions[uri] ?? 1) + 1
        versions[uri] = version
        notify("textDocument/didChange", [
            "textDocument": ["uri": uri, "version": version],
            "contentChanges": [["text": text]],
        ])
    }

    func diagnostics(path: String, timeout: Double) async throws -> [LSPDiagnostic] {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        try await open(path: url.path)
        let uri = url.absoluteString
        diagnosticsByURI[uri] = nil
        refresh(path: url.path)
        await waitForDiagnostics(uri: uri, timeout: timeout)
        let rows = diagnosticsByURI[uri] ?? []
        return rows.compactMap { row in
            guard let range = row["range"] as? [String: Any],
                  let start = range["start"] as? [String: Any] else { return nil }
            let end = range["end"] as? [String: Any] ?? start
            return LSPDiagnostic(
                path: url.path,
                line: (start["line"] as? Int ?? 0) + 1,
                column: (start["character"] as? Int ?? 0) + 1,
                endLine: (end["line"] as? Int ?? start["line"] as? Int ?? 0) + 1,
                endColumn: (end["character"] as? Int ?? start["character"] as? Int ?? 0) + 1,
                severity: row["severity"] as? Int ?? 3,
                message: row["message"] as? String ?? "",
                source: row["source"] as? String,
                code: Self.codeString(row["code"])
            )
        }
    }

    func symbols(path: String) async throws -> [LSPDocumentSymbol] {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        try await open(path: url.path)
        let result = try await request("textDocument/documentSymbol", [
            "textDocument": ["uri": url.absoluteString],
        ])
        guard let rows = result as? [[String: Any]] else { return [] }
        return Self.flattenSymbols(rows)
    }

    func codeActions(path: String, line: Int, character: Int) async throws -> [[String: Any]] {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        try await open(path: url.path)
        let start = ["line": max(line - 1, 0), "character": max(character - 1, 0)] as [String: Any]
        let range: [String: Any] = ["start": start, "end": start]
        let result = try await request("textDocument/codeAction", [
            "textDocument": ["uri": url.absoluteString],
            "range": range,
            "context": [
                "diagnostics": diagnosticsByURI[url.absoluteString] ?? [],
            ],
        ])
        return result as? [[String: Any]] ?? []
    }

    func request(_ method: String, _ params: Any, timeout: Double = 20) async throws -> Any {
        if closed { throw LSPError.message("The language server for \(language.rawValue) is not running.") }
        let id = nextID
        nextID += 1
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try send(["jsonrpc": "2.0", "id": id, "method": method, "params": params])
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
                return
            }
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self?.expire(id: id, method: method)
            }
        }
    }

    func notify(_ method: String, _ params: Any) {
        try? send(["jsonrpc": "2.0", "method": method, "params": params])
    }

    private func expire(id: Int, method: String) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(throwing: LSPError.timeout("\(language.rawValue): \(method) timed out."))
    }

    private func send(_ object: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: object)
        var message = Data("Content-Length: \(data.count)\r\n\r\n".utf8)
        message.append(data)
        try stdinPipe.fileHandleForWriting.write(contentsOf: message)
    }

    private func receive(_ chunk: Data) {
        buffer.append(chunk)
        while true {
            guard let headerEnd = buffer.range(of: Data("\r\n\r\n".utf8)) else { return }
            let headerData = buffer[buffer.startIndex..<headerEnd.lowerBound]
            guard let header = String(data: headerData, encoding: .utf8),
                  let lengthLine = header.split(separator: "\r\n").first(where: {
                      $0.lowercased().hasPrefix("content-length:")
                  }),
                  let length = Int(lengthLine.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) else {
                buffer.removeSubrange(buffer.startIndex..<headerEnd.upperBound)
                continue
            }
            let bodyStart = headerEnd.upperBound
            guard buffer.distance(from: bodyStart, to: buffer.endIndex) >= length else { return }
            let bodyEnd = buffer.index(bodyStart, offsetBy: length)
            let body = buffer[bodyStart..<bodyEnd]
            buffer.removeSubrange(buffer.startIndex..<bodyEnd)
            if let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                handle(object)
            }
        }
    }

    private func handle(_ object: [String: Any]) {
        if let id = object["id"] as? Int, let continuation = pending.removeValue(forKey: id) {
            if let error = object["error"] as? [String: Any] {
                continuation.resume(throwing: LSPError.message(error["message"] as? String ?? "Language server error."))
            } else {
                continuation.resume(returning: object["result"] ?? NSNull())
            }
            return
        }
        guard let method = object["method"] as? String else { return }
        if method == "textDocument/publishDiagnostics",
           let params = object["params"] as? [String: Any],
           let uri = params["uri"] as? String {
            diagnosticsByURI[uri] = params["diagnostics"] as? [[String: Any]] ?? []
            for waiter in waiters.removeValue(forKey: uri) ?? [] {
                waiter.resume()
            }
        } else if method == "workspace/configuration" {
            if let id = object["id"] {
                try? send(["jsonrpc": "2.0", "id": id, "result": [Any]()])
            }
        } else if let id = object["id"] {
            try? send(["jsonrpc": "2.0", "id": id, "result": NSNull()])
        }
    }

    private func waitForDiagnostics(uri: String, timeout: Double) async {
        await withCheckedContinuation { continuation in
            waiters[uri, default: []].append(continuation)
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self?.releaseWaiter(uri: uri)
            }
        }
    }

    private func releaseWaiter(uri: String) {
        for waiter in waiters.removeValue(forKey: uri) ?? [] {
            waiter.resume()
        }
    }

    private func markClosed() {
        closed = true
        for (id, continuation) in pending {
            pending.removeValue(forKey: id)
            continuation.resume(throwing: LSPError.message("The language server exited."))
        }
        for uri in waiters.keys {
            releaseWaiter(uri: uri)
        }
    }

    private static func codeString(_ value: Any?) -> String? {
        if let value = value as? String { return value }
        if let value = value as? Int { return String(value) }
        return nil
    }

    private static func flattenSymbols(_ rows: [[String: Any]], depth: Int = 0) -> [LSPDocumentSymbol] {
        var output: [LSPDocumentSymbol] = []
        for row in rows {
            let range = (row["selectionRange"] as? [String: Any]) ?? (row["range"] as? [String: Any])
            let start = range?["start"] as? [String: Any]
            let name = row["name"] as? String ?? "(anonymous)"
            output.append(LSPDocumentSymbol(
                name: String(repeating: "  ", count: depth) + name,
                kind: symbolKind(row["kind"] as? Int ?? 0),
                line: (start?["line"] as? Int ?? 0) + 1,
                column: (start?["character"] as? Int ?? 0) + 1,
                detail: row["detail"] as? String
            ))
            if let children = row["children"] as? [[String: Any]] {
                output.append(contentsOf: flattenSymbols(children, depth: depth + 1))
            }
        }
        return output
    }

    static func symbolKind(_ value: Int) -> String {
        switch value {
        case 1: return "file"
        case 2: return "module"
        case 3: return "namespace"
        case 4: return "package"
        case 5: return "class"
        case 6: return "method"
        case 7: return "property"
        case 8: return "field"
        case 9: return "constructor"
        case 10: return "enum"
        case 11: return "interface"
        case 12: return "function"
        case 13: return "variable"
        case 14: return "constant"
        case 15: return "string"
        case 16: return "number"
        case 17: return "boolean"
        case 18: return "array"
        case 19: return "object"
        case 20: return "key"
        case 21: return "null"
        case 22: return "enumMember"
        case 23: return "struct"
        case 24: return "event"
        case 25: return "operator"
        case 26: return "typeParameter"
        default: return "symbol"
        }
    }

    deinit {
        if process.isRunning { process.terminate() }
    }
}

actor LSPManager {
    static let shared = LSPManager()

    private var servers: [String: LSPServer] = [:]
    private var disabled: Set<String> = []

    static func executable(named name: String) -> URL? {
        let environment = ProcessInfo.processInfo.environment
        let paths = (environment["PATH"] ?? "")
            .split(separator: ":")
            .map(String.init) + [
                "/usr/bin",
                "/usr/local/bin",
                "/opt/homebrew/bin",
                "/opt/homebrew/opt/llvm/bin",
                "/usr/local/opt/llvm/bin",
            ]
        for directory in paths {
            let candidate = URL(fileURLWithPath: directory).appendingPathComponent(name)
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func availability() -> [(String, Bool)] {
        var rows: [(String, Bool)] = []
        var seen: Set<String> = []
        for language in [LSPLanguage.swift, .c, .cpp, .typescript, .python, .go, .rust, .json] {
            let name = language.command[0]
            if seen.contains(name) { continue }
            seen.insert(name)
            rows.append((name, executable(named: name) != nil))
        }
        return rows
    }

    func diagnostics(paths: [String], timeout: Double = 12, limit: Int = 40) async -> (results: [LSPDiagnostic], failures: [String]) {
        var results: [LSPDiagnostic] = []
        var failures: [String] = []
        for path in paths.prefix(limit) {
            do {
                results.append(contentsOf: try await diagnostics(path: path, timeout: timeout))
            } catch {
                failures.append("\(path): \(error.localizedDescription)")
            }
        }
        return (results, failures)
    }

    func diagnostics(path: String, timeout: Double = 12) async throws -> [LSPDiagnostic] {
        let server = try await server(for: path)
        return try await server.diagnostics(path: path, timeout: timeout)
    }

    func symbols(path: String) async throws -> [LSPDocumentSymbol] {
        let server = try await server(for: path)
        return try await server.symbols(path: path)
    }

    func codeActions(path: String, line: Int, character: Int) async throws -> [LSPCodeActionSummary] {
        let server = try await server(for: path)
        let raw = try await server.codeActions(path: path, line: line, character: character)
        return raw.enumerated().compactMap { index, row in
            guard let title = row["title"] as? String else { return nil }
            return LSPCodeActionSummary(
                index: index,
                title: title,
                kind: row["kind"] as? String,
                hasEdit: row["edit"] != nil || (row["command"] as? [String: Any])?["arguments"] != nil
            )
        }
    }

    func applyCodeAction(
        path: String,
        line: Int,
        character: Int,
        title: String
    ) async throws -> LSPApplyResult {
        let server = try await server(for: path)
        let raw = try await server.codeActions(path: path, line: line, character: character)
        guard let action = raw.first(where: { ($0["title"] as? String) == title }) else {
            let titles = raw.compactMap { $0["title"] as? String }
            throw LSPError.message(
                titles.isEmpty
                    ? "No code actions were offered at that position."
                    : "No code action titled '\(title)'. Available: " + titles.joined(separator: " · ")
            )
        }
        guard let edit = action["edit"] as? [String: Any] else {
            throw LSPError.message("The code action has no workspace edit; it may require an editor command.")
        }
        let summary = try Self.applyWorkspaceEdit(edit)
        for file in summary.files {
            if let changedServer = try? await self.server(for: file) {
                await changedServer.refresh(path: file)
            }
        }
        return LSPApplyResult(title: title, changedFiles: summary.files, editCount: summary.edits)
    }

    func format(path: String) async throws -> LSPApplyResult {
        let server = try await server(for: path)
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        let raw = try await server.request("textDocument/formatting", [
            "textDocument": ["uri": url.absoluteString],
            "options": ["tabSize": 4, "insertSpaces": true],
        ])
        guard let edits = raw as? [[String: Any]] else {
            throw LSPError.unsupported("This language server did not return formatting edits.")
        }
        guard !edits.isEmpty else {
            return LSPApplyResult(title: "Format", changedFiles: [], editCount: 0)
        }
        let workspaceEdit: [String: Any] = [
            "changes": [url.absoluteString: edits],
        ]
        let summary = try Self.applyWorkspaceEdit(workspaceEdit)
        for file in summary.files {
            await server.refresh(path: file)
        }
        return LSPApplyResult(title: "Format", changedFiles: summary.files, editCount: summary.edits)
    }

    func candidateFiles(in root: URL, limit: Int = 40) -> [String] {
        let extensions = Set(LSPLanguage.allCases.flatMap(\.extensions))
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsPackageDescendants]
        ) else { return [] }
        var files: [String] = []
        let skipped = Set([".git", ".build", "node_modules", "DerivedData", ".swiftpm", "Pods", "vendor"])
        for case let url as URL in enumerator {
            if url.pathComponents.contains(where: { skipped.contains($0) }) {
                enumerator.skipDescendants()
                continue
            }
            guard extensions.contains(url.pathExtension.lowercased()) else { continue }
            files.append(url.resolvingSymlinksInPath().path)
            if files.count >= limit { break }
        }
        return files
    }

    func stopAll() {
        for server in servers.values {
            Task { await server.shutdown() }
        }
        servers.removeAll()
    }

    private func server(for path: String) async throws -> LSPServer {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL
        guard let language = LSPLanguage.detect(path: url.path) else {
            throw LSPError.unsupported("No language server is configured for .\(url.pathExtension).")
        }
        let name = language.command[0]
        guard !disabled.contains(name) else {
            throw LSPError.unsupported("\(name) is unavailable.")
        }
        guard let executable = Self.executable(named: name) else {
            disabled.insert(name)
            throw LSPError.unsupported(
                "\(name) was not found on PATH. Install it to enable \(language.rawValue) diagnostics."
            )
        }
        let root = try workspaceRoot(for: url)
        let key = "\(language.identifier)|\(root.path)"
        if let existing = servers[key] { return existing }
        let server = LSPServer(
            language: language,
            root: root,
            executable: executable,
            arguments: Array(language.command.dropFirst())
        )
        do {
            try await server.start()
        } catch {
            throw error
        }
        servers[key] = server
        return server
    }

    private func workspaceRoot(for file: URL) throws -> URL {
        var directory = file.deletingLastPathComponent()
        let markers = ["Package.swift", "compile_commands.json", "tsconfig.json", "pyproject.toml", "go.mod", "Cargo.toml", ".git"]
        var levels = 0
        while levels < 6 {
            for marker in markers {
                if FileManager.default.fileExists(atPath: directory.appendingPathComponent(marker).path) {
                    return directory
                }
            }
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { break }
            directory = parent
            levels += 1
        }
        return file.deletingLastPathComponent()
    }

    static func applyWorkspaceEdit(_ edit: [String: Any]) throws -> (files: [String], edits: Int) {
        var byPath: [String: [[String: Any]]] = [:]
        if let changes = edit["changes"] as? [String: [[String: Any]]] {
            for (uri, edits) in changes {
                guard let path = fileURL(uri) else { continue }
                byPath[path, default: []].append(contentsOf: edits)
            }
        }
        if let documentChanges = edit["documentChanges"] as? [[String: Any]] {
            for change in documentChanges {
                guard let textDocument = change["textDocument"] as? [String: Any],
                      let uri = textDocument["uri"] as? String,
                      let edits = change["edits"] as? [[String: Any]],
                      let path = fileURL(uri) else { continue }
                byPath[path, default: []].append(contentsOf: edits)
            }
        }
        var files: [String] = []
        var count = 0
        for (path, edits) in byPath {
            guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
                throw LSPError.message("Could not read \(path).")
            }
            let mutable = NSMutableString(string: text)
            let sorted = edits.sorted(by: { startOffset($0, in: text) > startOffset($1, in: text) })
            for edit in sorted {
                guard let range = edit["range"] as? [String: Any],
                      let start = range["start"] as? [String: Any],
                      let end = range["end"] as? [String: Any],
                      let startIndex = utf16Offset(in: text, line: start["line"] as? Int ?? 0, character: start["character"] as? Int ?? 0),
                      let endIndex = utf16Offset(in: text, line: end["line"] as? Int ?? 0, character: end["character"] as? Int ?? 0),
                      startIndex <= endIndex, endIndex <= mutable.length else { continue }
                let replacement = edit["newText"] as? String ?? ""
                mutable.replaceCharacters(
                    in: NSRange(location: startIndex, length: endIndex - startIndex),
                    with: replacement
                )
                count += 1
            }
            do {
                try (mutable as String).write(toFile: path, atomically: true, encoding: .utf8)
                files.append(path)
            } catch {
                throw LSPError.message("Could not write \(path): \(error.localizedDescription)")
            }
        }
        return (files.sorted(), count)
    }

    private static func startOffset(_ edit: [String: Any], in text: String) -> Int {
        guard let range = edit["range"] as? [String: Any],
              let start = range["start"] as? [String: Any] else { return 0 }
        return utf16Offset(in: text, line: start["line"] as? Int ?? 0, character: start["character"] as? Int ?? 0) ?? 0
    }

    static func utf16Offset(in text: String, line: Int, character: Int) -> Int? {
        let units = Array(text.utf16)
        guard line >= 0, character >= 0 else { return nil }
        var currentLine = 0
        var index = 0
        while currentLine < line {
            guard index < units.count else { return nil }
            if units[index] == 10 { currentLine += 1 }
            index += 1
        }
        let offset = index + character
        return min(offset, units.count)
    }

    static func fileURL(_ uri: String) -> String? {
        if let url = URL(string: uri), url.isFileURL {
            return url.path
        }
        return nil
    }
}

extension LSPLanguage: CaseIterable {}
