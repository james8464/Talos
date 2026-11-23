# Talos architecture

Talos is a local-first Mac assistant. It listens or chats, remembers evidence,
plans actions, controls the Mac when asked, and improves its own source while
the operator is away. The repository root keeps two launch scripts; all
implementation lives in `talos_core` by domain.

## Layout

```
talos_core/
  cli.py                 unified `talos` command
  cognition/             conversation, goals, reminders, identity, spoken stream
  interfaces/            Telegram gateway, Pipecat voice bot, terminal TUI
  mac/                   private Mac data reads + native computer use
  memory/                SQLite transcripts, knowledge index, vault, vector search
  room/                   loopback HTTP room service and room features
  runtime/               model lifecycle, background jobs, activity journal
  self_improvement/      diagnosis, decomposition, patches, health, review workers
  recovery.py, review_ack.py, session_titles.py   adapted upstream primitives
agent.py                 terminal planner and console entry point
run_talos.py             native app launcher and worker supervisor
tests/                   unittest fixtures; no network or private data required
```

Data lives under `state/` (private, git-ignored): SQLite stores, receipts,
plans, screenshots, and proposal queues. Models live under `models/`.

## Request path

1. `room_service.Room` or `agent.py` receives a turn.
2. Memory retrieval uses FTS5 plus MMR diversity; optional vector search is
   lazy-loaded and degrades to empty results when CLIP/LanceDB are absent.
3. `room_dialogue` / `agent.plan` asks the local model for one structured
   decision (`look_up`, `act`, `reply`, `ask`, `wait`, `ignore`).
4. Reads are bounded; writes are recorded in the same SQLite transaction as the
   decision and its receipt.
5. Spoken turns stream through `spoken_stream`; the Swift room app speaks them.

## Self-improvement loop

```
incident / critique / operator idea
        │
        ▼
feature_queue proposal  ──►  decomposition.plan_task
        │                         │  manager splits until leaves are simple
        │                         ▼
        │                    local / cloud routed leaves
        ▼
idle_lab / section_patches draft a patch
        ▼
sandbox verification (git apply, focused regression, full suite)
        ▼
hash-guarded install with rollback and receipts
```

- **Detection.** `self_diagnosis` records incidents and replays trusted
  fixtures. `health` additionally checks syntax, import resolution, Ruff and
  the full regression suite, and runs every six hours from maintenance.
- **Decomposition.** `decomposition.Decomposer` expands an objective layer by
  layer. A manager marks a node as a leaf when it is bounded to one file and a
  small change. `HeuristicManager` works offline; `ModelManager` asks a local or
  cloud model with a prompt containing only that node. Every node carries its
  own context; depth, node count and estimated prompt tokens are capped.
- **Routing.** Trivial leaves go to the local model; broad implementation
  leaves go to a cloud model. The plan is persisted under `state/plans/`.
- **Verification.** `section_patches` applies candidate diffs in a copy,
  runs focused and existing tests, then installs only the verified target.
  `self_repair` keeps pinned contracts for two pure algorithms and swaps files
  atomically, retaining the displaced inode for rollback.
- **Capability audit.** `capability_audit.py` inventories the planner tools,
  computer-use primitives, CLI commands, room endpoints and maintenance jobs,
  checks which have a regression test, inventories the pinned OpenClaw and
  Hermes tool surfaces, and writes `state/capability-audit.json`. The best gaps
  are queued through decomposition and the verified patch loop every 12 hours.
- **Initiative.** `talos_core/self_improvement/initiative.py` inspects health
  failures, empty knowledge stores and Graphify communities without tests,
  optionally asks a model for one new capability, decomposes each accepted idea,
  and queues it for the existing verify/apply loop. It never edits source itself.
- **Cadence.** `run_talos.py` supervises the maintenance, vault, heartbeat,
  review and repair workers. Maintenance runs a full self-check every six
  hours; a failed check becomes diagnosis incidents and immediately triggers
  the verified-patch improvement cycle with rollback backups. All workers yield
  to foreground conversation.

## Mac control

`talos_core.mac.computer_use` provides the computer-use primitives:

| Need | Call |
| --- | --- |
| See the screen | `screenshot()`, `observe()`, `screenshot_jpeg()` |
| Frontmost app / windows | `frontmost()`, `apps()`, `windows(app)` |
| Focus / open | `activate(app)`, `open_path(path)` |
| Click | `click(x, y, count=1)` |
| Type / shortcut | `type_text(text)`, `press("cmd+shift+4")` |
| Menus | `menu(app, "File", "New Window")` |
| Discover unseen apps | `see(app)`, `find_controls(app, query)`, `ocr(app)` |
| Click a discovered control | `click_ref(app, ref)` |
| Click by intent | `click_goal(app, "Save")` |
| Fill a discovered field | `type_ref(app, ref, text)` |
| Scroll | `scroll(amount, app=...)` |
| Hover | `hover(x, y, app=...)` |
| Drag | `drag(x1, y1, x2, y2, mode="pid"\|"hid")` |
| Windows | `window("move\|resize\|minimize\|restore\|close", app=...)` |
| Clipboard | `clipboard()`, `set_clipboard(text)` |

