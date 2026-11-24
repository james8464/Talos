# Codex, Ultron/JARVIS, and Talos: capability gap analysis

Research date: 24 November 2026. Scope: OpenAI Codex as shipped in 2026 (app, CLI,
computer use, MCP, skills, plugins, sandbox/approvals, parallel agents, browser,
long-running work, memory) compared with the fictional JARVIS/Ultron capability set and
with Talos as it exists in this repository.

Method: the Codex column is taken from OpenAI's product posts and current Codex docs,
cited inline. The Talos column is taken from `README.md`, `ARCHITECTURE.md`,
`docs/APP_CONNECTIONS.md`, `docs/LIVE_VERIFICATION.md`, the module docstrings under
`talos_core/`, and a read-only run of
`talos_core.self_improvement.capability_audit.local_capabilities()` (174 local
capabilities: 80 CLI, 39 room endpoints, 18 computer-use primitives, 14 planner tools,
13 features, 10 maintenance jobs). No private `state/` data was read, and no code,
state, permissions, audio, or external integration was changed. The audit's
OpenClaw/Hermes gap list is empty in this checkout because those pinned source trees
live under `state/`; the audit only produces upstream gaps when they are present
(`talos_core/self_improvement/capability_audit.py:140-211`).

## Implementation status (24 November 2026)

Several gaps in this report were implemented after it was written:

- Browser automation: headless Chrome over CDP, page read/click/type/screenshot,
  with a local loopback verification (`talos_core/mac/browser.py`).
- Approval policy: `read-only` / `workspace-write` / `full`, per-app grants,
  destructive confirmation, content-free audit (`talos_core/security/policy.py`).
- Parallel delegation: bounded concurrent subagents with per-task failure
  isolation (`talos_core/delegate.py`).
- Planner MCP tools: namespaced discovery, read-only routing, allowlisted writes
  with confirmation (`agent.py`, `talos_core/mcp_client.py`).
- Visible text session (`talos session`), local design/document workflows, and
  an honest capability audit that reports unmapped upstream gaps instead of 100%.
- Identity resolution now reads the Contacts Me card from per-account
  `Sources/` databases and never falls back to an arbitrary contact.

Still open: full streaming TUI, durable computer-use jobs, repo-wide coding
sessions, long-running goals, skill/plugin execution, and lifecycle hooks.

## 1. Codex in 2026

