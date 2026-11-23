"""Dependency-free ASCII brain terminal UI for Talos.

The terminal is the fallback interface when speech is unavailable.  This
module keeps that fallback usable on a system Python with no third-party
packages: it draws a small ASCII brain, a status line, a scrolling thought
log and a prompt, and it degrades to a single static frame whenever the
output stream is not a terminal.

Everything time-dependent is driven by an injected ``clock`` callable, so a
test can step the animation without sleeping and a caller can share the
runtime's monotonic clock.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

DEFAULT_WIDTH = 56
DEFAULT_FPS = 2.0
MAX_LOG_LINES = 3
MAX_THOUGHTS = 200

_SPINNER = ("|", "/", "-", "\\")

# The brain is drawn once and the pulse glyph is substituted per frame.  The
# glyph walks dot -> small circle -> big circle -> spark, which reads as a
# slow heartbeat rather than a flicker.
_ART_TEMPLATE = (
    "      .-~~~~-.          .-~~~~-.",
    "    .'  @@@@  '.      .'  @@@@  '.",
    "   /  @@    @@  \\    /  @@    @@  \\",
    "  |  @  @@@@  @  |  |  @  @@@@  @  |",
    "  |  @  @  @  @  |  |  @  @  @  @  |",
    "  |  @  @@@@  @  |  |  @  @@@@  @  |",
    "   \\  @@    @@  /    \\  @@    @@  /",
    "    '.  @@@@  .'      '.  @@@@  .'",
    "      '~..~'            '~..~'",
)

_PULSE_GLYPHS = (".", "o", "O", "*")


def _build_frames() -> tuple[tuple[str, ...], ...]:
    """Return the brain frames with the pulse glyph applied."""
    return tuple(tuple(line.replace("@", glyph) for line in _ART_TEMPLATE) for glyph in _PULSE_GLYPHS)


BRAIN_FRAMES: tuple[tuple[str, ...], ...] = _build_frames()

__all__ = ["BRAIN_FRAMES", "BrainUI", "UIState", "main"]


@dataclass(frozen=True)
class UIState:
    """What the UI shows above the prompt.

    ``thoughts`` is ordered oldest to newest; the most recent lines fill the
    log area.  All fields have defaults so callers can pass only the part
    they want to change.
    """

    model: str = "local"
    status: str = "idle"
    thoughts: tuple[str, ...] = ()
    prompt: str = "> "


class BrainUI:
    """Render an animated ASCII brain plus a typed fallback prompt.

    Parameters
    ----------
    stream:
        Text stream used for output.  Defaults to ``sys.stdout``.
    clock:
        Callable returning seconds, used to pace the animation.  Defaults to
        ``time.monotonic``.
    width:
        Exact character width of every rendered line.
    input_stream:
        Stream used by :meth:`read_line`.  Defaults to ``sys.stdin``.
    fps:
        Animation frames per second.
    log_lines:
        Number of wrapped thought lines kept on screen.
    sleep:
        Callable used by :meth:`run_interactive`; tests inject a recorder.
    responder:
        Optional callable that turns user text into a reply line.  No model
        is invoked by default.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        clock: Callable[[], float] | None = None,
        width: int = DEFAULT_WIDTH,
        *,
        input_stream: TextIO | None = None,
        fps: float = DEFAULT_FPS,
        log_lines: int = MAX_LOG_LINES,
        sleep: Callable[[float], None] | None = None,
        responder: Callable[[str], str | None] | None = None,
        model: str = "local",
        status: str = "idle",
    ) -> None:
        if width < 1:
            raise ValueError("width must be positive")
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.stream = stream if stream is not None else sys.stdout
        self.input_stream = input_stream if input_stream is not None else sys.stdin
        self.clock = clock if clock is not None else time.monotonic
        self.sleep = sleep if sleep is not None else time.sleep
        self.responder = responder
        self.width = int(width)
        self.fps = float(fps)
        self.log_lines = max(1, int(log_lines))
        self.frames = BRAIN_FRAMES
        self.model = str(model)
        self.status = str(status)
        self.prompt_text = "> "
        self.thoughts: list[str] = []
        self._running = True

    # -- state ---------------------------------------------------------
    def _now(self) -> float:
        try:
            return float(self.clock())
        except (TypeError, ValueError):
            return 0.0

    def frame_index(self) -> int:
        """Return the animation frame selected by the clock."""
        return int(self._now() * self.fps) % len(self.frames)

    def _state(self, state: UIState | Mapping[str, object] | str | None) -> UIState:
        if state is None:
            return UIState(
                model=self.model,
                status=self.status,
                thoughts=tuple(self.thoughts),
                prompt=self.prompt_text,
            )
        if isinstance(state, UIState):
            return state
        if isinstance(state, str):
            return UIState(
                model=self.model,
                status=state,
                thoughts=tuple(self.thoughts),
                prompt=self.prompt_text,
            )
        if isinstance(state, Mapping):
            thoughts = state.get("thoughts", self.thoughts)
            if isinstance(thoughts, str):
                thoughts = (thoughts,)
            elif not isinstance(thoughts, Sequence):
                thoughts = (str(thoughts),)
            return UIState(
                model=str(state.get("model", self.model)),
                status=str(state.get("status", self.status)),
                thoughts=tuple(str(item) for item in thoughts),
                prompt=str(state.get("prompt", self.prompt_text)),
            )
        raise TypeError(f"unsupported state type: {type(state).__name__}")

    # -- rendering -----------------------------------------------------
    def _fit(self, text: str) -> str:
        """Clamp one line to the configured width."""
        return text[: self.width].ljust(self.width)

    def _center(self, text: str) -> str:
        if len(text) >= self.width:
            return self._fit(text)
        left = (self.width - len(text)) // 2
        return self._fit(" " * left + text)

    def _log_lines(self, thoughts: Sequence[str]) -> list[str]:
        wrapped: list[str] = []
        wrap_width = max(1, self.width - 2)
        for thought in thoughts:
            for physical in str(thought).splitlines() or [""]:
                pieces = textwrap.wrap(physical, width=wrap_width) or [""]
                wrapped.extend("  " + piece for piece in pieces)
        tail = wrapped[-self.log_lines :]
        return [""] * (self.log_lines - len(tail)) + tail

    def frame(self, state: UIState | Mapping[str, object] | str | None = None) -> str:
        """Return the complete rendered frame as one string (no trailing newline)."""
        view = self._state(state)
        spinner = _SPINNER[int(self._now() * 4) % len(_SPINNER)]
        status = f"[{spinner}] model: {view.model}  |  state: {view.status}"
        lines: list[str] = [self._center(line) for line in self.frames[self.frame_index()]]
        lines.append(self._fit(""))
        lines.append(self._fit(status))
        lines.append(self._fit("-" * self.width))
        lines.extend(self._fit(line) for line in self._log_lines(view.thoughts))
        lines.append(self._fit("-" * self.width))
        lines.append(self._fit(view.prompt))
        return "\n".join(lines)

    def render(self, state: UIState | Mapping[str, object] | str | None = None, *, newline: bool = True) -> str:
        """Write one frame to the stream and return it."""
        text = self.frame(state)
        self.stream.write(text + ("\n" if newline else ""))
        self.stream.flush()
        return text

    # -- input ---------------------------------------------------------
    def _remember(self, thought: str) -> None:
        self.thoughts.append(thought)
        del self.thoughts[:-MAX_THOUGHTS]

    def read_line(self) -> str | None:
        """Read one line without a prompt; return ``None`` at EOF."""
        raw = self.input_stream.readline()
        if raw == "":
            return None
        return raw.rstrip("\r\n")

    def prompt(self, state: UIState | Mapping[str, object] | str | None = None) -> str | None:
        """Render, read one line, process it, and return the raw input."""
        self.render(state, newline=False)
        line = self.read_line()
        if line is None:
            self._running = False
            return None
        if not self.handle_input(line):
            self._running = False
        return line

    def handle_input(self, line: str) -> bool:
        """Record one input line.  Return ``False`` when the session should end."""
        text = str(line).strip()
        if not text:
            self._remember("you: (empty)")
            return True
        command = text.lower()
        if command in {"/quit", "/exit", "quit", "exit"}:
            self._remember("talos: session closed")
            return False
        if command == "/help" or command == "/?":
            self._remember("talos: commands: /status /model NAME /help /quit")
            return True
        if command == "/status":
            self._remember(f"talos: model {self.model}, state {self.status}")
            return True
        if command == "/model" or command.startswith("/model "):
            name = text[len("/model") :].strip()
            if name:
                self.model = name
            self._remember(f"talos: model {self.model}")
            return True
        self._remember(f"you: {text}")
        if self.responder is not None:
            reply = self.responder(text)
            if reply:
                self._remember(f"talos: {reply}")
        return True

    # -- sessions ------------------------------------------------------
    def run_script(self, script: Iterable[str]) -> int:
        """Run a fixed list of input lines; used by tests and demos."""
        self._running = True
        self.render()
        for line in script:
            keep_going = self.handle_input(line)
            self.render()
            if not keep_going:
                break
        self._running = False
        return 0

    def is_tty(self) -> bool:
        isatty = getattr(self.stream, "isatty", None)
        try:
            return bool(isatty and isatty())
        except (OSError, ValueError):
            return False

    def run_interactive(self) -> int:
        """Animate on a real terminal; emit one static frame otherwise."""
        if not self.is_tty():
            self.render()
            return 0
        for _ in self.frames:
            self.render()
            self.sleep(1.0 / self.fps)
        self._running = True
        while self._running:
            if self.prompt() is None:
                break
        return 0


def main(
    argv: Iterable[str] | None = None,
    stream: TextIO | None = None,
    clock: Callable[[], float] | None = None,
    script: Iterable[str] | None = None,
    input_stream: TextIO | None = None,
    sleep: Callable[[float], None] | None = None,
    width: int | None = None,
) -> int:
    """Entry point for the text fallback.

    ``script`` runs a short deterministic session, which is what tests use.
    Without a script the UI reads stdin, but only a TTY gets the animation;
    piped output receives exactly one static frame and no sleeping.
    """
    parser = argparse.ArgumentParser(prog="brain-ui", description="Talos text fallback")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    args = parser.parse_args(list(argv) if argv is not None else [])
    chosen_width = args.width or width or DEFAULT_WIDTH

    ui = BrainUI(
        stream=stream,
        clock=clock,
        width=chosen_width,
        input_stream=input_stream,
        fps=args.fps,
        sleep=sleep,
    )
    ui.status = "text mode"
    ui.thoughts.extend(["speech unavailable", "typing fallback ready"])
    if script is not None:
        return ui.run_script(script)
    return ui.run_interactive()


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main(sys.argv[1:]))
