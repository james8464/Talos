import Foundation

struct Project: Identifiable, Codable, Equatable {
    var id = UUID()
    var path: String
    var name: String
    var lastOpened = Date()
}

@MainActor
final class ProjectStore: ObservableObject {
    @Published private(set) var projects: [Project] = []

    private var directory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos", isDirectory: true)
    }

    private var fileURL: URL {
        directory.appendingPathComponent("projects.json")
    }

    init() {
        load()
    }

    var sorted: [Project] {
        projects.sorted { $0.lastOpened > $1.lastOpened }
    }

    @discardableResult
    func open(path: String) -> Project {
        let url = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        if let index = projects.firstIndex(where: { $0.path == url.path }) {
            projects[index].lastOpened = Date()
            let project = projects[index]
            save()
            return project
        }
        let project = Project(
            path: url.path,
            name: url.lastPathComponent.isEmpty ? url.path : url.lastPathComponent
        )
        projects.append(project)
        save()
        return project
    }

    func remove(_ project: Project) {
        projects.removeAll { $0.id == project.id }
        save()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([Project].self, from: data) else {
            return
        }
        projects = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(projects) else { return }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            // Project history is best-effort; the workspace path remains the source of truth.
        }
    }
}

struct FileNode: Identifiable, Hashable, Sendable {
    let url: URL
    let isDirectory: Bool
    var children: [FileNode]?

    var id: String { url.path }
}

enum ProjectFileTree {
    static let skippedNames: Set<String> = [
        ".git", ".build", "node_modules", "DerivedData", ".DS_Store",
        ".talos-runtime", ".simplexai-runtime",
    ]

    static func build(root: URL, maxDepth: Int = 5, maxEntries: Int = 2500) -> [FileNode] {
        var count = 0
        return children(of: root, depth: 0, maxDepth: maxDepth, maxEntries: maxEntries, count: &count)
    }

    private static func children(
        of directory: URL,
        depth: Int,
        maxDepth: Int,
        maxEntries: Int,
        count: inout Int
    ) -> [FileNode] {
        guard depth < maxDepth, count < maxEntries else { return [] }
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: []
        ) else {
            return []
        }

        let sorted = urls
            .filter { !skippedNames.contains($0.lastPathComponent) }
            .sorted { lhs, rhs in
                let lhsDirectory = (try? lhs.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                let rhsDirectory = (try? rhs.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                if lhsDirectory != rhsDirectory { return lhsDirectory }
                return lhs.lastPathComponent.localizedStandardCompare(rhs.lastPathComponent) == .orderedAscending
            }

        var nodes: [FileNode] = []
        for url in sorted {
            guard count < maxEntries else { break }
            count += 1
            let isDirectory = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            if isDirectory {
                nodes.append(FileNode(
                    url: url,
                    isDirectory: true,
                    children: children(
                        of: url,
                        depth: depth + 1,
                        maxDepth: maxDepth,
                        maxEntries: maxEntries,
                        count: &count
                    )
                ))
            } else {
                nodes.append(FileNode(url: url, isDirectory: false, children: nil))
            }
        }
        return nodes
    }

    static func relativePath(of url: URL, in root: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        guard path.hasPrefix(rootPath) else { return path }
        return String(path.dropFirst(rootPath.count)).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

enum DocumentWriter {
    static let formats = ["md", "txt", "html", "csv", "json", "rtf", "docx"]

    static func create(
        name: String,
        format: String,
        content: String,
        in workspace: URL
    ) throws -> URL {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw ToolError.message("Give the file a name.")
        }
        let ext = format.lowercased()
        let fileName = trimmed.contains(".") ? trimmed : "\(trimmed).\(ext)"
        let url = workspace.appendingPathComponent(fileName).standardizedFileURL
        let rootPath = workspace.standardizedFileURL.path
        guard url.path == rootPath || url.path.hasPrefix(rootPath + "/") else {
            throw ToolError.message("New files must stay inside the project folder.")
        }
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        switch ext {
        case "rtf", "docx":
            let temp = FileManager.default.temporaryDirectory
                .appendingPathComponent("talos-doc-\(UUID().uuidString).txt")
            try content.write(to: temp, atomically: true, encoding: .utf8)
            defer { try? FileManager.default.removeItem(at: temp) }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/textutil")
            process.arguments = ["-convert", ext, "-output", url.path, temp.path]
            let pipe = Pipe()
            process.standardError = pipe
            try process.run()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                let error = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                throw ToolError.message(error.isEmpty ? "textutil could not create the document." : error)
            }
            return url
        default:
            try content.write(to: url, atomically: true, encoding: .utf8)
            return url
        }
    }
}