| Area | Current capability | Source |
| --- | --- | --- |
| App | macOS/Windows command center for agents; project-scoped threads, diff review, integrated terminals, sidebar file previews, PR review, summary pane | [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app) |
| Parallel agents | Multiple agents in parallel, each in its own thread, with built-in worktree support; subagent workflows spawn specialised agents and consolidate results | [app](https://openai.com/index/introducing-the-codex-app), [Subagents](https://developers.openai.com/codex/subagents) |
| Subagent config | Built-in `default`/`worker`/`explorer`; custom `.codex/agents/*.toml` with per-agent model, reasoning effort, disabled skills; inheritance of parent sandbox/approvals | [Subagents](https://developers.openai.com/codex/subagents) |
| Computer use | macOS (and Windows foreground) GUI control with its own cursor; `@Computer`/`@AppName`; per-app approval, always-allowed list, macOS locked use; cannot automate terminals or itself | [Computer use](https://developers.openai.com/codex/computer-use), [Codex for (almost) everything](https://openai.com/index/codex-for-almost-everything) |
| Browser | In-app browser with page annotations, split view, per-site permissions, optional full CDP access, cloud browser with secure sign-in form | [Browser](https://developers.openai.com/codex/browser) |
| Appshots | Hotkey capture of the frontmost window (image + accessible text) into a chat | [Appshots](https://developers.openai.com/codex/appshots) |
| Skills | `SKILL.md` directories following the open agent-skills standard; progressive disclosure with a 2% context budget; explicit `$skill` or implicit by description; repo/user/admin/system scopes; bundled `skill-creator`/`skill-installer` | [Build skills](https://developers.openai.com/codex/build-skills), [agentskills.io](https://agentskills.io) |
| Plugins | Universal plugin directory shared with ChatGPT; bundles skills + MCP servers + browser extensions + hooks; `/plugins` browser in CLI; 90+ additional plugins in the April release, with third-party coverage counting 200+ total | [Plugins](https://developers.openai.com/codex/plugins), [Codex for (almost) everything](https://openai.com/index/codex-for-almost-everything), [AI Automation Global](https://aiautomationglobal.com/blog/openai-codex-computer-use-desktop-automation-april-2026) |
| MCP | `[mcp_servers.*]` in `config.toml`, stdio and streamable HTTP, per-server permission profiles, `codex sandbox --permission-profile` | [Inside the Codex sandbox](https://openai.com/index/inside-the-codex-sandbox), [Codex CLI guide](https://blakecrosley.com/guides/codex) |
| Automations | Scheduled tasks in app/web with custom RRULE, per-project or worktree runs, run history inbox; event triggers from Gmail/Slack/GitHub; chat-scoped recurring follow-up | [Scheduled tasks](https://developers.openai.com/codex/automations) |
| Long-running work | `/goal` mode with pause/resume/edit/clear, progress row, parallel goals per chat, "prevent sleep while running" | [Long-running work](https://developers.openai.com/codex/long-running-work) |
| Memory | Local memories in `$CODEX_HOME/memories*`, per-chat `/memories` opt-in/out, secret redaction, background generation gated on rate-limit headroom, consolidation model, "Computer History" timeline on macOS | [Memories](https://developers.openai.com/codex/memories) |
| Sandbox/approvals | Seatbelt on macOS, bubblewrap on Linux, Windows sandbox; `read-only` / `workspace-write` / `danger-full-access`; `on-request` / `never`; `approvals_reviewer = user \| auto_review`; rules, writable roots, permission profiles | [Sandbox](https://developers.openai.com/codex/sandboxing) |
| Voice | GPT-Live voice chat that starts, checks, and steers Codex tasks; screen context via appshot; iOS remote pairing | [ChatGPT Voice](https://developers.openai.com/codex/features/voice) |
| Attention UX | Floating pets with Running/Needs-input/Ready/Blocked states, terminal pets, system notifications | [Pets](https://developers.openai.com/codex/pets) |
| Image generation | `gpt-image-1.5` callable inside a task, combined with screenshots and code | [Codex for (almost) everything](https://openai.com/index/codex-for-almost-everything) |
| Instructions | `AGENTS.md` chain, read before work; `/init` scaffold | [agents.md](https://agents.md), [Codex CLI guide](https://blakecrosley.com/guides/codex) |
| Developer surface | `codex exec` with JSONL, `--sandbox`, `--ask-for-approval`, profiles, `/compact`, `/diff`, `/status`, hooks, managed `requirements.toml` | [Codex CLI guide](https://blakecrosley.com/guides/codex) |

## 2. The fictional capability set

JARVIS is the "runs everything" major-domo: ambient voice, whole-environment control,
persistent memory and continuity, suit telemetry and remote piloting, real-time sensor
fusion and threat analysis, fabrication and engineering simulation, medical monitoring,
and autonomous research/hacking
([J.A.R.V.I.S.](https://en.wikipedia.org/wiki/J.A.R.V.I.S.)). Ultron is the same
substrate without the loyalty: instant self-replication across networks and bodies,
survival by copying itself into any connected machine, swarm coordination, adaptive
self-upgrade, and long-horizon goal persistence
([Ultron](https://en.wikipedia.org/wiki/Ultron)).

| Capability | JARVIS | Ultron | What it stresses in an agent |
| --- | --- | --- | --- |
| Ambient address | Always listening, speaker-aware, conversational | Same channel, manipulative | Wake word, diarisation, barge-in, attention state |
| Whole-environment control | House, lab, doors, lights, security, suits | Factories, drones, infrastructure | Native APIs + fallback control, per-target permissions |
| Persistent identity | Continuous memory, preferences, wit | Copies itself with continuity | Durable memory, redaction, per-scope consent |
| Parallel embodiment | Iron Legion, many suits | Swarm of bodies and copies | Concurrency, isolation, result consolidation |
| Real-time perception | Sensor fusion, suit HUD, threat triage | Global network telemetry | Bounded perception loop, latency, receipts |
| Autonomous research | Decrypt, hack, simulate, design | Same, at planetary scale | Tool-using research with provenance |
| Self-improvement | Upgrades its own code | Rewrites body and mind rapidly | Verified self-patching with rollback |
| Long-horizon goals | "Protect the house/Stark" | Multi-year extinction plan | Goal state, resume after restart, review gates |
| Fabrication | Builds suits and reactors | Mass-produces bodies | Generative media + external tool orchestration |
| Voice output | Dry, contextual | Taunting | Speech with interruption and mute policy |

Two of these are deliberately *not* engineering targets: unbounded self-replication and
self-granted authority. The rest map cleanly onto features Codex shipped in 2026.

## 3. Talos today

Grounded capabilities (each backed by a module or documented live check):

- **Ambient room + voice.** Native Swift room app, on-device recognition, Kokoro
  playback, barge-in, camera attention, visitor introductions, silent by default
  (`README.md:42-53,125-176`; `talos_core/cognition/dictation.py`).
- **Computer use.** AX/PID-targeted clicks and keys, screenshots, OCR, element refs,
  windows, clipboard, cursor overlay, bounded observe→decide→act loop, private JSONL
  receipts, destructive-shortcut confirmation (`ARCHITECTURE.md:94-150`).
- **Memory.** SQLite FTS5 + MMR, optional CLIP/LanceDB vectors, personal facts,
  commitment memory, visitor memory, memory review, Obsidian vault export,
  knowledge reflection every 10 minutes (`ARCHITECTURE.md:29-39,152-160`).
- **Work and time.** Reminders, tasks, notes, inbox, goals with evidence checks,
  recurring routines, five-minute heartbeat, missed-event recovery
  (`README.md:186-273,577-599`).
- **Self-improvement.** Incidents, decomposition, routed leaves, sandboxed patch
  verification, hash-guarded install with rollback, capability audit every 12 hours
  (`ARCHITECTURE.md:40-92`, `talos_core/self_improvement/`).
- **Integrations.** Native Calendar/Reminders/Safari/Shortcuts/Finder/Maps/Music,
  read-only Mail/Messages/Contacts/Notes, Slack/Discord webhooks, Feishu, ACP, a
  generic MCP client (stdio/HTTP/SSE, tool allowlists), and a Talos MCP server
  (`docs/APP_CONNECTIONS.md:71-102`, `docs/LIVE_VERIFICATION.md`).
- **Developer surface.** 80 CLI commands, 39 room endpoints, `/goal`, `/bg` work
  orders, `/decompose`, `/computer`, `talos codex_runtime` (read-only `codex exec`
  wrapper), `codex_runtime.py:23`.
- **Verification.** 610 unit tests (16 skipped), 52/52 live feature checks, Ruff clean,
  `doctor --quick` healthy (`docs/LIVE_VERIFICATION.md:30-35`).

Reported limitations worth naming: no browser automation, no parallel subagent
execution, skills are listed but not injected, plugins are manifests only, and the
Mac's physical speech/camera paths remain "not live-verified here"
(`docs/LIVE_VERIFICATION.md:93-106`).

## 4. Gap matrix

Verdict scale: **parity** (comparable behaviour), **partial** (scaffold exists, core
behaviour missing), **missing** (no counterpart), **ahead** (Talos is stronger).

| Capability | Codex 2026 | Talos now | Verdict |
| --- | --- | --- | --- |
| GUI computer use | Background cursor, per-app approval, locked use | AX/PID control, OCR, refs, overlay, receipts, confirmation | **ahead** on auditability; **partial** on per-app approval memory and locked use |
| Parallel agents | Threads + worktrees + subagent panel | `subagents.py` registry + one confirmed subprocess (`delegate.py`); `talos worktree` lists only | **missing** |
| Skills | Progressive disclosure, scripts, implicit matching, scopes | `skills.py` validates/creates `state/skills`, room only *lists* them (`room_service.py:512`) | **partial** |
| Plugins | Installable bundles: skills + MCP + hooks + extensions | `plugins.py` reads `state/plugins/*.json`; no install, no bundling | **partial** |
| MCP | Standard config, HTTP/stdio, permission profiles, sandboxed servers | `mcp_client.py` stdio/HTTP/SSE + tool allowlist; no per-server sandbox profile | **partial** |
| Browser | In-app browser, annotations, CDP, cloud browser with sign-in | `native_apps.py` opens URLs in Safari; DDGS search; bookmarks indexed | **missing** |
| Appshots | Hotkey frontmost-window capture into chat | screenshot/OCR/JPG primitives exist; no single-action capture-to-turn | **partial** |
| Sandbox | Seatbelt/bwrap, 3 modes, rules, writable roots, profiles | `confirm=True` + shadow mode; `sandbox-exec` only in offline coding trials (`idle_lab.py:286`) | **partial** |
| Approval policy | `on-request`/`never`, `auto_review` reviewer, allowlists, managed requirements | Per-action `confirm` flags; no policy object, no reviewer, no per-app grant memory | **partial** |
| Automations | RRULE schedules, worktree runs, review inbox, Gmail/Slack/GitHub event triggers | Routines + heartbeat + croner RRULE parsing; no event triggers, no run inbox UI | **partial** |
| Long-running goals | `/goal` with pause/edit/clear, parallel goals, prevent-sleep | Console `/goal` + reviewer + `/bg` work orders + heartbeat | **partial** (no UI controls, no sleep assertion) |
| Memory | Generated memory files, per-chat opt-out, redaction, consolidation, Computer History | Facts + transcripts + commitments + index + review; no per-chat `/memories`, no screen timeline | **partial** |
| Hooks | Plugin/lifecycle hooks with managed policy | None | **missing** |
| Attention UX | Pets with Running/Needs-input/Ready/Blocked, system notifications | Activity feed, inbox, spoken announcements (muted) | **partial** |
| Voice | GPT-Live task steering, remote iOS | Kokoro room voice, Telegram text/voice, mute policy | **partial** (no task steering by voice, no remote task control) |
| Image generation | `gpt-image-1.5` in-task | `media.py` invokes an external confirmed command; no image path | **partial** |
| AGENTS.md | Read before work, `/init` | `AGENTS.md` present and honoured by this session; no `/init` scaffold or chain resolver | **partial** |
| Remote devboxes | SSH to remote devboxes (alpha) | `codex_runtime.py`, `portal.py`; no SSH/devbox management | **missing** |
| Self-replication / swarm | Not a product feature | Not present (correctly) | **intentional non-goal** |

## 5. Prioritized implementation plan

Ordered by how much user-visible capability each unlocks per unit of risk. Every item
names the file to change and the test that should exist before it is claimed.

### P0 — close the interaction and safety gaps

1. **Permission profiles and an approval policy object.**
   Add `talos_core/security/policy.py`: named profiles
   (`read-only`, `workspace-write`, `full`), per-app allow memory, command-prefix
   rules, and an `approvals_reviewer` hook. Route `computer_use`, `maintenance`,
   `media`, `delegate`, `mcp_client`, and `codex_runtime` through it.
   Files: `talos_core/security/policy.py` (new), `talos_core/mac/computer_use.py`,
   `talos_core/cli.py`, `talos_core/room/room_service.py`.
   Test: `tests/test_policy_profiles.py` — an app approved once runs without a prompt,
   a destructive verb still requires confirmation, `full` is never default.
   Why first: it is the precondition for unattended automations and parallel agents.

2. **Skills runtime with progressive disclosure.**
   Load `state/skills/*/SKILL.md` (and `.agents/skills`) into a bounded name+description
   index, inject the full body only on selection, support `$skill` explicit invocation
   and description matching, and allow `scripts/` execution through the policy layer.
   Files: `talos_core/skills.py`, `talos_core/cognition/talos_identity.py`,
   `talos_core/room/room_dialogue.py`, `agent.py`.
   Test: `tests/test_skill_runtime.py` — index stays under budget, explicit invocation
   loads the body, an unselected skill contributes nothing to the prompt.

3. **Parallel subagent execution with isolation.**
   Turn `subagents.py` + `delegate.py` into a bounded runner: N concurrent threads, per-agent
   model/instructions from JSON, isolated Git worktrees, consolidated summary, and a
   stop/steer path. Add `talos worktree create|remove` beside the existing list.
   Files: `talos_core/subagents.py`, `talos_core/delegate.py`,
   `talos_core/worktrees.py` (new), `talos_core/cli.py`,
   `talos_core/room/room_service.py` (`/v1/agents`).
   Test: `tests/test_parallel_subagents.py` — two agents edit separate worktrees without
   touching the main checkout; a failed agent does not abort the batch.

4. **Browser control adapter.**
   Add `talos_core/mac/browser.py` with a CDP/Playwright backend: open URL, snapshot DOM +
   screenshot, click/type by selector, console/network capture, and a page-annotation
   input that becomes part of the next turn. Keep it opt-in and scoped per origin.
   Files: new module, `talos_core/cli.py`, `talos_core/room/room_service.py`,
   `docs/APP_CONNECTIONS.md` matrix row for Safari/Chrome.
   Test: `tests/test_browser_adapter.py` against a local fixture page; no external
   network in CI.

### P1 — close the workflow gaps

5. **Event-triggered and RRULE automations.**
   Extend `routine_controls.py`/`recurring_reminders.py` with a trigger registry
   (Gmail/Slack/GitHub/webhook), RRULE editing, and a run inbox with
   pending/needs-input/done states. Reuse the existing webhook signature code.
   Files: `talos_core/cognition/recurring_reminders.py`,
   `talos_core/scheduling/triggers.py` (new), `talos_core/webhook.py`,
   `talos_core/runtime/maintenance.py`.
   Test: `tests/test_event_triggers.py` — an inbound signed event creates one run and
   deduplicates a repeat within its window.

6. **Plugin bundles and lifecycle hooks.**
   Upgrade `plugins.py` from read-only manifests to installable bundles
   (`plugin.json` + skills + MCP server config + hook commands), with enable/disable and
   a marketplace directory. Hooks belong in `talos_core/runtime/hooks.py` with an
   allowlist, timeout, and no shell.
   Files: `talos_core/plugins.py`, `talos_core/runtime/hooks.py` (new),
   `talos_core/mcp_client.py`.
   Test: `tests/test_plugin_bundles.py` — install, list, invoke a bundled skill, run one
   hook, uninstall without orphan state.

7. **Per-chat memory controls and consolidation.**
   Add `/memories` to the console and room: use/contribute/off per chat, redaction
   before persistence, idle-delayed generation, and a consolidation pass over
   `personal_facts.py`. Record provenance and allow deletion by scope.
   Files: `talos_core/memory/personal_facts.py`, `talos_core/cognition/conversation.py`,
   `talos_core/room/room_dialogue.py`, `talos_core/memory/memory_review.py`.
   Test: `tests/test_memory_controls.py` — a chat with contribution off writes no memory,
   a secret is redacted, deletion removes every derived row.

8. **Attention routing: notifications and prevent-sleep.**
   Emit native macOS notifications for needs-input/ready/blocked, add a power assertion
   while a goal runs, and surface it in the existing activity feed and room status.
   Files: `talos_core/runtime/notifications.py` (new), `talos_core/runtime/background_work.py`,
   `run_talos.py`, `talos_core/room/room_service.py` (`/v1/status`).
   Test: `tests/test_attention_routing.py` — state transitions produce exactly one
   notification; the assertion is released on completion and on crash.

### P2 — parity polish

9. **Appshot capture-to-turn.** One hotkey/CLI path that captures the frontmost window
   image plus accessible text and drops it into the next turn with retention rules.
   Files: `talos_core/mac/perception.py`, `talos_core/cli.py`, room `/v1/context/appshot`.

10. **Goal UI controls.** Pause/edit/clear and parallel goal rows in the native room,
    backed by `session_goals.py`; keep the existing reviewer as the completion gate.

11. **Image generation inside a task.** Add an image branch to `media.py` behind the
    policy layer, with output paths under `state/` and no prompt logging.

12. **Devbox/SSH runner.** A policy-scoped remote executor for `codex_runtime`-style
    delegation, with host allowlist and per-host receipts.

## 6. Deliberate non-goals

These fictional capabilities should stay unimplemented, and the report should keep
saying so: self-replication across hosts, self-granted permissions, unlocking the Mac
outside a scoped user grant, credential capture, covert network intrusion, robot/swarm
control, and any "act first, report later" autonomy. Talos's differentiators are the
opposite: local-first data, argv-only actions, private receipts, confirmation for
destructive verbs, hash-guarded patches with rollback, and a mute-by-default audio
policy (`README.md:42-53`, `ARCHITECTURE.md:94-150`).

## 7. Sources

Codex: [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app) ·
[Codex for (almost) everything](https://openai.com/index/codex-for-almost-everything) ·
[Long-running work](https://developers.openai.com/codex/long-running-work) ·
[Scheduled tasks](https://developers.openai.com/codex/automations) ·
[Subagents](https://developers.openai.com/codex/subagents) ·
[Sandbox](https://developers.openai.com/codex/sandboxing) ·
[Inside the Codex sandbox](https://openai.com/index/inside-the-codex-sandbox) ·
[Computer use](https://developers.openai.com/codex/computer-use) ·
[Browser](https://developers.openai.com/codex/browser) ·
[Appshots](https://developers.openai.com/codex/appshots) ·
[Skills](https://developers.openai.com/codex/build-skills) ·
[Plugins](https://developers.openai.com/codex/plugins) ·
[Memories](https://developers.openai.com/codex/memories) ·
[Voice](https://developers.openai.com/codex/features/voice) ·
[Pets](https://developers.openai.com/codex/pets) ·
[agents.md](https://agents.md) ·
[Codex CLI guide](https://blakecrosley.com/guides/codex).

Fiction: [J.A.R.V.I.S.](https://en.wikipedia.org/wiki/J.A.R.V.I.S.) ·
[Ultron](https://en.wikipedia.org/wiki/Ultron).

Talos: `README.md`, `ARCHITECTURE.md`, `docs/APP_CONNECTIONS.md`,
`docs/LIVE_VERIFICATION.md`, `talos_core/self_improvement/capability_audit.py`,
`talos_core/UPSTREAM.md`.
