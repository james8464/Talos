import Foundation

// Held-out semantic regression set.
//
// These queries were written after the original 20-query set and were not used
// to choose ranking weights. Each query names the file that most directly
// implements the behaviour (`primary`) and every file whose code implements a
// piece of it (`expected`). Multi-file features can legitimately surface a
// caller first; `primary` measures how often the most specific answer wins.
//
// Usage:
//   swiftc -O apps/macos/Talos/SemanticIndex.swift \
//     apps/macos/Tools/SemanticEval/HeldOut/main.swift -o /tmp/talos-semantic-heldout
//   /tmp/talos-semantic-heldout .

struct Query {
    let text: String
    let primary: String
    let expected: Set<String>
}

let queries: [Query] = [
    Query(text: "where is shell command execution restricted by a sandbox",
          primary: "apps/macos/Talos/Tools.swift",
          expected: ["apps/macos/Talos/Tools.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "extract selectable text from PDF pages",
          primary: "apps/macos/Talos/PDFLoader.swift",
          expected: ["apps/macos/Talos/PDFLoader.swift"]),
    Query(text: "load the BPE vocabulary used for token counting",
          primary: "apps/macos/Talos/Tokenizer.swift",
          expected: ["apps/macos/Talos/Tokenizer.swift"]),
    Query(text: "browser debugging over a local socket",
          primary: "apps/macos/Talos/CDPClient.swift",
          expected: ["apps/macos/Talos/CDPClient.swift"]),
    Query(text: "install a plugin bundle from a signed catalog",
          primary: "apps/macos/Talos/Plugins.swift",
          expected: ["apps/macos/Talos/Plugins.swift"]),
    Query(text: "prompt used by the read-only approval reviewer",
          primary: "apps/macos/Talos/Reviewer.swift",
          expected: ["apps/macos/Talos/Reviewer.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "remove a worktree after its branch is merged",
          primary: "apps/macos/Talos/WorktreePanel.swift",
          expected: ["apps/macos/Talos/WorktreePanel.swift", "apps/macos/Talos/Git.swift"]),
    Query(text: "QR code for phone control",
          primary: "apps/macos/Talos/SettingsView.swift",
          expected: ["apps/macos/Talos/SettingsView.swift", "apps/macos/Talos/RemoteServer.swift"]),
    Query(text: "confirm that a hook can run commands",
          primary: "apps/macos/Talos/Hooks.swift",
          expected: ["apps/macos/Talos/Hooks.swift", "apps/macos/Talos/AutomationSettings.swift"]),
    Query(text: "how does a cloud worker claim a queued job",
          primary: "apps/macos/Talos/CloudTasks.swift",
          expected: ["apps/macos/Talos/CloudTasks.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "message prefix used when editing or retrying",
          primary: "apps/macos/Talos/ConversationBranching.swift",
          expected: ["apps/macos/Talos/ConversationBranching.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "server-sent events for app-server clients",
          primary: "apps/macos/Talos/AppServer.swift",
          expected: ["apps/macos/Talos/AppServer.swift"]),
    Query(text: "elicitation form fields and defaults from an MCP server",
          primary: "apps/macos/Talos/MCPElicitationView.swift",
          expected: ["apps/macos/Talos/MCPElicitationView.swift", "apps/macos/Talos/MCP.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "verify an Ed25519 managed policy envelope",
          primary: "apps/macos/Talos/ManagedPolicy.swift",
          expected: ["apps/macos/Talos/ManagedPolicy.swift"]),
    Query(text: "status item popover in the menu bar",
          primary: "apps/macos/Talos/MenuBarController.swift",
          expected: ["apps/macos/Talos/MenuBarController.swift"]),
]

let workspace = URL(
    fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ".",
    isDirectory: true
).standardizedFileURL

var top1 = 0
var primaryHits = 0
var top3 = 0
var reciprocalRank = 0.0

for (index, query) in queries.enumerated() {
    do {
        let result = try await SemanticIndex.shared.search(query: query.text, workspace: workspace, limit: 5)
        let hits = result.hits
        let firstMatch = hits.firstIndex { query.expected.contains($0.path) }
        let top = hits.first?.path ?? "none"
        let acceptable = query.expected.contains(top)
        if acceptable { top1 += 1 }
        if top == query.primary { primaryHits += 1 }
        if let firstMatch, firstMatch < 3 { top3 += 1 }
        if let firstMatch { reciprocalRank += 1.0 / Double(firstMatch + 1) }
        let marker = acceptable ? (top == query.primary ? "PASS" : "ALT") : "FAIL"
        print("Q\(index + 1) \(marker) top1=\(top)")
        if !acceptable {
            print("   top5=\(hits.map(\.path).joined(separator: " | "))")
        }
    } catch {
        print("Q\(index + 1) ERROR \(error.localizedDescription)")
    }
}

let total = Double(queries.count)
let mrr = reciprocalRank / total
print("TOP1: \(top1)/\(queries.count)")
print("PRIMARY: \(primaryHits)/\(queries.count)")
print("TOP3: \(top3)/\(queries.count)")
print("MRR: \(String(format: "%.3f", mrr))")

// Acceptable answers must all appear first; the most specific file must win at
// least 80% of the time. The second threshold catches ranking drift without
// pretending multi-file features have a single correct entry point.
let minimumPrimary = Int(ceil(0.8 * total))
exit(top1 == queries.count && primaryHits >= minimumPrimary ? 0 : 1)
