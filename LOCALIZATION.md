# Localization

Talos uses a String Catalog at
`apps/macos/Talos/Localizable.xcstrings` (`en` source, `ar` translations) and
ships Arabic as its first non-English localization. The catalog compiles into
`Talos.app/Contents/Resources/ar.lproj/Localizable.strings`.

## Coverage

Arabic currently covers **all Settings pages and the main menus**, in addition
to the primary surfaces:

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
- All Settings pages: General, Projects, Models, Providers, Tools, Profiles,
  Hooks, Memory, Chronicle, Policy, Cloud Tasks, App Server, Skills, Plugins,
  MCP, Remote, Appearance, Usage, Advanced, and About, including captions,
  toggles, pickers, permission rows, statuses, and export controls.
- File, Task, Model, View, and Help menu commands; Quick Switcher commands;
  project, Git, worktree, GitHub, and recent-document surfaces.

Provider names, model identifiers, session content, MCP tool names, file paths,
and technical tokens such as `JSON-RPC` or `Authorization: Bearer` remain in
English by design. The catalog supports further translation without code
changes.

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

## Automated tests

```sh
make localization-check   # CI-safe String Catalog validation
make localization-eval    # live Arabic AX traversal; needs Accessibility permission
```

`localization-check` verifies all 526 source keys have Arabic, Arabic script is
present, and placeholders survive translation. CI runs this on every push.

`localization-eval` launches the built app with `AppleLanguages=ar`, creates a
fresh task, opens Settings, visits all 20 pages, and fails on:

- app-owned English strings whose source key has an Arabic translation,
- Settings pages that do not switch,
- pages with fewer than two Arabic labels,
- unlabeled interactive controls.

Latest live result:

```text
MAIN arabic=167 english=0 unlabeled=0
عام / المشاريع / النماذج / المزوّدون / الأدوات / التكوينات
الخطافات / الذاكرة / السجل / السياسة / مهام السحابة / خادم التطبيق
المهارات / الإضافات / MCP / التحكم عن بعد / المظهر / الاستخدام
متقدم / حول
all pages: english=0 unlabeled=0
FAILURES: 0
```

## Verification

- Compiled catalog: `ar.lproj/Localizable.strings` present in the app bundle.
- Arabic AX labels confirmed for sidebar, welcome, composer, inspector, and
  Settings (`مهمة جديدة`, `بحث في المهام`, `ماذا تريد أن تفعل؟`,
  `إرسال الرسالة`, `إيماءات لوحة التتبع`, `حجم نص المحادثة`).
- Automated live traversal: main window Arabic labels 164, all 20 Settings
  pages with zero UI-owned English and zero unlabeled controls.
- AX audit with Arabic and Settings → Tools open: `APP_CONTROLS: 44`,
  `UNLABELED: 0`.
- OCR of the Arabic Settings pane read: `النص`, `حجم نص المحادثة`,
  `الشريط الجانبي`, `إيماءات لوحة التتبع`, `السحب لليسار`, `السحب لليمين`,
  `السحب لأعلى`, `السحب لأسفل`.

The catalog contains 526 keys. Arabic copy was authored for this project; a
native-speaker review is still recommended before shipping broadly.
