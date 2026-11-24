import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.room import room_vision

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-shirt.jpg"


class CameraVisionTests(unittest.TestCase):
    def setUp(self):
        self.frame = base64.b64encode(FIXTURE.read_bytes()).decode()

    def test_visual_questions_are_routed_to_one_transient_frame(self):
        self.assertTrue(room_vision.visual_question("What am I wearing?"))
        self.assertTrue(room_vision.visual_question("Hey Talos, describe what you see"))
        self.assertFalse(room_vision.visual_question("What is on my calendar?"))

    def test_camera_frame_is_validated_and_never_stored(self):
        with patch.object(room_vision, "describe_image", return_value="A blue shirt.") as vision:
            answer = room_vision.answer("What am I wearing?", self.frame)
        self.assertEqual(answer, "A blue shirt.")
        vision.assert_called_once_with("What am I wearing?", self.frame)

    def test_multiple_people_and_away_frames_do_not_pretend_to_see(self):
        self.assertIn("more than one person", room_vision.answer("What am I wearing?", self.frame, 2))
        self.assertIn("can't see a person", room_vision.answer("What am I wearing?", self.frame, 0))

    def test_presence_questions_use_scene_counts_without_identity(self):
        self.assertTrue(room_vision.visual_question("Is anyone there?"))
        self.assertIn("empty", room_vision.answer("Is anyone there?", self.frame, 0))
        self.assertIn("one person", room_vision.answer("Is the room occupied?", self.frame, 1))
        self.assertIn("2 people", room_vision.answer("How many people are here?", self.frame, 2))
        with patch.object(room_vision, "_scene_people", return_value=1):
            self.assertIn("one person", room_vision.answer("Is anyone there?", self.frame))

    def test_bad_or_missing_frames_fail_closed(self):
        self.assertIn("fresh camera frame", room_vision.answer("What do you see?", None))
        with self.assertRaises(ValueError):
            room_vision.answer("What do you see?", base64.b64encode(b"not a jpeg").decode())


if __name__ == "__main__":
    unittest.main()
