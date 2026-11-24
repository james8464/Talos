# On-screen guidance

Talos can point without speaking or clicking. `talos ask "Show me the save
button in TextEdit."` rings the control on screen and labels it for eight
seconds. The click-through overlay never takes focus.

The same action is available directly:

```sh
python3 -m talos_core.cli computer highlight TextEdit "save button"
```

The highlight is drawn by `tools/native/cursor_overlay.swift`; it is part of the
same native overlay as the agent cursor and control indicator. It does not
produce audio.
