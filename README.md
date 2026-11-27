# Talos

A native macOS client for the models you choose. Talos is intentionally a
wrapper: it does not add a persona or a system prompt, and each answer is
labeled with the model that produced it.

## Features

- Local models through Ollama, with model size and parameter counts
- Hosted models through OpenAI, Anthropic, and Google Gemini
- Model, reasoning effort, and response speed next to the composer
- Provider endpoint and key status one click from the prompt
- API keys stored in the macOS Keychain
- Native Settings window for models, providers, generation, and appearance of
  the transcript
- Streaming replies, stop, copy, and per-task model labels

Shell, patch application, approvals, and computer use are not implemented yet.
They are labeled **Not yet** in the app rather than exposed as non-working
controls.

## Build

Requirements: macOS 14+, Xcode 15+, and [Ollama](https://ollama.com) for local
models.

```sh
make run
```

The Xcode project is checked in, so `open apps/macos/Talos.xcodeproj` also works.
After changing `apps/macos/project.yml`, regenerate it with `make project`.

## Hosted providers

Add an API key in **Settings → Providers**. OpenAI's base URL can point at any
OpenAI-compatible service. ChatGPT Plus and Claude Pro subscriptions do not
include API access for third-party apps, so hosted use requires an API key from
the provider's developer console.

## Privacy

Local requests go only to the Ollama server configured in Settings. Hosted
requests go only to the selected provider. Keys stay in the macOS Keychain; the
repository contains no credentials and no prompt history.
