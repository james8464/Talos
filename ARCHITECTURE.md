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
| Clipboard | `clipboard()`, `set_clipboard(text)` |

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
