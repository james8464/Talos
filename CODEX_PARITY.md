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
| OpenCode Go | OAuth access imported from the local OpenCode CLI; OpenAI-compatible model discovery and chat |
| Reasoning effort | Only options reported or supported by the selected model |
| Response speed | Fast / Balanced / Thorough output budgets |
| Chat | Streaming, stop, per-message model labels, copy, retry, edit-and-resend |
| Message queue | Send while streaming; queue strip, remove, clear, auto-consume |
| Attachments | File picker, file/image chips, inline image previews |
| Projects | Project switcher, recent projects, project-scoped tasks |
| File browser | Lazy project tree, text/image preview, open/reveal, new file |
| Git review | Branch/status, changed files, colored diff, stage all, commit |
| Documents | `create_document` for md, txt, html, csv, json, rtf, docx |
| Shell | Sandboxed zsh commands under workspace root |
| Files | Read with line ranges, write, list, search |
| Patches | Unified diff through `git apply` with `patch` fallback |
| Computer use | Screenshot, frontmost app, windows, click, type, keys, scroll, activate app, open URL |
| Browser | Safari/Chrome accessibility page reading, indexed click/type, navigation, back/forward/reload, screenshot OCR |
| Web | Fetch and search, gated by network setting |
| Plan | `update_plan` with inspector progress |
| Image viewing | `view_image` and automatic tool-result previews |
| Approvals | On-request or never, Allow Once, Always Allow for This Task |
| Sandbox | Read-only, workspace-write, full access via `sandbox-exec` |
| Network | Denied by default in sandbox; explicit toggle |
| Context | Capability/model/override window, live usage meter |
| Compaction | Automatic threshold + `/compact`, same-model summarization |
| Usage | Reported provider tokens, estimate fallback, 12-week heatmap, per-model totals |
| Slash commands | `/new`, `/clear`, `/compact`, `/stop`, `/retry`, `/rewind`, `/fork`, `/archive`, `/rename`, `/model`, `/queue`, `/context`, `/status`, `/usage`, `/cost`, `/diff`, `/review`, `/security-review`, `/export`, `/doctor`, `/init`, `/memory`, `/resume`, `/permissions`, `/config`, `/providers`, `/models`, `/skills`, `/plugins`, `/mcp`, `/remote`, `/browser`, `/todos`, `/help` |
| Skills | `SKILL.md` discovery, enable/disable, prompt exposure, `read_skill` |
| Plugins | Local `plugin.json` bundles; skills are verified, declared stdio MCP servers are supported experimentally |
| MCP | Real stdio and Streamable HTTP MCP client: initialize, tools/list, tools/call, enable/disable |
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
| MCP | stdio and Streamable HTTP; no MCP elicitation or MCP apps yet |
| Plugins | local skill bundles and declared stdio MCP servers; plugin MCP is experimental, and there is no remote marketplace or installation UI |
| Computer use | native CGEvent/AppKit actions; browser control uses accessibility and OCR rather than a browser extension/CDP |
| Images | displayed in-app; not yet sent as multimodal model input |
| Reasoning | provider-specific options only where the transport exposes them |
| Usage | provider-reported where available, otherwise clearly marked estimated |
| Sandbox | shell/file enforcement via `sandbox-exec`; no managed network proxy/domain allowlist |
| Remote | local-network page only; no cloud relay or native mobile app |
| Sessions | persist, archive, fork, rename, retry; no resume picker yet |

## Not yet

These are Codex features Talos does not claim to have:

- Codex Cloud tasks and cloud apply
- Subagents, Ultra/Max fan-out, multi-agent orchestration
- Git worktrees and managed branches (diff review and commit are implemented)
- Hooks and hook trust
- Config profiles and managed/enterprise policy
- Memories, goals, Chronicle, ambient suggestions
- App-server, remote-control daemon, mobile native client
- Browser extension/Chrome control and full CDP access
- MCP elicitation, MCP apps, and MCP OAuth
- Plugin marketplaces and remote plugin catalogs
- Auto-review subagent for approvals
- Dictation/voice and menu-bar mode
- Exact tokenizer-based accounting; Talos uses reported counts plus estimates
- Image/PDF input to multimodal models
- Automatic retry/edit/fork of individual messages

The remaining list is intentionally explicit. Talos labels unimplemented tools
as unavailable instead of showing non-working controls.
