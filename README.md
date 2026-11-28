# Talos

A native macOS agent client for the models you choose. Talos is a wrapper: it
does not add a persona or system prompt of its own. Answers are labeled with the
model that produced them.

## What is included

- Local models through Ollama, plus OpenCode Go, OpenAI, Anthropic, and Google Gemini
- OpenCode Go subscription import from the local OpenCode login, with live
  context, vision, and reasoning metadata; chat-completions, Responses, and
  Anthropic Messages models are all routed
- Model controls beside the composer: model, only the reasoning options the
  model actually exposes, response speed, provider state
- Project switcher, project-scoped tasks, a file browser with previews, a
  Git changes panel with colored diffs, staging, and commit, plus a worktree
  manager that creates, switches, merges, and cleans up task branches
- Tools that work with every model: shell, file read/write, patch, file search,
  document creation, web fetch/search, task plan, computer use, and browser
  control (page reading, indexed actions, navigation, and screenshot OCR)
- Delegated read-only subagents that work in a fresh context and return a
  report with the files and facts they found
- Parallel fan-out across isolated read-only tasks with a concurrency cap and
  per-task reports
- Automatic read-only review of high-impact tool calls, shown in the approval
  sheet with a verdict, risk, and concrete reasons
- Named config profiles that switch model, reasoning, sandbox, approvals, and tools
- Event hooks for prompts, tools, and sessions, with command output, blocking,
  added context, and explicit trust confirmation
- Approval prompts, per-task “always allow”, sandbox modes, network control
- Message queueing while a response is streaming, attachments, inline
  image/file previews, and native image input for vision models
- Context-window accounting, automatic and manual conversation compaction
- Token usage history with a 12-week heatmap and per-model totals
- Slash commands including `/new`, `/clear`, `/compact`, `/stop`, `/retry`,
  `/fork`, `/archive`, `/rename`, `/model`, `/queue`, `/context`, `/status`,
  `/usage`, `/cost`, `/diff`, `/review`, `/security-review`, `/rewind`,
  `/export`, `/doctor`, `/init`, `/memory`, `/resume`, `/permissions`,
  `/providers`, `/models`, `/skills`, `/plugins`, `/profiles`, `/hooks`,
  `/mcp`, `/remote`, `/browser`, `/worktree`, `/worktrees`, `/merge`, `/subagent`, `/fanout`,
  `/todos`, `/help`
- Skills, plugin bundles, stdio and HTTP MCP servers, including OAuth sign-in,
  interactive elicitation forms, and approved model sampling for servers
- Local phone control with a token-protected web page and QR code
- Session persistence with archive, fork, rename, retry, and edit-and-resend
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
