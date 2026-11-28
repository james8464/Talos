import Foundation
import AppKit

struct CatalogPlugin: Identifiable, Codable, Equatable, Sendable {
    var id: String
    var name: String
    var version: String
    var description: String = ""
    var author: String? = nil
    var source: String
    var homepage: String? = nil
}

struct PluginCatalog: Identifiable, Codable, Equatable, Sendable {
    var id: String
    var name: String
    var plugins: [CatalogPlugin]
}

struct InstalledPlugin: Identifiable, Codable, Equatable, Sendable {
    var id: String
    var version: String
    var path: String
    var catalog: String
    var installedAt = Date()
}

enum PluginError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

struct PluginManifest: Identifiable, Equatable, Sendable {
    let name: String
    let description: String
    let path: String
    let skills: [String]
    let mcpServers: [MCPServerConfig]

    var id: String { path }
}

@MainActor
final class PluginStore: ObservableObject {
    @Published private(set) var plugins: [PluginManifest] = []
    @Published private(set) var disabled: Set<String> = []
    @Published private(set) var catalogs: [PluginCatalog] = []
    @Published private(set) var installedPlugins: [InstalledPlugin] = []

    private let defaults = UserDefaults.standard
    private let baseDirectory: URL
    private let catalogFileOverride: URL?

    init(baseDirectory: URL = PluginStore.defaultBaseDirectory(), catalogFile: URL? = nil) {
        self.baseDirectory = baseDirectory
        self.catalogFileOverride = catalogFile
        if let values = defaults.stringArray(forKey: "disabledPlugins") {
            disabled = Set(values)
        }
        reload()
        reloadCatalogs()
    }

