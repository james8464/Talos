import unittest

from talos_core.self_improvement.decomposition import MAX_NODES


class TestMaxNodes(unittest.TestCase):
    def test_max_nodes(self):
        self.assertEqual(MAX_NODES, 96)
