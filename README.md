# Talos

A native macOS agent client for the models you choose. Talos is a wrapper: it
does not add a persona or system prompt of its own. Answers are labeled with the
model that produced them.

## What is included

- See [`PARITY_MATRIX.md`](PARITY_MATRIX.md) for the cross-tool feature audit
  and [`HIG_AUDIT.md`](HIG_AUDIT.md) for the Apple Human Interface Guidelines audit
- Local models through Ollama, plus OpenCode Go, OpenAI, Anthropic, and Google Gemini
- OpenCode Go subscription import from the local OpenCode login, with live
  context, vision, and reasoning metadata; chat-completions, Responses, and
  Anthropic Messages models are all routed
- Model controls beside the composer: model, only the reasoning options the
  model actually exposes, response speed, provider state
- A task-first macOS interface: date-grouped tasks, a ⌘K quick switcher for
  tasks and commands, searchable Settings, a guided welcome screen, a live plan
  strip, an inspector with semantic-index status, a focused composer with image
  and PDF attachment thumbnails, live context and a working status bar, and
  tool results as compact cards
- Project switcher, project-scoped tasks, a file browser with previews, a
  Git changes panel with colored diffs, staging, and commit, plus a worktree
  manager that creates, switches, merges, and cleans up task branches
- Tools that work with every model: shell, a persistent PTY-backed terminal,
  file read/write, patch, file search,
  document creation, web fetch/search, task plan, computer use, and browser
  control (page reading, indexed actions, navigation, and screenshot OCR),
  plus Chrome DevTools Protocol control with DOM selectors and JavaScript
- On-device semantic code search (`search_codebase`) using Apple sentence
  embeddings plus lexical ranking, with a cached, incrementally rebuilt index
- A headless `talos` CLI that drives the app server: sessions, streamed
  prompts, stop, and approvals, with JSON output for scripts
- Language-server diagnostics, symbols, quick fixes and refactors, and
  formatting through `sourcekit-lsp`, `clangd`, and other installed servers,
  exposed to every model and in the Changes panel
- Delegated read-only subagents that work in a fresh context and return a
  report with the files and facts they found, including nested delegation to
  a bounded depth with isolated contexts
- Parallel fan-out across isolated read-only tasks with a concurrency cap and
  per-task reports
- Automatic read-only review of high-impact tool calls, shown in the approval
  sheet with a verdict, risk, and concrete reasons
- Persistent memories and goals that are injected into every task, with a
  local editor and model-facing `remember` and `update_goal` tools
- Opt-in Chronicle history and dismissible ambient suggestions based on local
  activity, with no server dependency
- Named config profiles that switch model, reasoning, sandbox, approvals, and tools
- Managed policies from MDM preferences, signed envelopes, or local files that
  lock model, safety, hooks, and MCP settings
- Event hooks for prompts, tools, and sessions, with command output, blocking,
  added context, and explicit trust confirmation
- Audit log export as JSON with sessions, token usage, policy, and hook
  activity, from Settings → Usage or the `talos audit` CLI command
- Undo and redo for task deletion through the standard window UndoManager
- Approval prompts with a parsed multi-file patch preview before edits are
  applied, per-task “always allow”, sandbox modes, network control
- Message queueing while a response is streaming, attachments, inline
  image/file previews, native image input for vision models, PDF input through
  extracted text plus rendered pages, and a per-model vision override for
  uncatalogued models
- Context-window accounting with exact cl100k/o200k tokenizers for supported
  OpenAI models plus Anthropic, Gemini, and Ollama provider counters,
  automatic and manual conversation compaction
- Token usage history with a 12-week heatmap and per-model totals
- Slash commands including `/new`, `/clear`, `/compact`, `/stop`, `/retry`,
  `/fork`, `/archive`, `/rename`, `/model`, `/queue`, `/context`, `/status`,
  `/usage`, `/cost`, `/diff`, `/review`, `/security-review`, `/rewind`,
  `/export`, `/doctor`, `/init`, `/memory`, `/resume`, `/permissions`,
  `/providers`, `/models`, `/skills`, `/plugins`, `/profiles`, `/hooks`,
  `/mcp`, `/remote`, `/browser`, `/worktree`, `/worktrees`, `/merge`, `/subagent`, `/fanout`,
  `/todos`, `/help`
- A plugin marketplace with local or signed remote catalogs, dependency
  resolution, verified downloads, and install/update/remove flows
- Skills, plugin bundles, stdio and HTTP MCP servers, including OAuth sign-in,
  interactive elicitation forms, approved model sampling, a read-only resource
  browser with templates, and sandboxed HTML MCP apps that can call their own
  server's tools
- Self-hosted cloud tasks with an HTTP queue and worker protocol, cancellable
  tasks, isolated worktrees, and an optional local worker
- A headless local JSON-RPC + SSE app server for sessions, prompts, approvals,
  cancellation, and live transcript events
- A packaged Chrome extension and native messaging host for sending pages and
  selections to Talos
- A menu-bar quick panel for status, model switching, new tasks, stop, and
  opening the main window
- Composer dictation through macOS Speech and AVAudioEngine, with on-device
  preference, permission handling, and verified live microphone transcription
- Local phone control with a token-protected web page and QR code
- A headless remote-control daemon with a mobile web client and a native
  SwiftUI iOS companion for sessions, prompts, stop, and approvals
- Session persistence with archive, fork, rename, retry, and edit-and-resend
- Persisted message lineage with retry, edit, and fork for any selected message
- Notifications, appearance controls, and a native sidebar Settings window

## Build

Requirements: macOS 14+, Xcode 16+, and [Ollama](https://ollama.com) for local
models.

```sh
make run
```

The Xcode project is checked in, so `open apps/macos/Talos.xcodeproj` also works.
After changing `apps/macos/project.yml`, run `make project`.

## Projects and tools

Open a folder from the folder menu in the toolbar. The project folder becomes
the workspace for shell commands, file edits, search, documents, and the file
browser. Tools are provided by Talos, not by the model.

Approvals and the sandbox are real: shell and file tools run under
`sandbox-exec` for read-only and workspace-write modes, and network access is
denied unless enabled. Computer use requires macOS Accessibility and Screen
Recording permissions.

## Skills, plugins, and MCP

- Skills live in `~/Library/Application Support/Talos/skills` and the current
  project's `.talos/skills` folder. Each skill is a folder with `SKILL.md`.
- Plugins live in `~/Library/Application Support/Talos/plugins`. A `plugin.json`
  can bundle skills and declare stdio MCP servers; plugin-bundled MCP is still
  experimental.
- MCP servers are configured in Settings → MCP. Stdio and Streamable HTTP
  endpoints are supported. Their tools are exposed to every model as
  `mcp__server__tool`.

## Phone control

Enable it in Settings → Remote and scan the QR code from a phone on the same
network. The page can send prompts, stop the current turn, compact history, and
show project/model/context/queue state. The token in the URL is required for
every API call.

## Privacy

Local requests go only to the Ollama server configured in Settings. Hosted
requests go only to the selected provider. Keys stay in the macOS Keychain. The
repository contains no credentials or prompt history.

See [CODEX_PARITY.md](CODEX_PARITY.md) for the honest feature-by-feature status.
