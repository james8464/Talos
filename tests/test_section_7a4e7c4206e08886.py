import unittest

from agent import MAX_LOOKUPS


class TestMaxLookups(unittest.TestCase):
    def test_max_lookups(self):
        self.assertEqual(MAX_LOOKUPS, 4)
