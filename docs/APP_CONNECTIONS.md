# App connections: MCP, native APIs, and computer use

Talos should use the most reliable path first:

1. **Structured native API / local data** — EventKit, JXA/AppleScript dictionaries,
   SQLite records, `shortcuts` CLI, URL schemes.
2. **A configured MCP server** — especially official hosted servers for Slack,
   Notion, GitHub and Google Workspace, or a trusted local EventKit server for
   Apple apps.
3. **Accessibility/computer use** — AX tree, PID-targeted clicks/keys, screenshots
   and OCR for apps with no usable API or MCP server.

The last path is the Codex-style fallback, not the default. It is slower and
requires Accessibility + Screen Recording, but it works across arbitrary apps.

## Matrix

| App | MCP status | Talos native path | Fallback |
| --- | --- | --- | --- |
| Reminders | No official Apple MCP. Community EventKit servers exist. | `native_apps.py` reads the native EventKit export read-only; use a configured MCP server for writes. | Accessibility/computer use with confirmation |
| Calendar | No official Apple MCP. Community EventKit servers (`redpop/apple-calendar-mcp`, `FradSer/mcp-server-apple-events`, `orshemtov/apple-calendar-mcp`) exist. | Live JXA read; EventKit export fallback. | Accessibility/computer use |
| Notes | No official Apple MCP; community servers (`macos-apps-mcp`, `cupertino`, Apple Notes MCP). | Existing JXA Notes importer; native search/create is the next step. | Accessibility/computer use |
| Mail | No official Apple MCP; community servers. | Existing read-only Envelope Index/`mac_context`; compose/send requires Automation/Accessibility approval. | Accessibility/computer use |
| Messages | No official Apple MCP; community servers. | Existing read-only `chat.db`/`mac_context`; sending requires Automation. | Accessibility/computer use |
| Contacts | Community servers. | Existing Contacts database reader (`mac_context`). | Accessibility/computer use |
| Safari | No app MCP. Web content can use Playwright/CDP or an MCP browser server. | `native_apps.py` opens URLs and searches in Safari; AX for app UI. | Accessibility/computer use |
| Finder | No built-in MCP. | `open` CLI for folders/paths. | Accessibility/computer use |
| Shortcuts | No MCP needed. | `/usr/bin/shortcuts list` and `shortcuts run`. | Accessibility |
| Maps | No official MCP. | `maps://` URL scheme. | Accessibility |
| Music | No official MCP. | AppleScript/JXA state read. Playback is disabled while Talos is muted. | Accessibility |
| System Settings | No MCP. | None safe. | Accessibility/computer use with explicit confirmation |
| Obsidian | Community MCP/plugin; local REST API possible. | Vault files + existing graph/index. | Accessibility/computer use |
| Slack | Official hosted MCP: `https://mcp.slack.com/mcp` (OAuth, admin approval). | Webhook connector today. | Accessibility/computer use |
| Discord | Webhooks; community MCP servers. | Webhook connector today. | Accessibility/computer use |
| Notion | Official MCP: `https://mcp.notion.com/mcp`. | None yet. | Browser/accessibility |
| GitHub | Official hosted/local MCP server. | Git CLI + existing repository tooling. | Browser/accessibility |
| Google Workspace | Official Developer Preview MCP endpoints for Gmail, Calendar, Drive, etc. OAuth required. | None yet. | Browser/accessibility |
| Feishu | No general MCP assumed. | Existing authenticated API client. | Browser/accessibility |

## Relevant MCP servers found

- **Apple Reminders/Calendar:** `FradSer/mcp-server-apple-events` (EventKit,
  reminders + calendar), `redpop/apple-calendar-mcp` (EventKit, free-time
  search), `orshemtov/apple-reminders-mcp`, `orshemtov/apple-calendar-mcp`,
  `xiaofeng-han/apple-mcp` (Notes/Reminders/Journal/Contacts), and
  `elfensky/macos-apps-mcp` (Mail, Calendar, Reminders, Notes, Messages,
  Contacts, Photos, Safari, Music, Shortcuts).
- **Unified Mac app suites:** `mgcrea/cupertino` and `elfensky/macos-apps-mcp`
  are broad local servers with per-app permissions and gated writes.
- **Hosted official servers:** Slack (`https://mcp.slack.com/mcp`), Notion
  (`https://mcp.notion.com/mcp`), GitHub (`https://api.githubcopilot.com/mcp/`),
  and Google Workspace product endpoints such as
  `https://calendarmcp.googleapis.com/mcp/v1` and
  `https://gmailmcp.googleapis.com/mcp/v1` (Developer Preview, OAuth).
