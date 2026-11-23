import unittest

from talos_core.self_improvement.initiative import MAX_IDEAS


class TestInitiative(unittest.TestCase):
    def test_max_ideas(self):
        self.assertEqual(MAX_IDEAS, 6)
