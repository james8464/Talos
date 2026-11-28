import Foundation
import CoreFoundation

// Tool call types and parsing live in their own static library so they can be
// unit-tested without launching the app. The app imports this module directly.

public enum ToolValue: Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([ToolValue])
    case object([String: ToolValue])
    case null

    public init(any value: Any) {
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

    public var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var int: Int? {
        switch self {
        case .number(let value): return Int(value)
        case .string(let value): return Int(value)
        default: return nil
        }
    }

    public var bool: Bool? {
        switch self {
        case .bool(let value): return value
        case .string(let value): return ["true", "yes", "1"].contains(value.lowercased())
        default: return nil
        }
    }

    public var array: [ToolValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var object: [String: ToolValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var anyValue: Any {
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

public struct ToolCall: Sendable {
    public let name: String
    public let arguments: [String: ToolValue]

    public init(name: String, arguments: [String: ToolValue]) {
        self.name = name
        self.arguments = arguments
    }

    public func string(_ key: String) -> String? { arguments[key]?.string }
    public func int(_ key: String) -> Int? { arguments[key]?.int }
    public func bool(_ key: String) -> Bool? { arguments[key]?.bool }
    public func array(_ key: String) -> [ToolValue]? { arguments[key]?.array }

    public var summary: String {
        switch name {
        case "shell":
            return string("command") ?? "Run a shell command"
        case "terminal":
            return [string("action") ?? "run", string("command") ?? string("text")]
                .compactMap { $0 }
                .joined(separator: ": ")
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
        case "browser":
            return [string("action") ?? "browser", string("url")].compactMap { $0 }.joined(separator: " ")
        case "browser_cdp":
            return [string("action") ?? "cdp", string("url") ?? string("text") ?? string("selector")].compactMap { $0 }.joined(separator: " ")
        case "update_plan":
            return "Update the task plan"
        case "remember":
            return string("text") ?? "Save a memory"
        case "update_goal":
            return string("text") ?? string("goal") ?? "Update a goal"
        case "subagent":
            return string("label") ?? string("task") ?? "Delegate a subagent"
        case "fanout":
            let count = array("tasks")?.count ?? 0
            return "Fan out \(count) read-only task\(count == 1 ? "" : "s")"
        default:
            return name
        }
    }
}

public enum ToolParser {
    public struct Parsed: Sendable {
        public let calls: [ToolCall]
        public let cleaned: String
    }

    /// Extract every tool call from a reply. Models occasionally emit more than
    /// one fenced block, or switch to their native markup mid-run (DeepSeek's
    /// DSML tags are the common case). All recognised calls are returned in
    /// order and every call block is removed from the visible text.
    public static func extractAll(from text: String) -> Parsed {
        // Collect calls and the exact source ranges that should disappear from
        // the visible reply. Parsing every syntax against the original text and
        // sorting by position keeps mixed replies in textual order.
        var found: [(range: Range<String.Index>, call: ToolCall?)] = []

        // Fenced JSON blocks: ```tool {...} ``` or ```json {...} ```
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
            let blockRange = fenceStart.lowerBound..<fenceEnd.upperBound
            if language.isEmpty || language == "tool" || language == "json" {
                if let call = parse(body, allowInference: language == "tool") {
                    found.append((blockRange, call))
                } else if language == "tool" {
                    // An explicit ```tool block is always a failed call, so
                    // hide the raw JSON. A ```json block may be illustrative
                    // output and is left alone.
                    found.append((blockRange, nil))
                }
            }
            searchStart = fenceEnd.upperBound
        }

        // DeepSeek native markup:
        // <|DSML|tool_calls><|DSML|invoke name="read_file">
        //   <|DSML|parameter name="path" string="true">…</|DSML|parameter>
        // </|DSML|invoke></|DSML|tool_calls>
        // The vertical bar is U+FF5C, so match around the DSML token rather
        // than hard-coding the full-width characters.
        let invokePattern = #"<[^>]{0,40}DSML[^>]{0,40}invoke\s+name="([^"]+)"[^>]*>(.*?)</[^>]{0,40}DSML[^>]{0,40}invoke>"#
        if let invokeRegex = try? NSRegularExpression(pattern: invokePattern, options: [.dotMatchesLineSeparators]) {
            for match in invokeRegex.matches(in: text, range: NSRange(location: 0, length: (text as NSString).length)) {
                guard let range = Range(match.range, in: text),
                      match.numberOfRanges >= 3,
                      let nameRange = Range(match.range(at: 1), in: text),
                      let bodyRange = Range(match.range(at: 2), in: text) else { continue }
                let name = String(text[nameRange])
                let body = String(text[bodyRange])
                found.append((range, ToolCall(name: name, arguments: parseParameters(body))))
            }
        }

        // Generic XML-ish tool tags emitted by some models:
        // <tool>{"name":"read_file",…}</tool>
        // <tool_call name="read_file">{…}</tool_call>
        let tagPattern = #"<(tool|tool_call|tool_calls|function_call)(?:\s+name="([^"]+)")?[^>]*>\s*(\{.*?\})\s*</\1>"#
        if let tagRegex = try? NSRegularExpression(pattern: tagPattern, options: [.dotMatchesLineSeparators]) {
            for match in tagRegex.matches(in: text, range: NSRange(location: 0, length: (text as NSString).length)) {
                guard let range = Range(match.range, in: text),
                      match.numberOfRanges >= 4,
                      let bodyRange = Range(match.range(at: 3), in: text) else { continue }
                let attributeName = match.range(at: 2).location == NSNotFound
                    ? nil
                    : Range(match.range(at: 2), in: text).map { String(text[$0]) }
                let body = String(text[bodyRange])
                var call: ToolCall?
                if let data = body.data(using: .utf8),
                   let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    let rawArguments: [String: Any]
                    if let nested = object["arguments"] as? [String: Any] {
                        rawArguments = nested
                    } else {
                        rawArguments = object.filter { $0.key != "name" && $0.key != "tool" }
                    }
                    let explicit = attributeName
                        ?? (object["name"] as? String)
                        ?? (object["tool"] as? String)
                    if let name = explicit.flatMap({ $0.isEmpty ? nil : $0 }) ?? inferName(from: rawArguments) {
                        call = ToolCall(name: name, arguments: rawArguments.mapValues(ToolValue.init(any:)))
                    }
                }
                found.append((range, call))
            }
        }

        var cleaned = text
        for range in found.map(\.range).sorted(by: { $0.lowerBound > $1.lowerBound }) {
            cleaned.removeSubrange(range)
        }

        // Remove the now-empty wrapper tags and any orphaned DSML lines.
        cleaned = cleaned.replacingOccurrences(
            of: #"</?[^>]{0,40}DSML[^>]{0,40}(?:calls|tool_calls)>"#,
            with: "",
            options: .regularExpression
        )
        cleaned = cleaned
            .components(separatedBy: "\n")
            .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("<") || !$0.contains("DSML") }
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        let calls = found
            .sorted { $0.range.lowerBound < $1.range.lowerBound }
            .compactMap(\.call)
        return Parsed(calls: calls, cleaned: cleaned)
    }

    /// Backwards-compatible single-call helper for callers that only need the
    /// first result.
    public static func extract(from text: String) -> (call: ToolCall, cleaned: String)? {
        let parsed = extractAll(from: text)
        guard let call = parsed.calls.first else { return nil }
        return (call, parsed.cleaned)
    }

    private static func parseParameters(_ body: String) -> [String: ToolValue] {
        var arguments: [String: ToolValue] = [:]
        let pattern = #"<[^>]{0,40}DSML[^>]{0,40}parameter\s+name="([^"]+)"([^>]*)>(.*?)</[^>]{0,40}DSML[^>]{0,40}parameter>"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) else {
            return arguments
        }
        let ns = body as NSString
        for match in regex.matches(in: body, range: NSRange(location: 0, length: ns.length)) {
            guard match.numberOfRanges >= 4,
                  let nameRange = Range(match.range(at: 1), in: body),
                  let attributesRange = Range(match.range(at: 2), in: body),
                  let valueRange = Range(match.range(at: 3), in: body) else { continue }
            let name = String(body[nameRange])
            let attributes = String(body[attributesRange])
            let raw = decodeEntities(String(body[valueRange]).trimmingCharacters(in: .whitespacesAndNewlines))
            if attributes.contains("string=\"false\"") {
                if let data = raw.data(using: .utf8),
                   let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) {
                    arguments[name] = ToolValue(any: value)
                } else {
                    arguments[name] = .string(raw)
                }
            } else {
                arguments[name] = .string(raw)
            }
        }
        return arguments
    }

    private static func decodeEntities(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&apos;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
    }

    private static func parse(_ body: String, allowInference: Bool = true) -> ToolCall? {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        let rawArguments: [String: Any]
        if let nested = object["arguments"] as? [String: Any] {
            rawArguments = nested
        } else {
            rawArguments = object.filter { key, _ in
                key != "name" && key != "tool"
            }
        }
        let explicit = (object["name"] as? String) ?? (object["tool"] as? String)
        let resolved = explicit.flatMap { $0.isEmpty ? nil : $0 }
            ?? (allowInference ? inferName(from: rawArguments) : nil)
        guard let name = resolved else {
            return nil
        }
        let arguments = rawArguments.mapValues(ToolValue.init(any:))
        return ToolCall(name: name, arguments: arguments)
    }

    /// Recover the tool name from its schema when a model omits `name`.
    /// Signatures are deliberately narrow and only cover unambiguous shapes.
    static func inferName(from arguments: [String: Any]) -> String? {
        func has(_ key: String) -> Bool { arguments[key] != nil }
        if has("patch") { return "apply_patch" }
        if has("steps") { return "update_plan" }
        if has("task") { return "subagent" }
        if has("content"), has("path") {
            return has("format") ? "create_document" : "write_file"
        }
        if has("path"), has("start_line") || has("end_line") {
            return "read_file"
        }
        let action = (arguments["action"] as? String)?.lowercased()
        if has("path"), action == "list" { return "list_files" }
        if has("path"), action == nil || action == "read" || action == "view" {
            return "read_file"
        }
        if has("command") { return "shell" }
        if has("query") {
            if has("glob") { return "search_files" }
            if has("path") || has("limit") { return "search_files" }
            return "search_files"
        }
        if has("url") { return "web_fetch" }
        return nil
    }
}

