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
| Browser CDP | `browser_cdp` talks to Chrome's DevTools Protocol for status, page read with selectors, navigation, JavaScript evaluation, click by selector/text, typing, and PNG screenshots; endpoint configurable, mutating actions approval-gated |
| Browser extension | Packaged Manifest V3 extension plus a native messaging host; saves page/selection context, sends browser prompts into the app server, polls replies, cancels runs, and opens Talos; installer pins the extension ID and registers the host. Verified loaded in Chrome for Testing with popup ping, native page save, and the “Save page context for Talos” context menu |
| Browser | Safari/Chrome accessibility page reading, indexed click/type, navigation, back/forward/reload, screenshot OCR; verified live against Safari and example.com |
| Web | Fetch and search, gated by network setting |
| Plan | `update_plan` with inspector progress |
| Image viewing | `view_image` and automatic tool-result previews |
| Approvals | On-request or never, Allow Once, Always Allow for This Task |
| Sandbox | Read-only, workspace-write, full access via `sandbox-exec` |
| Network | Denied by default in sandbox; explicit toggle |
| Context | Capability/model/override window, live usage meter, exact tokenizer counts for supported families |
| Compaction | Automatic threshold + `/compact`, same-model summarization |
| Usage | Reported provider tokens, exact cl100k/o200k BPE fallback for supported OpenAI/OpenCode GPT families, character estimate elsewhere, 12-week heatmap, per-model totals |
| Slash commands | `/new`, `/clear`, `/compact`, `/stop`, `/retry`, `/rewind`, `/fork`, `/archive`, `/rename`, `/model`, `/queue`, `/context`, `/status`, `/usage`, `/cost`, `/diff`, `/review`, `/security-review`, `/export`, `/doctor`, `/init`, `/memory`, `/resume`, `/permissions`, `/config`, `/providers`, `/models`, `/skills`, `/plugins`, `/profiles`, `/policy`, `/cloud`, `/app-server`, `/hooks`, `/mcp`, `/remote`, `/browser`, `/worktree`, `/worktrees`, `/merge`, `/subagent`, `/fanout`, `/todos`, `/help` |
| Subagents | `subagent` tool and `/subagent` run a read-only child task in a fresh context and return a report with tool trace; read-only enforcement blocks writes and shell |
| Nested subagents | Children can delegate again while under an explicit depth limit of 2 (parent → child → grandchild); deeper calls are blocked, every level keeps its own read-only context, and nested delegation is only advertised while allowed |
| Fan-out | `fanout` tool and `/fanout` run multiple isolated read-only tasks concurrently with a configurable cap, per-task timeouts and usage, independent failures, and one report per task |
| Auto-review | A read-only reviewer runs against the pending tool call, arguments, and working tree diff before an approval prompt; the sheet shows verdict, risk, reasons, and reviewer model |
| Hooks | Typed `session_start`, `session_end`, `user_prompt_submit`, `pre_tool_use`, and `post_tool_use` hooks with JSON/env payloads, tool matchers, block decisions, additional context, timeouts, activity log, and per-hook trust confirmation |
| Profiles | Named profiles switch provider, model, reasoning, sandbox, approval, network, speed, and tool toggles; persisted with the active profile shown in status |
| Managed policy | Loads MDM/managed preferences (AnyUser/CurrentHost), an Ed25519-signed envelope, or a local policy; managed preferences override the file field by field and accept wrapped or flat keys; locks provider, model, reasoning, sandbox, approval, network, tool toggles, hook list, and MCP server list; read-only Policy pane with source, signature status, and locked values |
| Skills | `SKILL.md` discovery, enable/disable, prompt exposure, `read_skill` |
| Plugins | Local `plugin.json` bundles; skills are verified, declared stdio MCP servers are supported experimentally |
| Plugin marketplace | Local JSON catalogs plus signed remote catalogs over HTTPS/localhost; Ed25519 envelope verification, SHA-256 zip downloads, plugin dependency resolution with cycle/missing checks, install/update/remove, numeric version ordering, a persisted installed registry, safe plugin IDs, and dependency-aware uninstall guards |
| MCP | Real stdio and Streamable HTTP MCP client: initialize, tools/list, tools/call, resources/list, resources/templates/list, resources/read, enable/disable, elicitation forms, OAuth discovery + dynamic registration + PKCE + refresh |
| MCP sampling | `sampling/createMessage` is bridged to the active model with an approval sheet showing the server, prompt, system prompt, token/temperature limits, and image count; per-server session approval is supported |
| MCP apps | Tool `_meta.ui.resourceUri` and `_meta.ui/resourceUri` are read, `resources/read` fetches text or base64 HTML, and a sandboxed WKWebView renders it with a scoped `window.talos.callTool` bridge to the owning server |
| MCP resources | Standalone `resources/list` and `resources/templates/list` discovery with a read-only browser in MCP settings; the reader shows text, images from base64 blobs, MIME type, byte count, copy, and per-resource reload |
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
| MCP | stdio and Streamable HTTP; elicitation, OAuth, sampling, standalone resource browsing, and tool-linked MCP apps are implemented; app launch remains tool-linked |
| Plugins | local skill bundles and declared stdio MCP servers; plugin MCP is experimental, and there is no remote marketplace or installation UI |
| OpenCode Go | all advertised models with a chat-completions, Responses, or Anthropic Messages endpoint are routed; other transports are filtered out |
| Computer use | native CGEvent/AppKit actions; browser control offers accessibility/OCR and optional Chrome CDP rather than a packaged browser extension |
| Images | attachments and tool screenshots are sent as native image blocks to models identified as vision-capable |
| PDF input | PDF attachments are text-extracted for every model and rendered page-by-page for vision models (8 pages, 16-image cap); image-only PDFs work through page rendering |
| Reasoning | provider-specific options only where the transport exposes them |
| Usage | provider-reported where available, otherwise clearly marked estimated |
| Sandbox | shell/file enforcement via `sandbox-exec`; no managed network proxy/domain allowlist |
| Remote | local-network page only; no cloud relay or native mobile app |
| Cloud tasks | Self-hosted JSON task queue with a bearer-token HTTP worker protocol: enqueue/list/get/cancel/delete, claim, event logs, completion and failure, persisted tasks, isolated git worktrees, and a local worker that runs tasks with shell and file tools |
| App server | Headless local JSON-RPC + SSE API with bearer auth: initialize, session create/list/get, prompt, cancel, pending approval, approval resolve, and live transcript/tool/approval/run events |
| Menu bar | NSStatusItem quick panel with live ready/working/error status, model menu, context and queue state, New Task, Stop while running, Open Talos, Settings, and Quit |
| Voice input | macOS Speech + AVAudioEngine live dictation with on-device preference, composer mic control, partial-transcript merging, permission status/request UI, and a 15s authorization timeout. Verified with a generated AIFF through file transcription and live microphone capture, including a recognized transcript in the composer |
| Memory and goals | Persistent local memories and active goals injected into every system prompt; enabled/disabled state, goal status, add/edit/delete UI, `/memory`, and model-facing `remember` and `update_goal` tools |
| Sessions | persist, archive, fork, rename, retry; no resume picker yet |

## Not yet

These are Codex features Talos does not claim to have:

- Chronicle and ambient suggestions
- Remote-control daemon and native mobile client
- Exact tokenization for Anthropic, Gemini, Ollama, and other non-BPE families
- Image input for uncatalogued vision models
- Automatic retry/edit/fork of individual messages

The remaining list is intentionally explicit. Talos labels unimplemented tools
as unavailable instead of showing non-working controls.
