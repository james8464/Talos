# Apple Human Interface Guidelines audit

Audited against the published HIG at
[developer.apple.com/design/human-interface-guidelines](https://developer.apple.com/design/human-interface-guidelines/),
fetched November 28, 2026, including the macOS page, foundations, patterns,
components, inputs, technologies, and generative-AI guidance.

Legend: ✅ met and verified · 🟡 partial or not fully verified · ➖ not
applicable to a single-user, local-first macOS model client.

## Fixes applied from this audit

- Removed the app-specific Light/Dark theme picker; Talos now follows the
  system appearance, as the Dark Mode page requires.
- Added drag-and-drop of files onto the composer, with the paperclip as the
  alternative path.
- Respect Reduce Motion and Reduce Transparency: material backgrounds fall back
  to solid colors and scroll/collapse animations are skipped.
- Added a confirmation dialog for task deletion, then wired deletion into the
  window UndoManager so ⌘Z restores the task and ⇧⌘Z deletes it again.
- Resolved the duplicate ⌘K shortcut (Quick Switch vs. Choose Model), which now
  uses ⌘⇧M.
- Moved key text to semantic system styles instead of fixed point sizes.
- Enlarged icon-only hit targets to at least 24×24 pt, and 28×28 pt for
  send/stop.
- Added a Help menu and a shortcut/reference sheet (⌘?).
- Added File > New Window (⇧⌘N), backed by a second standard WindowGroup;
  verified as two independently resizable windows.
- Verified right-to-left layout with a forced RTL pass: sidebar, detail,
  inspector, composer, quick switcher, and modal sheets mirror correctly, and
  tool output stays left-to-right for code and paths.
- Added persisted sidebar customization: group by date, project, or none, with
  preview and message-count toggles in the sidebar menu and Settings.
- Added a persisted recent-documents store, File > Open Recent, and a browser
  with open, reveal, remove, clear, missing-file handling, and relative dates.
- Replaced the custom usage heatmap with Swift Charts: labeled axes, an average
  rule, peak/average context, and an accessibility description.
- Completed a VoiceOver walkthrough of the main window, Settings, and an MCP
  app; added speaker prefixes to transcript messages, and recorded the spoken
  labels in [`VOICEOVER_AUDIT.md`](VOICEOVER_AUDIT.md).
- Documented the on-device semantic index end to end in
  [`SEMANTIC_INDEX.md`](SEMANTIC_INDEX.md): data flow, privacy, measured
  performance, fallbacks, and limitations.
- Added a persisted per-file vector cache and switched the local index from
  sentence inference to averaged word embeddings plus lexical ranking. Talos
  workspace startup dropped from 66.4 s to 7.3 s first-ever and 0.6 s cached;
  one changed file takes 0.69 s. Search ranking checks continue to pass.
- Added configurable two-finger trackpad gestures with persisted action
  mappings, an optional Option-key gate, and keyboard/menu equivalents. Live
  verification: left/right switched tasks, up opened Quick Switch, and down
  toggled the inspector.
- Completed an inclusion review across Arabic/Hebrew/Chinese/Japanese/emoji/
  combining-accent content, long unbroken titles, mixed-script composer input,
  Reduce Motion/Transparency, Increase Contrast, RTL, and VoiceOver. Removed
  build-assumptive welcome/composer wording. Recorded in
  [`INCLUSION_AUDIT.md`](INCLUSION_AUDIT.md).
- Added a String Catalog and Arabic localization for the main window, composer,
  sidebar, inspector, and primary Settings surfaces; verified strings and RTL at
  runtime. Recorded in [`LOCALIZATION.md`](LOCALIZATION.md).
- Hardened the MCP web view: nonpersistent store, main-frame-only bridge,
  same-origin iframe sandboxing, blocked file/data/javascript navigation,
  external links only in the browser, no downloads or automatic windows, media
  user-action requirements, accessible web-view label, and loading/error states.
- Added searchable Settings and image/PDF attachment thumbnails.

## Design fundamentals

| HIG page | Guidance checked | Status | Evidence / note |
| --- | --- | --- | --- |
| Design principles | Direct manipulation, feedback, metaphors, user control | ✅ | Real tools with visible results, approvals before impact, undoable edits through retry/fork, no hidden automation |
| Getting started | Understand platform conventions before designing | ✅ | Native SwiftUI controls, standard menu bar, Settings scene, standard sheets and alerts |
| Designing for macOS | Resizable windows, menu bar, keyboard, precision input | ✅ | Standard resizable window, full menu bar, keyboard shortcuts, pointer-sized targets |
| Designing for iPhone Duo | Adaptive multi-display iPhone layout | ➖ | macOS-only product decision |

## Foundations

| HIG page | Guidance checked | Status | Evidence / note |
| --- | --- | --- | --- |
| Accessibility | Label controls, support assistive settings, don't rely on color | ✅ | `make ax-audit` reports 0 unlabeled interactive controls; statuses pair icons with text; Reduce Motion/Transparency supported |
| App icons | Distinct, recognizable app icon | ✅ | Brand icon is used in the welcome screen, About, and app icon |
| Branding | Express identity without impersonating the system | ✅ | Talos branding, model names shown on replies, no synthetic persona |
| Color | Semantic colors, sufficient contrast, accent color | ✅ | System colors and materials; bronze accent via `.tint`; no hard-coded light-only surfaces |
| Dark Mode | Follow the system appearance and test both modes | ✅ | App-specific theme override removed; all surfaces use semantic NSColor/SwiftUI styles |
| Icons | Use SF Symbols consistently | ✅ | SF Symbols throughout, consistent weights and scales |
| Images | Show meaningful image content, provide alternatives | ✅ | Image attachments render thumbnails; filenames serve as textual alternatives |
| Immersive experiences | Spatial/immersive UI | ➖ | Not a spatial app |
| Inclusion | Avoid assumptions and stereotypes | ✅ | Removed build-assumptive wording; verified Arabic, Hebrew, Chinese, Japanese, emoji, combining accents, long titles, mixed-script input, RTL, Reduce Motion/Transparency, Increase Contrast, and VoiceOver. Added a 526-key String Catalog with Arabic localization for the main window, composer, sidebar, inspector, all Settings pages, and menus, verified by a live Arabic/RTL AX traversal with zero UI English strings and zero unlabeled controls. See `INCLUSION_AUDIT.md` and `LOCALIZATION.md` |
| Layout | Resizable layouts, comfortable density, margins | ✅ | NavigationSplitView, resizable window (min 900×600), 24–32 pt content margins, 760 pt transcript measure |
| Materials | Use materials purposefully and respect Reduce Transparency | ✅ | Materials used for chrome/status; solid fallback when Reduce Transparency is on |
| Motion | Purposeful, optional motion | ✅ | Motion limited to scroll/collapse/status; skipped under Reduce Motion |
| Privacy | Local-first data, explicit provider disclosure | ✅ | Sessions/index/usage local; keys in Keychain; provider privacy note in Inspector |
| Right to left | Leading/trailing layout, mirrored controls | ✅ | Forced-RTL verification: sidebar moved to the right edge (x=1308), detail/welcome to the left (x=807), composer mirrored (send x=462, attach x=1044), inspector moved left (x=305), quick switcher and sheets mirrored. Tool output is pinned LTR. UI strings remain English pending localization |
| SF Symbols | System symbols, correct variants | ✅ | No custom bitmaps for controls; symbol weights match surrounding text |
| Spatial layout | Spatial computing layout | ➖ | Not a visionOS app |
| Typography | System fonts, text styles, monospaced digits | ✅ | Semantic styles in key surfaces, monospaced tool output, monospaced digits for metrics |
| Writing | Clear labels, title case controls, ellipses for more input | ✅ | “New Worktree…”, “Rename…”, “Review Changes…” use ellipses; sentence-case help text |

## Patterns

| HIG page | Guidance checked | Status | Evidence / note |
| --- | --- | --- | --- |
| Charting data | Clear, labeled data visualization | ✅ | Swift Charts bar chart of daily tokens with date and token axes, an average rule, peak/average context, and an accessibility label/value for the chart |
| Collaboration and sharing | Share/collaborate flows | ➖ | Single-user local app |
| Drag and drop | Support drag and drop plus an alternative | ✅ | Composer accepts dropped files; paperclip picker remains |
| Entering data | Clear fields, defaults, validation | ✅ | Composer, settings forms, labeled fields, inline errors |
| Feedback | Accessible status feedback, confirm significant actions | ✅ | Streaming bar, tool status labels, sidebar running indicator, delete confirmation |
| File management | Open/save/attach files predictably | ✅ | Attachments, projects, document creation, and a persisted recent-documents store with File > Open Recent, a browser, missing-file handling, reveal, remove, and clear |
| Going full screen | Support standard full screen | ✅ | Standard resizable window and system full-screen control |
| Launching | Show content immediately, avoid startup alerts | ✅ | Welcome/empty states render immediately; errors are inline/alerts after actions |
| Live-viewing apps | Live media viewing | ➖ | Not a media app |
| Loading | Show progress and keep the app usable | ✅ | Streaming text, progress indicators, searchable index runs off the main actor |
| Managing accounts | Account management | ➖ | Provider keys only; no Talos account |
| Managing notifications | Ask before notifying, respect settings | ✅ | Notification toggle requests authorization; macOS notification center |
| Modality | One sheet at a time, clear dismissal | ✅ | One sheet per surface; approval sheet offers Deny plus Allow actions |
| Multitasking | Support multiple tasks and windows | ✅ | Tasks switch from the sidebar and ⌘[/⌘]; File > New Window (⇧⌘N) opens a second standard window, verified at 1200×780 alongside an independently resized 1000×688 window. Windows share the task store and current selection |
| Offering help | Provide discoverable help | ✅ | Help menu (⌘?) with a shortcut/concept sheet and Settings link |
| Onboarding | Teach in context, avoid forced tours | ✅ | Welcome screen with suggestions; no blocking onboarding |
| Playing audio | Audio feedback conventions | ➖ | Only an optional completion sound |
| Playing haptics | Haptic feedback | ➖ | Mac without haptic API use here |
| Playing video | Video playback conventions | ➖ | Not a video app |
| Printing | Printing flows | ➖ | No printing surface |
| Ratings and reviews | Rating prompts | ➖ | No ratings |
| Searching | Primary search, clear scope, live results | ✅ | Sidebar task search, ⌘K switcher, searchable Settings, live filtering |
| Settings | Standard Settings window, ⌘, minimal options | ✅ | Settings scene, searchable sidebar, defaults, follows system settings |
| Undo and redo | Standard undo/redo; confirm irreversible actions | ✅ | Text undo is standard; task deletion is confirmed and registered with the window UndoManager. Verified live: ⌘Z restored the task (50→51), Shift-⌘Z deleted it again (51→50), ⌘Z restored it again |
| Workouts | Workout-specific patterns | ➖ | Not a fitness app |

## Components

### Content

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Charts | ✅ | Settings → Usage uses the Swift Charts framework with `BarMark`, `RuleMark`, axis formatting, and accessible marks |
| Image views | ✅ | Attachment thumbnails and image previews preserve aspect ratio |
| Text views | ✅ | Transcript uses selectable text with semantic styles |
| Web views | ✅ | MCP apps run in a nonpersistent WKWebView with a main-frame-only bridge, per-server tool scoping, iframe sandboxing without `allow-same-origin`, blocked file/data/javascript navigation, external opens only for http(s)/mailto, no downloads or automatic windows, user-action media, an AX label, and loading/error states. Live audit app proved bridge success, iframe isolation, and blocked file navigation |

### Layout and organization

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Boxes | ✅ | Cards and grouped surfaces follow system corner radii |
| Collections | ✅ | Native List provides selection, sections, and context menus; no custom collection layout is needed |
| Column views | ➖ | No browser-style column navigation |
| Disclosure controls | ✅ | Tool output and plan details expand/collapse |
| Labels | ✅ | Control labels and value labels are paired and readable |
| Lists and tables | ✅ | Sidebar task list with sections, selection, and context menus |
| Lockups | ✅ | App icon + name lockup on welcome and About |
| Outline views | ➖ | Session hierarchy is flat |
| Split views | ✅ | NavigationSplitView with sidebar, detail, and inspector |
| Tab views | ➖ | No tabbed content |

### Menus and actions

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Activity views | ➖ | No share sheet |
| Buttons | ✅ | Standard push buttons, destructive roles, ellipses for windows |
| Context menus | ✅ | Task rows and messages expose rename, fork, archive, delete, copy |
| Dock menus | ➖ | No custom Dock menu |
| Edit menus | ✅ | Standard Edit menu and text undo/copy/paste |
| Home Screen quick actions | ➖ | Not an iOS app |
| Menus | ✅ | Full menu bar with Task, Model, View, Window, Help |
| Ornaments | ➖ | visionOS-only |
| Pop-up buttons | ✅ | Pickers in settings and model menu |
| Pull-down buttons | ✅ | Toolbar and composer menus |
| The menu bar | ✅ | App, File, Edit, View, Task, Model, Window, Help menus; standard shortcuts |
| Toolbars | ✅ | Deliberate items, labels/tooltips, no custom background |

### Navigation and search

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Path controls | ➖ | No file-path navigation control |
| Search fields | ✅ | Placeholder scope, live filtering, clear button, Esc |
| Sidebars | ✅ | Group by date/project/none, toggle previews and message counts, show/hide archived, and hide/show the sidebar. Choices persist across launches and are available in both the sidebar menu and Settings |
| Tab bars | ➖ | No tab bar |
| Token fields | ➖ | No token entry control |

### Presentation

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Action sheets | ➖ | macOS uses alerts/confirmation dialogs |
| Alerts | ✅ | Errors, destructive delete confirmation, approval sheet |
| Page controls | ➖ | No pagination |
| Panels | ➖ | Inspector is an inline split-view column |
| Popovers | ✅ | Model picker uses a transient popover |
| Scroll views | ✅ | Transcript and settings use native scrolling |
| Sheets | ✅ | Approval, GitHub, diagnostics, help, quick switcher; one at a time |
| Windows | ✅ | Standard SwiftUI window, resizable, no custom frame |

### Selection and input

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Color wells | ➖ | No color picker |
| Combo boxes | ➖ | No combo box |
| Digit entry views | ➖ | No code/digit entry |
| Image wells | ➖ | Attachments use file picker/drop |
| Pickers | ✅ | Model, reasoning, speed, review event, GitHub base |
| Segmented controls | ➖ | No segmented control |
| Sliders | ✅ | Transcript size, GitHub line stepper adjacent |
| Steppers | ✅ | GitHub review line |
| Text fields | ✅ | Composer, task title, GitHub fields with labels/placeholders |
| Toggles | ✅ | Tool, notification, draft toggles with labels |
| Virtual keyboards | ➖ | macOS app |

### Status

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Activity rings | ➖ | Not a fitness app |
| Gauges | ➖ | No gauge |
| Progress indicators | ✅ | Run progress, context meter, index rebuild, tool states |
| Rating indicators | ➖ | No ratings |

### System experiences

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| App Shortcuts | ➖ | Not an iOS app |
| Complications | ➖ | watchOS-only |
| Controls | ➖ | No Control Center controls |
| Live Activities | ➖ | iOS-only |
| Notifications | ✅ | Optional completion notifications through Notification Manager |
| Snippets | ➖ | Not applicable |
| Status bars | ➖ | macOS window chrome |
| Top Shelf | ➖ | tvOS-only |
| Watch faces | ➖ | watchOS-only |
| Widgets | ➖ | No widget surface |

## Inputs

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Action button | ➖ | Hardware control not present on Mac |
| Apple Pencil and Scribble | ➖ | No pencil input |
| Camera Control | ➖ | iPhone hardware |
| Digital Crown | ➖ | watchOS hardware |
| Eyes | ➖ | visionOS input |
| Focus and selection | ✅ | Composer focus, search focus, keyboard task switching, native selection |
| Game controls | ➖ | No game input |
| Gestures | ✅ | Optional two-finger swipes (left/right/up/down) map to persisted actions: next/previous task, Quick Switch, toggle inspector, new task, stop, compact, or none. Option-key gate available; scrolling is never consumed; every action has a keyboard/menu equivalent. Verified live and with a 10-case recognizer harness |
| Gyroscope and accelerometer | ➖ | Not applicable |
| Keyboards | ✅ | Menu shortcuts, ⌘K, ⌘N, ⌘., ⌘[/], ⌘?, standard text editing |
| Nearby interactions | ➖ | No UWB/NFC interaction |
| Pointing devices | ✅ | Hover states, tooltips, context menus, 24–28 pt targets |
| Remotes | ➖ | tvOS input |

## Technologies

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| AirPlay | ➖ | No media output |
| Always On | ➖ | iPhone feature |
| App Clips | ➖ | iOS feature |
| Apple In-App Purchase | ➖ | No purchases |
| Apple Pay | ➖ | No payments |
| Augmented reality | ➖ | No AR |
| CareKit | ➖ | Not a health app |
| CarPlay | ➖ | Not a driving app |
| Game Center | ➖ | Not a game |
| Generative AI | ✅ | Model identity on replies, no persona, retry/edit/fork, approvals, local history, provider disclosure |
| HealthKit | ➖ | Not a health app |
| HomeKit | ➖ | No home automation |
| iCloud | ➖ | Local storage by decision |
| ID Verifier | ➖ | No identity verification |
| iMessage apps and stickers | ➖ | Not an iMessage app |
| Live Photos | ➖ | No photo library |
| Mac Catalyst | ➖ | Native AppKit/SwiftUI app |
| Machine learning | ✅ | On-device `NLEmbedding` word-vector index with a persisted per-file cache and a 20-query workspace regression set (20/20 top-1 vs 19/20 for sentence vectors). Measured 93 files/975 chunks: 7.3 s first-ever build, 0.6 s cached launch, 0.69 s after one file changed, 25 ms warm query, 65 MB peak; fallbacks, privacy, cache path, and limits documented in `SEMANTIC_INDEX.md` |
| Maps | ➖ | No maps |
| NFC | ➖ | No NFC |
| Photo editing | ➖ | Attachments are not edited |
| ResearchKit | ➖ | Not a research app |
| SharePlay | ➖ | No shared sessions |
| ShazamKit | ➖ | No audio recognition |
| Sign in with Apple | ➖ | Provider keys only |
| Siri | ➖ | No Siri intent |
| Tap to Pay on iPhone | ➖ | iPhone feature |
| VoiceOver | ✅ | VoiceOver enabled and Quickstart completed; VO cursor and AX tree captured across the main window, Settings, and a live MCP app. App-owned unlabeled controls: 0. Transcript messages now announce the speaker before their text. See `VOICEOVER_AUDIT.md` |
| Wallet | ➖ | No passes |

## Remaining partial items

