# Live verification

Last run: 24 November 2026, macOS, Talos room service running under launchd as
`local.talos.room` with `--mute` and `TALOS_MUTE=1`.

## Silent operation

- `POST /v1/voice` returns `503 {"error": "Kokoro voice is unavailable."}`.
- The launchd plist contains `--mute` and `TALOS_MUTE=1`; `Start-Talos.command`
  also launches muted by default.
- `talos status` prints `Audio · muted` when `TALOS_MUTE=1`.
- No `afplay`, `/usr/bin/say`, or Kokoro process was running after the checks.
- Kokoro and Telegram voice-note synthesis raise `Audio output is muted.` before
  loading models, so callers fall back to text.
- Typed turns, memory, reminders, tasks, Mac control, maintenance and the local
  model continue to work.

## Local checks

```sh
python3 -m talos_core.cli doctor --quick
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m tools.talos_live_features --json
```

Latest results:

- `doctor --quick`: healthy; syntax, imports and Ruff pass.
- Unit suite: `574 tests`, `OK` (`16` skipped by design).
- Live feature pass: `41/41` checks.
- Ruff: clean.

## Live feature pass

`tools/talos_live_features.py` asks the running room to perform each check:

| Area | Check |
| --- | --- |
| Chat | capabilities, time, date, arithmetic, small talk, public facts, general knowledge, wellbeing, identity, mute questions, presence handshake |
| Memory | save/recall a personal fact, search memory, forget a fact, missing-fact answer |
| Work | reminder, task, task list, note, inbox, goals |
| Knowledge | knowledge search, code-graph search |
| Mac | screenshot, window list, frontmost app, running apps, control search, inspect app, accessible-text/OCR |
| Local features | skills, nodes, todos, maintenance report, health, model, plugins, subagents, checkpoints |
| Safety | `/v1/voice` 503, no audio processes |

## Native Mac checks

Exercised through `talos computer` against TextEdit and Calculator:

- `frontmost`, `apps`, `windows`, `screenshot`, `see`, `find`, `ocr`
- `activate`, `type` and `key` (`12+7` then Return produced `19`)
- `click-ref` on Calculator's All Clear control
- Accessibility and Screen Recording both granted

## CLI, MCP and service checks

- `talos capabilities`, `audit`, `graph`, `doctor`
- `talos skills` create/list, `nodes` register/list/remove, `todo` add/list/done
- `talos decompose` and `talos initiative`
- MCP stdio `initialize`, `tools/list` (4 tools) and a `talos_graph_query` call
- launchd service remains running after quiet restart and repeated checks

## Not live-verified here

These require external credentials, endpoints, hardware or operator input that
were not available in this environment:

- Telegram pairing/bot polling (no owner token configured)
- Slack and Discord sends (no webhook URLs configured)
- Feishu document/drive calls (no app credentials)
- ACP command sessions (no external ACP command selected)
- Camera-based vision (no camera frame supplied)
- Microphone dictation/playback (silent environment, no audio hardware use)

The clients and configuration paths exist and are covered by the unit suite, but
no live external send or camera/microphone claim is made.
