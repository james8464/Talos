"""Tests for native Calendar/Reminders and safe app dispatch."""

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from talos_core.mac import native_apps


class NativeAppsTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.now = datetime.now(UTC)

    def write_export(self, kind, rows):
        folder = self.state / "knowledge-imports" / kind
        folder.mkdir(parents=True)
        (folder / "native.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8",
        )

    def test_calendar_uses_export_when_jxa_is_unavailable(self):
        future = (self.now + timedelta(days=1)).isoformat()
        past = (self.now - timedelta(days=1)).isoformat()
        self.write_export("Calendar", [
            {"title": "Future standup", "start": future, "calendar": "Work"},
            {"title": "Old event", "start": past, "calendar": "Home"},
        ])
        with patch.object(native_apps, "_jxa", side_effect=native_apps.NativeAppError("no access")):
            rows = native_apps.calendar_events(self.state, days=2)
        self.assertEqual([row["title"] for row in rows], ["Future standup"])

    def test_reminders_export_filters_completed(self):
        self.write_export("Reminders", [
            {"title": "Open task", "status": "pending", "due": "", "list": "Talos"},
            {"title": "Finished task", "status": "completed", "due": "", "list": "Talos"},
        ])
        rows = native_apps.reminders(self.state)
        self.assertEqual([row["title"] for row in rows], ["Open task"])
        self.assertIn("Open task", native_apps.command("Show my Apple reminders.", self.state))

    def test_command_routes_safe_native_actions(self):
        with patch.object(native_apps, "calendar_events", return_value=[
                {"title": "Standup", "start": self.now.isoformat(), "calendar": "Work"}]):
            self.assertIn("Standup", native_apps.command("What's on my calendar?", self.state))
        with patch.object(native_apps, "open_url", return_value="Opened URL.") as opened:
            self.assertEqual(native_apps.command("open https://example.com", self.state), "Opened URL.")
            opened.assert_called_once_with("https://example.com")
        with patch.object(native_apps, "shortcuts_list", return_value=["Backup"]):
            self.assertIn("Backup", native_apps.command("List my shortcuts.", self.state))
        with patch.object(native_apps, "maps_directions", return_value="Opening Maps.") as maps:
            self.assertEqual(native_apps.command("directions to Paris", self.state), "Opening Maps.")
            maps.assert_called_once_with("Paris")
        with patch.object(native_apps, "music_state", return_value="Music is stopped."):
            self.assertEqual(native_apps.command("What's playing?", self.state), "Music is stopped.")

    def test_read_only_app_search_uses_existing_index(self):
        with patch("talos_core.memory.personal_index.search", return_value={"reply": "Notes hit"}) as search:
            reply = native_apps.command("Search my notes for syllabus.", self.state)
        self.assertEqual(reply, "Notes hit")
        self.assertEqual(search.call_args.kwargs["app_filter"], "Notes")

    def test_only_http_urls_are_opened(self):
        with self.assertRaises(native_apps.NativeAppError):
            native_apps.open_url("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
