"""Publish navigable views of the local index without copying raw Mac records."""

from feature_targets import proposal_ledger
import re
import sqlite3
import fcntl
from contextlib import closing
from datetime import datetime
from pathlib import Path

import mac_context
from vault_graph import MARKER, code_name, link, publish_connections, write_note


def publish_work(state, folder):
    queue = state / "feature-requests.jsonl"
    work = folder / "Changes"
    work.mkdir(mode=0o700, exist_ok=True)
    records, outcomes = {}, {}
    if queue.is_file():
        with queue.open() as stream:
            records, outcomes = proposal_ledger(stream)
    groups = {}
    migrated = []
    for key, event in records.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{16}", key):
            continue
        outcome = outcomes.get(key, {})
        declined = outcome.get("status") == "declined"
        if outcome.get("status") == "done":
            event["verification"] = outcome.get("verification")
        elif declined:
            event["reason"] = outcome.get("reason")
        source = Path(event.get("source", "")).name
        area = (
            source if re.fullmatch(r"[A-Za-z0-9_-]+[.](py|swift|ts|mjs)", source) else "Other"
        )
        words = re.findall(r"[A-Za-z0-9]+", event["feature"])
        title = " ".join(words[:10])[:96].strip() + " " + key[:8]
        groups.setdefault(area, []).append(title)
        lines = [
            "# "
            + ("Declined proposal" if declined else "Verified change" if event.get("verification") else "Proposed change")
        ]
        for field in ("feature", "acceptance", "verification", "reason"):
            if event.get(field):
                lines.extend(
                    [
                        "",
                        field.capitalize(),
                        "",
                        "    " + " ".join(event[field].split()),
                    ]
                )
        if (outcome.get("status") == "done" and event.get("origin") == "verified-upstream-port" and event.get("verification")
                and event.get("upstream") in ("Hermes", "OpenClaw") and isinstance(event.get("reference"), str)):
            migrated.append(f"- [[Talos Memory/Talos maps/Changes/{title}|{event['upstream']} · {' '.join(words)}]]")
            lines.extend(("", "Upstream source", "", "    " + event["upstream"] + "/" + event["reference"]))
        module = code_name(event.get("source", ""))
        if re.fullmatch(r"[a-zA-Z0-9_. -]+", module) and (folder / "Code" / (module + ".md")).is_file():
            lines.extend(["", link("Code", module)])
        body = "\n".join(lines) + "\n"
        write_note(work / (title + ".md"), body)
    if migrated:
        write_note(state / "Talos migrations.md", MARKER + "\n# Verified upstream migrations\n\n"
                   + "\n".join(migrated) + "\n", managed=True)
    links = []
    for area, keys in sorted(groups.items()):
        title = area.replace(".", " ") + " changes"
        links.append(f"[[Talos maps/Changes/{title}|{area}]]")
        write_note(
            work / (title + ".md"),
            f"# {area}\n\n"
            + "\n".join(f"- [[Talos maps/Changes/{change}]]" for change in keys)
            + "\n",
        )
    write_note(
        folder / "Development history.md",
        "# Development history\n\n" + "\n".join("- " + link for link in links) + "\n",
    )


def publish(state):
    with (state / "vault-publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _publish(state)


def _publish(state):
    vault = state / "Talos Memory"
    folder = vault / "Talos maps"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    enabled = mac_context.enabled(state)
    counts = {}
    index = state / mac_context.INDEX_NAME
    if index.is_file():
        with closing(
            sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.execute("BEGIN")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='source_summary'"
            ).fetchone():
                counts = dict(
                    db.execute("SELECT app,SUM(count) FROM source_summary GROUP BY app")
                )
            changed = publish_connections(db, folder, enabled)
    else:
        changed = publish_connections(None, folder, enabled)
    sources = []
    for app in sorted(set(mac_context.APPS) | counts.keys()):
        path = folder / (app + " index.md")
        if app in mac_context.APPS and app not in enabled:
            path.unlink(missing_ok=True)
            continue
        sources.append("[[Talos maps/" + path.stem + "|" + app + "]]")
        count = counts.get(app)
        summary = (
            f"{count:,} searchable records at the last refresh."
            if count is not None
            else "Awaiting an available source."
        )
        write_note(path, f"# {app}\n\n{summary}\n\n#talos/source\n")
    notes = sorted(
        (
            p
            for p in vault.glob("Conversation-*.md")
            if re.fullmatch(r"Conversation-\d+", p.stem)
        ),
        key=lambda p: int(p.stem.split("-")[1]),
        reverse=True,
    )
    write_note(
        folder / "Conversation index.md",
        "# Conversations\n\n" + "\n".join(f"- [[{p.stem}]]" for p in notes) + "\n",
    )
    publish_work(state, folder)
    links = [
        "[[Talos maps/Development history|Development history]]",
        "[[Talos maps/Conversation index|Conversations]]",
        "[[Talos maps/Heartbeat|Heartbeat]]",
        *sources,
    ]
    links.append("[[Talos maps/Project connections|Project connections]]")
    if (folder / "Code index.md").is_file():
        links.append("[[Talos maps/Code index|Source code]]")
    links.extend(
        f"[[{title}]]"
        for title in (
            "Personal memory overview",
            "Personal knowledge overview",
            "Talos activity",
            "Feature test results",
        )
        if (vault / (title + ".md")).is_file()
    )
    write_note(
        folder / "Talos memory map.md",
        "# Talos memory map\n\n" + "\n".join("- " + link for link in links) + "\n",
    )
    return changed


def publish_heartbeat(state, status):
    stamp = (
        datetime.fromtimestamp(status["checked_at"])
        .astimezone()
        .isoformat(timespec="seconds")
    )
    lines = [
        "# Heartbeat",
        "",
        f"Last checked {stamp}",
        "",
        f"Dialogue regression suite: {status['baseline']}",
        "",
        f"Worker: {status['outcome']}",
    ]
    lines.extend(["", f"Due goals checked: {status.get('goals_checked', 0)}"])
    if status.get("trial"):
        lines.extend(
            [
                "",
                f"Trial {status['trial']} · {status['stage']} · {status['last_result']}",
            ]
        )
    folder = state / "Talos Memory" / "Talos maps"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_note(folder / "Heartbeat.md", "\n".join(lines) + "\n")
