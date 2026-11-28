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
- `Talos/Skills.swift` — SKILL.md discovery
- `Talos/Plugins.swift` — plugin bundles containing skills and declared MCP servers
- `Talos/MCP.swift` — stdio and Streamable HTTP MCP client and tool bridge
- `Talos/Browser.swift` — Safari/Chrome accessibility control and screenshot OCR
- `Talos/Subagent.swift` — isolated read-only child task loop and report
- `Talos/MCP.swift` — stdio/HTTP MCP, OAuth, and elicitation
- `Talos/MCPElicitationView.swift` — native form for server-requested input
- `Talos/MCPSamplingView.swift` — approval sheet for server-initiated sampling
- `Talos/MCPAppView.swift` — sandboxed WebKit host and JS tool bridge for MCP apps
- `Talos/Hooks.swift` — typed event hooks, trust, and execution log
- `Talos/Profiles.swift` — named provider/model/safety profiles
- `Talos/Reviewer.swift` — read-only auto-review for approval prompts
- `Talos/ManagedPolicy.swift` — signed/local managed policy loader and signature checks
- `Talos/PolicySettings.swift` — read-only policy status pane
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

## Phone control

Settings → Remote starts a local HTTP page. It is protected by a random token
and can send, stop, and compact the current task. No cloud relay is involved.

## Parity

See the repository [CODEX_PARITY.md](../../CODEX_PARITY.md).
