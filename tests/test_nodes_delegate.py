import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from talos_core import delegate, nodes, subagents


class NodeDelegateTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_nodes_register_list_and_remove(self):
        nodes.register(self.state, "mac-mini", kind="remote", endpoint="ssh://mini")
        found = nodes.nodes(self.state)
        self.assertEqual(found[0]["name"], "mac-mini")
        self.assertTrue(nodes.remove(self.state, "mac-mini"))
        self.assertEqual(nodes.nodes(self.state), [])
        self.assertFalse(nodes.remove(self.state, "missing"))

    def test_delegate_requires_confirmation_and_builds_argv(self):
        folder = self.state / "subagents"
        folder.mkdir()
        (folder / "review.json").write_text(json.dumps({
            "name": "review", "command": ["/usr/bin/echo"], "enabled": True,
        }))
        with self.assertRaises(delegate.DelegateRefused):
            delegate.delegate(self.state, "review", "check")
        result = delegate.delegate(
            self.state, "review", "check", confirm=True,
            runner=lambda argv, **options: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["output"], "ok")

    def test_missing_subagent_is_refused(self):
        with self.assertRaises(ValueError):
            subagents.command_for(self.state, "missing", "task")


if __name__ == "__main__":
    unittest.main()
