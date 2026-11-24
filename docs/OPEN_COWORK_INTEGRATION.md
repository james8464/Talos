# OpenCowork computer-use integration

Upstream: <https://github.com/OpenCoworkAI/open-cowork>
Commit: `e94ee2c50239e64ba0e18921e339d49b93dae31f` (v3.3.1)
License: MIT, Copyright (c) 2026 OpenCoworkAI.

The upstream renderer was evaluated and deliberately not kept: Talos runs in the
terminal, so a second web UI had no use. The downloaded clone was deleted after
the computer-use behaviour was ported.

## Ported behaviour

- `talos_core/mac/gui_apps.py` — managed GUI application lifecycle: start,
  readiness wait, private log, status, process-group stop.
- `talos_core/mac/gui_actions.py` — unified click, double-click, right-click,
  move, type, key, drag, screenshot, wait, natural-language targeting, and
  Retina screenshot-to-screen mapping.
- `talos_core/mac/computer_use.py` — right-click and pointer-move primitives,
  plus `talos computer gui-*` commands.
- `talos_core/mac/cursor.py` and `tools/native/cursor_overlay.swift` — the
  visible agent cursor, click ripple, and “Talos is controlling this Mac”
  indicator. The overlay is click-through, never takes focus, and hides itself.

Talos does not call `cliclick` or `xdotool`; the port delegates to its existing
`ComputerUse` primitives so receipts, PID-targeted input, policy checks, and
permission reporting are shared.

Not ported: Docker/Xvfb containers, VNC, the remote vision provider, the MCP
wrapper, and the Electron renderer/backend.

## Verification

- `tests/test_gui_apps.py`, `tests/test_gui_actions.py`,
  `tests/test_computer_use.py`, `tests/test_cursor_overlay.py`.
- Live: managed Tk window started and stopped; the cursor/indicator overlay was
  captured on screen and stayed click-through without taking focus; OCR located
  the Tk button and delivered a click, though that fixture did not expose the
  resulting label change reliably. No audio ran.
