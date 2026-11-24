"""Declared upstream-to-local capability mappings.

This module is the only place where an upstream OpenClaw or Hermes capability
may be equated with a Talos capability. The audit does not infer equivalence
from shared words: an upstream name without an entry here is reported as an
unmapped gap, even when its words appear in local code or tests.

``Mapping.local`` is a capability identifier in the form ``kind:name`` exactly
as produced by :func:`talos_core.self_improvement.capability_audit.local_capabilities`.
Coverage additionally requires verified test evidence for that local
capability. ``Mapping.evidence`` can name a test explicitly as
``tests/test_file.py::test_name`` (or ``tests/test_file.py::Class::test_name``)
when the test name does not contain the capability identifier.

``LOCAL_EVIDENCE`` is the explicit escape hatch for local capabilities whose
behavior is covered by a test that does not name them. Every reference is
validated by parsing the test file; a stale or invented reference is treated as
missing evidence rather than coverage.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mapping:
    local: str
    note: str = ""
    evidence: str = ""


def _same_named_cli(names: tuple[str, ...]) -> dict[str, Mapping]:
    """Declare Hermes subcommands whose Talos counterpart has the same name."""
    return {
        f"cli {name}": Mapping(f"cli:{name}", note="same-named CLI subcommand")
        for name in names
    }


# Hermes subcommands whose names match a Talos CLI command exactly. A shared
# name is an explicit declaration here; it is not discovered by overlap.
_HERMES_SAME_NAMED_CLI = (
    "acp", "approvals", "auth", "backup", "bundles", "checkpoints", "clipboard",
    "codex_runtime", "completion", "config", "connectors", "console", "curator",
    "debug", "doctor", "dump", "egress", "fallback", "gui", "hooks", "import_cmd",
    "insights", "journey", "login", "logout", "logs", "mcp", "memory", "migrate",
    "moa", "model", "monitoring", "pairing", "peer", "pets", "plugins", "profile",
    "prompt_size", "secrets", "security", "setup", "skills", "skin", "slack",
    "status", "tools", "uninstall", "update", "usage", "vault", "verify", "webapp",
    "webhook", "whatsapp", "worktree",
)

UPSTREAM_MANIFEST: dict[str, dict[str, Mapping]] = {
    "OpenClaw": {
        "github-publish": Mapping("feature:github_publish"),
        "music-generate": Mapping("feature:music_generate"),
        "nodes": Mapping("feature:nodes"),
        "pdf": Mapping("feature:pdf"),
        "personal-instructions": Mapping("feature:personal_instructions"),
        "plugins": Mapping("feature:plugins"),
        "portal": Mapping("feature:portal"),
        "secrets": Mapping("feature:secrets"),
        "skill-workshop": Mapping("feature:skill_workshop"),
        "subagents": Mapping("feature:subagents"),
        "terminal": Mapping("cli:terminal"),
        "theme": Mapping("feature:theme"),
        "video-generate": Mapping("feature:video_generate"),
    },
    "Hermes": {
        "computer_use": Mapping("cli:computer", note="local computer command"),
        "delegate": Mapping("feature:delegate"),
        "discord": Mapping("cli:discord"),
        "feishu_doc": Mapping("cli:feishu_doc"),
        "feishu_drive": Mapping("cli:feishu_drive"),
        "focus_pane": Mapping("cli:focus_pane"),
        "mcp": Mapping("cli:mcp"),
        "memory": Mapping("cli:memory"),
        "set_theme": Mapping("feature:theme"),
        "skill_manager": Mapping("cli:skills", note="local skills command manages the catalog"),
        "skills": Mapping("cli:skills"),
        "terminal": Mapping("cli:terminal"),
        "tip": Mapping("cli:tip"),
        "todo": Mapping("cli:todo"),
        "tour": Mapping("cli:tour"),
        "video_generation": Mapping("feature:video_generate"),
        **_same_named_cli(_HERMES_SAME_NAMED_CLI),
    },
}

# Local capability identifier -> explicit test reference. Empty by default:
# add an entry only after confirming that the named test actually exercises the
# behavior. The audit verifies the reference exists before counting it.
LOCAL_EVIDENCE: dict[str, str] = {
    "feature:browser": "tests/test_browser.py::BrowserTests::test_snapshot_reports_url_title_text_and_elements",
    "feature:browser_actions": "tests/test_browser_actions.py::BrowserActionsTests::test_reads_and_summarizes_a_page",
    "feature:design_studio": (
        "tests/test_design_studio.py::DesignStudioTests::"
        "test_command_routes_document_and_blender_render"
    ),
    "feature:policy": (
        "tests/test_policy_profiles.py::PolicyProfileTests::"
        "test_destructive_actions_still_require_confirmation"
    ),
    "feature:pages": "tests/test_pages.py::PagesTests::test_create_saves_a_private_document",
    "feature:gui_apps": (
        "tests/test_gui_apps.py::GuiAppManagerTests::test_start_status_log_and_stop"
    ),
    "feature:gui_actions": (
        "tests/test_gui_actions.py::GuiActionTests::"
        "test_ocr_fallback_clicks_a_custom_drawn_control"
    ),
    "feature:room_client": (
        "tests/test_room_client.py::RoomClientTests::"
        "test_turn_sends_the_question_and_returns_the_payload"
    ),
}
