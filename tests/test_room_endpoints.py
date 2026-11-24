import tempfile
import unittest
from pathlib import Path

from talos_core.room.room_service import Room


class RoomEndpointTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.room = Room(self.state / "room.sqlite", "fixture", online=False, apply=False,
                         model_state=lambda: "warming")

    def test_context_activity_and_sources_are_readable(self):
        status, activity = self.room.route("GET", "/v1/context/activity", {})
        self.assertEqual(status, 200)
        self.assertIn("report", activity)
        status, sources = self.room.route("GET", "/v1/context/sources", {})
        self.assertEqual(status, 200)
        self.assertIn("available_apps", sources)
        self.assertIn("sources", sources)

    def test_shadow_mode_refuses_writes_and_goal_changes(self):
        for path, body in (
            ("/v1/context/apps", {"apps": []}),
            ("/v1/visitors/enroll", {}),
            ("/v1/visitors/forget-reference", {"id": "x"}),
            ("/v1/goals/save", {}),
            ("/v1/goals/calendar", {}),
            ("/v1/goals/pause", {}),
            ("/v1/goals/resume", {}),
            ("/v1/goals/check", {}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.room.route("POST", path, body)[0], 403)

    def test_meeting_brief_and_voice_endpoint_answer_without_services(self):
        status, brief = self.room.route("POST", "/v1/meeting/brief", {"topic": "Project Atlas"})
        self.assertEqual(status, 200)
        self.assertIn("reply", brief)
        status, endpoint = self.room.route("POST", "/v1/voice/endpoint", {"text": "hello"})
        self.assertEqual(status, 200)
        self.assertFalse(endpoint["available"])

    def test_room_activity_acknowledges_foreground_work(self):
        status, body = self.room.route("POST", "/v1/room/activity", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["active"])

    def test_work_apply_requires_a_known_plan(self):
        with self.assertRaises(ValueError):
            self.room.route("POST", "/v1/work/apply", {})
        status, missing = self.room.route("POST", "/v1/work/apply", {"id": "missing"})
        self.assertEqual(status, 404)
        self.assertIn("error", missing)


if __name__ == "__main__":
    unittest.main()
