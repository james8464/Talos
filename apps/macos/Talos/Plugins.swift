import Foundation
import AppKit

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

    private let defaults = UserDefaults.standard

    init() {
        if let values = defaults.stringArray(forKey: "disabledPlugins") {
            disabled = Set(values)
        }
        reload()
    }

    var directory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos/plugins", isDirectory: true)
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
                    guard let command = server["command"] as? String, !command.isEmpty else { continue }
                    let args = (server["args"] as? [Any])?.compactMap { $0 as? String } ?? []
                    let env = (server["env"] as? [String: Any])?.compactMapValues { $0 as? String } ?? [:]
                    servers.append(MCPServerConfig(
                        name: server["name"] as? String ?? "MCP",
                        command: command,
                        args: args,
                        env: env
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
    }

    func setEnabled(_ plugin: PluginManifest, _ enabled: Bool) {
        if enabled {
            disabled.remove(plugin.path)
        } else {
            disabled.insert(plugin.path)
        }
        defaults.set(Array(disabled), forKey: "disabledPlugins")
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
