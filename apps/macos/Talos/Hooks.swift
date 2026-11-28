import Foundation

enum HookEvent: String, Codable, CaseIterable, Identifiable, Sendable {
    case sessionStart = "session_start"
    case sessionEnd = "session_end"
    case userPromptSubmit = "user_prompt_submit"
    case preToolUse = "pre_tool_use"
    case postToolUse = "post_tool_use"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .sessionStart: return "Session start"
        case .sessionEnd: return "Session end"
        case .userPromptSubmit: return "Prompt submitted"
        case .preToolUse: return "Before a tool"
        case .postToolUse: return "After a tool"
        }
    }

    var detail: String {
        switch self {
        case .sessionStart: return "Runs when a new task is created."
        case .sessionEnd: return "Runs when a task is archived."
        case .userPromptSubmit: return "Runs before your message is sent. It can block the prompt or add context."
        case .preToolUse: return "Runs before a tool executes. A non-zero exit or a block decision stops the tool."
        case .postToolUse: return "Runs after a tool returns. It can add context to the tool result."
        }
    }

    var symbol: String {
        switch self {
        case .sessionStart: return "play.circle"
        case .sessionEnd: return "stop.circle"
        case .userPromptSubmit: return "text.bubble"
        case .preToolUse: return "shield.lefthalf.filled"
        case .postToolUse: return "checkmark.seal"
        }
    }
}

struct HookDefinition: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var name: String
    var event: HookEvent
    var command: String
    var matcher: String = ""
    var timeout: Int = 30
    var enabled = true
    var trusted = false

    var summary: String {
        let scope = matcher.trimmingCharacters(in: .whitespaces).isEmpty ? "all tools" : matcher
        return "\(event.title) · \(scope)"
    }

    func matches(toolName: String?) -> Bool {
        guard event == .preToolUse || event == .postToolUse else { return true }
        let wanted = matcher
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
        guard !wanted.isEmpty else { return true }
        guard let toolName else { return false }
        return wanted.contains(toolName.lowercased())
    }
}

struct HookOutcome: Sendable {
    let exitCode: Int32
    let timedOut: Bool
    let output: String
    let blocked: Bool
    let reason: String?
    let context: String?

    var blockReason: String? {
        guard blocked else { return nil }
        if let reason, !reason.isEmpty { return reason }
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Hook exited with \(exitCode)." : trimmed
    }
}

enum HookRunner {
    static func run(
        _ hook: HookDefinition,
        event: HookEvent,
        workspace: URL,
        sessionID: UUID,
        tool: ToolCall? = nil,
        toolResult: String? = nil
    ) async -> HookOutcome {
        var payload: [String: Any] = [
            "event": event.rawValue,
            "hook": hook.name,
            "session_id": sessionID.uuidString,
            "cwd": workspace.path,
        ]
        if let tool {
            payload["tool_name"] = tool.name
            payload["tool_arguments"] = tool.arguments.mapValues(\.anyValue)
        }
        if let toolResult {
            payload["tool_result"] = String(toolResult.prefix(30_000))
        }
        let input = (try? JSONSerialization.data(withJSONObject: payload)) ?? Data("{}".utf8)
        let environment = [
            "TALOS_EVENT": event.rawValue,
            "TALOS_HOOK": hook.name,
            "TALOS_SESSION_ID": sessionID.uuidString,
            "TALOS_WORKSPACE": workspace.path,
            "TALOS_TOOL_NAME": tool?.name ?? "",
        ]
        let timeout = TimeInterval(min(max(hook.timeout, 1), 300))

        let result = await Task.detached(priority: .userInitiated) { () -> (Int32, String, Bool) in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/zsh")
            process.arguments = ["-lc", hook.command]
            process.currentDirectoryURL = workspace
            process.environment = ProcessInfo.processInfo.environment
                .merging(environment) { _, new in new }
                .merging(["PWD": workspace.path]) { _, new in new }
            let stdin = Pipe()
            let stdout = Pipe()
            let stderr = Pipe()
            process.standardInput = stdin
            process.standardOutput = stdout
            process.standardError = stderr
            do {
                try process.run()
            } catch {
                return (-1, error.localizedDescription, false)
            }
            stdin.fileHandleForWriting.write(input)
            try? stdin.fileHandleForWriting.close()

            let flag = HookTimeoutFlag()
            let timer = DispatchSource.makeTimerSource(queue: .global())
            timer.schedule(deadline: .now() + timeout)
            timer.setEventHandler {
                if process.isRunning {
                    flag.set()
                    process.terminate()
                }
            }
            timer.resume()

            let out = stdout.fileHandleForReading.readDataToEndOfFile()
            let err = stderr.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            timer.cancel()
            let text = String(decoding: out + err, as: UTF8.self)
            return (process.terminationStatus, text, flag.isSet)
        }.value

        let output = result.1.count > 8_000 ? String(result.1.prefix(8_000)) + "\n…" : result.1
        let parsed = parse(output)
        let blocked = parsed.blocked
            || ((event == .preToolUse) && (result.2 || result.0 != 0))
        return HookOutcome(
            exitCode: result.0,
            timedOut: result.2,
            output: output,
            blocked: blocked,
            reason: parsed.reason,
            context: parsed.context
        )
    }

