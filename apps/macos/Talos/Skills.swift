import Foundation
import AppKit

struct Skill: Identifiable, Equatable, Sendable {
    let name: String
    let description: String
    let path: String

    var id: String { path }
}

@MainActor
final class SkillStore: ObservableObject {
    @Published private(set) var skills: [Skill] = []
    @Published private(set) var disabled: Set<String> = []

    private let defaults = UserDefaults.standard

    init() {
        if let values = defaults.stringArray(forKey: "disabledSkills") {
            disabled = Set(values)
        }
        reload()
    }

    var enabled: [Skill] {
        skills.filter { !disabled.contains($0.path) }
    }

    var directory: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("Talos/skills", isDirectory: true)
    }

    func reload(workspace: URL? = nil, extraDirectories: [URL] = []) {
        var roots = [directory]
        if let workspace {
            roots.append(workspace.appendingPathComponent(".talos/skills", isDirectory: true))
        }
        roots.append(contentsOf: extraDirectories)
        var found: [Skill] = []
        for root in roots {
            guard let entries = try? FileManager.default.contentsOfDirectory(
                at: root,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            ) else { continue }
            for entry in entries {
                let skillFile = entry.appendingPathComponent("SKILL.md")
                guard FileManager.default.fileExists(atPath: skillFile.path) else { continue }
                if let skill = parse(skillFile, fallbackName: entry.lastPathComponent) {
                    found.append(skill)
                }
            }
        }
        skills = found.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    func setEnabled(_ skill: Skill, _ enabled: Bool) {
        if enabled {
            disabled.remove(skill.path)
        } else {
            disabled.insert(skill.path)
        }
        defaults.set(Array(disabled), forKey: "disabledSkills")
    }

    func openFolder() {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(directory)
    }

    func createStarter() {
        let folder = directory.appendingPathComponent("example-skill", isDirectory: true)
        let file = folder.appendingPathComponent("SKILL.md")
        guard !FileManager.default.fileExists(atPath: file.path) else {
            openFolder()
            return
        }
        let body = """
        ---
        name: Example Skill
        description: A short description of when the model should use this skill.
        ---

        # Example Skill

        Replace this with reusable instructions, checklists, or project conventions.
        The model sees this text only when it calls `read_skill`.
        """
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try body.write(to: file, atomically: true, encoding: .utf8)
            reload()
            openFolder()
        } catch {
            /* The folder can be opened manually if creation fails. */
        }
    }

    private func parse(_ url: URL, fallbackName: String) -> Skill? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        var name = fallbackName
        var description = ""
        if text.hasPrefix("---") {
            let parts = text.components(separatedBy: "---")
            if parts.count > 2 {
                let frontmatter = parts[1]
                for line in frontmatter.split(separator: "\n") {
                    let pair = line.split(separator: ":", maxSplits: 1).map(String.init)
                    guard pair.count == 2 else { continue }
                    let key = pair[0].trimmingCharacters(in: .whitespaces)
                    let value = pair[1]
                        .trimmingCharacters(in: .whitespaces)
                        .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                    if key == "name", !value.isEmpty { name = value }
                    if key == "description" { description = value }
                }
            }
        }
        return Skill(name: name, description: description, path: url.path)
    }
}
