# Repository Guide

This repository contains one app: the native SwiftUI client in `apps/macos`.

## Commands

- `make build`: compile the app.
- `make run`: compile and launch the app.
- `make project`: regenerate `apps/macos/Talos.xcodeproj` from `project.yml`.
- `make clean`: remove the local build products.

## Conventions

- SwiftUI with a macOS 14 deployment target.
- Talos is a model client, not a persona. Do not add a Talos system prompt.
- Provider credentials live in the macOS Keychain through `Keychain.swift`.
- Provider configuration and generation preferences belong in `SettingsStore`;
  request payloads belong in the provider clients.
- Keep the UI honest: unimplemented tools are labeled **Not yet**.
- Do not commit `.build`, secrets, logs, or user prompts.
