import Foundation
import AppKit
import ApplicationServices
import CoreGraphics
import CoreFoundation

enum ToolValue: Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([ToolValue])
    case object([String: ToolValue])
    case null

    init(any value: Any) {
        switch value {
        case let value as String:
            self = .string(value)
        case let value as NSNumber:
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                self = .bool(value.boolValue)
            } else {
                self = .number(value.doubleValue)
            }
        case let value as [Any]:
            self = .array(value.map(ToolValue.init(any:)))
        case let value as [String: Any]:
            self = .object(value.mapValues(ToolValue.init(any:)))
        default:
            self = .null
        }
    }

    var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var int: Int? {
        switch self {
        case .number(let value): return Int(value)
        case .string(let value): return Int(value)
        default: return nil
        }
    }

    var bool: Bool? {
        switch self {
        case .bool(let value): return value
        case .string(let value): return ["true", "yes", "1"].contains(value.lowercased())
        default: return nil
        }
    }

    var array: [ToolValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var object: [String: ToolValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var anyValue: Any {
        switch self {
        case .string(let value): return value
        case .number(let value): return value
        case .bool(let value): return value
        case .array(let values): return values.map(\.anyValue)
        case .object(let values): return values.mapValues(\.anyValue)
        case .null: return NSNull()
        }
    }
}

struct ToolCall: Sendable {
    let name: String
    let arguments: [String: ToolValue]

    func string(_ key: String) -> String? { arguments[key]?.string }
    func int(_ key: String) -> Int? { arguments[key]?.int }
    func bool(_ key: String) -> Bool? { arguments[key]?.bool }
    func array(_ key: String) -> [ToolValue]? { arguments[key]?.array }

    var summary: String {
        switch name {
        case "shell":
            return string("command") ?? "Run a shell command"
        case "read_file", "write_file", "view_image", "create_document":
            return string("path") ?? name
        case "list_files":
            return string("path") ?? "Workspace"
        case "search_files":
            return string("query") ?? "Search files"
        case "read_skill":
            return string("name") ?? "Read a skill"
        case "apply_patch":
            let patch = string("patch") ?? ""
            return patch.split(separator: "\n").first.map(String.init) ?? "Apply a patch"
        case "computer":
            let action = string("action") ?? "action"
            if let x = int("x"), let y = int("y") {
                return "\(action) at \(x),\(y)"
            }
            return action
        case "web_fetch":
            return string("url") ?? "Fetch a URL"
        case "web_search":
            return string("query") ?? "Search the web"
        case "update_plan":
            return "Update the task plan"
        default:
            return name
        }
    }
}

struct ToolResult: Sendable {
    let status: ToolStatus
    let output: String
    var attachmentPath: String? = nil

    static func ok(_ output: String, attachment: String? = nil) -> ToolResult {
        ToolResult(status: .succeeded, output: output, attachmentPath: attachment)
    }

    static func fail(_ output: String) -> ToolResult {
        ToolResult(status: .failed, output: output)
    }

    static func denied(_ output: String) -> ToolResult {
        ToolResult(status: .denied, output: output)
    }
}

struct ToolContext: Sendable {
    let workspace: String
    let sandbox: SandboxMode
    let network: Bool
    let enabledShell: Bool
    let enabledFileEdits: Bool
    let enabledComputerUse: Bool
    let enabledWeb: Bool
    let enabledPlan: Bool
    let skills: [Skill]
    let mcpTools: [MCPTool]
    let maxOutputCharacters = 30_000
}

enum ToolParser {
    struct Parsed: Sendable {
        let call: ToolCall
        let cleaned: String
    }

    static func extract(from text: String) -> Parsed? {
        var searchStart = text.startIndex
        while let fenceStart = text.range(of: "```", range: searchStart..<text.endIndex) {
            let afterFence = fenceStart.upperBound
            let languageEnd = text[afterFence...].firstIndex(of: "\n") ?? text.endIndex
            let language = text[afterFence..<languageEnd]
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
            guard let fenceEnd = text.range(of: "```", range: languageEnd..<text.endIndex) else {
                break
            }
            let body = text[languageEnd..<fenceEnd.lowerBound]
                .trimmingCharacters(in: .whitespacesAndNewlines)

            if language.isEmpty || language == "tool" || language == "json",
               let call = parse(body) {
                var cleaned = text
                cleaned.removeSubrange(fenceStart.lowerBound..<fenceEnd.upperBound)
                return Parsed(call: call, cleaned: cleaned.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            searchStart = fenceEnd.upperBound
        }
        return nil
    }

    private static func parse(_ body: String) -> ToolCall? {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let name = (object["name"] as? String) ?? (object["tool"] as? String),
              !name.isEmpty else {
            return nil
        }
        let rawArguments = object["arguments"] as? [String: Any] ?? [:]
        let arguments = rawArguments.mapValues(ToolValue.init(any:))
        return ToolCall(name: name, arguments: arguments)
    }
}

enum ToolPrompt {
    static func system(context: ToolContext) -> String {
        var lines = [
            "You are a coding agent with access to real tools on the user's Mac.",
            "Use tools when you need to inspect files, run commands, edit code, search the web, make a plan, or control the desktop.",
            "To call a tool, reply with exactly one fenced block and no other content in the block:",
            "```tool",
            "{\"name\":\"tool_name\",\"arguments\":{...}}",
            "```",
            "Use one tool per reply. The app runs it and sends you a message beginning with \"Tool result\". Then continue.",
            "Never invent or predict tool output. If a tool fails, read the error and adjust.",
            "Do not claim an action happened unless a tool result confirms it. When the task is complete, answer normally without a tool block.",
            "",
            "Workspace: \(context.workspace)",
            "Sandbox: \(context.sandbox.title). Network: \(context.network ? "enabled" : "disabled").",
            "All listed tools work with every model; the app executes them.",
            "",
            "Available tools:",
        ]
        if context.enabledShell {
            lines.append("- shell: {\"command\":\"...\",\"timeout_seconds\":30} Run a zsh command in the workspace.")
        }
        if context.enabledFileEdits {
            lines.append("- read_file: {\"path\":\"...\",\"start_line\":1,\"end_line\":200} Read a text file.")
            lines.append("- list_files: {\"path\":\".\",\"depth\":2,\"limit\":200} List files in the workspace.")
            lines.append("- write_file: {\"path\":\"...\",\"content\":\"...\"} Write a text file.")
            lines.append("- create_document: {\"path\":\"notes/plan.md\",\"content\":\"...\",\"format\":\"md\"} Create a document from scratch (md, txt, html, csv, json, rtf, docx).")
            lines.append("- search_files: {\"query\":\"...\",\"path\":\".\",\"glob\":\"*.swift\",\"limit\":100} Search text files in the project.")
            lines.append("- view_image: {\"path\":\"...\"} Show an image or file from disk to the user.")
            lines.append("- apply_patch: {\"patch\":\"...\"} Apply a unified diff inside the workspace.")
        }
        if context.enabledComputerUse {
            lines.append("- computer: {\"action\":\"screenshot\"|\"frontmost_app\"|\"list_windows\"|\"click\"|\"type\"|\"key\"|\"scroll\"|\"activate_app\"|\"open_url\", ...} Control the Mac. click takes x,y; type takes text; key takes key (for example cmd+shift+p); scroll takes dx,dy.")
        }
        if context.enabledWeb {
            lines.append("- web_fetch: {\"url\":\"https://...\"} Fetch a page as text.")
            lines.append("- web_search: {\"query\":\"...\"} Search the web.")
        }
        if context.enabledPlan {
            lines.append("- update_plan: {\"steps\":[{\"title\":\"...\",\"status\":\"pending\"|\"in_progress\"|\"completed\"}]} Replace the task plan.")
        }
        if !context.skills.isEmpty {
            lines.append("")
            lines.append("Skills (load the full instructions before applying one):")
            for skill in context.skills {
                let detail = skill.description.isEmpty ? skill.name : "\(skill.name): \(skill.description)"
                lines.append("- \(detail)")
            }
            lines.append("- read_skill: {\"name\":\"...\"} Load a skill's full instructions.")
        }
        if !context.mcpTools.isEmpty {
            lines.append("")
            lines.append("MCP tools:")
            for tool in context.mcpTools {
                lines.append("- \(tool.toolName): \(tool.promptDescription)")
            }
        }
        return lines.joined(separator: "\n")
    }
}

enum ToolPermission {
    static var accessibility: Bool {
        AXIsProcessTrusted()
    }

    static var screenRecording: Bool {
        CGPreflightScreenCaptureAccess()
    }

    static func requestAccessibility() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    static func requestScreenRecording() {
        _ = CGRequestScreenCaptureAccess()
    }

    static func openAccessibilitySettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    static func openScreenRecordingSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }
}

struct ToolRunner: Sendable {
    let context: ToolContext

    static func requiresApproval(_ call: ToolCall) -> Bool {
        switch call.name {
        case "shell", "write_file", "apply_patch":
            return true
        case "computer":
            let action = call.string("action") ?? ""
            return !["screenshot", "frontmost_app", "list_windows"].contains(action)
        default:
            return false
        }
    }

    func run(_ call: ToolCall) async -> ToolResult {
        switch call.name {
        case "shell":
            return await runShell(call)
        case "read_file":
            return readFile(call)
        case "list_files":
            return listFiles(call)
        case "write_file":
            return writeFile(call)
        case "create_document":
            return await createDocument(call)
        case "search_files":
            return await searchFiles(call)
        case "read_skill":
            return readSkill(call)
        case "view_image":
            return viewImage(call)
        case "apply_patch":
            return await applyPatch(call)
        case "computer":
            return await runComputer(call)
        case "web_fetch":
            return await webFetch(call)
        case "web_search":
            return await webSearch(call)
        default:
            return .fail("Unknown tool '\(call.name)'.")
        }
    }

    private var workspaceURL: URL {
        URL(fileURLWithPath: context.workspace, isDirectory: true).standardizedFileURL
    }

    private func runShell(_ call: ToolCall) async -> ToolResult {
        guard context.enabledShell else { return .fail("Shell is disabled in Settings.") }
        guard let command = call.string("command"), !command.isEmpty else {
            return .fail("shell requires a command.")
        }
        let timeout = min(max(call.int("timeout_seconds") ?? 30, 1), 120)
        let result = await runProcess(
            "/bin/zsh",
            ["-lc", command],
            directory: workspaceURL,
            timeout: TimeInterval(timeout)
        )
        if result.timedOut {
            return .fail("Command timed out after \(timeout)s.\n\(result.text)")
        }
        let status: ToolStatus = result.code == 0 ? .succeeded : .failed
        return ToolResult(status: status, output: "exit \(result.code)\n\n\(result.text)")
    }

    private func readFile(_ call: ToolCall) -> ToolResult {
        guard let path = call.string("path"), !path.isEmpty else {
            return .fail("read_file requires a path.")
        }
        let url = resolve(path)
        guard let data = try? Data(contentsOf: url) else {
            return .fail("Could not read \(url.path).")
        }
        guard data.count <= 2_000_000 else {
            return .fail("File is larger than 2 MB.")
        }
        guard let content = String(data: data, encoding: .utf8) else {
            return .fail("File is not UTF-8 text.")
        }
        let lines = content.components(separatedBy: "\n")
        let start = min(max(call.int("start_line") ?? 1, 1), max(lines.count, 1))
        let requestedEnd = call.int("end_line") ?? min(lines.count, start + 199)
        let end = min(max(requestedEnd, start), lines.count)
        let numbered = (start...end).map { index in
            "\(index)| \(lines[index - 1])"
        }.joined(separator: "\n")
        return .ok("\(url.path) (\(lines.count) lines)\n\(numbered)", attachment: url.path)
    }

    private func listFiles(_ call: ToolCall) -> ToolResult {
        let relativePath = call.string("path") ?? "."
        let root = resolve(relativePath)
        let depth = min(max(call.int("depth") ?? 2, 1), 5)
        let limit = min(max(call.int("limit") ?? 200, 1), 1000)
        let skipped = Set([".git", ".build", "node_modules", "DerivedData", ".DS_Store"])

        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey],
            options: [.skipsPackageDescendants]
        ) else {
            return .fail("Could not list \(root.path).")
        }

        var entries: [String] = []
        for case let url as URL in enumerator {
            let components = url.pathComponents
            if components.contains(where: { skipped.contains($0) }) {
                enumerator.skipDescendants()
                continue
            }
            let relative = url.path.replacingOccurrences(of: root.path + "/", with: "")
            if relative.split(separator: "/").count > depth { continue }
            let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
            if values?.isDirectory == true {
                entries.append(relative + "/")
            } else {
                let size = values?.fileSize.map { " (\($0) bytes)" } ?? ""
                entries.append(relative + size)
            }
            if entries.count >= limit { break }
        }
        return .ok(entries.sorted().joined(separator: "\n"))
    }

    private func writeFile(_ call: ToolCall) -> ToolResult {
        guard context.enabledFileEdits else { return .fail("File editing is disabled in Settings.") }
        guard let path = call.string("path"), !path.isEmpty, let content = call.string("content") else {
            return .fail("write_file requires path and content.")
        }
        let url = resolve(path)
        do {
            try ensureWriteAllowed(url)
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try Data(content.utf8).write(to: url, options: .atomic)
            return .ok("Wrote \(content.utf8.count) bytes to \(url.path).", attachment: url.path)
        } catch {
            return .fail(error.localizedDescription)
        }
    }

    private func createDocument(_ call: ToolCall) async -> ToolResult {
        guard context.enabledFileEdits else { return .fail("File editing is disabled in Settings.") }
        guard let path = call.string("path"), !path.isEmpty, let content = call.string("content") else {
            return .fail("create_document requires path and content.")
        }
        let format = call.string("format") ?? URL(fileURLWithPath: path).pathExtension
        let relative: String
        if path.hasPrefix("/") {
            relative = ProjectFileTree.relativePath(of: URL(fileURLWithPath: path), in: workspaceURL)
        } else {
            relative = path
        }
        let url = resolve(relative)
        do {
            try ensureWriteAllowed(url)
            let created = try DocumentWriter.create(
                name: relative,
                format: format.isEmpty ? "md" : format,
                content: content,
                in: workspaceURL
            )
            return .ok("Created \(created.path).", attachment: created.path)
        } catch {
            return .fail(error.localizedDescription)
        }
    }

    private func searchFiles(_ call: ToolCall) async -> ToolResult {
        guard let query = call.string("query"), !query.isEmpty else {
            return .fail("search_files requires a query.")
        }
        let root = resolve(call.string("path") ?? ".")
        let limit = min(max(call.int("limit") ?? 100, 1), 500)
        var arguments = ["-R", "-n", "--binary-files=without-match"]
        if let glob = call.string("glob"), !glob.isEmpty {
            arguments.append("--include=\(glob)")
        }
        arguments.append(contentsOf: ["--", query, root.path])
        let result = await runProcess(
            "/usr/bin/grep",
            arguments,
            directory: workspaceURL,
            timeout: 30
        )
        if result.code == 1 {
            return .ok("No matches for \(query).")
        }
        guard result.code == 0 else {
            return .fail(result.text.isEmpty ? "Search failed." : result.text)
        }
        let lines = result.text
            .split(separator: "\n")
            .map(String.init)
            .filter { line in
                !line.contains("/.git/")
                    && !line.contains("/node_modules/")
                    && !line.contains("/.build/")
                    && !line.contains("/DerivedData/")
            }
            .prefix(limit)
        return .ok(lines.joined(separator: "\n"))
    }

    private func readSkill(_ call: ToolCall) -> ToolResult {
        guard let name = call.string("name"), !name.isEmpty else {
            return .fail("read_skill requires a name.")
        }
        guard let skill = context.skills.first(where: {
            $0.name.caseInsensitiveCompare(name) == .orderedSame || $0.id == name
        }) else {
            return .fail("No enabled skill matches '\(name)'.")
        }
        guard let text = try? String(contentsOfFile: skill.path, encoding: .utf8) else {
            return .fail("Could not read \(skill.path).")
        }
        return .ok(String(text.prefix(60_000)))
    }

    private func viewImage(_ call: ToolCall) -> ToolResult {
        guard let path = call.string("path"), !path.isEmpty else {
            return .fail("view_image requires a path.")
        }
        let url = resolve(path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return .fail("No file exists at \(url.path).")
        }
        return .ok("Opened \(url.lastPathComponent) for viewing.", attachment: url.path)
    }

    private func applyPatch(_ call: ToolCall) async -> ToolResult {
        guard context.enabledFileEdits else { return .fail("File editing is disabled in Settings.") }
        guard let patch = call.string("patch"), !patch.isEmpty else {
            return .fail("apply_patch requires a patch.")
        }
        do {
            try ensureWriteAllowed(workspaceURL)
        } catch {
            return .fail(error.localizedDescription)
        }
        let patchURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-\(UUID().uuidString).patch")
        do {
            try patch.write(to: patchURL, atomically: true, encoding: .utf8)
        } catch {
            return .fail("Could not stage the patch: \(error.localizedDescription)")
        }
        defer { try? FileManager.default.removeItem(at: patchURL) }

        let git = await runProcess(
            "/usr/bin/git",
            ["apply", "--whitespace=nowarn", patchURL.path],
            directory: workspaceURL,
            timeout: 30
        )
        if git.code == 0 {
            return .ok(git.text.isEmpty ? "Patch applied." : git.text)
        }
        let patchResult = await runProcess(
            "/usr/bin/patch",
            ["-p1", "--forward", "--batch", "-i", patchURL.path],
            directory: workspaceURL,
            timeout: 30
        )
        if patchResult.code == 0 {
            return .ok(patchResult.text)
        }
        return .fail("git apply failed:\n\(git.text)\npatch failed:\n\(patchResult.text)")
    }

    @MainActor
    private func runComputer(_ call: ToolCall) async -> ToolResult {
        guard context.enabledComputerUse else { return .fail("Computer use is disabled in Settings.") }
        guard let action = call.string("action"), !action.isEmpty else {
            return .fail("computer requires an action.")
        }

        switch action {
        case "screenshot":
            guard ToolPermission.screenRecording else {
                ToolPermission.requestScreenRecording()
                return .fail("Screen Recording is not enabled for Talos. Enable it in System Settings, then try again.")
            }
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("TalosScreenshots")
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let path = directory.appendingPathComponent("talos-\(UUID().uuidString).png")
            let result = await runProcessDirect(
                "/usr/sbin/screencapture",
                ["-x", path.path],
                directory: directory,
                timeout: 15
            )
            guard result.code == 0 else {
                return .fail("Screenshot failed: \(result.text)")
            }
            let size = NSImage(contentsOf: path)?.size ?? .zero
            return .ok(
                "Screenshot saved to \(path.path) (\(Int(size.width))x\(Int(size.height))).",
                attachment: path.path
            )

        case "frontmost_app":
            guard let app = NSWorkspace.shared.frontmostApplication else {
                return .fail("No frontmost application.")
            }
            return .ok("\(app.localizedName ?? "Unknown") — \(app.bundleIdentifier ?? "no bundle id") (pid \(app.processIdentifier))")

        case "list_windows":
            let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
            guard let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
                return .fail("Could not list windows.")
            }
            let lines = raw.prefix(25).compactMap { window -> String? in
                guard let owner = window[kCGWindowOwnerName as String] as? String else { return nil }
                let name = window[kCGWindowName as String] as? String ?? ""
                let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
                let x = bounds["X"] ?? ""
                let y = bounds["Y"] ?? ""
                let w = bounds["Width"] ?? ""
                let h = bounds["Height"] ?? ""
                return "\(owner) | \(name) | \(x),\(y) \(w)x\(h)"
            }
            return .ok(lines.joined(separator: "\n"))

        case "click":
            guard ToolPermission.accessibility else {
                ToolPermission.requestAccessibility()
                return .fail("Accessibility is not enabled for Talos. Enable it in System Settings, then try again.")
            }
            guard let x = call.int("x"), let y = call.int("y") else {
                return .fail("computer click requires x and y screen coordinates.")
            }
            let button = call.string("button")?.lowercased() ?? "left"
            let count = min(max(call.int("click_count") ?? 1, 1), 3)
            let point = CGPoint(x: x, y: y)
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            let down: CGEventType = button == "right" ? .rightMouseDown : .leftMouseDown
            let up: CGEventType = button == "right" ? .rightMouseUp : .leftMouseUp
            for _ in 0..<count {
                CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: point, mouseButton: button == "right" ? .right : .left)?.post(tap: .cghidEventTap)
                CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: point, mouseButton: button == "right" ? .right : .left)?.post(tap: .cghidEventTap)
                usleep(80_000)
            }
            return .ok("Clicked \(button) at \(Int(point.x)),\(Int(point.y)).")

        case "type":
            guard ToolPermission.accessibility else {
                ToolPermission.requestAccessibility()
                return .fail("Accessibility is not enabled for Talos. Enable it in System Settings, then try again.")
            }
            guard let text = call.string("text"), !text.isEmpty else {
                return .fail("computer type requires text.")
            }
            let pasteboard = NSPasteboard.general
            let previous = pasteboard.string(forType: .string)
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            postKey(9, flags: .maskCommand)
            try? await Task.sleep(nanoseconds: 150_000_000)
            if let previous {
                pasteboard.clearContents()
                pasteboard.setString(previous, forType: .string)
            }
            return .ok("Typed \(text.count) characters.")

        case "key":
            guard ToolPermission.accessibility else {
                ToolPermission.requestAccessibility()
                return .fail("Accessibility is not enabled for Talos. Enable it in System Settings, then try again.")
            }
            guard let key = call.string("key"), !key.isEmpty else {
                return .fail("computer key requires a key combination.")
            }
            guard let parsed = parseKey(key) else {
                return .fail("Unknown key combination '\(key)'.")
            }
            postKey(parsed.code, flags: parsed.flags)
            return .ok("Pressed \(key).")

        case "scroll":
            guard ToolPermission.accessibility else {
                ToolPermission.requestAccessibility()
                return .fail("Accessibility is not enabled for Talos. Enable it in System Settings, then try again.")
            }
            let dx = Int32(call.int("dx") ?? 0)
            let dy = Int32(call.int("dy") ?? 0)
            CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: dy, wheel2: dx, wheel3: 0)?
                .post(tap: .cghidEventTap)
            return .ok("Scrolled by \(dx),\(dy).")

        case "activate_app":
            guard let name = call.string("app"), !name.isEmpty else {
                return .fail("computer activate_app requires app.")
            }
            guard let app = NSWorkspace.shared.runningApplications.first(where: {
                $0.localizedName == name || $0.bundleIdentifier == name
            }) else {
                return .fail("No running app matches '\(name)'.")
            }
            app.activate(options: [.activateIgnoringOtherApps])
            return .ok("Activated \(app.localizedName ?? name).")

        case "open_url":
            guard let raw = call.string("url"), let url = URL(string: raw) else {
                return .fail("computer open_url requires a URL.")
            }
            NSWorkspace.shared.open(url)
            return .ok("Opened \(raw).")

        default:
            return .fail("Unknown computer action '\(action)'.")
        }
    }

    private func webFetch(_ call: ToolCall) async -> ToolResult {
        guard context.enabledWeb else { return .fail("Web tools are disabled in Settings.") }
        guard context.network || context.sandbox == .fullAccess else {
            return .fail("Network access is disabled in Settings.")
        }
        guard let raw = call.string("url"), let url = URL(string: raw) else {
            return .fail("web_fetch requires a URL.")
        }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 30
            request.setValue("Talos/0.2", forHTTPHeaderField: "User-Agent")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return .fail("The page returned an error.")
            }
            let html = String(decoding: data, as: UTF8.self)
            return .ok(truncate(stripHTML(html)))
        } catch {
            return .fail(error.localizedDescription)
        }
    }

    private func webSearch(_ call: ToolCall) async -> ToolResult {
        guard context.enabledWeb else { return .fail("Web tools are disabled in Settings.") }
        guard context.network || context.sandbox == .fullAccess else {
            return .fail("Network access is disabled in Settings.")
        }
        guard let query = call.string("query"), !query.isEmpty else {
            return .fail("web_search requires a query.")
        }
        var components = URLComponents(string: "https://html.duckduckgo.com/html/")
        components?.queryItems = [URLQueryItem(name: "q", value: query)]
        guard let url = components?.url else { return .fail("Could not build the search URL.") }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 30
            request.setValue("Talos/0.2", forHTTPHeaderField: "User-Agent")
            let (data, _) = try await URLSession.shared.data(for: request)
            let html = String(decoding: data, as: UTF8.self)
            let pattern = #"class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>"#
            let regex = try NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators, .caseInsensitive])
            let range = NSRange(html.startIndex..<html.endIndex, in: html)
            let matches = regex.matches(in: html, range: range).prefix(8)
            guard !matches.isEmpty else {
                return .fail("No results were returned. DuckDuckGo may be rate-limiting this machine.")
            }
            let lines = matches.compactMap { match -> String? in
                guard let linkRange = Range(match.range(at: 1), in: html),
                      let titleRange = Range(match.range(at: 2), in: html) else { return nil }
                let link = decodeEntities(String(html[linkRange]))
                let title = stripHTML(String(html[titleRange]))
                return "- \(title)\n  \(link)"
            }
            return .ok(lines.joined(separator: "\n"))
        } catch {
            return .fail(error.localizedDescription)
        }
    }

    private func resolve(_ path: String) -> URL {
        let expanded = (path as NSString).expandingTildeInPath
        if expanded.hasPrefix("/") {
            return URL(fileURLWithPath: expanded).standardizedFileURL
        }
        return workspaceURL.appendingPathComponent(expanded).standardizedFileURL
    }

    private func ensureWriteAllowed(_ url: URL) throws {
        switch context.sandbox {
        case .fullAccess:
            return
        case .readOnly:
            throw ToolError.message("The sandbox is read-only. Change the sandbox mode in Settings to edit files.")
        case .workspaceWrite:
            let roots = [
                workspaceURL,
                FileManager.default.temporaryDirectory.standardizedFileURL,
                URL(fileURLWithPath: "/tmp", isDirectory: true),
                URL(fileURLWithPath: "/private/tmp", isDirectory: true),
            ]
            guard roots.contains(where: { isWithin(url, $0) }) else {
                throw ToolError.message("The sandbox only permits writes inside \(workspaceURL.path) and temporary folders.")
            }
        }
    }

    private func isWithin(_ url: URL, _ root: URL) -> Bool {
        let path = url.standardizedFileURL.path
        let rootPath = root.standardizedFileURL.path
        return path == rootPath || path.hasPrefix(rootPath + "/")
    }

    private struct ProcessOutput: Sendable {
        let code: Int32
        let text: String
        let timedOut: Bool
    }

    private func runProcess(
        _ executable: String,
        _ arguments: [String],
        directory: URL,
        timeout: TimeInterval
    ) async -> ProcessOutput {
        guard context.sandbox != .fullAccess else {
            return await runProcessDirect(executable, arguments, directory: directory, timeout: timeout)
        }
        let profileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("talos-\(UUID().uuidString).sb")
        do {
            try sandboxProfile().write(to: profileURL, atomically: true, encoding: .utf8)
        } catch {
            return ProcessOutput(code: -1, text: "Could not create the sandbox profile: \(error.localizedDescription)", timedOut: false)
        }
        defer { try? FileManager.default.removeItem(at: profileURL) }
        return await runProcessDirect(
            "/usr/bin/sandbox-exec",
            ["-f", profileURL.path, executable] + arguments,
            directory: directory,
            timeout: timeout
        )
    }

    private func runProcessDirect(
        _ executable: String,
        _ arguments: [String],
        directory: URL,
        timeout: TimeInterval
    ) async -> ProcessOutput {
        await Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
            process.currentDirectoryURL = directory
            process.environment = ProcessInfo.processInfo.environment.merging(["PWD": directory.path]) { _, new in new }
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            process.standardInput = FileHandle.nullDevice

            do {
                try process.run()
            } catch {
                return ProcessOutput(code: -1, text: error.localizedDescription, timedOut: false)
            }

            let timedOut = TimeoutFlag()
            let timer = DispatchSource.makeTimerSource(queue: .global())
            timer.schedule(deadline: .now() + timeout)
            timer.setEventHandler {
                if process.isRunning {
                    timedOut.set()
                    process.terminate()
                }
            }
            timer.resume()

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            timer.cancel()
            let text = String(decoding: data, as: UTF8.self)
            let truncated = text.count > 30_000 ? String(text.prefix(30_000)) + "\n… output truncated …" : text
            return ProcessOutput(code: process.terminationStatus, text: truncated, timedOut: timedOut.isSet)
        }.value
    }

    private func sandboxProfile() -> String {
        var rules = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
        ]
        let temp = FileManager.default.temporaryDirectory.path
        var writable = [temp, "/tmp", "/private/tmp"]
        if context.sandbox == .workspaceWrite {
            writable.append(workspaceURL.path)
        }
        let subpaths = writable
            .map { "    (subpath \(schemeString($0)))" }
            .joined(separator: "\n")
        rules.append("(allow file-write*\n\(subpaths)\n)")
        rules.append("(allow file-write* (literal \"/dev/null\") (literal \"/dev/stdout\") (literal \"/dev/stderr\"))")
        if !context.network {
            rules.append("(deny network*)")
        }
        return rules.joined(separator: "\n")
    }

    private func schemeString(_ value: String) -> String {
        "\"\(value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\""))\""
    }

    private func stripHTML(_ html: String) -> String {
        var text = html
        text = text.replacingOccurrences(of: #"<script[\s\S]*?</script>"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: #"<style[\s\S]*?</style>"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: #"<br\s*/?>"#, with: "\n", options: .regularExpression)
        text = text.replacingOccurrences(of: #"</p>"#, with: "\n", options: .regularExpression)
        text = text.replacingOccurrences(of: #"<[^>]+>"#, with: " ", options: .regularExpression)
        text = decodeEntities(text)
        text = text.replacingOccurrences(of: #"[ \t]+"#, with: " ", options: .regularExpression)
        text = text.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func decodeEntities(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&#x27;", with: "'")
            .replacingOccurrences(of: "&nbsp;", with: " ")
    }

    private func truncate(_ text: String) -> String {
        text.count > context.maxOutputCharacters
            ? String(text.prefix(context.maxOutputCharacters)) + "\n… truncated …"
            : text
    }

    private func postKey(_ code: CGKeyCode, flags: CGEventFlags) {
        let source = CGEventSource(stateID: .hidSystemState)
        let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }

    private func parseKey(_ raw: String) -> (code: CGKeyCode, flags: CGEventFlags)? {
        let parts = raw.lowercased()
            .replacingOccurrences(of: " ", with: "")
            .split(separator: "+")
            .map(String.init)
        guard let key = parts.last else { return nil }
        var flags: CGEventFlags = []
        for modifier in parts.dropLast() {
            switch modifier {
            case "cmd", "command": flags.insert(.maskCommand)
            case "shift": flags.insert(.maskShift)
            case "option", "alt": flags.insert(.maskAlternate)
            case "control", "ctrl": flags.insert(.maskControl)
            default: return nil
            }
        }
        let codes: [String: CGKeyCode] = [
            "return": 36, "enter": 36, "tab": 48, "space": 49, "escape": 53, "esc": 53,
            "delete": 51, "backspace": 51, "left": 123, "right": 124, "down": 125, "up": 126,
            "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
            "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
            "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
            "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
            "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
            "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
            ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
        ]
        guard let code = codes[key] else { return nil }
        return (code, flags)
    }
}

private final class TimeoutFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    func set() {
        lock.lock()
        value = true
        lock.unlock()
    }

    var isSet: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

enum ToolError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}
