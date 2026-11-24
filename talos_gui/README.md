# Talos GUI

This is the OpenCowork renderer (MIT, Copyright (c) 2026 OpenCoworkAI) vendored
into Talos. `talos gui` serves the committed `dist/` build and bridges chat to
the local Talos room.

- Source: `src/renderer`, `src/shared`
- Adapter: `public/talos-bridge.js`
- Server: `talos_core/interfaces/gui_bridge.py`
- Provenance: `UPSTREAM_COMMIT`, `UPSTREAM_VERSION`, `LICENSE-OpenCowork`

Rebuild after changing the UI:

```sh
npm install
npm run build
```
