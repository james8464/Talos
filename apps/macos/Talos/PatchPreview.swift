import Foundation

struct PatchLinePreview: Identifiable, Sendable {
    enum Kind: String, Sendable {
        case addition
        case removal
        case context
    }

    let id = UUID()
    let kind: Kind
    let text: String
}

struct PatchHunkPreview: Identifiable, Sendable {
    let id = UUID()
    let header: String
    let lines: [PatchLinePreview]
}

struct PatchFilePreview: Identifiable, Sendable {
    let id = UUID()
    let path: String
    let added: Int
    let removed: Int
    let isNew: Bool
    let isDeleted: Bool
    let isBinary: Bool
    let hunks: [PatchHunkPreview]

    var changeLabel: String {
        "\(path)  +\(added) −\(removed)"
    }

    func previewHunks(limit: Int = 160) -> (hunks: [PatchHunkPreview], truncated: Bool) {
        var remaining = limit
        var output: [PatchHunkPreview] = []
        for hunk in hunks {
            guard remaining > 0 else { break }
            let take = min(hunk.lines.count, remaining)
            if take > 0 {
                output.append(PatchHunkPreview(
                    header: hunk.header,
                    lines: Array(hunk.lines.prefix(take))
                ))
                remaining -= take
            } else {
                output.append(PatchHunkPreview(header: hunk.header, lines: []))
            }
        }
        let total = hunks.reduce(0) { $0 + $1.lines.count }
        return (output, total > limit)
    }
}

struct PatchPreview: Sendable {
    let files: [PatchFilePreview]
    let rawLineCount: Int

    var added: Int { files.reduce(0) { $0 + $1.added } }
    var removed: Int { files.reduce(0) { $0 + $1.removed } }

    var summary: String {
        let fileText = "\(files.count) file\(files.count == 1 ? "" : "s")"
        return "\(fileText) · +\(added) −\(removed)"
    }

    static func parse(_ patch: String) -> PatchPreview {
        let lines = patch.components(separatedBy: "\n")
        var blocks: [[String]] = []
        if patch.contains("diff --git ") {
            for line in lines {
                if line.hasPrefix("diff --git ") {
                    blocks.append([line])
                } else if !blocks.isEmpty {
                    blocks[blocks.count - 1].append(line)
                }
            }
        } else {
            var current: [String] = []
            var index = 0
            while index < lines.count {
                let line = lines[index]
                let next = index + 1 < lines.count ? lines[index + 1] : ""
                if line.hasPrefix("--- "), next.hasPrefix("+++ "), !current.isEmpty {
                    blocks.append(current)
                    current = []
                }
                current.append(line)
                index += 1
            }
            if !current.isEmpty { blocks.append(current) }
        }
        return PatchPreview(files: blocks.compactMap(parseBlock), rawLineCount: lines.count)
    }

    private static func parseBlock(_ block: [String]) -> PatchFilePreview? {
        var path = ""
        var added = 0
        var removed = 0
        var isNew = false
        var isDeleted = false
        var isBinary = false
        var hunks: [PatchHunkPreview] = []
        var hunkHeader = ""
        var hunkLines: [PatchLinePreview] = []

        func finishHunk() {
            guard !hunkHeader.isEmpty else { return }
            hunks.append(PatchHunkPreview(header: hunkHeader, lines: hunkLines))
            hunkHeader = ""
            hunkLines = []
        }

        for line in block {
            if line.hasPrefix("diff --git ") {
                if let parsed = gitPath(line) { path = parsed }
                continue
            }
            if hunkHeader.isEmpty, line.hasPrefix("--- ") {
                let candidate = cleanedPath(String(line.dropFirst(4)))
                if candidate == "/dev/null" {
                    isNew = true
                } else if path.isEmpty {
                    path = candidate
                }
                continue
            }
            if hunkHeader.isEmpty, line.hasPrefix("+++ ") {
                let candidate = cleanedPath(String(line.dropFirst(4)))
                if candidate == "/dev/null" {
                    isDeleted = true
                } else {
                    path = candidate
                }
                continue
            }
            if line.hasPrefix("@@") {
                finishHunk()
                hunkHeader = line
                continue
            }
            if hunkHeader.isEmpty {
                if line.hasPrefix("new file mode") {
                    isNew = true
                } else if line.hasPrefix("deleted file mode") {
                    isDeleted = true
                } else if line.hasPrefix("Binary files") || line.hasPrefix("GIT binary patch") {
                    isBinary = true
                }
                continue
            }
            if line.hasPrefix("+") {
                added += 1
                hunkLines.append(PatchLinePreview(kind: .addition, text: String(line.dropFirst())))
            } else if line.hasPrefix("-") {
                removed += 1
                hunkLines.append(PatchLinePreview(kind: .removal, text: String(line.dropFirst())))
            } else if line.hasPrefix(" ") {
                hunkLines.append(PatchLinePreview(kind: .context, text: String(line.dropFirst())))
            } else if line.hasPrefix("\\ No newline") {
                continue
            }
        }
        finishHunk()
        guard !path.isEmpty else { return nil }
        return PatchFilePreview(
            path: path,
            added: added,
            removed: removed,
            isNew: isNew,
            isDeleted: isDeleted,
            isBinary: isBinary,
            hunks: hunks
        )
    }

    private static func gitPath(_ line: String) -> String? {
        let value = line.replacingOccurrences(of: "diff --git ", with: "")
        guard let range = value.range(of: " b/") else { return nil }
        return String(value[range.upperBound...])
    }

    private static func cleanedPath(_ value: String) -> String {
        var path = value
        if let tab = path.firstIndex(of: "\t") { path = String(path[..<tab]) }
        if path.hasPrefix("a/") || path.hasPrefix("b/") { path.removeFirst(2) }
        return path
    }
}
