# Held-out evaluation

This document records checks that were written **after** the corresponding
implementation and were not used to choose ranking weights, translations, or
tool-call parsing rules. The point is to catch the difference between “the
authored regression set still passes” and “the feature actually works on cases
we did not pick.”

Reproduce everything below with:

```sh
make semantic-eval       # authored 20-query set
make semantic-heldout    # held-out 15-query set
make localization-check
make localization-coverage
make localization-eval   # live Arabic AX traversal
```

## Semantic retrieval

The held-out set lives in `apps/macos/Tools/SemanticEval/HeldOut/main.swift`.
It contains 15 queries written after the original 20-query set. Each entry names
the file that most directly implements the behaviour (`primary`) and every file
that implements a piece of it (`expected`). Multi-file features are allowed to
surface a caller first; `PRIMARY` measures how often the most specific file
wins.

| Run | Authored TOP1 | Held-out acceptable TOP1 | Held-out primary | Held-out TOP3 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| First held-out run | 20/20 | 10/15 | — | 14/15 | 0.778 |
| After ranking fixes | 20/20 | 15/15 | 12/15 | 15/15 | 1.000 |

The first run exposed four concrete ranking defects:

1. Results repeated the same file because the top-N list contained chunks, not
   files. Results are now collapsed to one entry per file using its best chunk.
2. A flat token count let ubiquitous words (`model`, `page`, `local`) outweigh
   rare identifiers. Chunk lexical scoring now blends token coverage with
   inverse document frequency at 75/25, and file-level coverage uses IDF.
3. Declaration names were ignored. `func install`, `struct ApprovalReviewer`,
   and similar declarations now contribute a small score term, which moved
   `Plugins.swift` above the Settings UI for “install a plugin bundle from a
   signed catalog”.
4. The support bonus for repeated chunk hits rewarded large files. It was
   removed in favour of file-level term coverage.

Two held-out queries were corrected because the original wording described
behaviour the repository does not have: PDF pages are converted to **text**, not
images, and message branching keeps a prefix rather than a “lineage”. The
expected files were verified independently by reading the implementation.

Residual primary misses are genuine ambiguity, not wrong answers:

- `prompt used by the read-only approval reviewer` → `AppModel.swift` (the
  `generateApprovalReview` caller) before `Reviewer.swift` (the prompt text).
- `confirm that a hook can run commands` → `AutomationSettings.swift` before
  `Hooks.swift`.
- `elicitation form fields and defaults` → `MCP.swift` before
  `MCPElicitationView.swift`.

All three alternatives are listed as acceptable and the accepted answer is
always in the top three.

### What this does not prove

The set is small, written by the same author as the implementation, and scored
with hand-written relevance sets. It is a regression guard, not an independent
benchmark. `NLEmbedding` is a general English word model, so identifier-heavy
queries remain weaker than exact `search_files` or LSP lookup.

## Tool-call parser unit tests

`apps/macos/ToolParser/ToolParser.swift` is a small static library
(`TalosToolParser`) consumed by the app. `apps/macos/Tests/ToolParserTests.swift`
is a host-free macOS unit-test target (`TalosTests`), so CI does not need to
launch the app to test parsing. It runs through `make test` (`make check` runs
the app build and the tests). Nine tests cover:

- DeepSeek DSML `invoke`/`parameter` tags, including `string="false"` values,
  numbers, booleans, and XML entities;
- multiple DSML invokes in one wrapper;
- `<tool>` and `<tool_call name="…">` XML wrappers;
- multiple calls of mixed syntax in textual order;
- missing-name inference for `read_file`, `search_files`, `shell`,
  `apply_patch`, `list_files`, `write_file`, and `update_plan`;
