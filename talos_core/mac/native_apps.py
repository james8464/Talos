"""Native app adapters for the built-in macOS apps.

Preference order is: structured native data/API, then a safe CLI/AppleScript
call, then accessibility/computer use. Audio-producing actions are deliberately
not offered while Talos runs muted.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
SHORTCUTS = "/usr/bin/shortcuts"
MAX_REPLY = 595


class NativeAppError(RuntimeError):
    pass


def _run(argv, timeout=15):
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise NativeAppError(str(error)[:200]) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise NativeAppError(detail[:300] or f"native command exited {completed.returncode}")
    return completed.stdout.strip()


def _jxa(script, *args, timeout=15):
    output = _run([OSASCRIPT, "-l", "JavaScript", "-e", script, *map(str, args)], timeout=timeout)
    try:
        return json.loads(output or "null")
    except ValueError as error:
        raise NativeAppError("Native app returned an invalid response.") from error


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _export_rows(state, kind):
    path = Path(state) / "knowledge-imports" / kind / "native.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


_CALENDAR_JXA = r'''
function run(argv) {
  const start = new Date(argv[0]);
  const end = new Date(argv[1]);
  const limit = parseInt(argv[2], 10) || 20;
  const app = Application("Calendar");
  const out = [];
  app.calendars().forEach(function (calendar) {
    const events = calendar.events();
    const titles = events.summary();
    const starts = events.startDate();
    const ends = events.endDate();
    const locations = events.location();
    for (let i = 0; i < starts.length; i++) {
      const at = starts[i];
      if (at && at >= start && at < end) {
        out.push({
          title: titles[i] || "Untitled event",
          start: at.toISOString(),
          end: ends[i] ? ends[i].toISOString() : "",
          calendar: calendar.name(),
          location: locations[i] || ""
        });
      }
    }
  });
  out.sort(function (a, b) { return a.start.localeCompare(b.start); });
  return JSON.stringify(out.slice(0, limit));
}
'''


def calendar_events(state, *, days=1, limit=8):
    start = datetime.now(UTC)
    end = start + timedelta(days=days)
    try:
        rows = _jxa(_CALENDAR_JXA, start.isoformat(), end.isoformat(), str(limit), timeout=12)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    except NativeAppError:
        pass
    rows = []
    for row in _export_rows(state, "Calendar"):
        at = _parse(row.get("start"))
        if at and start <= at < end:
            rows.append(row)
    rows.sort(key=lambda row: str(row.get("start", "")))
    return rows[:limit]


def reminders(state, *, limit=10, include_completed=False):
    """Read pending reminders from the native EventKit export (stale-safe)."""
    rows = []
    for row in _export_rows(state, "Reminders"):
        if not include_completed and str(row.get("status", "")).casefold() == "completed":
            continue
        rows.append(row)
    rows.sort(key=lambda row: (not bool(row.get("due")), str(row.get("due", ""))))
    return rows[:limit]


def open_url(url):
    if not isinstance(url, str) or not re.match(r"^https?://", url.strip(), re.I):
        raise NativeAppError("Only http(s) URLs can be opened.")
    _run([OPEN, url.strip()], timeout=10)
    return f"Opened {url.strip()} in the default browser."


def search_safari(query):
    query = " ".join(str(query).split())
    if not query:
        raise NativeAppError("What should I search for?")
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    _run([OPEN, "-a", "Safari", url], timeout=10)
    return f"Searching Safari for {query}."


def shortcuts_list(limit=12):
    output = _run([SHORTCUTS, "list"], timeout=15)
    names = [line.strip() for line in output.splitlines() if line.strip()]
    return names[:limit]


def run_shortcut(name):
    name = " ".join(str(name).split())
    if not name or len(name) > 120:
        raise NativeAppError("Give one shortcut name.")
    _run([SHORTCUTS, "run", name], timeout=90)
    return f"Ran shortcut {name}."


def open_path(path):
    target = Path(str(path)).expanduser()
    if not target.exists():
        raise NativeAppError(f"Path does not exist: {target}")
    _run([OPEN, str(target)], timeout=10)
    return f"Opened {target}."


def maps_directions(query):
    query = " ".join(str(query).split())
    if not query:
        raise NativeAppError("Where should I navigate?")
    _run([OPEN, "maps://?q=" + urllib.parse.quote_plus(query)], timeout=10)
    return f"Opening Maps directions for {query}."


def music_state():
    script = r'''
function run() {
  try {
    const app = Application("Music");
    const track = app.currentTrack.name() || "";
    return JSON.stringify({state: app.playerState(), track: track});
  } catch (error) {
    return JSON.stringify({state: "stopped", track: ""});
  }
}
'''
    try:
        data = _jxa(script, timeout=8)
    except NativeAppError:
        return "Music is not running."
    if not isinstance(data, dict):
        return "Music state is unavailable."
    track = data.get("track") or ""
    return f"Music is {data.get('state', 'unknown')}." + (f" Playing {track}." if track else "")


def event_lines(rows):
    lines = []
    for row in rows:
        at = _parse(row.get("start"))
        if not at:
            continue
        when = at.astimezone().strftime("%a %d %b %H:%M")
        title = str(row.get("title") or "Untitled event")
        calendar = str(row.get("calendar") or "")
        lines.append(f"{when} {title}" + (f" ({calendar})" if calendar else ""))
    return lines


def reminder_lines(rows):
    lines = []
    for row in rows:
        due = _parse(row.get("due"))
        title = str(row.get("title") or "Untitled reminder")
        when = f" due {due.astimezone():%a %d %b %H:%M}" if due else ""
        lines.append(title + when)
    return lines


def command(text, state):
    """Handle native-app read/safe-action requests; return text or None."""
    if not isinstance(text, str):
        return None
    words = text.strip()
    calendar = re.fullmatch(
        r"(?:what(?:'s| is) on my calendar|show my calendar|my calendar)(?: (today|tomorrow))?[?.! ]*",
        words, re.I,
    )
    if calendar:
        days = 2 if (calendar.group(1) or "").casefold() == "tomorrow" else 1
        rows = calendar_events(state, days=days)
        lines = event_lines(rows)
        return "Calendar: " + "; ".join(lines)[:MAX_REPLY] if lines else "No events in that Calendar window."
    reminders_request = re.fullmatch(
        r"(?:(?:show|list)(?: me)? (?:my )?apple reminders|"
        r"what(?:'s| are) in (?:my )?apple reminders)[?.! ]*",
        words, re.I,
    )
    if reminders_request:
        rows = reminders(state)
        lines = reminder_lines(rows)
        return "Apple Reminders: " + "; ".join(lines)[:MAX_REPLY] if lines else "No pending Apple reminders."
    app_search = re.fullmatch(
        r"search (?:my |apple )?(notes|mail|messages|contacts) for (.+?)[?.!]*",
        words, re.I,
    )
    if app_search:
        from talos_core.memory import personal_index
        app = {"notes": "Notes", "mail": "Mail", "messages": "Messages",
               "contacts": "Contacts"}[app_search.group(1).casefold()]
        result = personal_index.search(
            state, Path.home() / "Library", app_search.group(2).strip(), app_filter=app,
        )
        return str(result.get("reply") or "No matching records.")[:MAX_REPLY]
    url = re.fullmatch(r"(?:open|visit|go to) (https?://\S+)[.!]?", words, re.I)
    if url:
        return open_url(url.group(1))
    safari = re.fullmatch(r"search (?:safari|the web in safari) for (.+?)[?.!]*", words, re.I)
    if safari:
        return search_safari(safari.group(1))
    if re.fullmatch(r"(?:list|show) (?:my )?shortcuts[?.! ]*", words, re.I):
        names = shortcuts_list()
        return "Shortcuts: " + ", ".join(names) if names else "No shortcuts are available."
    shortcut = re.fullmatch(r"run shortcut (.+?)[.!]?", words, re.I)
    if shortcut:
        return run_shortcut(shortcut.group(1))
    folders = {"downloads": "~/Downloads", "documents": "~/Documents", "desktop": "~/Desktop",
               "home": "~", "applications": "/Applications"}
    folder = re.fullmatch(r"open (?:my )?(downloads|documents|desktop|home|applications)[.!]?", words, re.I)
    if folder:
        return open_path(folders[folder.group(1).casefold()])
    path = re.fullmatch(r"open folder (.+?)[.!]?", words, re.I)
    if path:
        return open_path(path.group(1).strip())
    directions = re.fullmatch(r"(?:directions|navigate) to (.+?)[.!]?", words, re.I)
    if directions:
        return maps_directions(directions.group(1))
    if re.fullmatch(r"(?:what(?:'s| is) playing|music status)[?.! ]*", words, re.I):
        return music_state()
    return None
