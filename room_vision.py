"""Explicit visual questions use one transient image and a local vision model."""

import base64
import os
import re
from local_runtime import check_ollama_model, request_ollama_json


_PEOPLE_COUNT = re.compile(
    r"how many (?:people|persons) (?:can you see|do you see|are (?:in (?:the room|the frame)|here))"
    r"(?: (?:right )?now)?[.!?]*"
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
        _PEOPLE_COUNT.fullmatch(words) or re.match(
            r"(?:what (?:does my (?:shirt|hat|jacket) (?:say|look like)|outfit (?:am i wearing|do i have on))|(?:what am i dressed in|see my (?:outfit|shirt)|describe my clothes)|what (?:colou?r (?:shirt|sweater|top|jacket) )?am i wearing|describe (?:what you see|my appearance)|what i(?: am|\x27m) wearing|what (?:am i|are we|is (?:he|she)) (?:wearing|holding|showing)|"
            r"what(?:'s| is) (?:in (?:my hand|front of (?:me|you)|the (?:camera|frame))|on my (?:head|shirt|desk))|"
            r"what colou?r (?:is|are) (?:my (?:shirt|jacket|hat|sweater|outfit|top|shoes)|this|these|the (?:shirt|jacket|hat|sweater|outfit))|(?:describe|look at) (?:my (?:outfit|shirt|clothes|desk)|the (?:camera|scene|room))|"
            r"(?:am i|is (?:he|she)) wearing|how (?:do i|does my (?:outfit|shirt)) look|"
            r"what (?:can|do) you see|look (?:at|through) (?:this|the camera))\b",
            words,
        )
    )


def answer(text, frame, people=None):
    if not frame:
        return "I need a fresh camera frame to answer that. Check that the room camera is on."
    if not isinstance(frame, str) or len(frame) > 240000:
        raise ValueError("Expected a small camera JPEG.")
    image = base64.b64decode(frame, validate=True)
    if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise ValueError("Expected a camera JPEG.")
    if people is not None and (type(people) is not int or people < 0):
        raise ValueError("Expected a person count from the same frame.")
    if _PEOPLE_COUNT.fullmatch(_question_words(text)):
        if people is None:
            return "I have no person count for this camera frame."
        count = {0: "no people", 1: "one person"}.get(people, f"{people} people")
        return f"The camera detected {count} in this frame."
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
                        "content": "Answer briefly using only visible details from the current frame. If unclear say so. Do not identify people.",
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
