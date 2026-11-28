# VoiceOver walkthrough

Performed on macOS with VoiceOver enabled from the keyboard shortcut and the
built-in Quickstart completed through **Use VoiceOver**. The VoiceOver cursor
was moved with Control-Option-Right, and each stop was captured through
`AXFocusedUIElement`. A second pass enumerated the accessibility tree and
recorded the label VoiceOver speaks for each control.

System window chrome (close/minimize/zoom and scrollbar arrows/pages) is
excluded from the unlabeled count, matching the project’s accessibility audit.

## Main window

VoiceOver cursor sequence from the composer:

| Role | Spoken label |
| --- | --- |
| Text field | Message |
| Button | Attach files |
| Button | Start dictation |
| Button | deepseek-v4.1-flash (Model — OpenCode Go · deepseek-v4.1-flash) |
| Menu button | Low (Reasoning — OpenCode Go reasoning effort) |
| Menu button | Balanced (Response speed — The default output budget for most conversations) |
| Button | Provider and tool settings |

Sidebar and toolbar controls announce their labels, including New Task,
Quick Switch, Changes, Stop, Details, Hide Sidebar, and the session rows with
title, relative time, and message count.

## Settings

The Settings scene was opened with ⌘, and the search field was focused:

| Role | Spoken label / value |
| --- | --- |
| Search field | Search Settings |
| Pop-up button | Active model — OpenCode Go · deepseek-v4.1-flash |
| Button | Refresh |
| Pop-up button | Reasoning effort |
| Pop-up button | Response speed |
| Switch | Show details inspector |
| Switch | Show Talos in the menu bar |
| Outline | Sidebar |
| Button | Hide Sidebar |

Tab order with Full Keyboard Access off skips pop-up buttons, as macOS expects;
VoiceOver still reaches them. No app-owned control was unlabeled.

## MCP app

Opened a live MCP app in the sandboxed web view:

| Role | Spoken label |
| --- | --- |
| HTML content web area | VoiceOver App |
| Group | open_ui app, sandboxed web view |
| Heading | VoiceOver audit app |
| Button | Say hello |
| Link | External link |

VoiceOver treats the web view as an HTML content area; entering it requires the
standard VO-Shift-Down interaction. The underlying web controls expose their
text as accessible names.

## Findings

- App-owned interactive controls: **0 unlabeled** in the main window and
  Settings pass (45 spoken controls; 13 unlabeled were system chrome).
- MCP app: **0 app-owned unlabeled** (37 spoken controls; 11 system chrome).
- Added a speaker prefix to transcript messages so VoiceOver announces
  “You said: …” or “<model> said: …” before the message text.
- VoiceOver was turned off after the walkthrough.
