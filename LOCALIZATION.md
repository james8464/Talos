# Localization

Talos ships English as the source language and **French** as its supported
non-English localization. Both languages are covered by automated checks and a
live accessibility traversal.

The catalog lives at `apps/macos/Talos/Localizable.xcstrings` and compiles into
`Talos.app/Contents/Resources/fr.lproj/Localizable.strings`.

## Coverage

French currently covers:

- Sidebar: search prompt, New Task, date groups, archived controls, grouping
  menu, preview/count/archived toggles, project header copy, empty state.
- Welcome screen, composer, transcript states, tool cards, inspector, and
  command palette.
- File, Task, Model, View, and Help menus; the menu-bar extra menu.
- Rename/delete dialogs, approval sheet, hook-trust prompt, provider editor,
  recent documents, MCP elicitation and sampling, MCP app shell, worktree sheet,
  project browser, Git panel, and Quick Switcher.
- All Settings pages: General, Projects, Models, Providers, Tools, Profiles,
  Hooks, Memory, Chronicle, Policy, Cloud Tasks, App Server, Skills, Plugins,
  MCP, Remote, Appearance, Usage, Advanced, and About.
- Accessibility labels and help text on the audited controls.

Provider names, model identifiers, paths, user content, shell commands, and
technical tokens such as `JSON-RPC` or `Authorization: Bearer` stay in English.

## Try it

```sh
defaults write app.talos.macos AppleLanguages -array fr
defaults write app.talos.macos AppleLocale -string fr_FR
killall cfprefsd 2>/dev/null
killall Talos 2>/dev/null
open apps/macos/.build/Build/Products/Debug/Talos.app
```

To return to English:

```sh
defaults delete app.talos.macos AppleLanguages
defaults delete app.talos.macos AppleLocale
killall cfprefsd 2>/dev/null
```

## Automated tests

```sh
make localization-check      # catalog completeness and placeholders
make localization-coverage   # source scan for untranslated UI literals
make localization-eval       # live French AX traversal; needs Accessibility
make test                    # ToolParser unit tests
make check                   # build + unit tests
```

`localization-check` verifies all 738 keys have French, placeholders survive
translation, and non-brand keys do not silently fall back to English.

`localization-coverage` scans SwiftUI call sites, conditional titles, fallback
strings, and explicit `String(localized:)`/`LocalizedStringKey` wrappers. It
reports 654 UI literals with 0 missing French.

`localization-eval` launches the app with `AppleLanguages=fr`, creates a task,
checks the main window, menu bar, rename/delete, Quick Switcher, Help, Project
Browser, Git panel, approval, MCP elicitation/sampling, recent documents,
provider editor, hook trust, MCP app, and worktree sheet, then visits all 20
Settings pages. It fails on English UI strings, missing French labels, or
unlabeled controls.

Latest live result:

```text
MAIN french=117 english=0 unlabeled=0
MENUBAR french=60 english=0 items=106
SURFACE Rename Task french=120 english=0 unlabeled=0
SURFACE Delete Task french=120 english=0 unlabeled=0
SURFACE Quick Switcher french=128 english=0 unlabeled=0
SURFACE Help french=138 english=0 unlabeled=0
SURFACE Project Browser french=120 english=0 unlabeled=0
SURFACE Git Panel french=128 english=0 unlabeled=0
SURFACE Approval Sheet french=120 english=0 unlabeled=0
SURFACE MCP Elicitation french=123 english=0 unlabeled=0
SURFACE MCP Sampling french=121 english=0 unlabeled=0
SURFACE Recent Documents french=121 english=0 unlabeled=0
SURFACE Provider Editor french=132 english=0 unlabeled=0
SURFACE Hook Trust french=121 english=0 unlabeled=0
SURFACE MCP App french=119 english=0 unlabeled=0
SURFACE Worktree Sheet french=123 english=0 unlabeled=0
20 Settings pages: english=0 unlabeled=0
FAILURES: 0
```

French copy was authored for this project with machine assistance and reviewed
for UI consistency; a native-speaker review is still recommended before shipping
broadly.

## Adding a language

Only English and French are supported today. Adding another language means
adding a localization set to the String Catalog and extending the live
traversal’s page titles and required labels. No source changes are required for
strings that already use `Text`, `Button`, `Toggle`, `Picker`,
`accessibilityLabel`, or explicit `String(localized:)`/`LocalizedStringKey`.
