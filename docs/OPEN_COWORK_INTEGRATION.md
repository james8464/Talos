# OpenCowork GUI and computer-use integration

Upstream: <https://github.com/OpenCoworkAI/open-cowork>
Commit: `e94ee2c50239e64ba0e18921e339d49b93dae31f` (v3.3.1)
License: MIT, Copyright (c) 2026 OpenCoworkAI. The upstream license is kept at
`talos_gui/LICENSE-OpenCowork`.

The downloaded clone was used only as a source, then deleted. No upstream
repository tree remains in Talos or in the workspace.

## What was merged

### GUI

The actual OpenCowork renderer is vendored, not reimplemented:

- `talos_gui/src/renderer` — the OpenCowork React interface
- `talos_gui/src/shared` — its shared types used by the renderer
- `talos_gui/public`, `index.html`, Tailwind/PostCSS config
- `talos_gui/dist` — committed production build
- `talos_gui/public/talos-bridge.js` — Talos adapter for `window.electronAPI`

`talos gui` serves that build on loopback and opens it in the default browser.
The adapter forwards chat turns to the local Talos room. The room token stays in
the Python bridge (`talos_core/interfaces/gui_bridge.py`); it is never embedded
in the page. Remote pairing, VM sandboxes, hosted providers, and Electron's main
process are not vendored: Talos already has its own room, policy, MCP, and
session services, so keeping Electron's backend would duplicate them.

The renderer was started with the real UI and exercised directly: the page
title was `Talos · OpenCowork UI`, the composer sent `Who am I?`, and the
assistant reply appeared in the OpenCowork chat view in 48 ms. Audio stayed
off; no speech or playback process ran.

### Computer use

Behavior ported from `src/main/mcp/software-dev-server-example.ts`:

- `talos_core/mac/gui_apps.py` — managed GUI application lifecycle: start,
  readiness wait, private log, status, and process-group stop.
- `talos_core/mac/gui_actions.py` — unified click, double-click, right-click,
  move, type, key, drag, screenshot, and wait actions; natural-language element
  targeting through the existing accessibility tree and local vision model;
  Retina screenshot-to-screen coordinate mapping.
- `talos_core/mac/computer_use.py` — added right-click and pointer-move
  primitives (global and PID-targeted), and `talos computer gui-*` commands.

Talos does not call `cliclick` or `xdotool`. The port delegates to its existing
`ComputerUse` primitives so receipts, PID-targeted input, policy checks, and
permission reporting are shared instead of duplicated. `start_gui_application`,
`gui_interact`, and `gui_interact_vision` therefore exist without a second input
stack.

Not ported: Docker/Xvfb test containers, VNC, the vision HTTP provider, the MCP
"software-development-server" wrapper, and the requirement tracker. Talos
already has local vision, MCP, and task tracking; copying those would create
duplicate code paths.

## Run

```sh
python3 -m talos_core.cli gui              # serve and open the OpenCowork UI
python3 -m talos_core.cli gui --no-open   # serve only (default port 8766)
```

Rebuild the UI after editing `talos_gui/src`:

```sh
cd talos_gui
npm install
npm run build
```

## Verification

- `tests/test_gui_bridge.py` — static serving, SPA fallback, traversal refusal,
  turn proxy, invalid input, room failure, missing-bundle hint.
- `tests/test_gui_apps.py` — confirm gate, start/status/log/stop, failure
  reporting, corrupt state, no-op stop.
- `tests/test_gui_actions.py` — every action, failure isolation, vision target,
  coordinate mapping.
- `tests/test_computer_use.py` — existing input/AX behaviour remains green.
- Full suite after consolidation: `726 tests`, `OK` (`17` skipped).
- Live: vendored UI loaded, answered `Show my recent activity.` in 97 ms, and
  the room live pass completed `58/58` checks with no audio process running.
