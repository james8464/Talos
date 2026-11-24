"""Explicit visual questions use one transient image and a local vision model."""

import base64
import os
import re

from talos_core.runtime.local_runtime import check_ollama_model, request_ollama_json

_PEOPLE_COUNT = re.compile(
    r"how many (?:people|persons) (?:can you see|do you see|are (?:in (?:the room|the frame)|here))"
    r"(?: (?:right )?now)?[.!?]*"
)
_PRESENCE = re.compile(
    r"(?:(?:is|are) (?:anyone|anybody|someone|somebody) (?:there|here|in (?:the )?(?:room|frame))"
    r"|is (?:the )?room (?:empty|occupied)|is (?:it|the room) (?:empty|occupied)"
    r"|who is (?:there|in (?:the )?room)|do you see (?:anyone|anybody|someone|somebody|a person)"
    r"|is there (?:a person|someone|anyone))[.!?]*"
)


def _question_words(text):
    words = " ".join(text.casefold().replace("’", "'").split())
    words = re.sub(r"^(?:(?:hey|okay) )?talos[, ]+", "", words)
    words = re.sub(r"^(?:(?:can|could) you (?:tell me |describe )?|please )", "", words)
    return words


def visual_question(text):
    if not isinstance(text, str):
        return False
    words = _question_words(text)
    return bool(
        _PRESENCE.fullmatch(words) or _PEOPLE_COUNT.fullmatch(words) or re.match(
            r"(?:what (?:does my (?:shirt|hat|jacket) (?:say|look like)|outfit (?:am i wearing|do i have on))|(?:what am i dressed in|see my (?:outfit|shirt)|describe my clothes)|what (?:colou?r (?:shirt|sweater|top|jacket) )?am i wearing|describe (?:what you see|my appearance)|what i(?: am|\x27m) wearing|what (?:am i|are we|is (?:he|she)) (?:wearing|holding|showing)|"
            r"what(?:'s| is) (?:in (?:my hand|front of (?:me|you)|the (?:camera|frame))|on my (?:head|shirt|desk))|"
            r"what colou?r (?:is|are) (?:my (?:shirt|jacket|hat|sweater|outfit|top|shoes)|this|these|the (?:shirt|jacket|hat|sweater|outfit))|(?:describe|look at) (?:my (?:outfit|shirt|clothes|desk)|the (?:camera|scene|room))|"
            r"(?:am i|is (?:he|she)) wearing|how (?:do i|does my (?:outfit|shirt)) look|"
            r"what (?:can|do) you see|look (?:at|through) (?:this|the camera))\b",
            words,
        )
    )


def _scene_people(frame: str, state=None):
    import tempfile
    from pathlib import Path

    from talos_core.mac import scene

    data = base64.b64decode(frame, validate=True)
    goal = Path(state) if state is not None else Path(tempfile.gettempdir())
    folder = goal / "senses"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=".room-scene-", suffix=".jpg", dir=folder)
    try:
        import os
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        result = scene.detect(goal, path)
        return int(result.get("people", 0))
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def answer(text, frame, people=None, state=None):
    if not frame:
        return "I need a fresh camera frame to answer that. Check that the room camera is on."
    if not isinstance(frame, str) or len(frame) > 240000:
        raise ValueError("Expected a small camera JPEG.")
    image = base64.b64decode(frame, validate=True)
    if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise ValueError("Expected a camera JPEG.")
    if people is not None and (type(people) is not int or people < 0):
        raise ValueError("Expected a person count from the same frame.")
    if _PRESENCE.fullmatch(_question_words(text)) or _PEOPLE_COUNT.fullmatch(
            _question_words(text)):
        count = people if people is not None else _scene_people(frame, state)
        if count is None:
            return "I couldn't inspect the camera frame."
        if count == 0:
            return "The room looks empty."
        if count == 1:
            return "I see one person. I can't identify them without an enrolled face."
        return f"I see {count} people."
    if re.search(
        r"\b(?:wearing|(?:am i|is he|is she) (?:holding|showing)|"
        r"my (?:outfit|shirt|hat|clothes|appearance|jacket|sweater|top|shoes|hand|head)|i look|i dressed)\b",
        text,
        re.I,
    ):
        if people == 0:
            return "I can't see a person clearly enough in this frame to answer that."
        if people is not None and people > 1:
            return (
                "I see more than one person. Could you step into the frame on your own?"
            )
    return describe_image(text, frame)


def describe_image(text, frame):
    """Describe one validated, base64-encoded JPEG without recording a turn."""
    model = os.environ.get("TALOS_VISION_MODEL", "qwen2.5vl:3b")
    try:
        check_ollama_model(model)
        capabilities = request_ollama_json(
            "POST", "/api/show", payload={"model": model}
        ).get("capabilities", [])
        if "vision" not in capabilities:
            return "The selected local model cannot inspect images."
        result = request_ollama_json(
            "POST",
            "/api/chat",
            timeout=80,
            payload={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Answer briefly using only details visible in the supplied image. If unclear say so. Do not identify people.",
                    },
                    {"role": "user", "content": text, "images": [frame]},
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 64},
            },
        )
        return (
            result.get("message", {}).get("content", "").strip()[:450]
            or "I couldn't make that out in the camera frame."
        )
    except RuntimeError:
        return "I couldn't inspect the frame with the local vision model."