    private static func parse(_ output: String) -> (blocked: Bool, reason: String?, context: String?) {
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("{"),
              let data = trimmed.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return (false, nil, nil)
        }
        let decision = (object["decision"] as? String)?.lowercased()
        let blocked = decision == "block" || decision == "deny" || object["block"] as? Bool == true
        let context = (object["additional_context"] as? String)
            ?? (object["additionalContext"] as? String)
            ?? (object["context"] as? String)
        return (blocked, object["reason"] as? String, context)
    }
}

final class HookTimeoutFlag: @unchecked Sendable {
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

enum HookTrustDecision: Sendable {
    case runOnce
    case alwaysTrust
    case skip
}

struct HookTrustRequest: Identifiable {
    let id = UUID()
    let hook: HookDefinition
    let event: HookEvent
}

struct HookSummary: Sendable {
    var blocked = false
    var reason: String?
    var contexts: [String] = []
    var notices: [String] = []

    var hasContext: Bool { !contexts.isEmpty }
}

struct HookLogEntry: Identifiable, Sendable {
    let id = UUID()
    let date = Date()
    let hookName: String
    let event: HookEvent
    let status: String
    let output: String
}

@MainActor
final class HookStore: ObservableObject {
    private let defaults = UserDefaults.standard
    private static let storageKey = "hooks"

    @Published private(set) var hooks: [HookDefinition] = []
    @Published private(set) var log: [HookLogEntry] = []
    private(set) var managedLocked = false

    init() {
        if let data = defaults.data(forKey: Self.storageKey),
           let decoded = try? JSONDecoder().decode([HookDefinition].self, from: data) {
            hooks = decoded
        }
    }

    var enabled: [HookDefinition] {
        hooks.filter(\.enabled)
    }

    func matching(event: HookEvent, toolName: String? = nil) -> [HookDefinition] {
        enabled.filter { $0.event == event && $0.matches(toolName: toolName) }
    }

    func applyManaged(_ hooks: [HookDefinition]) {
        self.hooks = hooks
        managedLocked = true
    }

    func clearManaged() {
        managedLocked = false
        if let data = defaults.data(forKey: Self.storageKey),
           let decoded = try? JSONDecoder().decode([HookDefinition].self, from: data) {
            hooks = decoded
        }
    }

    func add(_ hook: HookDefinition) {
        guard !managedLocked else { return }
        hooks.append(hook)
        save()
    }

    func update(_ hook: HookDefinition) {
        guard !managedLocked,
              let index = hooks.firstIndex(where: { $0.id == hook.id }) else { return }
        hooks[index] = hook
        save()
    }

    func remove(_ hook: HookDefinition) {
        guard !managedLocked else { return }
        hooks.removeAll { $0.id == hook.id }
        save()
    }

    func setEnabled(_ hook: HookDefinition, _ enabled: Bool) {
        guard !managedLocked,
              let index = hooks.firstIndex(where: { $0.id == hook.id }) else { return }
        hooks[index].enabled = enabled
        save()
    }

    func setTrusted(_ hook: HookDefinition, _ trusted: Bool) {
        guard !managedLocked,
              let index = hooks.firstIndex(where: { $0.id == hook.id }) else { return }
        hooks[index].trusted = trusted
        save()
    }

    func record(_ hook: HookDefinition, event: HookEvent, status: String, output: String) {
        log.insert(
            HookLogEntry(
                hookName: hook.name,
                event: event,
                status: status,
                output: output.count > 500 ? String(output.prefix(500)) + "…" : output
            ),
            at: 0
        )
        if log.count > 40 { log.removeLast(log.count - 40) }
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(hooks) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}
