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
- Menu-bar menu, approval sheet, worktree sheet, GitHub panel, diagnostics
  panel, recent documents, project browser, Chronicle strip, policy empty
  state, and the MCP app, elicitation, and sampling views.
- Accessibility labels and help text on every audited control.

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
make localization-check      # CI-safe String Catalog validation
make localization-coverage   # source scan for unlocalized UI literals
make localization-eval       # live Arabic AX traversal; needs Accessibility permission
make test                    # ToolParser unit tests (macOS test host)
make check                   # build + unit tests
```

`localization-check` verifies all 685 source keys have Arabic, Arabic script is
present, and placeholders survive translation. `localization-coverage` scans
SwiftUI call sites for user-visible literals and fails when one has no Arabic
entry. CI runs both on every push.

The held-out coverage scan found 131 missing literals on its first run —
including the menu-bar menu, approval sheet, worktree sheet, GitHub panel, MCP
views, Chronicle controls, diagnostics panel, recent documents, and several
accessibility labels. All were translated; the scan now reports 618 UI literals
and 0 missing. Details and raw results are in `HELDOUT_EVAL.md`.

`localization-eval` launches the built app with `AppleLanguages=ar`, creates a
fresh task, opens rename/delete, the Quick Switcher, Help, Project Browser, Git
panel, worktree sheet, approval, MCP elicitation/sampling, recent documents, and
the provider editor, checks the menu bar, then opens Settings and visits all 20
pages. It fails on:

- app-owned English strings whose source key has an Arabic translation,
- Settings pages that do not switch,
- pages with fewer than two Arabic labels,
- unlabeled interactive controls.

Latest live result:

```text
MAIN arabic=177 english=0 unlabeled=0
SURFACE Quick Switcher arabic=188 english=0 unlabeled=0
SURFACE Help arabic=196 english=0 unlabeled=0
SURFACE Project Browser arabic=178 english=0 unlabeled=0
SURFACE Git Panel arabic=187 english=0 unlabeled=0
SURFACE Worktree Sheet arabic=181 english=0 unlabeled=0
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
- Automated live traversal: main window Arabic labels 177, 98 Arabic menu-bar
  labels, twelve dialogs/sheets, and all 20 Settings pages with zero UI-owned
  English and zero unlabeled controls.
- AX audit with Arabic and Settings → Tools open: `APP_CONTROLS: 44`,
  `UNLABELED: 0`.
- OCR of the Arabic Settings pane read: `النص`, `حجم نص المحادثة`,
  `الشريط الجانبي`, `إيماءات لوحة التتبع`, `السحب لليسار`, `السحب لليمين`,
  `السحب لأعلى`, `السحب لأسفل`.

The catalog contains 685 keys. Arabic copy was authored for this project; a
native-speaker review is still recommended before shipping broadly.
