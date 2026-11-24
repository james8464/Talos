import tempfile
import unittest
from pathlib import Path

from talos_core import todo


class TodoTests(unittest.TestCase):
    def test_add_list_complete(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        first = todo.add(state, "Check the build")
        self.assertEqual(first["id"], 1)
        self.assertEqual(len(todo.items(state)), 1)
        self.assertTrue(todo.complete(state, 1))
        self.assertTrue(todo.items(state)[0]["done"])
        self.assertFalse(todo.complete(state, 99))
        with self.assertRaises(ValueError):
            todo.add(state, "   ")

    def test_file_is_private(self):
        state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        todo.add(state, "x")
        self.assertEqual((state / todo.FILE).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
