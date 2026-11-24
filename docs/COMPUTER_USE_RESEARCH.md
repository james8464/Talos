# Computer-use research and decisions

Research date: 24 November 2026. The goal was to reuse existing macOS control
code and avoid reimplementing input, cursor, and status UI.

## What the research found

1. **Accessibility first.** The reliable macOS pattern is AX actions
   (`AXPress`, `AXValue`) for controls that expose them, then
   activate-then-control for custom-drawn apps. Background mouse injection is
   hit-tested against the window under the pointer; `CGEventPostToPid` queues
   events but AppKit can still ignore them unless the target is active.
   - <https://stackoverflow.com/questions/42657655/how-to-send-mouse-click-event-to-a-window-in-mac-osx>
   - <https://github.com/aislopware/slop-desk/blob/main/docs/05-input-window-control.md>
2. **HID clicks need click state.** Synthetic clicks must set
   `kCGMouseEventClickState` (1 for a single click, 2 for double) or some apps
   ignore the click.
   - <https://stackoverflow.com/questions/2369806/simulating-mouse-clicks-on-mac-os-x-does-not-work-for-some-applications>
3. **`cliclick` is the established CLI.** It uses `CGEventCreateMouseEvent`,
   but it is another Homebrew dependency. Talos already has an installed
   harness with the same click-state implementation, so no new tool was added.
   - <https://github.com/BlueM/cliclick/>
4. **Agent cursor + status chip is the common pattern.** Existing open-source
   computer-use servers all draw a separate agent pointer and an “agent
   working/controlling” indicator: Mac Computer Use, Munim Computer Use,
   cua-rs, Computer Use MCP, Open Computer Use, and dcc-cua.
   - <https://github.com/iamngoni/mac-computer-use>
   - <https://github.com/munimtechnologies/munim-computer-use>
   - <https://github.com/maestrojeong/cua-rs-mcp>
   - <https://github.com/minghinmatthewlam/computer-use-mcp>
   - <https://github.com/andrewslabmd/opencomputeruse>
   - <https://github.com/dcc-mcp/dcc-cua>
5. **The installed `computer_harness` already does the right HID click.**
   `engine.click` moves the pointer and sets the click-state field; the same
   package provides `post_click_to_pid` and `move_mouse`.

## Decisions

- Keep AX first (`perception.choose`/`click_ref`).
- Fall back to OCR-visible text when a control has no AX node.
- Use `computer_harness.engine.click` (then `post_click_to_pid`) for coordinate
  clicks; never add `cliclick` as a required dependency.
- Keep the native Swift cursor overlay: agent pointer, click ripple, cyan
  control border, and “TALOS IS CONTROLLING THIS MAC” pill. It is click-through,
  focus-safe, and persists for a whole `ComputerUseLoop` session.
- Keep Pages on its AppleScript model and `.docx` via `textutil` only as the
  fast fallback.

## Verified outcomes

- Tk fixture: AX had no button; OCR found “Start check”; the harness HID click
  changed the label to “started”.
- Calculator: intent click selected “7” and the display showed 7; All Clear
  returned it to 0.
- Pages: AppleScript created a real `.pages` file and opened it with Pages
  frontmost.
- No audio process ran during any of these checks.
