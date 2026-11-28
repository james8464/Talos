# Localization

Talos uses a String Catalog at
`apps/macos/Talos/Localizable.xcstrings` (`en` source, `ar` translations) and
ships Arabic as its first non-English localization. The catalog compiles into
`Talos.app/Contents/Resources/ar.lproj/Localizable.strings`.

## Coverage

Arabic currently covers:

- Sidebar: search prompt, New Task, date groups, archived controls, grouping
  menu, preview/count/archived toggles, project header copy, empty state.
- Welcome screen: headline, capability description, all four suggestion cards
  and their descriptions, command hint.
- Composer: prompt field, accessibility label, attach/dictation/send/stop
  actions, drag-and-drop hint, provider footer, Return hints, command hint.
- Transcript: speaker labels, thinking/working states, tool status and output
  disclosure, plan controls, copy action.
- Inspector: project, model, tools, index, task, and privacy labels plus
  enabled/disabled values.
- Settings navigation and the primary General, Appearance, Tools, and Usage
  controls: model and reasoning pickers, window toggles, transcript size,
  sidebar grouping, trackpad gestures and action pickers, completion settings,
  tool access toggles, usage metrics, audit export.
- File menu commands and Task menu items covered by the catalog.

Provider names, model identifiers, session content, MCP tool names, and some
secondary Settings pages remain in English by design or pending translation.
The catalog infrastructure supports adding them without code changes.

## Right-to-left

Arabic launches in a right-to-left layout. The app resolves layout direction
from the preferred language (with the `TALOS_LAYOUT_DIRECTION` override for
tests), so the sidebar moves to the right, the inspector to the left, and the
composer mirrors. Tool output and code stay left-to-right.

Verified positions with Arabic:

```text
sidebar heading "اليوم"            x = 1319
welcome "ماذا تريد أن تفعل؟"       x = 879
send "إرسال الرسالة"              x = 462
attach "إرفاق ملفات"              x = 1044
```

## Try it

```sh
defaults write app.talos.macos AppleLanguages -array ar
defaults write app.talos.macos AppleLocale -string ar_SA
killall Talos 2>/dev/null; open apps/macos/.build/Build/Products/Debug/Talos.app
```

To return to English:

```sh
defaults delete app.talos.macos AppleLanguages
defaults delete app.talos.macos AppleLocale
```

The same catalog can be edited in Xcode’s String Catalog editor or as JSON.
Adding a language adds a new localization set; no source changes are required
for strings that already use `Text`, `Button`, `Toggle`, `Picker`, or
`accessibilityLabel` literals. Computed strings use `String(localized:)`.

## Verification

- Compiled catalog: `ar.lproj/Localizable.strings` present in the app bundle.
- Arabic AX labels confirmed for sidebar, welcome, composer, inspector, and
  Settings (`مهمة جديدة`, `بحث في المهام`, `ماذا تريد أن تفعل؟`,
  `إرسال الرسالة`, `إيماءات لوحة التتبع`, `حجم نص المحادثة`).
- AX audit with Arabic and Settings open: `APP_CONTROLS: 36`,
  `UNLABELED: 0`.
- OCR of the Arabic Settings pane read: `النص`, `حجم نص المحادثة`,
  `الشريط الجانبي`, `إيماءات لوحة التتبع`, `السحب لليسار`, `السحب لليمين`,
  `السحب لأعلى`, `السحب لأسفل`.

Arabic copy was authored for this project; a native-speaker review is still
recommended before shipping broadly.
