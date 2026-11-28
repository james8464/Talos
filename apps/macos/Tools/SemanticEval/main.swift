import Foundation

// Semantic search regression set for the Talos workspace.
// Usage:
//   swiftc -O apps/macos/Talos/SemanticIndex.swift \
//     apps/macos/Tools/SemanticEval/main.swift -o /tmp/talos-semantic-eval
//   /tmp/talos-semantic-eval .

struct Query {
    let text: String
    let expected: Set<String>
}

let queries: [Query] = [
    Query(text: "where are tool approvals resolved", expected: ["apps/macos/Talos/AppServer.swift", "apps/macos/Talos/AppModel.swift", "apps/macos/CLI/TalosCLI.swift"]),
    Query(text: "persist sessions to disk", expected: ["apps/macos/Talos/SessionPersistence.swift"]),
    Query(text: "MCP OAuth discovery and PKCE", expected: ["apps/macos/Talos/MCP.swift"]),
    Query(text: "discover local Ollama models", expected: ["apps/macos/Talos/OllamaClient.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "compact long conversations automatically", expected: ["apps/macos/Talos/AppModel.swift", "apps/macos/Talos/SettingsView.swift"]),
    Query(text: "store provider API keys in the keychain", expected: ["apps/macos/Talos/Keychain.swift", "apps/macos/Talos/SettingsStore.swift"]),
    Query(text: "run hooks before a tool executes", expected: ["apps/macos/Talos/Hooks.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "native messaging host for the browser extension", expected: ["apps/macos/NativeHost/TalosNativeHost.swift", "apps/macos/Talos/AppModel.swift", "apps/macos/scripts/install-native-host.sh"]),
    Query(text: "create a GitHub pull request", expected: ["apps/macos/Talos/GitHubService.swift", "apps/macos/Talos/GitHubPanel.swift"]),
    Query(text: "language server diagnostics and code actions", expected: ["apps/macos/Talos/LSPService.swift", "apps/macos/Talos/DiagnosticsPanel.swift", "apps/macos/Talos/Tools.swift"]),
    Query(text: "cache semantic embeddings on disk", expected: ["apps/macos/Talos/SemanticIndex.swift"]),
    Query(text: "usage chart of daily tokens", expected: ["apps/macos/Talos/SettingsView.swift", "apps/macos/Talos/UsageStore.swift"]),
    Query(text: "merge a worktree branch", expected: ["apps/macos/Talos/Git.swift", "apps/macos/Talos/AppModel.swift", "apps/macos/Talos/WorktreePanel.swift"]),
    Query(text: "menu bar status panel", expected: ["apps/macos/Talos/MenuBarController.swift"]),
    Query(text: "dictation using speech recognition", expected: ["apps/macos/Talos/DictationController.swift"]),
    Query(text: "remote daemon HTTP API", expected: ["apps/macos/RemoteDaemon/RemoteDaemon.swift"]),
    Query(text: "verify plugin signatures", expected: ["apps/macos/Talos/Plugins.swift"]),
    Query(text: "queue cloud tasks in a worktree", expected: ["apps/macos/Talos/CloudTasks.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "fork or edit a message branch", expected: ["apps/macos/Talos/ConversationBranching.swift", "apps/macos/Talos/AppModel.swift"]),
    Query(text: "JSON-RPC dispatch for the app server", expected: ["apps/macos/Talos/AppServer.swift"]),
]

let workspacePath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
let workspace = URL(fileURLWithPath: workspacePath, isDirectory: true).standardizedFileURL
var top1 = 0
var top3 = 0
var reciprocalRank = 0.0

for (index, query) in queries.enumerated() {
    do {
        let result = try await SemanticIndex.shared.search(
            query: query.text,
            workspace: workspace,
            limit: 5
        )
        let ranked = result.hits
        let firstMatch = ranked.firstIndex { query.expected.contains($0.path) }
        let firstPath = ranked.first?.path ?? "none"
        let passed = query.expected.contains(firstPath)
        if passed { top1 += 1 }
        if let firstMatch, firstMatch < 3 { top3 += 1 }
        if let firstMatch { reciprocalRank += 1.0 / Double(firstMatch + 1) }
        print("Q\(index + 1) \(passed ? "PASS" : "FAIL") top1=\(firstPath)")
        if !passed {
            print("   top5=\(ranked.map(\.path).joined(separator: " | "))")
        }
    } catch {
        print("Q\(index + 1) ERROR \(error.localizedDescription)")
    }
}

let mrr = reciprocalRank / Double(queries.count)
print("TOP1: \(top1)/\(queries.count)")
print("TOP3: \(top3)/\(queries.count)")
print("MRR: \(String(format: "%.3f", mrr))")
exit(top1 == queries.count ? 0 : 1)
