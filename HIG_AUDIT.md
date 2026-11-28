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
- Added a confirmation dialog for irreversible task deletion.
- Resolved the duplicate ⌘K shortcut (Quick Switch vs. Choose Model), which now
  uses ⌘⇧M.
- Moved key text to semantic system styles instead of fixed point sizes.
- Enlarged icon-only hit targets to at least 24×24 pt, and 28×28 pt for
  send/stop.
- Added a Help menu and a shortcut/reference sheet (⌘?).
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
| Inclusion | Avoid assumptions and stereotypes | 🟡 | Plain, neutral language and no inferred user traits; no formal diverse-user study |
| Layout | Resizable layouts, comfortable density, margins | ✅ | NavigationSplitView, resizable window (min 900×600), 24–32 pt content margins, 760 pt transcript measure |
| Materials | Use materials purposefully and respect Reduce Transparency | ✅ | Materials used for chrome/status; solid fallback when Reduce Transparency is on |
| Motion | Purposeful, optional motion | ✅ | Motion limited to scroll/collapse/status; skipped under Reduce Motion |
| Privacy | Local-first data, explicit provider disclosure | ✅ | Sessions/index/usage local; keys in Keychain; provider privacy note in Inspector |
| Right to left | Leading/trailing layout, mirrored controls | 🟡 | All layouts use leading/trailing and system controls; no RTL screenshot test |
| SF Symbols | System symbols, correct variants | ✅ | No custom bitmaps for controls; symbol weights match surrounding text |
| Spatial layout | Spatial computing layout | ➖ | Not a visionOS app |
| Typography | System fonts, text styles, monospaced digits | ✅ | Semantic styles in key surfaces, monospaced tool output, monospaced digits for metrics |
| Writing | Clear labels, title case controls, ellipses for more input | ✅ | “New Worktree…”, “Rename…”, “Review Changes…” use ellipses; sentence-case help text |

## Patterns

| HIG page | Guidance checked | Status | Evidence / note |
| --- | --- | --- | --- |
| Charting data | Clear, labeled data visualization | 🟡 | Usage heatmap with labeled totals; not a full charting surface |
| Collaboration and sharing | Share/collaborate flows | ➖ | Single-user local app |
| Drag and drop | Support drag and drop plus an alternative | ✅ | Composer accepts dropped files; paperclip picker remains |
| Entering data | Clear fields, defaults, validation | ✅ | Composer, settings forms, labeled fields, inline errors |
| Feedback | Accessible status feedback, confirm significant actions | ✅ | Streaming bar, tool status labels, sidebar running indicator, delete confirmation |
| File management | Open/save/attach files predictably | 🟡 | Attachments, projects, and document creation; no recent-documents browser |
| Going full screen | Support standard full screen | ✅ | Standard resizable window and system full-screen control |
| Launching | Show content immediately, avoid startup alerts | ✅ | Welcome/empty states render immediately; errors are inline/alerts after actions |
| Live-viewing apps | Live media viewing | ➖ | Not a media app |
| Loading | Show progress and keep the app usable | ✅ | Streaming text, progress indicators, searchable index runs off the main actor |
| Managing accounts | Account management | ➖ | Provider keys only; no Talos account |
| Managing notifications | Ask before notifying, respect settings | ✅ | Notification toggle requests authorization; macOS notification center |
| Modality | One sheet at a time, clear dismissal | ✅ | One sheet per surface; approval sheet offers Deny plus Allow actions |
| Multitasking | Support multiple tasks and windows | 🟡 | Tasks switch from the sidebar and ⌘[/⌘]; File > New Window is replaced by New Task |
| Offering help | Provide discoverable help | ✅ | Help menu (⌘?) with a shortcut/concept sheet and Settings link |
| Onboarding | Teach in context, avoid forced tours | ✅ | Welcome screen with suggestions; no blocking onboarding |
| Playing audio | Audio feedback conventions | ➖ | Only an optional completion sound |
| Playing haptics | Haptic feedback | ➖ | Mac without haptic API use here |
| Playing video | Video playback conventions | ➖ | Not a video app |
| Printing | Printing flows | ➖ | No printing surface |
| Ratings and reviews | Rating prompts | ➖ | No ratings |
| Searching | Primary search, clear scope, live results | ✅ | Sidebar task search, ⌘K switcher, searchable Settings, live filtering |
| Settings | Standard Settings window, ⌘, minimal options | ✅ | Settings scene, searchable sidebar, defaults, follows system settings |
| Undo and redo | Standard undo; confirm irreversible actions | 🟡 | Text undo is standard and deletion is confirmed; no custom undo for task deletion |
| Workouts | Workout-specific patterns | ➖ | Not a fitness app |

## Components

### Content

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Charts | 🟡 | Usage heatmap is a custom grid, not the Charts framework |
| Image views | ✅ | Attachment thumbnails and image previews preserve aspect ratio |
| Text views | ✅ | Transcript uses selectable text with semantic styles |
| Web views | 🟡 | MCP apps use a sandboxed WKWebView with a tool bridge |

### Layout and organization

| HIG page | Status | Evidence / note |
| --- | --- | --- |
| Boxes | ✅ | Cards and grouped surfaces follow system corner radii |
| Collections | 🟡 | Sidebar uses native List rather than a custom collection |
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
| Sidebars | ✅ | Date groups, hide/show commands, native selection; no user customization (🟡 if required) |
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
| Gestures | 🟡 | Standard trackpad/window gestures work; no custom gesture surface |
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
| Machine learning | 🟡 | On-device sentence embeddings for semantic search; no further ML claims |
| Maps | ➖ | No maps |
| NFC | ➖ | No NFC |
| Photo editing | ➖ | Attachments are not edited |
| ResearchKit | ➖ | Not a research app |
| SharePlay | ➖ | No shared sessions |
| ShazamKit | ➖ | No audio recognition |
| Sign in with Apple | ➖ | Provider keys only |
| Siri | ➖ | No Siri intent |
| Tap to Pay on iPhone | ➖ | iPhone feature |
| VoiceOver | 🟡 | AX labels/roles audited with 0 unlabeled controls; no full spoken walkthrough |
| Wallet | ➖ | No passes |

## Remaining partial items

- Right-to-left layout has not been screenshot-tested.
- File > New Window is not exposed; tasks replace windows.
- Task deletion has confirmation but no custom undo.
- Sidebar content is grouped but not user-customizable.
- VoiceOver has structural labels but no full spoken walkthrough.
- No recent-documents browser.
- Charts are a custom heatmap rather than the Charts framework.
