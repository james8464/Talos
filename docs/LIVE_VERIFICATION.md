# Live verification

Last run: 24 November 2026, macOS, Talos room service running under launchd as
`local.talos.room` with `--mute` and `TALOS_MUTE=1`.

## Silent operation

- `POST /v1/voice` returns `503 {"error": "Kokoro voice is unavailable."}`.
- The launchd service runs with `--mute` and `TALOS_MUTE=1`; the room
  launcher is silent by default and `--unmute` is the explicit speech opt-in.
  `TALOS_MUTE` accepts `1`, `true`, `yes`, or `on`.
- The native app is text-only by default. The macOS/system-voice fallback has
  been removed; if the high-quality Kokoro model is unavailable, replies stay
  on screen. `Start-Talos.command` also launches muted by default.
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
- Unit suite: `609 tests`, `OK` (`16` skipped by design).
- Live feature pass: `52/52` checks.
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
| Local features | skills, nodes, todos, maintenance report, health, model, plugins, subagents, checkpoints, recent activity |
| Native apps | live Calendar, native Reminders export, Shortcuts, Safari URL/search, Finder, Maps, read-only Music, private-index app search (Notes/Mail/Messages/Contacts), app status |
| MCP client | official-SDK stdio and Streamable-HTTP tools/list/tools-call; room discovery and call against Talos and `mcp-server-apple-events` |
| Safety | `/v1/voice` 503, no audio processes, speech/music requests blocked, unverified app writes refused |

## Native Mac checks

Exercised through `talos computer` against TextEdit and Calculator:

- `frontmost`, `apps`, `windows`, `screenshot`, `see`, `find`, `ocr`
- `activate`, `type` and `key` (`12+7` then Return produced `19`)
- `click-ref` on Calculator's All Clear control
- `scroll`, `hover`, `window list`, `window resize`/restore, `window move`/restore, `window minimize`/restore/focus
- `clipboard` write/read/restore without logging clipboard content
- Accessibility and Screen Recording both granted

## CLI, MCP and service checks

- `talos capabilities`, `audit`, `graph`, `doctor`
- `talos skills` create/list, `nodes` register/list/remove, `todo` add/list/done
- `talos decompose` and `talos initiative`
- MCP stdio `initialize`, `tools/list` (4 tools) and a `talos_graph_query` call
- launchd service remains running after quiet restart and repeated checks

## Local protocol checks

- Slack and Discord clients were configured and sent to a local self-signed
  HTTPS webhook. The server received `{"text": "live test"}` and
  `{"content": "live test"}` with HTTP 200.
- ACP was exercised against a local JSON-RPC fixture after fixing its
  `--command` parser collision; `initialize`, `session/new` and
  `session/prompt` returned `pong`.

## Cloud model

The optional OpenCode Go bridge was verified against the subscription:

- `talos cloud "Reply with exactly CLOUD_OK." --json` returned
  `{"ok": true, "model": "deepseek-v4.1-flash", "reply": "CLOUD_OK"}`.
- The room route `Ask the cloud to reply with exactly CLOUD_OK.` returned
  `CLOUD_OK`; `Is the cloud available?` reported it as configured.
- The key is stored only in the private runtime state (or the OpenCode auth
  store), never logged. The route sends only the requested text, not room
  history or saved private context. The path is text-only.

## Not live-verified here

These require external credentials, endpoints, hardware or operator input that
were not available in this environment:

- Telegram pairing/bot polling (no owner token configured)
- Real Slack/Discord workspace webhooks (only a local HTTPS receiver was used)
- Feishu document/drive calls (no app credentials)
- External ACP agents (only a local protocol fixture was used)
- Camera-based vision (no camera frame supplied)
- Microphone dictation/playback (silent environment, no audio hardware use)

The clients and configuration paths exist and are covered by the unit suite, but
no live external send or camera/microphone claim is made.


### Existing Apple Events MCP server through the room

Registered with `talos mcp-client add apple-events --command npx --server-args
'["-y","mcp-server-apple-events","--require-write-confirmation"]'`.

- `List MCP tools on apple-events.` returned
  `reminders_tasks, reminders_lists, calendar_events, calendar_calendars,
  reminders_subtasks`.
- A Calendar read returned `Permission denied: Only write access to calendar.
  Full access is required.` This is the expected macOS grant boundary for the
  host process; the native JXA/EventKit-export path remains available.
