# Cross-tool parity matrix

Approximate audit of Talos against documented Codex, Claude Code, and Cursor
surfaces as of November 2026. Peer cells describe public/documented capability,
not exhaustive testing of those products.

Legend: ✅ supported · 🟡 partial, preview, or workflow-dependent · ❌ not offered/not implemented.

| Surface | Codex | Claude Code | Cursor | Talos | Talos evidence / remaining gap |
| --- | --- | --- | --- | --- | --- |
| **Core agent** | | | | | |
| Local models | 🟡 | ❌ | 🟡 | ✅ | Ollama discovery, capabilities, vision, provider token counts |
| OpenAI / Anthropic / Gemini | ✅ | ✅ | ✅ | ✅ | Native clients, streaming usage, images, PDF |
| Subscription routing | ✅ | ✅ | ✅ | ✅ | OpenCode Go chat/Responses/Messages transports |
| Streaming output and cancel | ✅ | ✅ | ✅ | ✅ | SSE/NDJSON clients, Stop, app-server cancel |
| Reasoning controls | ✅ | 🟡 | ✅ | ✅ | Capability-driven options; no fake levels |
| Exact token accounting | ✅ | ✅ | ✅ | ✅ | cl100k/o200k BPE plus Anthropic/Gemini/Ollama counters |
| Context meter and compaction | ✅ | ✅ | ✅ | ✅ | Live meter, auto/manual compaction |
| Session persistence and resume | ✅ | ✅ | ✅ | ✅ | JSON sessions, archive, fork, rename |
| Retry/edit/fork individual messages | 🟡 | 🟡 | ✅ | ✅ | Persisted lineage, selected-message actions |
| Image and PDF input | ✅ | ✅ | ✅ | ✅ | Native images and rendered PDF pages |
| Uncatalogued vision models | 🟡 | 🟡 | 🟡 | ✅ | Per-model vision override |
| **Editing and repo workflow** | | | | | |
| Shell and PTY-like commands | ✅ | ✅ | ✅ | 🟡 | Sandboxed non-interactive shell; no persistent terminal |
| File read/write/search/patch | ✅ | ✅ | ✅ | ✅ | Native tools plus unified-diff apply |
| Approvals and sandbox | ✅ | ✅ | ✅ | ✅ | Approval policies, sandbox-exec, network gate |
| Git diff/stage/commit | ✅ | ✅ | ✅ | ✅ | Changes panel and Git service |
| Worktrees and branch lifecycle | ✅ | 🟡 | 🟡 | ✅ | Create, list, switch, merge, cleanup |
| PR creation and review comments | ✅ | 🟡 | ✅ | ✅ | GitHub service reads `origin`, uses the `gh` CLI token or `GITHUB_TOKEN`, and supports draft PR creation, open-PR lookup, inline review comments, and review submit from the Changes panel; verified against live GitHub and a mock API |
| LSP diagnostics and refactors | ❌ | ❌ | ✅ | ✅ | JSON-RPC client for `sourcekit-lsp`, `clangd`, and installed TypeScript/Python/Go/Rust/JSON servers: diagnostics, document symbols, code actions with workspace-edit application, and formatting; model tool plus a Diagnostics panel in Changes; verified live against sourcekit-lsp and clangd |
| Inline autocomplete/Tab | ❌ | ❌ | ✅ | ❌ | Not an editor |
| Semantic codebase index | 🟡 | ❌ | ✅ | ❌ | Uses file tree/ripgrep, no embeddings index |
| Debugger integration | ❌ | ❌ | ✅ | ❌ | No debug adapter |
| Multi-file apply preview | ✅ | ✅ | ✅ | 🟡 | Edits and diffs exist; no staged multi-file apply UI |
| IDE extensions | ✅ | ✅ | ✅ (IDE) | ❌ | App/server/daemon only |
| Headless chat CLI | ✅ | ✅ | 🟡 | 🟡 | App server, remote daemon, and native host; no `talos chat` CLI |
| **Tools and desktop** | | | | | |
| Web fetch/search | ✅ | ✅ | ✅ | ✅ | Network-gated tools |
| Browser automation | 🟡 | 🟡 | ✅ | ✅ | Safari accessibility, Chrome CDP, packaged extension |
| Full desktop control | ❌ | 🟡 | ❌ | ✅ | Screenshot, click, type, keys, windows |
| MCP tools | ✅ | ✅ | ✅ | ✅ | stdio and Streamable HTTP |
| MCP resources and templates | 🟡 | 🟡 | 🟡 | ✅ | Standalone browser/reader |
| MCP apps | 🟡 | 🟡 | 🟡 | ✅ | Sandboxed WKWebView plus tool bridge |
| MCP OAuth/elicitation/sampling | 🟡 | 🟡 | 🟡 | ✅ | Discovery, DCR, PKCE, forms, sampling approval |
| Skills | ✅ | ✅ | 🟡 | ✅ | SKILL.md discovery and read_skill |
| Hooks | 🟡 | ✅ | 🟡 | ✅ | Typed events, trust, JSON context, blocking |
| Plugins/marketplace | 🟡 | ✅ | 🟡 | ✅ | Local and signed remote catalogs, dependencies |
| Profiles/config | ✅ | ✅ | ✅ | ✅ | Named profiles and managed policy |
| Memory and goals | 🟡 | ✅ | ✅ | ✅ | Persistent injection plus model tools |
| Chronicle/ambient suggestions | 🟡 | 🟡 | ✅ | ✅ | Opt-in local history and dismissible rules |
| **Agents and remote execution** | | | | | |
| Subagents | ✅ | ✅ | ✅ | ✅ | Read-only isolated child tasks |
| Nested delegation / fan-out | ✅ | ✅ | ✅ | ✅ | Depth-2 nesting and bounded parallel fan-out |
| Automatic review before approval | ✅ | ✅ | 🟡 | ✅ | Read-only reviewer in approval sheet |
| Self-hosted cloud task queue | 🟡 | 🟡 | 🟡 | ✅ | HTTP queue, worker, worktree isolation |
| Vendor-hosted cloud agents | ✅ | 🟡 | ✅ | ❌ | No vendor cloud backend |
| Parallel cloud workers | ✅ | 🟡 | ✅ | ❌ | Local fan-out only; queue worker is serial |
| App server / SDK transport | ✅ | 🟡 | 🟡 | ✅ | JSON-RPC + SSE, sessions/messages/approvals |
| Remote-control daemon | ✅ | ✅ | 🟡 | ✅ | Token-authenticated daemon and mobile web UI |
| Native mobile client | ✅ | ✅ | 🟡 | ✅ | SwiftUI iOS client; no Android client |
| Menu-bar quick panel | ✅ | ❌ | ❌ | ✅ | Status, model, new task, stop, show/settings |
| Voice dictation | ✅ | ✅ | ✅ | ✅ | Speech + AVAudioEngine, verified live |
| **Enterprise, platform, operations** | | | | | |
| MDM/signed managed policy | ✅ | ✅ | ✅ | ✅ | Managed preferences, Ed25519 file, local file |
| Signed plugin catalogs | 🟡 | 🟡 | 🟡 | ✅ | Ed25519 + SHA-256 + dependencies |
| SSO/OIDC and SCIM | ✅ | ✅ | ✅ | ❌ | No identity-provider integration |
| Team admin, billing, quotas | ✅ | ✅ | ✅ | ❌ | Single-user local app |
| Cloud sync across devices | ✅ | ✅ | ✅ | ❌ | Local storage only |
| Central audit export | ✅ | ✅ | ✅ | 🟡 | Local history/hook logs; no export/retention policy |
| Auto-update and notarized distribution | ✅ | ✅ | ✅ | ❌ | Development build only |
| Windows and Linux | ✅ | ✅ | ✅ | ❌ | macOS-only app |
| Accessibility of the client UI | ✅ | ✅ | ✅ | 🟡 | Basic labels; no formal WCAG audit |

## Verified, targeted gaps

- **Native mobile UI actions** — `TalosMobileUITests.testSendStopAndApprove`
  passed on the iOS Simulator against a live daemon, covering send, stop, and
  allow-approval plus tool result.
- **GitHub pull request workflow** — unit-level checks against a mock API passed,
  and a live draft PR round-trip on `james8464/Talos` (create, lookup, inline
  comment, review submit) passed; the verification PR and branch were then
  deleted. The native panel rendered the live PR in the app.
- **Language servers** — `sourcekit-lsp` and `clangd` both returned diagnostics,
  symbols, quick fixes, and applied edits in tests; the tool path was exercised
  through a live model session and the native Diagnostics panel showed the same
  errors. TypeScript, Python, Go, Rust, and JSON servers are supported when
  their executables are installed but were not present on this machine.
- **Comprehensive parity** — this matrix is an audit, not proof of equivalence.
  The largest remaining deltas are IDE/LSP/editor integration, PR review
  workflows, vendor-hosted cloud agents, team/SSO/billing, cloud sync, and
  non-macOS distribution.
