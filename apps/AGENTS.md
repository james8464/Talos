# Talos macOS

The repository contains one app: the native SwiftUI client in `apps/macos`.

## Build

```sh
make run
```

`make build` compiles without launching. `make project` regenerates the Xcode
project from `apps/macos/project.yml`; the generated project is checked in.

## Conventions

- SwiftUI, macOS 14 deployment target.
- The app is a model client, not a persona. Do not add a Talos system prompt.
- Provider credentials live in the macOS Keychain through `Keychain.swift`.
- Provider and generation settings belong in `SettingsStore`; request payloads
  are built in the provider clients.
- Keep the transcript honest: features that are not implemented are labeled
  **Not yet**, not shown as enabled controls.
