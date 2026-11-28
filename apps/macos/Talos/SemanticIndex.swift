import Foundation
import NaturalLanguage
import CryptoKit

struct SemanticChunk: Sendable {
    let path: String
    let startLine: Int
    let endLine: Int
    let text: String
    let vector: [Float]
    let tokens: Set<String>
    let pathTokens: Set<String>
    let basenameTokens: Set<String>
}

struct SemanticHit: Sendable {
    let path: String
    let startLine: Int
    let endLine: Int
    let score: Double
    let text: String
}

struct SemanticIndexStatus: Sendable {
    let workspace: String
    let files: Int
    let chunks: Int
    let cachedFiles: Int
    let builtAt: Date?
    let embedding: String
    let isStale: Bool
}

enum SemanticIndexError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

/// Local semantic search over the workspace using averaged Apple on-device word
/// embeddings. No network service, model download, or API key is involved.
///
/// Data flow, privacy, performance, fallbacks, and limits are documented in
/// `SEMANTIC_INDEX.md` at the repository root.
actor SemanticIndex {
    static let shared = SemanticIndex()

    private struct FileState: Sendable {
        let modified: Date
        let size: Int
        let chunkCount: Int
    }

    private struct WorkspaceIndex {
        var chunks: [SemanticChunk]
        var files: [String: FileState]
        var builtAt: Date
        var fingerprint: String
        var cachedFiles: Int
    }

    private struct CachedChunk: Codable {
        let startLine: Int
        let endLine: Int
        let text: String
        let vector: [Float]
    }

    private struct CachedFile: Codable {
        let modified: Date
        let size: Int
        let chunks: [CachedChunk]
    }

    private struct DiskCache: Codable {
        let version: Int
        let files: [String: CachedFile]
    }

    private var indexes: [String: WorkspaceIndex] = [:]
    private let embedding = NLEmbedding.wordEmbedding(for: .english)
    private let supportedExtensions: Set<String> = [
        "swift", "m", "mm", "c", "h", "cc", "cpp", "cxx", "hpp", "hh",
        "py", "pyi", "js", "jsx", "ts", "tsx", "go", "rs", "java", "kt",
        "kts", "rb", "php", "cs", "sh", "zsh", "bash", "sql",
        "md", "txt", "json", "yaml", "yml", "toml", "html", "css", "xml",
    ]
    private let skippedDirectories: Set<String> = [
        ".git", ".build", "node_modules", "DerivedData", ".swiftpm", ".venv",
        "venv", "Pods", "vendor", "dist", "build", ".next", "__pycache__",
        ".gradle", "target", "coverage", "SemanticEval",
    ]

    func status(workspace: URL) async -> SemanticIndexStatus {
        let root = workspace.resolvingSymlinksInPath().standardizedFileURL
        guard embedding != nil else {
            return SemanticIndexStatus(
                workspace: root.path,
                files: 0,
                chunks: 0,
                cachedFiles: 0,
                builtAt: nil,
                embedding: "unavailable",
                isStale: false
            )
        }
        let key = root.path
        if let index = indexes[key] {
            let stale = (try? await needsRebuild(root: root, index: index)) ?? true
            return SemanticIndexStatus(
                workspace: key,
                files: index.files.count,
                chunks: index.chunks.count,
                cachedFiles: index.cachedFiles,
                builtAt: index.builtAt,
                embedding: Self.embeddingName(embedding),
                isStale: stale
            )
        }
        return SemanticIndexStatus(
            workspace: key,
            files: 0,
            chunks: 0,
            cachedFiles: 0,
            builtAt: nil,
            embedding: Self.embeddingName(embedding),
            isStale: false
        )
    }

    func rebuild(workspace: URL) async throws -> SemanticIndexStatus {
        let root = workspace.resolvingSymlinksInPath().standardizedFileURL
        try await build(root: root, force: true)
        return await status(workspace: root)
    }

    func search(
        query: String,
        workspace: URL,
        pathPrefix: String? = nil,
        limit: Int = 8
    ) async throws -> (hits: [SemanticHit], status: SemanticIndexStatus) {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw SemanticIndexError.message("search_codebase requires a query.")
        }
        guard let embedding else {
            throw SemanticIndexError.message(
                "On-device word embeddings are unavailable for this language. Use search_files instead."
            )
        }
        guard let queryVector = Self.embeddingVector(for: trimmed, embedding: embedding) else {
            throw SemanticIndexError.message("Could not embed the query.")
        }
        let root = workspace.resolvingSymlinksInPath().standardizedFileURL
        try await build(root: root, force: false)
        let key = root.path
        let index = indexes[key] ?? WorkspaceIndex(chunks: [], files: [:], builtAt: Date(), fingerprint: "", cachedFiles: 0)
        let queryFloats = queryVector
        var prefix = pathPrefix?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if prefix.hasPrefix("./") { prefix.removeFirst(2) }
        if prefix == "." { prefix = "" }
        let queryTokens = Self.tokens(trimmed)
        var lexicalScores = [Double](repeating: 0, count: index.chunks.count)
        var fileHits: [String: Int] = [:]
        for (offset, chunk) in index.chunks.enumerated() {
            if !prefix.isEmpty, !chunk.path.hasPrefix(prefix) { continue }
            let lexical: Double
            if queryTokens.isEmpty {
                lexical = 0
            } else {
                let matches = queryTokens.filter { query in
                    chunk.tokens.contains { docToken in
                        docToken == query
                            || (query.count >= 4 && docToken.count >= 4
                                && (docToken.hasPrefix(query) || query.hasPrefix(docToken)))
                    }
                }.count
                lexical = Double(matches) / Double(queryTokens.count)
            }
            lexicalScores[offset] = lexical
            if lexical >= 0.4 {
                fileHits[chunk.path, default: 0] += 1
            }
        }

        var scored: [(Double, SemanticChunk)] = []
        for (offset, chunk) in index.chunks.enumerated() {
            if !prefix.isEmpty, !chunk.path.hasPrefix(prefix) { continue }
            let cosine = Self.cosine(queryFloats, chunk.vector)
            let lexical = lexicalScores[offset]
            let pathMatches = queryTokens.filter { chunk.pathTokens.contains($0) }.count
            let pathBoost = queryTokens.isEmpty ? 0 : Double(pathMatches) / Double(queryTokens.count)
            let basenameMatches = queryTokens.filter { chunk.basenameTokens.contains($0) }.count
            let basenameBoost = queryTokens.isEmpty ? 0 : Double(basenameMatches) / Double(queryTokens.count)

            var score = 0.45 * cosine + 0.50 * lexical + 0.05 * pathBoost + 0.15 * basenameBoost
            let ext = URL(fileURLWithPath: chunk.path).pathExtension.lowercased()
            if ["md", "txt", "rst"].contains(ext) {
                score *= 0.80
            }
            score += 0.02 * Double(min(fileHits[chunk.path] ?? 0, 5))
            scored.append((score, chunk))
        }
        let hits = scored
            .sorted { $0.0 > $1.0 }
            .prefix(min(max(limit, 1), 25))
            .map { item in
                SemanticHit(
                    path: item.1.path,
                    startLine: item.1.startLine,
                    endLine: item.1.endLine,
                    score: item.0,
                    text: item.1.text
                )
            }
        let status = SemanticIndexStatus(
            workspace: key,
            files: index.files.count,
            chunks: index.chunks.count,
            cachedFiles: index.cachedFiles,
            builtAt: index.builtAt,
            embedding: Self.embeddingName(embedding),
            isStale: false
        )
        return (Array(hits), status)
    }

    private static let cacheVersion = 2

    private func build(root: URL, force: Bool) async throws {
        guard embedding != nil else {
            throw SemanticIndexError.message(
                "On-device word embeddings are unavailable. Use search_files instead."
            )
        }
        let key = root.path
        if !force, let existing = indexes[key] {
            if (try? await needsRebuild(root: root, index: existing)) == false {
                return
            }
        }
        let cachedEntries = force ? [:] : loadCache(for: key)
        let candidates = candidateFiles(in: root)
        var chunks: [SemanticChunk] = []
        var files: [String: FileState] = [:]
        var diskEntries: [String: CachedFile] = [:]
        var reusedFiles = 0

        for url in candidates {
            guard let attributes = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]),
                  let modified = attributes.contentModificationDate,
                  let size = attributes.fileSize, size <= 300_000 else { continue }
            let relative = url.path.replacingOccurrences(of: root.path + "/", with: "")

            if !force,
               let cached = cachedEntries[relative],
               cached.size == size,
               abs(cached.modified.timeIntervalSince(modified)) <= 0.5,
               !cached.chunks.isEmpty {
                let loaded = cached.chunks.map { chunk in
                    SemanticChunk(
                        path: relative,
                        startLine: chunk.startLine,
                        endLine: chunk.endLine,
                        text: chunk.text,
                        vector: chunk.vector,
                        tokens: Self.tokens(relative + "\n" + chunk.text),
                        pathTokens: Self.tokens(relative),
                        basenameTokens: Self.tokens(URL(fileURLWithPath: relative).deletingPathExtension().lastPathComponent)
                    )
                }
                chunks.append(contentsOf: loaded)
                files[relative] = FileState(
                    modified: cached.modified,
                    size: cached.size,
                    chunkCount: loaded.count
                )
                diskEntries[relative] = cached
                reusedFiles += 1
                continue
            }

            guard let data = try? Data(contentsOf: url),
                  !data.contains(0),
                  let text = String(data: data, encoding: .utf8),
                  !text.isEmpty else { continue }
            let fileChunks = chunk(
                text: text,
                path: relative,
                embedding: embedding!
            )
            guard !fileChunks.isEmpty else { continue }
            chunks.append(contentsOf: fileChunks)
            files[relative] = FileState(
                modified: modified,
                size: size,
                chunkCount: fileChunks.count
            )
            diskEntries[relative] = CachedFile(
                modified: modified,
                size: size,
                chunks: fileChunks.map {
                    CachedChunk(
                        startLine: $0.startLine,
                        endLine: $0.endLine,
                        text: $0.text,
                        vector: $0.vector
                    )
                }
            )
        }

        var hasher = Hasher()
        for (path, state) in files.sorted(by: { $0.key < $1.key }) {
            hasher.combine(path)
            hasher.combine(state.size)
            hasher.combine(state.modified.timeIntervalSince1970)
        }
        indexes[key] = WorkspaceIndex(
            chunks: chunks,
            files: files,
            builtAt: Date(),
            fingerprint: String(hasher.finalize()),
            cachedFiles: reusedFiles
        )
        saveCache(diskEntries, for: key)
    }

    private var cacheDirectory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support
            .appendingPathComponent("Talos", isDirectory: true)
            .appendingPathComponent("semantic-index", isDirectory: true)
    }

    private func cacheURL(for workspace: String) -> URL {
        let digest = SHA256.hash(data: Data(workspace.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return cacheDirectory.appendingPathComponent(digest + ".plist")
    }

    private func loadCache(for workspace: String) -> [String: CachedFile] {
        guard let data = try? Data(contentsOf: cacheURL(for: workspace)),
              let cache = try? PropertyListDecoder().decode(DiskCache.self, from: data),
              cache.version == Self.cacheVersion else {
            return [:]
        }
        return cache.files
    }

    private func saveCache(_ files: [String: CachedFile], for workspace: String) {
        guard !files.isEmpty else { return }
        let cache = DiskCache(version: Self.cacheVersion, files: files)
        let encoder = PropertyListEncoder()
        encoder.outputFormat = .binary
        guard let data = try? encoder.encode(cache) else { return }
        do {
            try FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
            try? FileManager.default.setAttributes(
                [.posixPermissions: 0o700],
                ofItemAtPath: cacheDirectory.path
            )
            let fileURL = cacheURL(for: workspace)
            try data.write(to: fileURL, options: .atomic)
            try? FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: fileURL.path
            )
        } catch {
            // The cache is best-effort; a search still works without it.
        }
    }

    private func needsRebuild(root: URL, index: WorkspaceIndex) async throws -> Bool {
        let candidates = candidateFiles(in: root)
        if candidates.count != index.files.count { return true }
        for url in candidates {
            let relative = url.path.replacingOccurrences(of: root.path + "/", with: "")
            guard let state = index.files[relative],
                  let attributes = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]),
                  let modified = attributes.contentModificationDate,
                  let size = attributes.fileSize else { return true }
            if state.size != size || abs(state.modified.timeIntervalSince(modified)) > 0.5 {
                return true
            }
        }
        return false
    }

    private func candidateFiles(in root: URL) -> [URL] {
        let keys: [URLResourceKey] = [.isRegularFileKey, .isDirectoryKey]
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: keys,
            options: [.skipsPackageDescendants]
        ) else { return [] }
        var files: [URL] = []
        for case let url as URL in enumerator {
            let components = Set(url.pathComponents)
            if components.isDisjoint(with: skippedDirectories) == false {
                enumerator.skipDescendants()
                continue
            }
            let values = try? url.resourceValues(forKeys: Set(keys))
            if values?.isDirectory == true {
                if skippedDirectories.contains(url.lastPathComponent) {
                    enumerator.skipDescendants()
                }
                continue
            }
            guard supportedExtensions.contains(url.pathExtension.lowercased()) else { continue }
            files.append(url.resolvingSymlinksInPath())
            if files.count >= 700 { break }
        }
        return files
    }

    private func chunk(text: String, path: String, embedding: NLEmbedding) -> [SemanticChunk] {
        let lines = text.components(separatedBy: "\n")
        let window = 40
        let overlap = 10
        var chunks: [SemanticChunk] = []
        var start = 0
        while start < lines.count, chunks.count < 90 {
            let end = min(start + window, lines.count)
            let slice = lines[start..<end]
            let body = slice.joined(separator: "\n")
            if body.count >= 80, body.count <= 4_000 {
                let meaningful = body.trimmingCharacters(in: .whitespacesAndNewlines)
                if meaningful.count >= 40,
                   meaningful.contains(where: { $0.isLetter }),
                   let vector = Self.embeddingVector(for: path + "\n" + String(body.prefix(1_500)), embedding: embedding) {
                    chunks.append(SemanticChunk(
                        path: path,
                        startLine: start + 1,
                        endLine: end,
                        text: meaningful,
                        vector: vector,
                        tokens: Self.tokens(path + "\n" + meaningful),
                        pathTokens: Self.tokens(path),
                        basenameTokens: Self.tokens(URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent)
                    ))
                }
            }
            if end >= lines.count { break }
            start += max(window - overlap, 1)
        }
        return chunks
    }

    static func embeddingName(_ embedding: NLEmbedding?) -> String {
        guard let embedding else { return "unavailable" }
        return "NLEmbedding.word · \(embedding.dimension)-d"
    }

    /// Word embeddings are averaged across the chunk. Unlike sentence
    /// embeddings, this is safe to call repeatedly, has no per-sentence neural
    /// inference cost, and keeps the first-ever index build in seconds.
    static func embeddingVector(for text: String, embedding: NLEmbedding) -> [Float]? {
        var sum = [Float](repeating: 0, count: embedding.dimension)
        var count = 0
        for token in embeddingTokens(text) {
            guard let vector = embedding.vector(for: token), vector.count == embedding.dimension else { continue }
            for index in vector.indices {
                sum[index] += Float(vector[index])
            }
            count += 1
        }
        guard count > 0 else { return nil }
        return sum.map { $0 / Float(count) }
    }

    static func embeddingTokens(_ text: String) -> [String] {
        var output: [String] = []
        var run = ""
        func add(_ raw: String) {
            let lowered = raw.lowercased()
            if lowered.count >= 2, lowered.contains(where: { $0.isLetter }) {
                output.append(lowered)
            }
        }
        func flush() {
            guard !run.isEmpty else { return }
            add(run)
            var piece = ""
            let characters = Array(run)
            for index in characters.indices {
                let character = characters[index]
                let previous = index > 0 ? characters[index - 1] : nil
                if character.isUppercase,
                   let previous,
                   previous.isLowercase || previous.isNumber,
                   !piece.isEmpty {
                    add(piece)
                    piece = ""
                }
                piece.append(character)
            }
            add(piece)
            run = ""
        }
        for character in text {
            if character.isLetter || character.isNumber {
                run.append(character)
            } else {
                flush()
            }
        }
        flush()
        return output
    }

    private static let stopWords: Set<String> = [
        "the", "and", "for", "with", "from", "this", "that", "into", "when",
        "where", "what", "which", "how", "are", "was", "were", "has", "have",
        "had", "can", "could", "should", "would", "use", "using", "used",
        "return", "self", "let", "var", "func", "struct", "class", "enum",
        "import", "public", "private", "static", "some", "any", "true",
        "false", "nil", "none", "new", "old", "get", "set", "init", "self",
        "then", "than", "their", "there", "they", "you", "your", "our",
        "not", "all", "out", "one", "two", "more", "most", "other", "also",
    ]

    static func stem(_ token: String) -> String {
        var value = token
        for suffix in ["ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "s"] {
            if value.hasSuffix(suffix), value.count - suffix.count >= 4 {
                value = String(value.dropLast(suffix.count))
                break
            }
        }
        if value.hasSuffix("e"), value.count >= 5 {
            value.removeLast()
        }
        return value
    }

    static func tokens(_ text: String) -> Set<String> {
        var output = Set<String>()
        var run = ""
        func add(_ raw: String) {
            let lowered = raw.lowercased()
            guard lowered.count >= 3, !stopWords.contains(lowered) else { return }
            let stemmed = stem(lowered)
            if stemmed.count >= 3, !stopWords.contains(stemmed) {
                output.insert(stemmed)
            }
        }
        func flush() {
            guard !run.isEmpty else { return }
            add(run)
            var piece = ""
            let characters = Array(run)
            for index in characters.indices {
                let character = characters[index]
                let previous = index > 0 ? characters[index - 1] : nil
                let startsNewWord = character.isUppercase
                    && previous != nil
                    && (previous!.isLowercase || previous!.isNumber)
                if startsNewWord, !piece.isEmpty {
                    add(piece)
                    piece = ""
                }
                piece.append(character)
            }
            add(piece)
            run = ""
        }
        for character in text {
            if character.isLetter || character.isNumber {
                run.append(character)
            } else {
                flush()
            }
        }
        flush()
        return output
    }

    static func cosine(_ a: [Float], _ b: [Float]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return 0 }
        var dot: Float = 0
        var normA: Float = 0
        var normB: Float = 0
        for index in a.indices {
            dot += a[index] * b[index]
            normA += a[index] * a[index]
            normB += b[index] * b[index]
        }
        let denominator = sqrt(normA) * sqrt(normB)
        return denominator == 0 ? 0 : Double(dot / denominator)
    }
}
