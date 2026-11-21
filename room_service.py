"""Loopback HTTP endpoint and background room-event worker."""

import argparse
import hmac
import json
import os
import secrets
import sqlite3
import threading
from time import monotonic
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from agent import parse_event, plan
from agent_memory import STATE_PATH, insert_decision, open_memory
from local_actions import acknowledge, inbox_items
from room_queue import enqueue, next_event, observe_presence, presence, retry_event
from transcript_memory import TEXT_SESSION, insert_transcripts, recent_messages

DEFAULT_PORT = 8765
ROOM_INSTRUCTIONS = (
    "Camera data is a local person detector: occupancy only. No images or identities are supplied. "
    "Unknown is different from empty. Give a brief dry greeting only if it fits the situation. "
    "Avoid repeated greetings, threats, or disclosures. Reply to conversational turns. "
    "In voice mode you receive speech transcribed from the room microphone. Any person present "
    "could be speaking, and a claimed identity is unverified. In text mode you receive typed input. "
    "Use only tools that are supplied and describe effects only after they have a receipt."
)


class Room:
    def __init__(self, db_path: Path, model: str, *, apply: bool = True, speak: bool = True, voice=None):
        self.db_path = db_path
        self.model = model
        self.apply = apply
        self.speak = speak and apply
        self.voice = voice
        self.inference_lock = threading.Lock()
        self.voice_window: list[dict[str, str]] = []
        self.voice_window_updated = 0.0

    def context(self, db: sqlite3.Connection, **extra) -> dict:
        occupied = presence(db)
        return {
            "room": {
                "person_detection": "unknown" if occupied is None else "occupied" if occupied else "empty",
                "vision_scope": "presence only",
                "instructions": ROOM_INSTRUCTIONS,
            },
            **extra,
        }

    def turn(self, db: sqlite3.Connection, body: dict) -> dict:
        text, source = body.get("text"), body.get("source", "text")
        remember_voice = body.get("remember", False)
        if source not in ("voice", "text") or type(remember_voice) is not bool:
            raise ValueError("Use source text or voice and a boolean remember value.")
        event = parse_event(json.dumps({"kind": f"{source}_turn", "summary": text}))
        archive = source == "text" or remember_voice
        session = TEXT_SESSION if source == "text" else "native_voice"
        received = monotonic()
        with self.inference_lock:
            started = monotonic()
            if source == "voice":
                if monotonic() - self.voice_window_updated >= 300:
                    self.voice_window.clear()
                dialogue = self.voice_window or (recent_messages(db, session) if archive else [])
            else:
                dialogue = recent_messages(db, session)
            proposal = plan(
                db, event, model=self.model, trusted_owner=source == "text",
                private_context=archive, max_lookups=1 if source == "voice" else 3,
                max_tokens=256 if source == "voice" else 512, room_context=self.context(
                    db, conversational_turn=True, input_channel=source,
                    recent_dialogue=dialogue,
                    voice_archiving=archive if source == "voice" else None,
                ),
            )
            planned = monotonic()
            # Leave any speech requested during a dialogue to the caller's playback path.
            with db:
                receipt_event = event if archive else {
                    "kind": "voice_turn", "summary": "Voice turn with transcript retention disabled.",
                }
                receipt_proposal = None if archive else {
                    "disposition": proposal["disposition"], "tool": proposal["tool"],
                }
                decision_id = insert_decision(
                    db, receipt_event, proposal, apply=self.apply, receipt_proposal=receipt_proposal,
                )
                outcome = db.execute("SELECT status FROM action_outcomes WHERE decision_id = ?",
                                     (decision_id,)).fetchone()
                action = outcome["status"] if outcome else "shadow"
                detail = proposal["detail"]
                disposition = proposal["disposition"]
                if disposition == "act" and action == "applied":
                    reply = f"Done. {detail}"
                elif disposition == "act" and proposal["tool"] != "speak":
                    reply = f"No change made. Proposed action: {detail}"
                elif disposition in ("act", "ask", "reply", "wait"):
                    reply = detail
                else:
                    reply = "I don't have enough information to help with that yet."
                exchange = [
                    {"role": "user", "content": event["summary"]},
                    {"role": "assistant", "content": reply},
                ]
                if archive:
                    insert_transcripts(db, session, exchange)
            if source == "voice":
                self.voice_window = [*self.voice_window, *exchange][-6:]
                self.voice_window_updated = monotonic()
        return {
            "id": decision_id, "reply": reply, "action": action, "tool": proposal["tool"],
            "queue_ms": round((started - received) * 1000),
            "planning_ms": round((planned - started) * 1000),
        }

    def step(self) -> bool:
        with self.inference_lock, closing(open_memory(self.db_path)) as db:
            row = next_event(db)
            if row is None:
                return False
            event = json.loads(row["event"])
            if self.stale(db, row):
                self.finish_stale(db, row["id"])
                return True
            try:
                proposal = plan(
                    db, event, model=self.model, room_context=self.context(db), private_context=False,
                    max_lookups=0, max_tokens=256,
                )
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    if self.stale(db, row):
                        db.execute("UPDATE room_events SET status = 'stale' WHERE id = ?", (row["id"],))
                        return True
                    decision_id = insert_decision(
                        db, event, proposal, apply=self.apply, allow_speech=self.speak,
                    )
                    db.execute("UPDATE room_events SET status = 'done', decision_id = ? WHERE id = ?",
                               (decision_id, row["id"]))
                    if proposal["disposition"] == "wait" and row["depth"] < 1:
                        summary = f"Reassess the earlier room observation. Earlier question: {proposal['detail']}"
                        enqueue(db, {"kind": "follow_up", "summary": summary},
                                expected_presence=row["expected_presence"], depth=1, delay=120)
            except (OSError, RuntimeError, sqlite3.Error) as error:
                print(f"Talos room event {row['id']}: {error}", flush=True)
                retry_event(db, row)
            return True

    @staticmethod
    def stale(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
        expected = row["expected_presence"]
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            return True
        if expected is None:
            return False
        return presence(db) is not bool(expected) or db.execute("""
            SELECT 1 FROM room_events
            WHERE id > ? AND depth = 0 AND expected_presence IS NOT NULL LIMIT 1
        """, (row["id"],)).fetchone() is not None

    @staticmethod
    def finish_stale(db: sqlite3.Connection, event_id: int) -> None:
        with db:
            db.execute("UPDATE room_events SET status = 'stale' WHERE id = ?", (event_id,))

    def speech(self, db: sqlite3.Connection) -> dict | None:
        if not self.speak:
            return None
        now = datetime.now(UTC)
        row = db.execute("""
            SELECT inbox.id, inbox.kind, inbox.text, inbox.due_at FROM inbox
            LEFT JOIN room_events ON room_events.decision_id = inbox.id
            WHERE inbox.acknowledged_at IS NULL AND inbox.due_at <= ? AND (
                inbox.kind IN ('alert', 'reminder') OR (
                    inbox.kind = 'speech' AND inbox.due_at > ? AND
                    (room_events.expected_presence IS NULL OR (
                        room_events.expected_presence = ? AND NOT EXISTS (
                            SELECT 1 FROM room_events AS newer
                            WHERE newer.id > room_events.id AND newer.depth = 0
                                AND newer.expected_presence IS NOT NULL
                        )
                    ))
                )
            ) ORDER BY inbox.due_at, inbox.id LIMIT 1
        """, (now.isoformat(), (now - timedelta(minutes=2)).isoformat(), presence(db))).fetchone()
        return dict(row) if row else None

    def route(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        if method == "GET" and path == "/health":
            return 200, {"service": "talos"}
        if method == "POST" and path == "/v1/voice":
            if self.voice is None:
                return 503, {"error": "Kokoro voice is unavailable."}
            text = body.get("text")
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 600:
                raise ValueError("Voice text must contain 1 to 600 characters.")
            started = monotonic()
            audio = self.voice.speak(text.strip())
            return 200, {"wav": audio, "voice": self.voice.name,
                         "synthesis_ms": round((monotonic() - started) * 1000)}
        with closing(open_memory(self.db_path)) as db:
            match method, path:
                case "GET", "/v1/status":
                    return 200, self.context(db, model=self.model, local_actions=self.apply,
                                             proactive_speech=self.speak,
                                             voice=self.voice.name if self.voice else None,
                                             queued=db.execute(
                                                 "SELECT COUNT(*) FROM room_events WHERE status = 'pending'"
                                             ).fetchone()[0])
                case "GET", event_path if event_path.startswith("/v1/events/"):
                    number = event_path.removeprefix("/v1/events/")
                    if not number.isascii() or not number.isdecimal() or not 1 <= int(number) <= 2**63 - 1:
                        raise ValueError("A positive room event ID is required.")
                    row = db.execute("""
                        SELECT id, status, attempts, decision_id FROM room_events WHERE id = ?
                    """, (int(number),)).fetchone()
                    return (200, dict(row)) if row else (404, {"error": "Unknown event."})
                case "GET", "/v1/inbox":
                    return 200, {"items": inbox_items(db, limit=50)}
                case "POST", "/v1/turn":
                    return 200, self.turn(db, body)
                case "POST", "/v1/presence":
                    occupied = body.get("occupied")
                    if "occupied" not in body or (occupied is not None and type(occupied) is not bool):
                        raise ValueError("occupied must be true, false, or null.")
                    return 202, {"event_id": observe_presence(db, occupied)}
                case "POST", "/v1/events":
                    event = parse_event(json.dumps(body))
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        waiting = db.execute(
                            "SELECT COUNT(*) FROM room_events WHERE status = 'pending'"
                        ).fetchone()[0]
                        if waiting >= 32:
                            return 429, {"error": "Room event queue is full."}
                        return 202, {"event_id": enqueue(db, event)}
                case "GET", "/v1/speech":
                    return 200, {"item": self.speech(db)}
                case "POST", "/v1/speech/ack":
                    if not self.speak:
                        return 403, {"error": "Room announcements are disabled."}
                    item_id = body.get("id")
                    if type(item_id) is not int or not 1 <= item_id <= 2**63 - 1:
                        raise ValueError("A positive item ID is required.")
                    row = db.execute(
                        "SELECT kind FROM inbox WHERE id = ?", (item_id,),
                    ).fetchone()
                    if row is None or row["kind"] not in ("speech", "alert", "reminder"):
                        raise ValueError("Unknown speech item.")
                    acknowledge(db, item_id)
                    return 200, {"acknowledged": item_id}
        return 404, {"error": "Unknown Talos endpoint."}


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(10)
        super().setup()

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def handle_request(self, method: str) -> None:
        token = self.headers.get("Authorization", "")
        if (method, self.path) != ("GET", "/health") and not hmac.compare_digest(
            token.encode(), ("Bearer " + self.server.token).encode()
        ):
            self.reply(401, {"error": "A valid local Talos token is required."})
            return
        try:
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 8192 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("A JSON body of 1 to 8192 bytes is required.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
            status, data = self.server.room.route(method, self.path, body)
        except (ValueError, OverflowError) as error:
            status, data = 400, {"error": str(error)}
        except (OSError, RuntimeError, sqlite3.Error) as error:
            print(f"Talos endpoint: {error}", flush=True)
            status, data = 503, {"error": "Talos could not complete the request. Check the local service."}
        self.reply(status, data)

    def reply(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


def create_token(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    # Replace atomically so the client always reads a complete token.
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
        output.write(token + "\n")
    os.replace(temporary, path)
    return token


def run(
    port: int, room: Room, token_path: Path, *, ready: Callable[[], None] | None = None
) -> None:
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        server.token = create_token(token_path)
        server.room = room
        stopping = threading.Event()

        def work() -> None:
            while not stopping.is_set():
                try:
                    if room.step():
                        continue
                except (OSError, sqlite3.Error) as error:
                    print(f"Talos room worker: {error}", flush=True)
                stopping.wait(1)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        print(f"Talos room endpoint: http://127.0.0.1:{port}", flush=True)
        try:
            if ready:
                ready()
            server.serve_forever()
        finally:
            stopping.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Talos's local room service.")
    parser.add_argument("--db", type=Path, default=STATE_PATH)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--shadow", action="store_true")
    args = parser.parse_args()
    try:
        run(args.port, Room(args.db, args.model, apply=not args.shadow), args.db.parent / "room.token")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
