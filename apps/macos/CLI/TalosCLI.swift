import Foundation

#if canImport(Darwin)
import Darwin
#endif

struct CLIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct Options {
    var url: String = ProcessInfo.processInfo.environment["TALOS_APP_SERVER_URL"] ?? "http://127.0.0.1:8790"
    var token: String = ProcessInfo.processInfo.environment["TALOS_APP_SERVER_TOKEN"] ?? ""
    var json = false
    var yes = false
    var deny = false
    var noStream = false
    var title: String?
    var session: String?
    var prompt: String?
    var timeout: Double = 120
    var positionals: [String] = []

    mutating func parse(_ arguments: [String]) throws {
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--url", "-u":
                index += 1
                guard index < arguments.count else { throw CLIError(message: "--url requires a value.") }
                url = arguments[index]
            case "--token", "-t":
                index += 1
                guard index < arguments.count else { throw CLIError(message: "--token requires a value.") }
                token = arguments[index]
            case "--title":
                index += 1
                guard index < arguments.count else { throw CLIError(message: "--title requires a value.") }
                title = arguments[index]
            case "--session", "-s":
                index += 1
                guard index < arguments.count else { throw CLIError(message: "--session requires a value.") }
                session = arguments[index]
            case "--prompt", "-p":
                index += 1
                guard index < arguments.count else { throw CLIError(message: "--prompt requires a value.") }
                prompt = arguments[index]
            case "--timeout":
                index += 1
                guard index < arguments.count, let value = Double(arguments[index]), value > 0 else {
                    throw CLIError(message: "--timeout requires a positive number of seconds.")
                }
                timeout = value
            case "--json":
                json = true
            case "--yes", "-y":
                yes = true
            case "--deny":
                deny = true
            case "--no-stream":
                noStream = true
            case "--help", "-h":
                positionals.append("help")
            case "--version":
                positionals.append("version")
            default:
                if argument.hasPrefix("-"), argument != "-" {
                    throw CLIError(message: "Unknown option \(argument). Run `talos help`.")
                }
                positionals.append(argument)
            }
            index += 1
        }
    }
}

struct RPCClient {
    let baseURL: URL
    let token: String
    let timeout: Double

    init(url: String, token: String, timeout: Double) throws {
        var value = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { throw CLIError(message: "The app-server URL is empty.") }
        if !value.hasPrefix("http://") && !value.hasPrefix("https://") {
            value = "http://" + value
        }
        while value.hasSuffix("/") { value.removeLast() }
        guard let parsed = URL(string: value) else {
            throw CLIError(message: "Invalid app-server URL \(url).")
        }
        self.baseURL = parsed
        self.token = token
        self.timeout = timeout
    }