Typing and key chords with an ``app`` argument are posted with
``CGEventPostToPid``: they reach that app even while the user's foreground app
keeps focus and never type into whatever happens to be frontmost. The optional
``control`` extra adds `computer-harness` for Accessibility snapshots and
`macos-computer-use-kit` for stable element refs, set-of-mark screenshots,
on-device OCR and verified AX actions. `see(app)` returns a compact list of
visible controls; `find_controls` ranks them; `click_ref`/`type_ref` act on the
chosen ref. This is how a never-seen app becomes usable: observe its labels and
geometry, choose a control, act, and re-observe.

Window title-bar drags need `mode="hid"` (WindowServer only moves windows for
system-level events); content drags, hovers and scrolls stay on the background
PID path. Window move/resize/minimize/restore use AX and never steal focus.

The Talos cursor overlay is a click-through accessory window (`tools/native/
cursor_overlay.swift`). It never moves the physical pointer and fades after an
action; the user can see Talos's target while keeping their own cursor.

`ComputerUseLoop` runs the bounded observe → decide → act cycle. The decider is
injected, so a local vision model, a cloud model or a deterministic policy can
drive it; unknown actions are refused and the loop stops at `max_steps`.

Every action is argv-only (no shell), writes a private JSONL receipt, never
logs typed content, and refuses destructive shortcuts without `confirm=True`.
`permissions()` reports Accessibility and Screen Recording grants without
prompting. From a shell:

```sh
python3 -m talos_core.mac.computer_use permissions
python3 -m talos_core.mac.computer_use frontmost
python3 -m talos_core.mac.computer_use screenshot --path /tmp/shot.png
```

From the console, use `/computer ...`; shadow mode prints a preview and
`agent.py chat --apply` executes. `/decompose TASK` prints a routable plan.

## Knowledge reflection

`talos_core/knowledge_reflection.py` continuously turns recent conversations
into weighted relations across transcripts, personal facts, vault notes,
memory connections and the code graph. Many candidate explanations are scored
in parallel and then collapsed to the strongest few before storage. This is a
weighted multi-hypothesis system, not quantum hardware; the useful property is
that related context is recomputed continuously as new conversation arrives.
Maintenance runs it every ten minutes and initiative every four hours.

## Terminal fallback and dictation

`talos_core/interfaces/brain_ui.py` renders a dense, procedural ASCII brain:
two hemispheres with cortex folds, travelling waves and neuron sparks. A normal
terminal frame contains roughly 2,000–3,500 changing characters. It is
dependency-free and includes a scrolling thought log and a typed prompt for when speech is unavailable
(`talos console`). `talos_core/cognition/dictation.py` is a deterministic
attention state machine: idle, listening, user speaking, agent speaking,
thinking and interrupted. It distinguishes direct address ("Hey Talos...")
from third-person or background speech, supports barge-in, and combines text
with camera attention (one facing person vs a crowd/away). The native room owns
the microphone and playback; `Room.observe_voice` now feeds every voice turn into
the machine. Background/other-speaker turns and crowd/away frames without an
explicit address return no reply, and speech during an active stream is treated
as barge-in: the stream is cancelled and listening resumes.

`talos vision FILE QUESTION` asks the local vision model about a camera frame,
and the room's `/v1/voice/facing` path already sends one transient camera JPEG
for visual questions. Frames are validated and never logged.

## Knowledge graph

`talos_core/knowledge_graph.py` loads the complete Graphify graph
(``graphify-out/graph.json``) and exposes search, node lookup, neighbours and
shortest paths. `cognitive_engine.query_knowledge` includes code-graph hits for
code-oriented questions, and the console has `/graph QUERY|stats`. The CLI:

```sh
talos graph decomposition manager
talos graph --node ComputerUse --depth 2
talos graph --path decomposition ComputerUse
talos graph --neighbours SemanticEngine
```

## Token efficiency

- Graphify builds a local symbol graph: `graphify query "..." --budget 500`,
  `graphify path A B`, then verify only the cited lines with `rg`.
- RTK compresses noisy command output (`rtk test`, `rtk err`, `rtk read`).
- Decomposition caps estimated manager tokens and never sends the repository;
  each node gets only its title, detail, acceptance and files.
- Runtime prompts are bounded by message count, retrieval count and max tokens.
  The console exposes measured usage with `/context` and `/usage`.

## Commands

```sh
python3 -m talos_core.cli doctor          # full health report
python3 -m talos_core.cli doctor --fix    # safe Ruff fixes, then re-check
python3 -m talos_core.cli decompose "TASK" --file path/to/module.py
python3 -m talos_core.cli decompose "TASK" --model qwen3:4b   # AI manager
python3 -m talos_core.cli status
python3 -m talos_core.cli computer frontmost
python3 -m talos_core.cli computer screenshot --path /tmp/shot.png
python3 -m talos_core.cli test
python3 -m talos_core.self_improvement.improve_talos --cycle
```
