import unittest

from talos_core.interfaces.brain_ui import BrainUI


class TestBrainUI(unittest.TestCase):
    def test_default_fps(self):
        ui = BrainUI()
        self.assertEqual(ui.fps, 12.0)
