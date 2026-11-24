import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from talos_core.mac import computer_use as cu
from talos_core.security import policy as policy_module
from talos_core.security.policy import (
    ALLOW,
    DEFAULT_PROFILE,
    DENY,
    NEEDS_CONFIRMATION,
    Policy,
    PolicyDenied,
    Profile,
)


class PolicyProfileTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state"

    def mode(self, name):
        return (self.state / name).stat().st_mode & 0o777

    def test_default_profile_is_read_only_and_denies_writes(self):
        loaded = Policy.load(self.state)
        self.assertEqual(loaded.profile, Profile.READ_ONLY)
        self.assertEqual(DEFAULT_PROFILE, Profile.READ_ONLY)
        self.assertEqual(Policy().profile, Profile.READ_ONLY)

        decision = loaded.approve("write", target=self.state / "note.txt")
        self.assertEqual(decision.status, DENY)
        self.assertTrue(decision.denied)
        self.assertFalse(decision.allowed)

        self.assertTrue(loaded.approve("read", target=self.state / "note.txt").allowed)

    def test_workspace_write_only_allows_inside_roots(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "workspace"
        policy = Policy.from_document(
            {"profile": "workspace-write", "roots": [str(root)]}, state=self.state
        )
        self.assertEqual(policy.profile, Profile.WORKSPACE_WRITE)

        inside = policy.approve("write", target=root / "notes" / "a.md")
        self.assertEqual(inside.status, ALLOW)
        outside = policy.approve("write", target=self.state / "outside.txt")
        self.assertEqual(outside.status, DENY)
        sibling = policy.approve("write", target=str(root) + "-sibling/file")
        self.assertEqual(sibling.status, DENY)
        self.assertEqual(policy.approve("write").status, DENY)

    def test_per_app_grant_persists_and_does_not_reprompt(self):
        policy = Policy.from_document({"profile": "read-only"}, state=self.state)
        first = policy.approve("external-command", app="OBS Studio",
                               command=["ffmpeg", "-i", "in.mov"])
        self.assertEqual(first.status, NEEDS_CONFIRMATION)

        policy.grant_app("OBS Studio", "external-command")
        self.assertEqual(self.mode("policy-grants.json"), 0o600)
        self.assertIn("obs studio", json.dumps(policy.grants()))

        reloaded = Policy.load(self.state)
        second = reloaded.approve("external-command", app="obs studio",
                                  command=["ffmpeg", "-i", "in.mov"])
        self.assertEqual(second.status, ALLOW)
        self.assertEqual(second.reason, "per-app grant")
        self.assertNotEqual(first.status, second.status)

    def test_destructive_actions_still_require_confirmation(self):
        policy = Policy.from_document({"profile": "full"}, state=self.state)
        pending = policy.approve("key", app="Safari", destructive=True)
        self.assertEqual(pending.status, NEEDS_CONFIRMATION)
        with self.assertRaises(PolicyDenied):
            policy.require("key", app="Safari", destructive=True)
        confirmed = policy.approve("key", app="Safari", destructive=True, confirm=True)
        self.assertEqual(confirmed.status, ALLOW)
        self.assertTrue(confirmed.confirmed)

        policy.grant_app("Safari", "key")
        still_pending = policy.approve("key", app="Safari", destructive=True)
        self.assertEqual(still_pending.status, NEEDS_CONFIRMATION)

    def test_destructive_computer_use_shortcut_still_confirms(self):
        policy = Policy.from_document({"profile": "full"}, state=self.state)
        computer = cu.ComputerUse(self.state, runner=lambda *a, **k: None,
                                  cursor=False, policy=policy)
        with self.assertRaises(cu.PolicyError):
            computer.press("cmd+q")
        with self.assertRaises(cu.PolicyError):
            computer.window("close", app="TextEdit")

    def test_unknown_or_unparseable_policy_fails_closed(self):
        unknown = Policy.from_document({"profile": "root"}, state=self.state)
        self.assertIsNotNone(unknown.error)
        self.assertEqual(unknown.approve("read").status, DENY)
        self.assertEqual(unknown.approve("write", target=self.state / "x").status, DENY)

        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "policy.json").write_text("{not json", encoding="utf-8")
        broken = Policy.load(self.state)
        self.assertIsNotNone(broken.error)
        self.assertEqual(broken.approve("read").status, DENY)
        self.assertEqual(broken.approve("run", command=["ffmpeg"]).status, DENY)

        unknown_key = Policy.from_document(
            {"profile": "workspace-write", "surprise": True}, state=self.state
        )
        self.assertIsNotNone(unknown_key.error)
        self.assertEqual(unknown_key.approve("read").status, DENY)

        bad_rules = Policy.from_document(
            {"profile": "workspace-write", "commands": {"allow": "ffmpeg"}}, state=self.state
        )
        self.assertIsNotNone(bad_rules.error)
        self.assertEqual(bad_rules.approve("read").status, DENY)

    def test_command_rules_deny_first_and_allow_without_prompt(self):
        policy = Policy.from_document(
            {
                "profile": "read-only",
                "commands": {"allow": ["ffmpeg"], "deny": ["rm", "sudo"]},
            },
            state=self.state,
        )
        self.assertEqual(
            policy.approve("external-command", command=["ffmpeg", "-i", "x"], confirm=True).status,
            ALLOW,
        )
        self.assertEqual(policy.approve("run", command=["ffmpeg", "-i", "x"]).status, ALLOW)
        denied = policy.approve("run", command=["rm", "-rf", "x"], confirm=True)
        self.assertEqual(denied.status, DENY)
        self.assertEqual(denied.reason, "command matches the deny list")

    def test_audit_record_is_content_free(self):
        policy = Policy.from_document({"profile": "read-only"}, state=self.state)
        secret = "extremely secret prompt text"
        decision = policy.approve(
            "external-command",
            app="OBS Studio",
            command=["ffmpeg", "-i", secret],
            payload=secret,
            target=self.state / "clip.mp4",
        )
        serialized = json.dumps(decision.audit, sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertEqual(decision.audit["payload_chars"], len(secret))
        self.assertEqual(
            decision.audit["payload_sha256"], hashlib.sha256(secret.encode()).hexdigest()
        )
        self.assertEqual(decision.audit["command"], "ffmpeg")
        self.assertNotIn(secret, decision.audit["command_sha256"])

        log_text = (self.state / "policy-audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, log_text)
        self.assertNotIn("extremely", log_text)
        self.assertEqual(self.mode("policy-audit.jsonl"), 0o600)
        self.assertIn("needs-confirmation", log_text)

    def test_policy_module_exposes_named_profiles(self):
        self.assertEqual(set(policy_module.Profile), {
            Profile.READ_ONLY, Profile.WORKSPACE_WRITE, Profile.FULL,
        })
        self.assertEqual([str(item) for item in policy_module.Profile],
                         ["read-only", "workspace-write", "full"])


if __name__ == "__main__":
    unittest.main()
