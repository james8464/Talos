"""Owner-only Telegram text gateway with isolated, bounded conversation state."""

import argparse
import fcntl
import http.client
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from agent import model_text
from conversation import limit_history
from local_runtime import check_ollama_model


class ApiError(RuntimeError):
    def __init__(self, code, delay=0):
        super().__init__(f"Telegram returned status {code}.")
        self.code = code
        self.delay = delay


def owner_id(value):
    # Adapted from OpenClaw extensions/telegram/src/allow-from.ts.
    raw = re.sub(r"^(?:telegram|tg):", "", value.strip(), flags=re.I).strip()
    if not re.fullmatch(r"[0-9]{1,16}", raw) or not 0 < int(raw) < 2**52:
        raise argparse.ArgumentTypeError("Expected a positive numeric Telegram user ID.")
    return int(raw)


def api_client(token):
    def call(method, **params):
        with closing(http.client.HTTPSConnection("api.telegram.org", timeout=40)) as conn:
            conn.request("POST", f"/bot{token}/{method}", json.dumps(params),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            body = json.load(response)
            if not body.get("ok"):
                raise ApiError(body.get("error_code", response.status),
                               body.get("parameters", {}).get("retry_after", 0))
            return body["result"]
    return call


def open_channel(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS pairing (bot INTEGER PRIMARY KEY, owner INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS cursor (bot INTEGER PRIMARY KEY, seen INTEGER, stamp REAL)")
    db.execute("""CREATE TABLE IF NOT EXISTS delivery (
        bot INTEGER PRIMARY KEY, update_id INTEGER, status TEXT, message_id INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS outbox (
        bot INTEGER PRIMARY KEY, owner INTEGER, question TEXT, answer TEXT, remaining TEXT, save_turn INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS turns (
        bot INTEGER, owner INTEGER, seq INTEGER PRIMARY KEY, question TEXT, answer TEXT)""")
    return db


def reply_to(db, bot, owner, text, model):
    if text in ("/start", "/help"):
        return "Talk to Talos here. /new clears this chat’s local context. Mac files and actions are unavailable here."
    if text in ("/new", "/reset"):
        return "Started a fresh conversation."
    if text.startswith("/"):
        return "Unknown command. Use /help."
    check_ollama_model(model)
    messages = [{"role": "system", "content": (
        "You are Talos in a Telegram text chat. Your only evidence is what the user wrote. "
        "Speak as a conversational partner. You have no sensors, Mac data or actions here. "
        "Never claim to see, locate or verify an object. When the user corrects a fact, "
        "use the corrected fact. For a recall question, give just the current answer in "
        "one sentence, based on what the user told you. Return JSON with only a reply field "
        "containing your final answer. /no_think"
    )}]
    rows = db.execute("SELECT question,answer FROM turns WHERE bot=? AND owner=? ORDER BY seq DESC LIMIT 4",
                      (bot, owner)).fetchall()
    for question, answer in reversed(rows):
        messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
    raw = model_text([*limit_history(messages), {"role": "user", "content": text}], model=model,
                     schema={"type": "object", "required": ["reply"], "additionalProperties": False,
                             "properties": {"reply": {"type": "string"}}})
    try:
        result = json.loads(raw)
    except ValueError as error:
        raise RuntimeError("Incomplete conversational reply.") from error
    answer = result.get("reply") if isinstance(result, dict) else None
    if not isinstance(answer, str) or not answer.strip() or re.search(r"</?think>", answer, re.I):
        raise RuntimeError("Incomplete conversational reply.")
    return answer.strip()


def text_chunk(text):
    # Adapted from Hermes gateway/platforms/base.py's UTF-16 prefix calculation.
    if len(text.encode("utf-16-le")) <= 8000:
        return text
    low, high = 0, min(len(text), 4000)
    while low < high:
        middle = (low + high + 1) // 2
        if len(text[:middle].encode("utf-16-le")) <= 8000:
            low = middle
        else:
            high = middle - 1
    boundary = text.rfind(" ", low // 2, low)
    return text[:boundary + 1 if boundary >= 0 else low]


def flush_reply(db, call, bot, owner):
    pending = db.execute("""SELECT o.question,o.answer,o.remaining,o.save_turn,d.status FROM outbox o
                            JOIN delivery d USING(bot) WHERE o.bot=? AND o.owner=?""", (bot, owner)).fetchone()
    if not pending:
        return
    question, answer, remaining, save_turn, status = pending
    if status == "unconfirmed":
        # A previous send may have reached Telegram before the process stopped.
        with db:
            db.execute("DELETE FROM outbox WHERE bot=?", (bot,))
        print("Delivery unconfirmed. The reply will not be resent automatically.", file=sys.stderr)
        return
    while remaining:
        part = text_chunk(remaining)
        with db:
            db.execute("UPDATE delivery SET status='unconfirmed' WHERE bot=?", (bot,))
        try:
            sent = call("sendMessage", chat_id=owner, text=part)
        except ApiError as error:
            if error.code == 429:
                with db:
                    db.execute("UPDATE delivery SET status='rate limited' WHERE bot=?", (bot,))
            raise
        remaining = remaining[len(part):]
        with db:
            db.execute("UPDATE delivery SET status=?,message_id=? WHERE bot=?",
                       ("ready" if remaining else "confirmed", sent["message_id"], bot))
            if remaining:
                db.execute("UPDATE outbox SET remaining=? WHERE bot=?", (remaining, bot))
            else:
                db.execute("DELETE FROM outbox WHERE bot=?", (bot,))
                if save_turn:
                    db.execute("INSERT INTO turns(bot,owner,question,answer) VALUES(?,?,?,?)",
                               (bot, owner, question, answer))
                    db.execute("""DELETE FROM turns WHERE bot=? AND owner=? AND seq NOT IN
                               (SELECT seq FROM turns WHERE bot=? AND owner=? ORDER BY seq DESC LIMIT 4)""",
                               (bot, owner, bot, owner))


def poll(db, call, bot, owner, model):
    flush_reply(db, call, bot, owner)
    cursor = db.execute("SELECT seen,stamp FROM cursor WHERE bot=?", (bot,)).fetchone()
    # Telegram retains updates for at most a day and can randomize IDs after prolonged inactivity.
    seen = cursor[0] if cursor and time.time() - cursor[1] < 2 * 86400 else -1
    updates = call("getUpdates", offset=seen + 1, limit=10, timeout=20, allowed_updates=["message"])
    for update in updates:
        ident = update["update_id"]
        if ident <= seen:
            continue
        message = update.get("message") or {}
        chat = message.get("chat", {})
        sender = message.get("from", {})
        allowed = chat.get("type") == "private" and chat.get("id") == sender.get("id") == owner
        text = message.get("text", "").strip() if allowed and not sender.get("is_bot") else ""
        answer = None
        save_turn = bool(text and not text.startswith("/"))
        if text:
            try:
                answer = reply_to(db, bot, owner, text, model)
            except (RuntimeError, OSError):
                answer = "The local model is unavailable. Please try your message again."
                save_turn = False
        with db:
            if answer is not None:
                db.execute("INSERT OR REPLACE INTO delivery VALUES(?,?,'ready',NULL)", (bot, ident))
                db.execute("INSERT OR REPLACE INTO outbox VALUES(?,?,?,?,?,?)",
                           (bot, owner, text, answer, answer, save_turn))
            if text in ("/new", "/reset"):
                db.execute("DELETE FROM turns WHERE bot=? AND owner=?", (bot, owner))
            db.execute("INSERT OR REPLACE INTO cursor VALUES(?,?,?)", (bot, ident, time.time()))
        if answer is not None:
            flush_reply(db, call, bot, owner)
        seen = ident


def pair_owner(db, call, bot):
    existing = db.execute("SELECT owner FROM pairing WHERE bot=?", (bot,)).fetchone()
    if existing:
        return existing[0]
    code = secrets.token_urlsafe(18)
    print(f"Send /pair {code} in a private message to your bot within five minutes.", flush=True)
    deadline = time.monotonic() + 300
    offset = 0
    while time.monotonic() < deadline:
        updates = call("getUpdates", offset=offset, timeout=10, allowed_updates=["message"])
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or {}
            sender, chat = msg.get("from", {}), msg.get("chat", {})
            owner = sender.get("id")
            if (type(owner) is not int or owner <= 0 or sender.get("is_bot") or
                    chat.get("type") != "private" or chat.get("id") != owner):
                continue
            if not secrets.compare_digest(msg.get("text", "").encode(), f"/pair {code}".encode()):
                continue
            if time.monotonic() >= deadline:
                break
            with db:
                db.execute("INSERT INTO pairing VALUES(?,?)", (bot, owner))
                db.execute("INSERT OR REPLACE INTO cursor VALUES(?,?,?)", (bot, offset - 1, time.time()))
            return owner
    raise RuntimeError("Pairing expired. Run --pair to get a new code.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--owner", type=owner_id, help="Your numeric Telegram user ID")
    auth.add_argument("--pair", action="store_true", help="Pair this bot with your private chat, then exit")
    parser.add_argument("--check", action="store_true", help="Check bot identity, webhook and local model, then exit")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--state", type=Path, default=Path(__file__).resolve().parent / "state/telegram")
    args = parser.parse_args()
    token = os.environ.get("TALOS_TELEGRAM_TOKEN", "")
    if not re.fullmatch(r"[1-9]\d*:[A-Za-z0-9_-]+", token):
        parser.error("Set TALOS_TELEGRAM_TOKEN to your bot token.")
    bot = int(token.partition(":")[0])
    call = api_client(token)
    if args.check:
        try:
            identity = call("getMe")
            if identity.get("id") != bot or not identity.get("is_bot"):
                parser.exit(1, "Token identity mismatch.\n")
            if call("getWebhookInfo").get("url"):
                parser.exit(1, "A webhook is configured for this bot and prevents polling.\n")
            check_ollama_model(args.model)
        except (RuntimeError, OSError, http.client.HTTPException, ValueError) as error:
            parser.exit(1, f"Setup check failed: {type(error).__name__}.\n")
        print("Ready: Telegram bot identity, polling configuration and local model verified.")
        return
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state / f"{bot}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "This bot already has a running poller.\n")
        with closing(open_channel(args.state / "channel.sqlite3")) as db:
            if args.pair:
                try:
                    pair_owner(db, call, bot)
                except (RuntimeError, OSError, ValueError, http.client.HTTPException) as error:
                    parser.exit(1, f"Pairing failed: {error}\n")
                print("Owner paired. Start the gateway without --pair to chat.")
                return
            owner = args.owner
            if owner is None:
                paired = db.execute("SELECT owner FROM pairing WHERE bot=?", (bot,)).fetchone()
                if not paired:
                    parser.error("Run --pair or specify --owner with your numeric Telegram user ID.")
                owner = paired[0]
            backoff = 1
            while True:
                try:
                    poll(db, call, bot, owner, args.model)
                    backoff = 1
                except ApiError as error:
                    if error.code != 429 and error.code < 500:
                        parser.exit(1, f"{error}\n")
                    time.sleep(max(error.delay, backoff))
                    backoff = min(60, backoff * 2)
                except (RuntimeError, OSError, http.client.HTTPException, ValueError):
                    print("Gateway or local model unavailable. Retrying.", file=sys.stderr)
                    time.sleep(backoff)
                    backoff = min(60, backoff * 2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
