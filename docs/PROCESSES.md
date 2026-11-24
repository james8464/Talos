# Process inventory

`talos processes` reports Talos-owned processes by label and PID only:

```text
Processes: room service; source observer; worker x6 · ports: 8765 (pid 16731) · stale: 0 · audio: 0
```

It never prints command-line arguments, so prompts and private paths stay out
of logs. `talos processes --stop-stale` stops only known test/UI leftovers
(`talos_core.cli gui`, `tools/talos_live_features`, headless test browsers). It
never stops the room service, workers, cursor overlay, or audio processes.

`talos status` includes the same summary. A muted service reports
`audio: 0`, and the room voice endpoint remains disabled.
