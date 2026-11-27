# Talos for macOS

A native SwiftUI client for the models you choose. Talos is a wrapper: it does
not add a persona or a system prompt, and the transcript labels answers with the
model that produced them.

## Requirements

- macOS 14 or later
- Xcode 16 or later
- [Ollama](https://ollama.com) for local models
- API keys for OpenAI, Anthropic, or Google Gemini if you use hosted models

## Build and run

The Xcode project is checked in. Open `Talos.xcodeproj`, or use the root
`Makefile`:

```sh
make run
```

If you change `project.yml`, regenerate the project first:

```sh
make project
```

## Models

Model selection lives in the composer, next to the prompt:

- **Model**: local Ollama models with size and parameter count, plus the model
  configured for each hosted provider
- **Reasoning**: Off, Low, Medium, High. Applied as Ollama thinking, OpenAI
  reasoning effort, or an Anthropic thinking budget where the model supports it.
- **Speed**: Fast, Balanced, Thorough. Sets the output budget.
- **Provider**: endpoint, key status, and a shortcut to Settings

Settings has five tabs:

- **General**: active model, reasoning, speed, inspector
- **Models**: Ollama server, discovered local models, cloud endpoints and models
- **Providers**: API keys stored in the macOS Keychain
- **Advanced**: temperature, local model keep-alive, reset
- **About**: version and provider links

API keys can also be pointed at any OpenAI-compatible endpoint by changing the
base URL in **Models**. ChatGPT Plus and Claude Pro subscriptions do not include
API access for third-party apps, so hosted use requires an API key.

## Current scope

Chat streaming, model discovery, provider configuration, per-message model
labels, and a native Settings window. Shell, patch application, approvals, and
computer use are not implemented yet and are shown as **Not yet** rather than as
fake enabled controls.

## Privacy

Local requests go only to the Ollama server configured in Settings. Hosted
requests go only to the selected provider. Keys are stored in the user's macOS
Keychain, not in a file in this repository.