- malformed ```tool fences are stripped from the visible reply;
- illustrative ```json blocks are preserved, not executed;
- a ```json block with a `command` field but no `name` is **not** executed;
- plain text stays unchanged and `extract` returns nil.

```text
Executed 9 tests, with 0 failures
** TEST SUCCEEDED **
```

## Arabic localization

`make localization-coverage` is a held-out source scan: it walks every SwiftUI
call site (`Text`, `Button`, `Toggle`, `Picker`, `Section`, `LabeledContent`,
`TextField`, `Stepper`, `Menu`, `Link`, `navigationTitle`, `help`,
`accessibilityLabel`, alerts, and confirmation dialogs) and asserts that each
user-visible literal has an Arabic catalog entry. It excludes interpolation,
brand names, key caps, and technical tokens.

| Check | Before | After |
| --- | ---: | ---: |
| UI literals found | 486 | 618 |
| Missing Arabic | 131 | 0 |
| Catalog keys | 526 | 683 |

The 131 missing strings included the menu-bar menu, approval sheet, worktree
sheet, GitHub panel, MCP app/elicitation/sampling views, Chronicle controls,
recent documents, diagnostics panel, policy empty state, project browser, and
most accessibility labels added in the previous pass. All were translated and
added to `Localizable.xcstrings`.

The scan was then extended to wrapped and conditional forms. That found a
second class of bugs: strings that **had** Arabic catalog entries but rendered
English because SwiftUI treated the expression as `String` instead of
`LocalizedStringKey`. Examples:

- `HelpView` passed `String` parameters to `section(_:text:)`, and built its
  shortcut rows from raw tuple literals, so the whole Help sheet stayed English.
- `Text(isNew ? "New Profile" : "Edit Profile")` and similar conditional titles.
- `Text(branch ?? "No repository")`, `Text(selected?.path ?? "Diff")`.
- `.help(recording ? "Stop dictation" : "Dictate a message")`.

These now use `LocalizedStringKey(...)` or `String(localized:)`, and the source
scanner checks wrapped literals as well. The catalog grew to 683 keys.

Live traversal (`make localization-eval`) now launches with `AppleLanguages=ar`,
creates a task, opens five non-Settings surfaces, then opens Settings and visits
all 20 pages:

```text
MAIN arabic=175 english=0 unlabeled=0
SURFACE Quick Switcher arabic=188 english=0 unlabeled=0
SURFACE Help arabic=196 english=0 unlabeled=0
SURFACE Project Browser arabic=178 english=0 unlabeled=0
SURFACE Git Panel arabic=187 english=0 unlabeled=0
SURFACE Worktree Sheet arabic=181 english=0 unlabeled=0
20 Settings pages: english=0 unlabeled=0
FAILURES: 0
```

This is still not a native-speaker review. Provider names, model identifiers,
paths, user content, and technical tokens remain English by design.

## Parity scenarios against Codex

Only the Codex CLI was installed and authenticated on this machine
(`codex-cli 0.171.0`, ChatGPT login). Claude Code and Cursor CLIs were not
present, so this is a two-tool comparison, not a three-tool benchmark. The two
agents used different models, so the result measures the whole harness rather
than an isolated model.

Four tasks were run against the same repository with read-only tool access:

| # | Task | Talos | Codex |
| --- | --- | --- | --- |
| 1 | Find the function that blocks writes outside the workspace and cite file:line | ✅ `Tools.swift`, `ensureWriteAllowed`, line 1262 | ✅ same |
| 2 | Default app-server port and two files that start/document it | ✅ 8790, `AppServer.swift`, `AppServerSettings.swift` | ✅ 8790, `AppModel.swift`, README |
| 3 | `claim(worker:)` implementation and triggering HTTP route | ✅ `CloudTasks.swift`, `POST /v1/worker/claim` | ✅ same |
| 4 | Whether Talos adds a persona/system prompt, with the exact rule | ✅ `AGENTS.md`, “not a persona… Do not add a Talos system prompt” | ✅ same |
| | **Score** | **4/4** | **4/4** |

The first Talos run scored 3/4 and exposed a real tool-calling bug. The model
began with the documented fenced-JSON format, then switched to native DeepSeek
markup:

```text
<|DSML|tool_calls>
<|DSML|invoke name="read_file">
<|DSML|parameter name="path" string="true">…</|DSML|parameter>
</|DSML|invoke>
</|DSML|tool_calls>
```

Talos displayed that markup as the final answer. Fixes in
`ToolParser`/`AppModel`/`Subagent`:

- parse DeepSeek DSML `invoke` and `parameter` tags, including typed
  `string="false"` values and XML entities;
- parse `<tool>`, `<tool_call>`, and `<function_call>` wrappers;
- execute every tool call in one reply instead of only the first;
- infer a missing `name` from an unambiguous schema (`path` + line range →
  `read_file`, `patch` → `apply_patch`, `command` → `shell`, and so on);
- strip malformed tool fences from visible text;
- tell the model that `name` is mandatory and native markup is not accepted.

After the fix, the same four scenarios passed. The parser still cannot invent a
tool name when a model emits an unrecognisable payload; that remains a model
failure surfaced to the user rather than silently executed.

## Open evidence gaps

- No native-speaker Arabic review. The live traversal covers the main window,
  five panels/sheets, and all Settings pages, but not every dialog.
- Some display-only strings passed as `String` to `LabeledContent` values or
  custom helpers may still bypass localization; the source scan covers the
  known SwiftUI constructors, not arbitrary function parameters.
- No Claude Code or Cursor CLI comparison on this machine.
- Four parity tasks are too few to claim general behavioural parity.
- The semantic set is authored, not independently labelled.
