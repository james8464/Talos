"""Deterministic dictation and attention state machine.

The module deliberately performs no microphone, speaker, or network I/O.
Callers supply transcripts, speaker labels, timestamps, and optional camera
summaries, which makes every decision reproducible in tests and replay.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

IDLE = "idle"
LISTENING = "listening"
USER_SPEAKING = "user_speaking"
AGENT_SPEAKING = "agent_speaking"
INTERRUPTED = "interrupted"
THINKING = "thinking"

STATES = (IDLE, LISTENING, USER_SPEAKING, AGENT_SPEAKING, INTERRUPTED, THINKING)

# Stable reason strings. Tests and receipts can compare these exactly.
REASON_EMPTY = "empty utterance"
REASON_WAKE_LEADING = "wake word at the start of the utterance"
REASON_WAKE_GREETING = "greeting followed by the wake word"
REASON_WAKE_VOCATIVE = "vocative wake word"
REASON_WAKE_REQUEST = "wake word followed by a direct request"
REASON_NAME_MENTION = "Talos is mentioned but not addressed"
REASON_REPORTED_ASSISTANT = "assistant named in reported speech"
REASON_THIRD_PERSON = "third-person reference; speech is about someone else"
REASON_ABOUT_ASSISTANT = "Talos is the subject of a statement, not the addressee"
REASON_REQUEST = "second-person request"
REASON_BACKGROUND = "no wake word or direct-address cue; likely background speech"

_GREETING_WAKE = re.compile(
    r"^\s*(?:hey|hi|hello|ok|okay|yo|oi|excuse me|wake up)[\s,!.]*talos\b"
)
_LEADING_ASSISTANT = re.compile(r"^\s*talos\b")
_LEADING_STATEMENT = re.compile(
    r"^\s*talos\s+(?:"
    r"(?:is|was|has|had|does|did|seems?|said|says|told|isn't|wasn't|doesn't|won't)\b"
    r"|(?:can|could|will|would|should)\s+(?!you\b)"
    r")"
)
_REPORTED_ASSISTANT = re.compile(
    r"\b(?:told|asked|texted|called|reminded|mentioned(?:\s+to)?|said\s+to)\s+talos\b"
)
_REPORTED_PERSON = re.compile(
    r"\b(?:i|we)\s+(?:told|asked|texted|called|reminded|mentioned(?:\s+to)?|said\s+to)\s+"
    r"(?:him|her|them|my\s+\w+|the\s+\w+|a\s+\w+)\b"
)
_THIRD_PERSON = re.compile(
    r"^\s*(?:he|she|they)\b"
    r"|\b(?:he|she|they)\s+(?:said|says|asked|thinks|thought|wants|wanted|told|tells)\b"
)
_WAKE = re.compile(r"\btalos\b")
_MID_VOCATIVE = re.compile(r",\s*talos\b|\btalos\s*[,!?]")
_REQUEST = re.compile(
    r"^\s*(?:"
    r"(?:can|could|would|will)\s+you\b"
    r"|please\b"
    r"|help\s+(?:me|us)\b"
    r"|(?:wait|hold on|never mind|cancel|stop|set|start|open|close|play|pause|remind|show|find|search|add|create|make|turn|take|check)\b"
    r")"
)

_OTHER_SPEAKERS = frozenset(
    {
        "other",
        "other_person",
        "another_person",
        "background",
        "bystander",
        "someone_else",
        "third_party",
    }
)


@dataclass(frozen=True)
class Decision:
    """An attention decision with a human-readable, stable reason."""

    accepted: bool
    reason: str
    source: str = "text"
    cue: str | None = None

    def __bool__(self) -> bool:
        return self.accepted


@dataclass(frozen=True)
class Transition:
    at: float
    source: str | None
    target: str
    reason: str


@dataclass(frozen=True)
class Event:
    """The outcome of one dictation event, including any transitions it caused."""

    accepted: bool
    state: str
    reason: str
    barged_in: bool = False
    transitions: tuple[Transition, ...] = ()
    attention: Decision | None = None

    def __bool__(self) -> bool:
        return self.accepted


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def addressed(text: str) -> Decision:
    """Decide whether an utterance is explicitly directed at Talos.

    A wake word, a leading vocative, or a second-person request counts.
    Reported speech about Talos or another person does not.
    """
    if not isinstance(text, str):
        raise TypeError("Utterance must be a string.")
    text = _normalize(text)
    if not text:
        return Decision(False, REASON_EMPTY, cue="none")
    if _REPORTED_ASSISTANT.search(text):
        return Decision(False, REASON_REPORTED_ASSISTANT, cue="third_person")
    if _GREETING_WAKE.search(text):
        return Decision(True, REASON_WAKE_GREETING, cue="wake_word")
    leading = _LEADING_ASSISTANT.match(text)
    if leading:
        remainder = text[leading.end():]
        if not remainder.strip() or remainder.startswith((",", "!", "?")):
            return Decision(True, REASON_WAKE_VOCATIVE, cue="wake_word")
        if _LEADING_STATEMENT.match(text):
            return Decision(False, REASON_ABOUT_ASSISTANT, cue="third_person")
        return Decision(True, REASON_WAKE_LEADING, cue="wake_word")
    if _REPORTED_PERSON.search(text) or _THIRD_PERSON.search(text):
        return Decision(False, REASON_THIRD_PERSON, cue="third_person")
    if _WAKE.search(text):
        if _MID_VOCATIVE.search(text):
            return Decision(True, REASON_WAKE_VOCATIVE, cue="wake_word")
        if _REQUEST.search(text):
            return Decision(True, REASON_WAKE_REQUEST, cue="wake_word")
        return Decision(False, REASON_NAME_MENTION, cue="mention")
    if _REQUEST.search(text):
        return Decision(True, REASON_REQUEST, cue="request")
    return Decision(False, REASON_BACKGROUND, cue="none")


def _facing_toward(facing: bool | str | None) -> bool:
    if facing is True:
        return True
    if facing is False or facing is None:
        return False
    if isinstance(facing, str):
        value = facing.strip().casefold().replace("-", "_")
        if value in {"toward", "towards", "mac", "camera", "screen", "facing", "at_mac"}:
            return True
        if value in {"away", "elsewhere", "not_facing", "none"}:
            return False
    raise ValueError("Facing must be a boolean, 'toward', 'away', or None.")


def vision_attention(people: int | Iterable, facing: bool | str | None = None) -> Decision:
    """Decide whether the camera sees a single person attending to the Mac."""
    if isinstance(people, bool):
        raise TypeError("People must be a count or a collection of observations.")
    if isinstance(people, int):
        count = people
    elif isinstance(people, (str, bytes)):
        raise TypeError("People must be a count or a collection of observations.")
    else:
        count = len(list(people))
    if count < 0:
        raise ValueError("People cannot be negative.")
    toward = _facing_toward(facing)
    if count == 0:
        return Decision(False, "no one in view", "camera", "empty")
    if count > 1:
        return Decision(False, f"{count} people in view; audience is ambiguous", "camera", "crowd")
    if toward:
        return Decision(True, "one person facing the Mac", "camera", "single_facing")
    return Decision(False, "one person facing away from the Mac", "camera", "single_away")


def is_other_speaker(speaker: str | None) -> bool:
    """True for an explicitly labelled background/third-party speaker."""
    return _speaker_key(speaker) in _OTHER_SPEAKERS


def _speaker_key(speaker: str | None) -> str:
    return str(speaker or "unknown").strip().casefold().replace("-", "_").replace(" ", "_")


def attending(
    text: str,
    speaker: str = "user",
    *,
    people: int | Iterable | None = None,
    facing: bool | str | None = None,
) -> Decision:
    """Combine explicit address, speaker identity, and optional camera evidence.

    Explicit address always wins. Camera evidence can confirm an unaddressed
    topic from the primary user, but it never turns away a spoken wake word.
    """
    address = addressed(text)
    if address.accepted:
        reason = f"explicit address: {address.reason}"
        if people is not None:
            vision = vision_attention(people, facing)
            if not vision.accepted:
                reason += f"; camera ignored: {vision.reason}"
        return Decision(True, reason, "speech", address.cue)
    key = _speaker_key(speaker)
    if key in _OTHER_SPEAKERS:
        return Decision(False, f"speaker is {key}; {address.reason}", "speech", address.cue)
    if people is None:
        return Decision(False, f"no direct address; {address.reason}", "speech", address.cue)
    if address.reason == REASON_EMPTY or address.cue in {"third_person", "mention"}:
        return Decision(
            False,
            f"no direct address; {address.reason}; camera does not override the text",
            "speech",
            address.cue,
        )
    vision = vision_attention(people, facing)
    if vision.accepted:
        return Decision(True, f"no direct address; camera: {vision.reason}", "camera", vision.cue)
    return Decision(False, f"no direct address; camera: {vision.reason}", "camera", vision.cue)


def _timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Timestamp must be a number of seconds.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Timestamp must be finite.")
    return value


class DictationMachine:
    """ChatGPT-style dictation state machine with deterministic timestamps.

    ``idle -> listening -> user_speaking -> thinking -> agent_speaking -> listening``
    is the normal loop. New user speech during playback records an ``interrupted``
    step and resumes ``listening`` so barge-in is observable without leaving the
    machine stuck in a transient state.
    """

    def __init__(self, at: float = 0.0) -> None:
        self.updated_at = _timestamp(at)
        self.state = IDLE
        self.history: list[Transition] = [Transition(self.updated_at, None, IDLE, "initialized")]

    def _move(self, target: str, at: float, reason: str) -> Transition:
        if target not in STATES:
            raise ValueError(f"Unknown dictation state: {target}")
        if at < self.updated_at:
            raise ValueError("Dictation timestamps must not move backwards.")
        transition = Transition(at, self.state, target, reason)
        self.state = target
        self.updated_at = at
        self.history.append(transition)
        return transition

    def start(self, at: float) -> Event:
        """Begin listening from idle."""
        at = _timestamp(at)
        if self.state == LISTENING:
            return Event(True, LISTENING, "already listening")
        if self.state != IDLE:
            return Event(False, self.state, f"cannot start dictation from {self.state}")
        transition = self._move(LISTENING, at, "dictation started; listening")
        return Event(True, LISTENING, transition.reason, transitions=(transition,))

    def stop(self, at: float) -> Event:
        """Stop dictation and return to idle from any state."""
        at = _timestamp(at)
        if self.state == IDLE:
            return Event(True, IDLE, "already idle")
        transition = self._move(IDLE, at, "dictation stopped")
        return Event(True, IDLE, transition.reason, transitions=(transition,))

    def speech_started(
        self,
        at: float,
        text: str | None = None,
        speaker: str = "user",
        *,
        people: int | Iterable | None = None,
        facing: bool | str | None = None,
    ) -> Event:
        """Record user speech, including barge-in and background-speech gating."""
        at = _timestamp(at)
        attention = None
        if text is not None:
            attention = attending(text, speaker=speaker, people=people, facing=facing)

        if self.state == USER_SPEAKING:
            self.updated_at = max(self.updated_at, at)
            return Event(True, USER_SPEAKING, "continued user speech", attention=attention)

        if self.state == AGENT_SPEAKING:
            if attention is not None and not attention.accepted:
                return Event(
                    False,
                    AGENT_SPEAKING,
                    f"ignored speech during agent playback: {attention.reason}",
                    attention=attention,
                )
            interrupted = self._move(INTERRUPTED, at, "barge-in: user speech stopped playback")
            resumed = self._move(LISTENING, at, "barge-in: playback stopped; listening resumed")
            return Event(
                True,
                LISTENING,
                "barge-in: playback stopped and listening resumed",
                barged_in=True,
                transitions=(interrupted, resumed),
                attention=attention,
            )

        if attention is not None and not attention.accepted and self.state in {
            IDLE,
            LISTENING,
            THINKING,
            INTERRUPTED,
        }:
            return Event(False, self.state, f"ignored speech: {attention.reason}", attention=attention)

        if self.state == IDLE:
            listening_reason = (
                "attention confirmed; listening started"
                if attention is not None and attention.accepted
                else "speech detected; listening started"
            )
            listening = self._move(LISTENING, at, listening_reason)
            speaking = self._move(USER_SPEAKING, at, "user speech detected")
            return Event(
                True,
                USER_SPEAKING,
                speaking.reason,
                transitions=(listening, speaking),
                attention=attention,
            )
        if self.state == LISTENING:
            transition = self._move(USER_SPEAKING, at, "user speech detected")
        elif self.state == THINKING:
            transition = self._move(USER_SPEAKING, at, "user speech superseded thinking")
        else:  # interrupted
            listening = self._move(LISTENING, at, "listening resumed")
            speaking = self._move(USER_SPEAKING, at, "user speech detected")
            return Event(
                True,
                USER_SPEAKING,
                speaking.reason,
                transitions=(listening, speaking),
                attention=attention,
            )
        return Event(True, self.state, transition.reason, transitions=(transition,), attention=attention)

    def speech_ended(self, at: float) -> Event:
        """A completed user utterance hands off to the thinking state."""
        at = _timestamp(at)
        if self.state != USER_SPEAKING:
            return Event(False, self.state, f"no active user speech in {self.state}")
        transition = self._move(THINKING, at, "user finished speaking; thinking")
        return Event(True, THINKING, transition.reason, transitions=(transition,))

    def response_started(self, at: float) -> Event:
        """Start agent playback, including unprompted speech from idle or listening."""
        at = _timestamp(at)
        if self.state not in {IDLE, LISTENING, THINKING}:
            return Event(False, self.state, f"cannot start agent speech from {self.state}")
        transition = self._move(AGENT_SPEAKING, at, "agent began speaking")
        return Event(True, AGENT_SPEAKING, transition.reason, transitions=(transition,))

    def response_finished(self, at: float) -> Event:
        """Finish agent playback and return to listening."""
        at = _timestamp(at)
        if self.state != AGENT_SPEAKING:
            return Event(False, self.state, f"agent is not speaking in {self.state}")
        transition = self._move(LISTENING, at, "agent finished speaking; listening")
        return Event(True, LISTENING, transition.reason, transitions=(transition,))

    def interrupt(self, at: float, reason: str = "playback interrupted") -> Event:
        """Pause playback or thinking without claiming new user speech."""
        at = _timestamp(at)
        if self.state == AGENT_SPEAKING:
            transition = self._move(INTERRUPTED, at, reason)
        elif self.state == THINKING:
            transition = self._move(INTERRUPTED, at, "thinking interrupted")
        else:
            return Event(False, self.state, f"nothing to interrupt in {self.state}")
        return Event(True, INTERRUPTED, transition.reason, transitions=(transition,))

    def resume(self, at: float) -> Event:
        """Return from an explicit interruption to listening."""
        at = _timestamp(at)
        if self.state != INTERRUPTED:
            return Event(False, self.state, f"not interrupted; state is {self.state}")
        transition = self._move(LISTENING, at, "listening resumed")
        return Event(True, LISTENING, transition.reason, transitions=(transition,))


Dictation = DictationMachine

__all__ = [
    "AGENT_SPEAKING",
    "IDLE",
    "INTERRUPTED",
    "LISTENING",
    "STATES",
    "THINKING",
    "USER_SPEAKING",
    "Decision",
    "Dictation",
    "DictationMachine",
    "Event",
    "Transition",
    "addressed",
    "attending",
    "vision_attention",
]
