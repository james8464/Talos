import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from talos_core import github, portal, skills, subagents


class RemainingUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_skill_workshop_validates_creates_and_discovers(self):
        path = skills.create(self.state, "Release helper", "Checks release notes before publishing.",
                             "Read CHANGELOG.md and verify the version.")
        self.assertTrue(path.is_file())
        found = skills.discover(self.state)
        self.assertEqual(found[0]["name"], "Release helper")
        with self.assertRaises(ValueError):
            skills.validate("---\nname: bad!\ndescription: x\n---\n")

    def test_subagent_registry_builds_safe_argv(self):
        folder = self.state / "subagents"
        folder.mkdir()
        (folder / "review.json").write_text(json.dumps({
            "name": "review", "command": ["/usr/bin/echo", "--review"], "enabled": True,
        }))
        self.assertTrue(subagents.discover(self.state))
        self.assertEqual(subagents.command_for(self.state, "review", "check this"),
                         ["/usr/bin/echo", "--review", "check this"])
        with self.assertRaises(ValueError):
            subagents.command_for(self.state, "missing", "x")

    def test_portal_escapes_status(self):
        page = portal.render({"state": "<script>alert(1)</script>"})
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_github_publish_status_and_confirmation(self):
        def runner(argv, **options):
            text = " ".join(argv)
            if "status --porcelain" in text:
                return SimpleNamespace(returncode=0, stdout=" M agent.py\n", stderr="")
            if "get-url" in text:
                return SimpleNamespace(returncode=0, stdout="git@example.test:talos.git\n", stderr="")
            if "rev-parse" in text:
                return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        value = github.status(self.state, runner=runner)
        self.assertEqual(value["branch"], "main")
        self.assertTrue(value["dirty"])
        with self.assertRaises(github.PublishRefused):
            github.publish(self.state, "test publish", runner=runner)
        self.assertTrue(github.publish(self.state, "test publish", confirm=True, runner=runner)["published"])


if __name__ == "__main__":
    unittest.main()
