# Codex Parity

This is the feature-by-feature status against the Codex app and Codex CLI
surface. “Implemented” means the behavior exists in Talos and is not a
placeholder. “Partial” means a real implementation exists with a narrower
scope. “Not yet” is deliberately not exposed as a working control.

## Implemented

| Area | Talos |
| --- | --- |
| Model selection | Local Ollama models plus OpenCode Go, OpenAI, Anthropic, Gemini |
| Provider setup | Compact provider list, one editor sheet, editable base URL/model/discovery, Keychain keys, OpenCode subscription import |
| OpenCode Go | OAuth access imported from the local OpenCode CLI; live model metadata drives model list, context, vision, and reasoning effort for 30 models routed over chat-completions, Responses, and Anthropic Messages |
| Reasoning effort | Only options reported or supported by the selected model |
| Response speed | Fast / Balanced / Thorough output budgets |
| Chat | Streaming, stop, per-message model labels, copy, retry, edit-and-resend |
| Message queue | Send while streaming; queue strip, remove, clear, auto-consume |
| Attachments | File picker, file/image chips, inline image previews, native image blocks sent to vision models |
| Projects | Project switcher, recent projects, project-scoped tasks |
| File browser | Lazy project tree, text/image preview, open/reveal, new file |
| Git review | Branch/status, changed files, colored diff, stage all, commit |
| Git worktrees | Create, list, switch, merge, and clean up worktrees from a manager panel, `/worktree`, `/worktrees`, `/merge`, or Settings prefix; merges require a clean tree and roll back on conflict |
| Documents | `create_document` for md, txt, html, csv, json, rtf, docx |
| Shell | Sandboxed zsh commands under workspace root |
| Files | Read with line ranges, write, list, search |
| Patches | Unified diff through `git apply` with `patch` fallback |
| Computer use | Screenshot, frontmost app, windows, click, type, keys, scroll, activate app, open URL |
| Browser | Safari/Chrome accessibility page reading, indexed click/type, navigation, back/forward/reload, screenshot OCR; verified live against Safari and example.com |
| Web | Fetch and search, gated by network setting |
| Plan | `update_plan` with inspector progress |
| Image viewing | `view_image` and automatic tool-result previews |
| Approvals | On-request or never, Allow Once, Always Allow for This Task |
| Sandbox | Read-only, workspace-write, full access via `sandbox-exec` |
| Network | Denied by default in sandbox; explicit toggle |
| Context | Capability/model/override window, live usage meter |
| Compaction | Automatic threshold + `/compact`, same-model summarization |
| Usage | Reported provider tokens, estimate fallback, 12-week heatmap, per-model totals |
| Slash commands | `/new`, `/clear`, `/compact`, `/stop`, `/retry`, `/rewind`, `/fork`, `/archive`, `/rename`, `/model`, `/queue`, `/context`, `/status`, `/usage`, `/cost`, `/diff`, `/review`, `/security-review`, `/export`, `/doctor`, `/init`, `/memory`, `/resume`, `/permissions`, `/config`, `/providers`, `/models`, `/skills`, `/plugins`, `/profiles`, `/hooks`, `/mcp`, `/remote`, `/browser`, `/worktree`, `/worktrees`, `/merge`, `/subagent`, `/fanout`, `/todos`, `/help` |
| Subagents | `subagent` tool and `/subagent` run a read-only child task in a fresh context and return a report with tool trace; read-only enforcement blocks writes and shell |
| Fan-out | `fanout` tool and `/fanout` run multiple isolated read-only tasks concurrently with a configurable cap, per-task timeouts and usage, independent failures, and one report per task |
| Auto-review | A read-only reviewer runs against the pending tool call, arguments, and working tree diff before an approval prompt; the sheet shows verdict, risk, reasons, and reviewer model |
| Hooks | Typed `session_start`, `session_end`, `user_prompt_submit`, `pre_tool_use`, and `post_tool_use` hooks with JSON/env payloads, tool matchers, block decisions, additional context, timeouts, activity log, and per-hook trust confirmation |
| Profiles | Named profiles switch provider, model, reasoning, sandbox, approval, network, speed, and tool toggles; persisted with the active profile shown in status |
| Skills | `SKILL.md` discovery, enable/disable, prompt exposure, `read_skill` |
| Plugins | Local `plugin.json` bundles; skills are verified, declared stdio MCP servers are supported experimentally |
| MCP | Real stdio and Streamable HTTP MCP client: initialize, tools/list, tools/call, enable/disable, elicitation forms, OAuth discovery + dynamic registration + PKCE + refresh |
| MCP sampling | `sampling/createMessage` is bridged to the active model with an approval sheet showing the server, prompt, system prompt, token/temperature limits, and image count; per-server session approval is supported |
| MCP apps | Tool `_meta.ui.resourceUri` and `_meta.ui/resourceUri` are read, `resources/read` fetches text or base64 HTML, and a sandboxed WKWebView renders it with a scoped `window.talos.callTool` bridge to the owning server |
| Connectors | Any stdio MCP server, including connector-style servers |
| Phone control | Token-protected local page, QR, send/stop/compact/status |
| Sessions | Persisted history with backwards-compatible decoding, archive, fork, rename, retry |
| Appearance | System/light/dark, transcript font size, bronze brand tint |
| Notifications | Completion sound and optional macOS notification |
| Permissions | Accessibility and Screen Recording status plus request buttons |
| Branding | New graphite/bronze Talos icon, in-app mark, About page |
| Settings | Native sidebar Settings window with General, Projects, Models, Providers, Tools, Skills, Plugins, MCP, Remote, Appearance, Usage, Advanced, About |

## Partial

| Area | Current scope |
| --- | --- |
| MCP | stdio and Streamable HTTP; elicitation, OAuth, sampling, and tool-linked MCP apps are implemented; there is no standalone resource/app browser yet |
| Plugins | local skill bundles and declared stdio MCP servers; plugin MCP is experimental, and there is no remote marketplace or installation UI |
| OpenCode Go | all advertised models with a chat-completions, Responses, or Anthropic Messages endpoint are routed; other transports are filtered out |
| Computer use | native CGEvent/AppKit actions; browser control uses accessibility and OCR rather than a browser extension/CDP |
| Images | attachments and tool screenshots are sent as native image blocks to models identified as vision-capable; PDF input is not yet supported |
| Reasoning | provider-specific options only where the transport exposes them |
| Usage | provider-reported where available, otherwise clearly marked estimated |
| Sandbox | shell/file enforcement via `sandbox-exec`; no managed network proxy/domain allowlist |
| Remote | local-network page only; no cloud relay or native mobile app |
| Sessions | persist, archive, fork, rename, retry; no resume picker yet |

## Not yet

These are Codex features Talos does not claim to have:

- Codex Cloud tasks and cloud apply
- Nested subagents (parallel fan-out is implemented)
- Managed/enterprise policy distribution (personal profiles are implemented)
- Memories, goals, Chronicle, ambient suggestions
- App-server, remote-control daemon, mobile native client
- Browser extension/Chrome control and full CDP access
- Standalone MCP resource browsing outside tool-linked apps
- Plugin marketplaces and remote plugin catalogs
- Dictation/voice and menu-bar mode
- Exact tokenizer-based accounting; Talos uses reported counts plus estimates
- PDF input and image input for uncatalogued vision models
- Automatic retry/edit/fork of individual messages

The remaining list is intentionally explicit. Talos labels unimplemented tools
as unavailable instead of showing non-working controls.