    func call(_ method: String, _ params: [String: Any] = [:]) async throws -> Any {
        var request = URLRequest(url: baseURL.appendingPathComponent("rpc"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw CLIError(message: "The app server sent no response.")
        }
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        if http.statusCode == 401 || http.statusCode == 403 {
            throw CLIError(message: "Authentication failed. Check --token or TALOS_APP_SERVER_TOKEN.")
        }
        if let error = object["error"] {
            if let row = error as? [String: Any], let message = row["message"] as? String {
                throw CLIError(message: message)
            }
            if let message = error as? String {
                throw CLIError(message: message)
            }
        }
        guard http.statusCode == 200 else {
            throw CLIError(message: "The app server answered HTTP \(http.statusCode).")
        }
        guard let result = object["result"] else {
            throw CLIError(message: "The app server returned no result.")
        }
        return result
    }

    func events(sessionID: String) async throws -> URLSession.AsyncBytes {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("events"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [URLQueryItem(name: "sessionId", value: sessionID)]
        guard let url = components?.url else {
            throw CLIError(message: "Could not build the events URL.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 60 * 60
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw CLIError(message: "Event stream failed (HTTP \(status)). Check the token.")
        }
        return bytes
    }
}

actor ReadyGate {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func open() {
        guard !opened else { return }
        opened = true
        for waiter in waiters { waiter.resume() }
        waiters.removeAll()
    }

    func wait() async {
        if opened { return }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }
}

@main
struct TalosCLI {
    static func main() async {
        #if canImport(Darwin)
        _ = signal(SIGPIPE, SIG_IGN)
        #endif
        do {
            try await run()
            exit(0)
        } catch let error as CLIError {
            FileHandle.standardError.write(Data("talos: \(error.message)\n".utf8))
            exit(2)
        } catch {
            FileHandle.standardError.write(Data("talos: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }

    static func run() async throws {
        var options = Options()
        try options.parse(Array(CommandLine.arguments.dropFirst()))
        guard let command = options.positionals.first else {
            print(help)
            return
        }
        let rest = Array(options.positionals.dropFirst())
        if command == "help" || command == "--help" {
            print(help)
            return
        }
        if command == "version" || command == "--version" {
            print("talos 1.0")
            return
        }
        guard !options.token.isEmpty else {
            throw CLIError(message: "No app-server token. Set TALOS_APP_SERVER_TOKEN or pass --token.")
        }
        let client = try RPCClient(url: options.url, token: options.token, timeout: options.timeout)

        switch command {
        case "info", "ping":
            let info = try await client.call("initialize")
            try printJSONOrSummary(info, json: options.json)
        case "sessions", "list":
            let rows = try await client.call("session.list") as? [[String: Any]] ?? []
            if options.json {
                try printJSON(rows)
            } else if rows.isEmpty {
                print("No sessions.")
            } else {
                for row in rows {
                    let id = row["id"] as? String ?? "?"
                    let title = row["title"] as? String ?? "Untitled"
                    let count = row["messageCount"] as? Int ?? 0
                    print("\(id)  \(count) msg  \(title)")
                }
            }
        case "new", "create":
            let title = options.title ?? rest.joined(separator: " ").trimmingCharacters(in: .whitespaces)
            let result = try await client.call(
                "session.create",
                title.isEmpty ? [:] : ["title": title]
            )
            let id = (result as? [String: Any])?["sessionId"] as? String ?? ""
            if options.json {
                try printJSON(result)
            } else {
                print(id)
            }
        case "show", "get":
            guard let id = options.session ?? rest.first else {
                throw CLIError(message: "show requires a session id.")
            }
            let detail = try await client.call("session.get", ["sessionId": id])
            if options.json {
                try printJSON(detail)
            } else {
                printSession(detail)
            }
        case "send":
            guard let id = options.session ?? rest.first else {
                throw CLIError(message: "send requires a session id.")
            }
            let text = options.prompt ?? rest.dropFirst().joined(separator: " ")
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw CLIError(message: "send requires prompt text.")
            }
            try await runPrompt(client: client, sessionID: id, text: text, options: options)
        case "chat":
            try await chat(client: client, options: options, rest: rest)
        case "stop", "cancel":
            guard let id = options.session ?? rest.first else {
                throw CLIError(message: "stop requires a session id.")
            }
            let result = try await client.call("session.cancel", ["sessionId": id])
            let cancelled = (result as? [String: Any])?["cancelled"] as? Bool ?? false
            print(options.json ? "{\"cancelled\":\(cancelled)}" : "cancelled: \(cancelled)")
        case "approvals", "approval-list":
            let result = try await client.call("approval.pending")
            if options.json {
                try printJSON(result)
                return
            }
            if let row = result as? [String: Any], let id = row["approvalId"] as? String {
                print("\(id)  \(row["toolName"] as? String ?? "tool")  \(row["summary"] as? String ?? "")")
            } else {
                print("No pending approval.")
            }
        case "approve", "deny":
            let id = options.session ?? rest.first
            guard let id else {
                throw CLIError(message: "approve requires an approval id.")
            }
            let decision = command == "deny" || options.deny ? "deny" : "allow"
            let result = try await client.call("approval.resolve", [
                "approvalId": id,
                "decision": decision,
            ])
            let resolved = (result as? [String: Any])?["resolved"] as? Bool ?? false
            print("\(decision) resolved: \(resolved)")
            if !resolved { exit(1) }
        default:
            throw CLIError(message: "Unknown command \(command). Run `talos help`.")
        }
    }

    static func chat(client: RPCClient, options: Options, rest: [String]) async throws {
        var sessionID = options.session ?? rest.first
        if sessionID == nil {
            let title = options.title ?? "CLI Task"
            let created = try await client.call("session.create", ["title": title])
            sessionID = (created as? [String: Any])?["sessionId"] as? String
            guard let sessionID else { throw CLIError(message: "Could not create a session.") }
            if !options.json {
                print("Session \(sessionID)")
            }
        }
        guard let sessionID else { throw CLIError(message: "Could not resolve a session.") }

        if let prompt = options.prompt {
            try await runPrompt(client: client, sessionID: sessionID, text: prompt, options: options)
            return
        }

        if isatty(STDIN_FILENO) == 0 {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { throw CLIError(message: "No prompt on stdin.") }
            try await runPrompt(client: client, sessionID: sessionID, text: text, options: options)
            return
        }

        print("Session \(sessionID) · type your message, `exit` to quit, `/stop` to cancel.")
        while true {
            FileHandle.standardError.write(Data("you> ".utf8))
            guard let line = readLine() else { break }
            let text = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { continue }
            if ["exit", "quit", "/quit", "/exit"].contains(text.lowercased()) { break }
            if text == "/stop" {
                _ = try? await client.call("session.cancel", ["sessionId": sessionID])
                continue
            }
            try await runPrompt(client: client, sessionID: sessionID, text: text, options: options)
        }
    }

    static func runPrompt(client: RPCClient, sessionID: String, text: String, options: Options) async throws {
        if options.noStream {
            _ = try await client.call("session.prompt", ["sessionId": sessionID, "text": text])
            let detail = try await waitForCompletion(client: client, sessionID: sessionID)
            printSession(detail, assistantOnly: true)
            return
        }

        let gate = ReadyGate()
        let state = StreamState()
        installInterrupt(client: client, sessionID: sessionID)
        let stream = Task {
            try await streamEvents(client: client, sessionID: sessionID, state: state, gate: gate, options: options)
        }
        let timeout = Task {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            await gate.open()
        }
        await gate.wait()
        timeout.cancel()

        do {
            _ = try await client.call("session.prompt", ["sessionId": sessionID, "text": text])
        } catch {
            stream.cancel()
            throw error
        }
        try await stream.value

        let detail = try await client.call("session.get", ["sessionId": sessionID])
        if let final = lastAssistantMessage(detail), let text = final["text"] as? String {
            let shown = state.printed(final["id"] as? String ?? "")
            if text.hasPrefix(shown) {
                let suffix = String(text.dropFirst(shown.count))
                if !suffix.isEmpty {
                    print(suffix, terminator: "")
                    state.startLine()
                }
            } else if shown.isEmpty {
                print(text, terminator: "")
            }
            state.endLine()
        } else {
            state.endLine()
        }
    }

    static func streamEvents(
        client: RPCClient,
        sessionID: String,
        state: StreamState,
        gate: ReadyGate,
        options: Options
    ) async throws {
        let bytes = try await client.events(sessionID: sessionID)
        var eventName = ""
        for try await line in bytes.lines {
            if Task.isCancelled { return }
            if line.hasPrefix("event:") {
                eventName = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
                continue
            }
            guard line.hasPrefix("data:") else { continue }
            let payloadText = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
            guard let data = payloadText.data(using: .utf8),
                  let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let params = object["params"] as? [String: Any] else { continue }
            let method = (object["method"] as? String) ?? eventName
            switch method {
            case "ready":
                await gate.open()
            case "message.created":
                let role = params["role"] as? String ?? ""
                let content = params["text"] as? String ?? ""
                let messageID = params["messageId"] as? String
                if role == "tool" {
                    let name = params["toolName"] as? String ?? "tool"
                    if !content.isEmpty {
                        state.startLine()
                        print("\n[tool \(name)] \(truncate(content, 2_000))\n")
                    }
                } else if role == "assistant" && !content.isEmpty {
                    state.printedAssistant = true
                    state.record(messageID, content)
                    state.startLine()
                    print(content, terminator: "")
                    fflush(stdout)
                }
            case "message.delta":
                let delta = params["delta"] as? String ?? ""
                guard !delta.isEmpty else { continue }
                state.printedAssistant = true
                state.record(params["messageId"] as? String, delta)
                state.startLine()
                print(delta, terminator: "")
                fflush(stdout)
            case "message.completed":
                continue
            case "tool.result":
                let content = params["text"] as? String ?? ""
                let name = params["toolName"] as? String ?? "tool"
                if !content.isEmpty {
                    state.startLine()
                    print("\n[tool \(name)] \(truncate(content, 2_000))\n")
                }
            case "approval.requested":
                try await handleApproval(client: client, params: params, options: options, state: state)
            case "approval.resolved":
                print("[approval resolved]")
            case "run.finished":
                let finishedSession = params["sessionId"] as? String
                if finishedSession == nil || finishedSession == sessionID {
                    return
                }
            default:
                continue
            }
        }
    }

    static func handleApproval(
        client: RPCClient,
        params: [String: Any],
        options: Options,
        state: StreamState
    ) async throws {
        guard let id = params["approvalId"] as? String else { return }
        let tool = params["toolName"] as? String ?? "tool"
        let summary = params["summary"] as? String ?? ""
        state.endLine()
        var decision = "deny"
        if options.yes {
            decision = "allow"
            print("[approval] auto-allowed \(tool): \(summary)")
        } else if isatty(STDIN_FILENO) != 0 {
            print("[approval] \(tool): \(summary)")
            FileHandle.standardError.write(Data("Allow? [y/N] ".utf8))
            let answer = readLine()?.trimmingCharacters(in: .whitespaces).lowercased() ?? ""
            decision = ["y", "yes", "allow"].contains(answer) ? "allow" : "deny"
        } else {
            print("[approval] denied \(tool) (non-interactive; rerun with --yes): \(summary)")
        }
        let result = try await client.call("approval.resolve", [
            "approvalId": id,
            "decision": decision,
        ])
        let resolved = (result as? [String: Any])?["resolved"] as? Bool ?? false
        print("[approval] \(decision) resolved: \(resolved)")
    }

    static func waitForCompletion(client: RPCClient, sessionID: String) async throws -> Any {
        var last: Any = [:]
        for _ in 0..<2_400 {
            try await Task.sleep(nanoseconds: 500_000_000)
            let detail = try await client.call("session.get", ["sessionId": sessionID])
            last = detail
            guard let messages = (detail as? [String: Any])?["messages"] as? [[String: Any]] else { continue }
            let streaming = messages.contains { $0["isStreaming"] as? Bool == true }
            if !streaming,
               let final = messages.last,
               final["role"] as? String == "assistant",
               let text = final["text"] as? String,
               !text.isEmpty {
                return detail
            }
        }
        return last
    }

    static func installInterrupt(client: RPCClient, sessionID: String) {
        #if canImport(Darwin)
        signal(SIGINT, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
        source.setEventHandler {
            FileHandle.standardError.write(Data("\ntalos: stopping…\n".utf8))
            let semaphore = DispatchSemaphore(value: 0)
            Task {
                _ = try? await client.call("session.cancel", ["sessionId": sessionID])
                semaphore.signal()
            }
            _ = semaphore.wait(timeout: .now() + 5)
            exit(130)
        }
        source.resume()
        interruptSource = source
        #endif
    }

    nonisolated(unsafe) static var interruptSource: DispatchSourceSignal?

    static func printSession(_ detail: Any, assistantOnly: Bool = false) {
        guard let object = detail as? [String: Any] else { return }
        let title = object["title"] as? String ?? "Session"
        let id = object["id"] as? String ?? ""
        if !assistantOnly {
            print("\(title) · \(id)")
        }
        let messages = object["messages"] as? [[String: Any]] ?? []
        for message in messages {
            let role = message["role"] as? String ?? "?"
            let text = message["text"] as? String ?? ""
            if assistantOnly && role != "assistant" { continue }
            switch role {
            case "user":
                print("\nyou> \(text)")
            case "assistant":
                print(assistantOnly ? "talos> \(text)" : "\ntalos> \(text)")
            case "tool":
                let name = message["toolName"] as? String ?? "tool"
                print("\n[tool \(name)] \(truncate(text, 2_000))")
            default:
                print("\n\(role)> \(text)")
            }
        }
    }

    static func lastAssistantMessage(_ detail: Any) -> [String: Any]? {
        let messages = (detail as? [String: Any])?["messages"] as? [[String: Any]] ?? []
        return messages.last(where: { $0["role"] as? String == "assistant" })
    }

    static func printJSONOrSummary(_ value: Any, json: Bool) throws {
        if json {
            try printJSON(value)
            return
        }
        guard let object = value as? [String: Any] else { return }
        print("server:    \(object["server"] as? String ?? "?")")
        print("protocol:  \(object["protocol"] as? String ?? "?")")
        print("model:     \(object["model"] as? String ?? "?")")
        print("workspace: \(object["workspace"] as? String ?? "?")")
        print("streaming: \(object["streaming"] as? Bool ?? false)")
        print("approvals: \(object["approvalPolicy"] as? String ?? "?")")
        print("sandbox:   \(object["sandbox"] as? String ?? "?")")
        print("session:   \(object["sessionId"] as? String ?? "none")")
    }

    static func printJSON(_ value: Any) throws {
        let data = try JSONSerialization.data(
            withJSONObject: value,
            options: [.prettyPrinted, .sortedKeys]
        )
        print(String(decoding: data, as: UTF8.self))
    }

    static func truncate(_ text: String, _ limit: Int) -> String {
        text.count <= limit ? text : String(text.prefix(limit)) + "\n… (\(text.count - limit) more characters)"
    }

    static var help: String {
        """
        talos — headless client for the Talos app server

        USAGE
          talos [options] <command> [arguments]

        COMMANDS
          info                        Show server, model, workspace, and policy
          sessions                    List sessions
          new [--title TEXT]          Create a session (prints its id)
          show <sessionID> [--json]   Print a transcript
          send <sessionID> <text>     Send a prompt and stream the reply
          chat [sessionID]            Interactive chat; creates a session if needed
          stop <sessionID>            Cancel the active run
          approvals                   Show the pending approval, if any
          approve <approvalID>        Allow a pending approval
          deny <approvalID>           Deny a pending approval

        OPTIONS
          --url URL        App-server URL (env TALOS_APP_SERVER_URL, default http://127.0.0.1:8790)
          --token TOKEN    Bearer token (env TALOS_APP_SERVER_TOKEN)
          --session ID     Session id for send/chat/show/stop
          --prompt TEXT    Prompt for send/chat; skips the interactive loop
          --title TEXT     Title for a new chat session
          --json           Machine-readable output where supported
          --yes            Auto-allow approval requests
          --deny           Deny with the approve command
          --no-stream      Poll for the final reply instead of using SSE
          --timeout SEC    Request timeout (default 120)

        EXIT CODES
          0 success · 1 runtime error · 2 usage or authentication error · 130 interrupted
        """
    }
}

final class StreamState: @unchecked Sendable {
    var printedAssistant = false
    private var lineOpen = false
    private var printedByMessage: [String: String] = [:]

    func record(_ messageID: String?, _ text: String) {
        guard let messageID else { return }
        printedByMessage[messageID, default: ""] += text
    }

    func printed(_ messageID: String) -> String {
        printedByMessage[messageID] ?? ""
    }

    func startLine() {
        if !lineOpen {
            lineOpen = true
        }
    }

    func endLine() {
        if lineOpen {
            print("")
            lineOpen = false
        }
    }

    func finish() {
        endLine()
    }
}
