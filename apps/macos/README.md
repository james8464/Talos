# Talos for macOS

A native SwiftUI model client and local agent. The app owns the tool runtime;
tools work regardless of which model is selected.

## Requirements

- macOS 14 or later
- Xcode 16 or later
- [Ollama](https://ollama.com) for local models
- OpenCode Go, OpenAI, Anthropic, or Google Gemini credentials for hosted models
- OpenCode Go imports the local OpenCode OAuth access token via `opencode auth export`; other providers use API keys

## Build and run

```sh
make run
```

Or open `Talos.xcodeproj`. After changing `project.yml`, run `make project`.

## Layout

- `Talos/AppModel.swift` — sessions, turns, queue, approvals, compaction,
  projects, usage, MCP, skills, remote
- `Talos/Tools.swift` — sandboxed process execution, tools, prompt protocol
- `Talos/Projects.swift` — projects, file tree, document writer
- `Talos/Git.swift` and `GitPanel.swift` — status, diff, staging, commit
- `Talos/GitHubService.swift` and `GitHubPanel.swift` — pull request creation,
  open-PR lookup, inline review comments, and review submission
- `Talos/PatchPreview.swift` — parsed per-file diff preview for `apply_patch`
- `Talos/AuditExport.swift` — JSON audit document for sessions, usage, policy, and hooks
- `Talos/TerminalSession.swift` — persistent PTY-backed zsh session for the `terminal` tool
- `Talos/SemanticIndex.swift` — on-device embedding index for `search_codebase`
- `Talos/LSPService.swift` and `DiagnosticsPanel.swift` — language-server
  JSON-RPC client, diagnostics, symbols, code actions, and formatting
- `CLI/TalosCLI.swift` — headless `talos` client for the app server
- `Talos/Skills.swift` — SKILL.md discovery
- `Talos/Plugins.swift` — plugin bundles containing skills and declared MCP servers
- `Talos/MCP.swift` — stdio and Streamable HTTP MCP client and tool bridge
- `Talos/Browser.swift` — Safari/Chrome accessibility control and screenshot OCR
- `Talos/CDPClient.swift` — Chrome DevTools Protocol client for DOM control and screenshots
- `Talos/Subagent.swift` — isolated read-only child task loop and report
- `Talos/PDFLoader.swift` — PDF text extraction and page rendering for vision
- `Talos/Tokenizer.swift` — bundled cl100k/o200k BPE tokenizers and model mapping
- `Talos/ProviderTokenCounter.swift` — Anthropic, Gemini, and Ollama exact token counters
- `Talos/ConversationBranching.swift` — edit, retry, and fork plans for message lineage
- `Talos/MCP.swift` — stdio/HTTP MCP, OAuth, and elicitation
- `Talos/MCPElicitationView.swift` — native form for server-requested input
- `Talos/MCPSamplingView.swift` — approval sheet for server-initiated sampling
- `Talos/MCPAppView.swift` — sandboxed WebKit host and JS tool bridge for MCP apps
- `Talos/MCPResourceView.swift` — read-only text/image MCP resource reader
- `Talos/Hooks.swift` — typed event hooks, trust, and execution log
- `Talos/Profiles.swift` — named provider/model/safety profiles
- `Talos/Reviewer.swift` — read-only auto-review for approval prompts
- `Talos/ManagedPolicy.swift` — MDM/signed/local managed policy loader, merge, and signature checks
- `Talos/PolicySettings.swift` — read-only policy status pane
- `Talos/CloudTasks.swift` — self-hosted queue, HTTP worker protocol, and local worker
- `Talos/CloudTasksSettings.swift` — task server, worker, enqueue, and task list UI
- `Talos/AppServer.swift` — headless JSON-RPC and SSE app-server transport
- `Talos/AppServerSettings.swift` — app-server configuration and API reference
- `Talos/MenuBarController.swift` — NSStatusItem quick panel and window/settings actions
- `Talos/MemoryStore.swift` — persistent memories and goals
- `Talos/MemorySettings.swift` — memory and goal editor
- `Talos/Chronicle.swift` — local activity history and ambient suggestion rules
- `Talos/ChronicleUI.swift` — composer suggestion strip and Chronicle settings
- `Talos/DictationController.swift` — Speech/AVAudioEngine dictation and permissions
- `Extension/talos-browser` — Manifest V3 Chrome extension
- `NativeHost/TalosNativeHost.swift` — native messaging host for the extension
- `scripts/install-native-host.sh` — registers the host and pins the extension ID
- `Talos/Plugins.swift` — local/signed remote catalogs, dependencies, verified downloads, install/update/remove
- `Talos/FanOut.swift` — bounded-concurrency read-only task fan-out
- `Talos/WorktreePanel.swift` — worktree list, switch, merge, and cleanup UI
- `Talos/AutomationSettings.swift` — profiles and hooks settings UI
- `Talos/RemoteServer.swift` — local phone-control HTTP server
- `Talos/SettingsView.swift` — sidebar Settings window

## Tools

`shell`, `read_file`, `write_file`, `list_files`, `search_files`,
`apply_patch`, `create_document`, `view_image`, `computer`, `web_fetch`,
`web_search`, `browser`, `update_plan`, `read_skill`, and MCP tools. The model
calls them through a fenced `tool` block, so even models without native
function calling can use them.

Approval and sandbox behavior is configured in Settings → Tools. The shell tool
uses `/usr/bin/sandbox-exec`; read-only and workspace-write modes deny writes
outside the workspace and temporary folders. Network is denied unless enabled.

## Model capabilities

Reasoning controls come from the selected model:

- Ollama: `/api/show` capabilities. Thinking models get Off/On; others hide the
  control.
- OpenAI: reasoning models get Low/Medium/High.
- Anthropic: Claude thinking models get a token budget.
- Gemini: no reasoning control is shown until its compatible endpoint exposes
  one in this client.

No model gets a fake reasoning level.

## Images

Image attachments and tool screenshots are sent as native image blocks to
models whose capabilities include vision. Ollama discovery reads the `vision`
capability; OpenCode, OpenAI, Anthropic, and Gemini use known model families.
Text-only models receive the file paths and any OCR text instead.

## Context and usage

Context usage is estimated from the prompt and shown beside the composer. When
automatic compaction is enabled, Talos summarizes older messages before the
window fills. Providers that report token usage are recorded exactly; other
counts are marked estimated. Settings → Usage shows the totals and a 12-week
heatmap.

## Interface

The interface is audited against Apple's published Human Interface Guidelines;
see [`HIG_AUDIT.md`](../../HIG_AUDIT.md) for the page-by-page checklist and the
remaining partial items.

The main window uses a task-first layout: a date-grouped sidebar with a model
status footer, a ⌘K quick switcher for tasks and commands, searchable Settings,
a guided welcome screen, a collapsible plan strip, a composer with image and PDF
attachment thumbnails, a live context meter, and a streaming status bar with
stop and context. Tool results appear as compact
cards with expandable output, and the inspector shows semantic-index status
with a rebuild action.

## Semantic code search

`search_codebase` indexes the workspace with Apple's on-device sentence
embeddings and ranks chunks with a lexical boost, so conceptual queries work
without exact identifiers. The index is cached per workspace and rebuilt when
files change. Actions: a normal search, `status`, and `rebuild`.

## Persistent terminal

The `terminal` tool keeps a live zsh session through `/usr/bin/script` (a PTY),
so the working directory, environment, and shell state persist between tool
calls. Actions: `run` (with timeout and exit code), `send` (raw input for
REPLs), `read` (unread output), `interrupt` (Ctrl-C), and `close`. Like `shell`,
it needs approval and respects the shell-access setting.

## Accessibility audit

With the app running and Accessibility permission granted to your terminal:

```sh
make ax-audit
```

The script walks the live AX tree, skips the menu bar and system window or
scrollbar chrome, and fails if any app-owned interactive control has no title,
description, help, or placeholder. Run it on whichever panel you are auditing.

## Headless CLI

Build the CLI with `xcodebuild -scheme TalosCLI` (or `make cli`). The binary is
`.build/Build/Products/Debug/talos`.

```sh
export TALOS_APP_SERVER_URL=http://127.0.0.1:8790
export TALOS_APP_SERVER_TOKEN=…

talos info
talos sessions
talos chat --title "Fix the failing test"
talos send <session-id> "Summarize the diff" --yes
talos stop <session-id>
talos audit --output audit.json
talos approvals
talos approve <approval-id>
```

`send` and `chat` stream replies over SSE and prompt for tool approvals when
stdin is a terminal. Use `--yes` to auto-allow, `--json` for scripts, and
`--no-stream` to poll for the final reply. Exit codes: `0` success, `1` runtime
error, `2` usage or authentication error, `130` interrupted.

## Language servers

Talos starts the appropriate language server on demand and exposes it to models
as the `lsp` tool: `diagnostics`, `symbols`, `code_actions`,
`apply_code_action`, and `format`. Sources are supported for Swift
(`sourcekit-lsp`), C/C++/Objective-C (`clangd`), and TypeScript, Python, Go,
Rust, and JSON when the matching server is installed.

Changes → **Diagnostics** checks every changed file and lists errors and
warnings with file, line, and source. Applying code actions and formatting
require the usual approval.

## GitHub pull requests

Open Changes and choose **Pull Request…**. Talos reads the `origin` remote and
authenticates with `GITHUB_TOKEN`, `GH_TOKEN`, or the token from `gh auth token`.
You can push the current branch, create a draft or normal PR, browse the open PR
for that branch, add inline review comments, and submit a comment, approval, or
change request. Set `githubAPIBase` in Settings to point at GitHub Enterprise or
a test fixture.

## Phone control

Settings → Remote starts a local HTTP page. It is protected by a random token
and can send, stop, and compact the current task. No cloud relay is involved.

## Parity

See the repository [CODEX_PARITY.md](../../CODEX_PARITY.md).
