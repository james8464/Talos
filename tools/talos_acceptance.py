"""End-to-end acceptance pass against a muted Talos room.

Asks Talos to perform real tasks through its HTTP-facing Room API. No speech is
synthesized or played: the room is constructed with ``speak=False, voice=None``
and the voice endpoint must return 503.

Run:
    python3 -m tools.talos_acceptance
    python3 -m tools.talos_acceptance --model qwen2.5:1.5b --json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(model: str) -> dict:
    from talos_core.memory.agent_memory import open_memory
    from talos_core.room.room_service import Room

    checks = []

    def check(name, ok, detail="", **extra):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], **extra})

    with tempfile.TemporaryDirectory(prefix="talos-acceptance-") as folder:
        state = Path(folder)
        room = Room(state / "room.sqlite", model, apply=True, speak=False,
                    voice=None, online=False, model_state=lambda: "ready")

        status, reply = room.route("POST", "/v1/turn", {"text": "Hello Talos, what can you do?", "source": "text"})
        check("chat", status == 200 and bool(reply.get("reply")), reply.get("reply", ""))
        check("chat_is_text_only", "wav" not in reply)

        status, reminder = room.route("POST", "/v1/turn", {
            "text": "Remind me in five minutes to stretch", "source": "text"})
        check("reminder", status == 200 and reminder.get("action") == "applied",
              json.dumps(reminder)[:300])

        status, inbox = room.route("GET", "/v1/inbox", {})
        check("reminder_persisted", status == 200 and len(inbox.get("items", [])) >= 1,
              inbox.get("items", []))

        status, voice = room.route("POST", "/v1/voice", {"text": "This must never play."})
        check("audio_disabled", status == 503, voice)

        status, _memory = room.route("POST", "/v1/turn", {
            "text": "Remember that my workshop code is Gamma-9-Delta", "source": "text"})
        _status2, recall = room.route("POST", "/v1/turn", {
            "text": "What is my workshop code?", "source": "text"})
        check("personal_fact", status == 200 and "Gamma-9-Delta" in json.dumps(recall),
              recall.get("reply", ""))

        with open_memory(state / "room.sqlite") as db:
            from talos_core.cognition import cognitive_engine
            status, brief = room.route("GET", "/v1/cognitive/brief", {})
            check("knowledge_brief", status == 200 and "headline" in brief, brief.get("headline", ""))
            status, search = room.route("POST", "/v1/cognitive/search", {"query": "workshop code"})
            check("knowledge_search", status == 200 and bool(search.get("results")), len(search.get("results", [])))
            check("cognitive_status", "code_graph" in cognitive_engine.knowledge_status(db, state)["stores"])

        from uuid import uuid4
        status, generated = room.route("POST", "/v1/goals/save", {
            "id": str(uuid4()), "outcome": "Acceptance goal", "topic": "acceptance",
            "deadline": "2030-01-01T00:00:00+00:00"})
        check("goal_save", status == 200 and generated.get("id"), generated)

        status, health = room.route("GET", "/health", {})
        check("health_endpoint", status == 200 and health.get("service") == "talos", health)

        from talos_core import nodes, skills, todo
        check("todo", todo.add(state, "Run acceptance").get("text") == "Run acceptance")
        skill = skills.create(state, "Acceptance skill", "Runs the local acceptance suite.", "Run tools.talos_acceptance.")
        check("skills", skill.is_file())
        check("nodes", nodes.register(state, "local-mac", kind="local").get("name") == "local-mac")

        from talos_core.self_improvement import decomposition
        plan = decomposition.plan_task(state, "Add an acceptance check", files=("agent.py",))
        check("decomposition", len(plan["leaves"]) >= 1, len(plan["leaves"]))

    return {"model": model, "muted": True, "checks": checks,
            "ok": all(item["ok"] for item in checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("TALOS_MODEL", "qwen2.5:1.5b"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("TALOS_OLLAMA_PORT", "11434")
    result = run(args.model)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for item in result["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"] + " · " + item["detail"])
        print("OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