    nonisolated static func defaultBaseDirectory() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos", isDirectory: true)
    }

    var directory: URL {
        baseDirectory.appendingPathComponent("plugins", isDirectory: true)
    }

    var catalogDirectory: URL {
        baseDirectory.appendingPathComponent("catalogs", isDirectory: true)
    }

    var registryURL: URL {
        baseDirectory.appendingPathComponent("installed-plugins.json")
    }

    var enabled: [PluginManifest] {
        plugins.filter { !disabled.contains($0.path) }
    }

    var enabledSkillDirectories: [URL] {
        enabled.flatMap { plugin -> [URL] in
            let root = URL(fileURLWithPath: plugin.path, isDirectory: true)
            let declared = plugin.skills.isEmpty ? ["skills"] : plugin.skills
            return declared.map { root.appendingPathComponent($0, isDirectory: true) }
        }
    }

    var enabledMCPServers: [MCPServerConfig] {
        enabled.flatMap { plugin in
            plugin.mcpServers.map { server in
                var copy = server
                copy.name = "\(plugin.name)/\(server.name)"
                return copy
            }
        }
    }

    func reload() {
        var found: [PluginManifest] = []
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
            plugins = []
            return
        }
        for entry in entries {
            let manifestURL = entry.appendingPathComponent("plugin.json")
            guard let data = try? Data(contentsOf: manifestURL),
                  let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                continue
            }
            let name = raw["name"] as? String ?? entry.lastPathComponent
            let description = raw["description"] as? String ?? ""
            let skills = (raw["skills"] as? [Any])?.compactMap { $0 as? String } ?? []
            var servers: [MCPServerConfig] = []
            if let rawServers = raw["mcpServers"] as? [[String: Any]] {
                for server in rawServers {
                    let command = server["command"] as? String ?? ""
                    let url = server["url"] as? String
                    guard !command.isEmpty || (url?.isEmpty == false) else { continue }
                    let args = (server["args"] as? [Any])?.compactMap { $0 as? String } ?? []
                    let env = (server["env"] as? [String: Any])?.compactMapValues { $0 as? String } ?? [:]
                    servers.append(MCPServerConfig(
                        name: server["name"] as? String ?? "MCP",
                        command: command,
                        args: args,
                        env: env,
                        url: url
                    ))
                }
            }
            found.append(PluginManifest(
                name: name,
                description: description,
                path: entry.path,
                skills: skills,
                mcpServers: servers
            ))
        }
        plugins = found.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        installedPlugins = loadInstalled().filter { installed in
            FileManager.default.fileExists(atPath: installed.path)
        }
        saveInstalled()
    }

    func setEnabled(_ plugin: PluginManifest, _ enabled: Bool) {
        if enabled {
            disabled.remove(plugin.path)
        } else {
            disabled.insert(plugin.path)
        }
        defaults.set(Array(disabled), forKey: "disabledPlugins")
    }

    func reloadCatalogs() {
        var files: [URL] = []
        if let catalogFileOverride {
            files.append(catalogFileOverride)
        }
        if let environment = ProcessInfo.processInfo.environment["TALOS_PLUGIN_CATALOG"],
           !environment.isEmpty {
            files.append(URL(fileURLWithPath: environment))
        }
        try? FileManager.default.createDirectory(at: catalogDirectory, withIntermediateDirectories: true)
        if let entries = try? FileManager.default.contentsOfDirectory(
            at: catalogDirectory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) {
            files.append(contentsOf: entries.filter { $0.pathExtension.lowercased() == "json" })
        }
        var seen = Set<String>()
        var found: [PluginCatalog] = []
        for file in files where seen.insert(file.standardizedFileURL.path).inserted {
            guard let data = try? Data(contentsOf: file) else { continue }
            let base = file.deletingLastPathComponent()
            if let list = try? JSONDecoder().decode([PluginCatalog].self, from: data) {
                found.append(contentsOf: list.map { resolve($0, relativeTo: base) })
            } else if let catalog = try? JSONDecoder().decode(PluginCatalog.self, from: data) {
                found.append(resolve(catalog, relativeTo: base))
            }
        }
        catalogs = found.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    func installedPlugin(id: String) -> InstalledPlugin? {
        installedPlugins.first { $0.id == id }
    }

    func isInstalled(_ plugin: CatalogPlugin) -> Bool {
        installedPlugin(id: plugin.id) != nil
    }

    func updateAvailable(_ plugin: CatalogPlugin) -> Bool {
        guard let installed = installedPlugin(id: plugin.id) else { return false }
        return compareVersions(plugin.version, installed.version) == .orderedDescending
    }

    func install(_ plugin: CatalogPlugin, catalog: String) throws {
        let id = try safeID(plugin.id)
        guard installedPlugin(id: id) == nil else {
            throw PluginError.message("\(plugin.name) is already installed. Use Update.")
        }
        let source = try sourceURL(for: plugin)
        let destination = directory.appendingPathComponent(id, isDirectory: true)
        guard !FileManager.default.fileExists(atPath: destination.path) else {
            throw PluginError.message("A folder already exists at \(destination.path).")
        }
        try copyPlugin(from: source, to: destination)
        var installed = loadInstalled()
        installed.append(InstalledPlugin(
            id: id,
            version: plugin.version,
            path: destination.path,
            catalog: catalog
        ))
        saveInstalled(installed)
        installedPlugins = installed
        reload()
    }

    func update(_ plugin: CatalogPlugin, catalog: String) throws {
        let id = try safeID(plugin.id)
        guard installedPlugin(id: id) != nil else {
            throw PluginError.message("\(plugin.name) is not installed.")
        }
        let source = try sourceURL(for: plugin)
        let destination = directory.appendingPathComponent(id, isDirectory: true)
        let staging = directory.appendingPathComponent(".\(id)-update-\(UUID().uuidString)", isDirectory: true)
        try copyPlugin(from: source, to: staging)
        do {
            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.removeItem(at: destination)
            }
            try FileManager.default.moveItem(at: staging, to: destination)
        } catch {
            try? FileManager.default.removeItem(at: staging)
            throw PluginError.message("Could not replace \(plugin.name): \(error.localizedDescription)")
        }
        var installed = loadInstalled()
        if let index = installed.firstIndex(where: { $0.id == id }) {
            installed[index].version = plugin.version
            installed[index].path = destination.path
            installed[index].catalog = catalog
            installed[index].installedAt = Date()
        }
        saveInstalled(installed)
        installedPlugins = installed
        reload()
    }

    func uninstall(_ plugin: CatalogPlugin) throws {
        try uninstall(id: plugin.id)
    }

    func uninstall(id: String) throws {
        let safe = try safeID(id)
        guard let installed = installedPlugin(id: safe) else {
            throw PluginError.message("\(id) is not installed.")
        }
        let path = URL(fileURLWithPath: installed.path).standardizedFileURL
        let root = directory.standardizedFileURL.path
        guard path.path.hasPrefix(root + "/") else {
            throw PluginError.message("Refusing to remove a plugin outside the plugin folder.")
        }
        if FileManager.default.fileExists(atPath: path.path) {
            try FileManager.default.removeItem(at: path)
        }
        disabled.remove(installed.path)
        defaults.set(Array(disabled), forKey: "disabledPlugins")
        var list = loadInstalled()
        list.removeAll { $0.id == safe }
        saveInstalled(list)
        installedPlugins = list
        reload()
    }

    func openCatalogFolder() {
        try? FileManager.default.createDirectory(at: catalogDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(catalogDirectory)
    }

    private func resolve(_ catalog: PluginCatalog, relativeTo base: URL) -> PluginCatalog {
        var result = catalog
        result.plugins = catalog.plugins.map { plugin in
            var copy = plugin
            if !copy.source.hasPrefix("/"), !copy.source.hasPrefix("file://") {
                copy.source = base.appendingPathComponent(copy.source).standardizedFileURL.path
            }
            return copy
        }
        return result
    }

    private func sourceURL(for plugin: CatalogPlugin) throws -> URL {
        let raw = plugin.source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.lowercased().hasPrefix("http://"), !raw.lowercased().hasPrefix("https://") else {
            throw PluginError.message("Only local plugin sources are supported.")
        }
        let url: URL
        if raw.hasPrefix("file://") {
            guard let parsed = URL(string: raw) else {
                throw PluginError.message("The plugin source URL is invalid.")
            }
            url = parsed
        } else {
            url = URL(fileURLWithPath: raw, isDirectory: true)
        }
        let standardized = url.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: standardized.path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            throw PluginError.message("The plugin source folder does not exist: \(standardized.path)")
        }
        let manifest = standardized.appendingPathComponent("plugin.json")
        guard FileManager.default.fileExists(atPath: manifest.path) else {
            throw PluginError.message("The plugin source has no plugin.json.")
        }
        return standardized
    }

    private func copyPlugin(from source: URL, to destination: URL) throws {
        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        do {
            try FileManager.default.copyItem(at: source, to: destination)
        } catch {
            throw PluginError.message("Could not copy \(source.lastPathComponent): \(error.localizedDescription)")
        }
    }

    private func safeID(_ raw: String) throws -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        guard !trimmed.isEmpty,
              trimmed != ".",
              trimmed != "..",
              trimmed.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
            throw PluginError.message("Invalid plugin id '\(raw)'. Use letters, numbers, dots, dashes, and underscores.")
        }
        return trimmed
    }

    private func compareVersions(_ lhs: String, _ rhs: String) -> ComparisonResult {
        let left = lhs.split(separator: ".").map { Int($0) ?? 0 }
        let right = rhs.split(separator: ".").map { Int($0) ?? 0 }
        for index in 0..<max(left.count, right.count) {
            let a = index < left.count ? left[index] : 0
            let b = index < right.count ? right[index] : 0
            if a != b { return a < b ? .orderedAscending : .orderedDescending }
        }
        return .orderedSame
    }

    private func loadInstalled() -> [InstalledPlugin] {
        guard let data = try? Data(contentsOf: registryURL),
              let decoded = try? JSONDecoder().decode([InstalledPlugin].self, from: data) else {
            return []
        }
        return decoded
    }

    private func saveInstalled(_ list: [InstalledPlugin]? = nil) {
        let values = list ?? installedPlugins
        guard let data = try? JSONEncoder().encode(values) else { return }
        try? data.write(to: registryURL, options: .atomic)
    }

    func openFolder() {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(directory)
    }

    func createStarter() {
        let folder = directory.appendingPathComponent("example-plugin", isDirectory: true)
        let manifest = folder.appendingPathComponent("plugin.json")
        guard !FileManager.default.fileExists(atPath: manifest.path) else {
            openFolder()
            return
        }
        let skillFolder = folder.appendingPathComponent("skills/example", isDirectory: true)
        let skill = skillFolder.appendingPathComponent("SKILL.md")
        let manifestBody = """
        {
          "name": "Example Plugin",
          "description": "A local plugin that bundles a skill and an optional MCP server.",
          "skills": ["skills"],
          "mcpServers": []
        }
        """
        let skillBody = """
        ---
        name: Example Plugin Skill
        description: Use this when the user asks for the example plugin workflow.
        ---

        # Example Plugin Skill

        Replace this with reusable instructions. Plugins can also declare stdio
        MCP servers under `mcpServers`, which become tools for every model.
        """
        do {
            try FileManager.default.createDirectory(at: skillFolder, withIntermediateDirectories: true)
            try manifestBody.write(to: manifest, atomically: true, encoding: .utf8)
            try skillBody.write(to: skill, atomically: true, encoding: .utf8)
            reload()
            openFolder()
        } catch {
            openFolder()
        }
    }
}
