# Inclusion review

Review of Talos against the HIG inclusion guidance: avoid assumptions and
stereotypes, support varied writing systems and content, and work with the
system accessibility settings people choose.

## Method

- Inventoried user-facing strings for gendered, ability-based, culturally
  specific, or task-assumptive wording.
- Created live tasks with Arabic, Hebrew, Chinese, Japanese, emoji (including a
  ZWJ sequence), combining accents, and very long unbroken titles.
- Entered mixed-script and long unbroken text into the composer.
- Ran the interface with **Reduce Motion**, **Reduce Transparency**, and
  **Increase Contrast** enabled, then restored the system settings.
- Reused the VoiceOver results from `VOICEOVER_AUDIT.md` and `make ax-audit`.
- Added a String Catalog with French translations and launched the app in French
  to verify translated strings across menus, dialogs, and Settings.

## Wording

Two strings assumed that everyone using Talos is building software:

| Before | After |
| --- | --- |
| “What should we build?” | “What would you like to do?” |
| “Ask Talos to build, fix, or explain…” | “Ask Talos anything…” |

No gendered pronouns, ability assumptions, “normal user” language, sanity/
blacklist/whitelist terminology, or cultural references were found in
user-facing strings. Tool descriptions are task-oriented rather than
person-oriented.

## Multilingual and long content

Test titles:

```text
مرحبا بالعالم — مهمة عربية طويلة لاختبار تخطيط الشريط الجانبي والقص
שלום עולם — בדיקת ממשק עם טקסט עברי ארוך
你好，世界 — 这是一个很长的中文任务名称，用于测试侧边栏布局和截断
こんにちは世界 — 日本語の長いタスク名でレイアウトを確認する
👩🏽‍💻🚀✨ VeryLongUnbrokenTokenVeryLongUnbrokenTokenVeryLongUnbrokenToken
Café naïve résumé — combining áéíóú and long names
```

Results:

- All six rows rendered with single-line ellipsis; none clipped the row,
  overlapped the timestamp, or broke the sidebar width.
- Accessibility values kept the complete original titles even when the visible
  row truncated, so VoiceOver reads the full title.
- The composer accepted mixed Arabic, Chinese, Japanese, an emoji ZWJ sequence,
  accented Latin, and a long unbroken token. Its AX value exactly matched the
  input; the field did not resize the composer or push the send controls out.

## Localization

`apps/macos/Talos/Localizable.xcstrings` contains 738 French keys covering the
sidebar, welcome screen, composer, transcript states, inspector, menu bar, all
Settings pages, and the audited dialogs and sheets. A CI catalog validator, a
held-out source scan for unlocalized UI literals, and a live French AX traversal
(`make localization-check`, `make localization-coverage`,
`make localization-eval`) guard the coverage.

Latest live result: 0 English UI strings and 0 unlabeled controls across the
main window, menu bar, twelve dialogs/sheets, and all 20 Settings pages. Full
details are in `LOCALIZATION.md`.

## Accessibility settings

With Reduce Motion, Reduce Transparency, and Increase Contrast enabled and the
app relaunched:

```text
APP_CONTROLS: 26
UNLABELED: 0
```

The multilingual sidebar and composer still rendered, materials fell back to
solid surfaces, motion was skipped, and semantic colors remained legible. The
system settings were restored after the test.

VoiceOver: the complete walkthrough is in `VOICEOVER_AUDIT.md`; app-owned
unlabeled controls were 0 across the main window, Settings, and a live MCP app.

## Color and status

- Success, failure, and denial use both a symbol and a text label.
- Plan progress includes a numeric count and per-step symbols in addition to
  color.
- Chart values are encoded by bar height with labeled axes, not color alone.
- Disabled controls remain in the accessibility tree with their labels.

## Limitations

- French is the supported non-English localization and covers all Settings
  pages, menus, and audited dialogs. Provider/model identifiers, paths,
  technical tokens, and user-generated content remain in English by design.
- French copy was authored with machine assistance and should receive
  native-speaker review before broad distribution.
- This is a structured review rather than a formal study with a diverse panel
  of external participants.
- Task suggestions remain oriented toward a coding agent (explore the codebase,
  fix a test, review changes, automate a workflow), reflecting the product’s
  scope rather than assumptions about the person using it.