- **Computer-use servers:** `cua-rs-mcp` uses the accessibility tree and
  PID-targeted input rather than moving the physical pointer. Codex’s macOS
  computer use is built on the AX tree with background cursors and per-app
  approval.

## Permissions

- EventKit data needs Calendar and/or Reminders access for the binary that
  launches the process.
- AppleScript/JXA control needs Automation access per target app.
- AX control needs Accessibility; screenshots/OCR also need Screen Recording.
- MCP grants are per host process; hosted servers use OAuth and may require
  workspace-admin approval.
- Talos should never widen permissions on its own, and destructive app actions
  (send, delete, quit, change settings) should require explicit confirmation.

## Current Talos coverage

Implemented or already present:

- `talos_core/mac/native_apps.py`: Calendar (live JXA + EventKit export
  fallback), Reminders (EventKit export), Safari URL/search, Shortcuts,
  Finder, Maps, read-only Music state, and read-only Notes/Mail/Messages/
  Contacts search through the existing private index.
- `talos_core/mac/native_apps.py` also reports app status: Accessibility,
  Screen Recording, EventKit exports, private sources and configured MCP servers.
- `talos_core/mac/mac_context.py`: Contacts, Messages, Mail and WhatsApp reads.
- `talos_core/mac/computer_use.py` / `perception.py`: AX tree, PID-targeted
  clicks/keys, screenshots, OCR, windows and background control.
- Slack/Discord webhooks, Feishu client, ACP client, MCP server.
- `talos_core/mcp_client.py`: generic MCP stdio client on the official SDK,
  with `talos mcp-client list|add|tools|call` and room commands for listing
  servers/tools and calling a tool; tested against Talos's own server.
- Native Swift app EventKit import for Calendar/Reminders memory.

Next implementation steps:

1. Configure an existing reminders/calendar/notes MCP server through
   `talos mcp-client`; expose only read tools by default and require explicit
   confirmation for writes.
2. Add Notes/Mail/Messages actions by reusing established MCP servers or their
   native APIs, not new per-app protocols.
3. Add HTTP MCP transports and a per-server read-only policy.
4. Add a permission/health command that reports EventKit, Automation, AX, Screen
   Recording and configured MCP servers per app.
5. Keep accessibility/computer use as the fallback for apps without APIs or MCP.

## Sources

- Slack MCP guide: https://slack.com/help/articles/48855576908307-Guide-to-MCP-in-Slack
- Notion MCP: https://developers.notion.com/guides/mcp/get-started-with-mcp
- GitHub MCP: https://github.com/github/github-mcp-server
- Google MCP: https://github.com/google/mcp
- Apple Events MCP: https://github.com/FradSer/mcp-server-apple-events
- Apple Calendar MCP: https://github.com/redpop/apple-calendar-mcp
- macOS apps MCP: https://github.com/elfensky/macos-apps-mcp
- Cupertino: https://github.com/mgcrea/cupertino
- Computer-use server: https://github.com/maestrojeong/cua-rs-mcp
- Codex computer use: https://learn.chatgpt.com/docs/computer-use
- Codex AX background control analysis: https://www.macstories.net/notes/openais-new-codex-app-has-the-best-computer-use-feature-ive-ever-tested/

## Registering an existing stdio MCP server

Install the optional MCP client dependency once:

```sh
uv pip install --python .venv/bin/python 'mcp>=1.0'
```

Register a server without writing any adapter code:

```sh
# Example: an existing Apple Calendar MCP server
talos mcp-client add apple-calendar --command npx \
  --server-args '["-y","@redpop/apple-calendar-mcp"]'

# Example: an existing Apple Reminders/Calendar EventKit server
talos mcp-client add apple-events --command npx \
  --server-args '["-y","mcp-server-apple-events"]'

talos mcp-client tools apple-calendar
```

In the room:

```text
List MCP servers
List MCP tools on apple-calendar
Call mcp tool list_events on apple-calendar with {"start":"2026-11-25","end":"2026-11-26"}
```

Use each server's own permission prompts and documentation. Start with read
tools; only enable write tools after deciding how the server confirms or gates
changes. HTTP/remote MCP transports are not wired into this client yet; the
current client is stdio-only, which covers the local Apple app servers.
