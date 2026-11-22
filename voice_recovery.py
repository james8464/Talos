"""Local reports of speech output symptoms and bounded, text-free playback receipts."""

import re
from datetime import UTC, datetime

OUTCOMES = {
    "kokoro_failed": "Kokoro audio failed. Talos selected the macOS voice.",
    "macos_started": "The macOS synthesizer reported that speech started.",
    "macos_finished": "The macOS synthesizer reported that the utterance finished.",
    "macos_unavailable": "The macOS synthesizer failed to start or finish. Replies remain visible in the room.",
    "kokoro_finished": "The Kokoro audio player reported completed playback.",
}

COMPLAINT = re.compile(
    r"(?:i (?:still )?(?:can't|cannot|can not) hear you(?: (?:speak|talk|anymore))?|"
    r"(?:you're|you are) (?:having (?:issues?|problems?|trouble)|struggling) "
    r"(?:with )?(?:talking|speaking)(?: to me)?|"
    r"(?:you're|you are) not (?:speaking|talking)(?: to me)?|"
    r"your (?:voice|speech|audio) (?:isn't working|is not working|stopped working)|"
    r"(?:check|test|fix) (?:your|the) (?:voice|speech|audio)(?: output)?)"
)
STATUS = {"voice status", "speech status", "show voice status", "is your voice working"}


def recent(db):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='voice_output_receipts'"
    ).fetchone():
        return []
    return [
        dict(outcome=code, time=stamp)
        for code, stamp in db.execute(
            "SELECT outcome,time FROM voice_output_receipts ORDER BY id DESC LIMIT 5"
        )
    ]


def report(db, payload):
    code = payload.get("outcome")
    if set(payload) != {"outcome"} or not isinstance(code, str) or code not in OUTCOMES:
        raise ValueError("Expected one known voice output outcome.")
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS voice_output_receipts(
            id INTEGER PRIMARY KEY, outcome TEXT NOT NULL, time TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO voice_output_receipts(outcome,time) VALUES(?,?)",
            (code, datetime.now(UTC).isoformat()),
        )
        db.execute(
            "DELETE FROM voice_output_receipts WHERE id <= (SELECT max(id)-32 FROM voice_output_receipts)"
        )
    return {"recorded": code}


def handle(db, text, *, apply):
    words = " ".join(text.lower().replace("’", "'").strip().rstrip(".!?").split())
    words = re.sub(r"^(?:hey[ ,]+)?talos[ ,]+", "", words)
    if words in STATUS:
        receipts = recent(db)
        summary = (
            OUTCOMES[receipts[0]["outcome"]]
            if receipts
            else "There is no recent playback test result."
        )
        return {"reply": summary, "voice_receipts": receipts}
    words = re.sub(
        r"[,;.?! ]+(?:can you (?:fix (?:it|that|yourself)|check)|please fix (?:it|that))$",
        "",
        words,
    )
    if not COMPLAINT.fullmatch(words):
        return None
    if not apply:
        return {
            "reply": "This room is in preview mode. A live voice check would play one short test."
        }
    return {
        "reply": "I’ll check voice output with one short test. The result will appear here.",
        "voice_recovery": "test_output",
        "voice_receipts": recent(db),
    }
