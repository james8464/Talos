"""Real browser automation for Talos via the Chrome DevTools Protocol.

This is the piece that lets Talos act like Codex's in-app browser: open a page,
read its DOM and text, click, type, capture screenshots and evaluate page
JavaScript. It drives an already-installed Chrome-family browser instead of
shipping a new engine.

Transport order:

1. CDP against a locally launched Chrome/Chromium/Edge/Brave started with
   ``--remote-debugging-port=0`` and a throwaway profile, or against an
   existing browser when ``TALOS_CDP_PORT`` names a live DevTools endpoint.
2. Safari AppleScript, only when no Chrome-family binary or no ``websockets``
   package exists. It requires Safari to already be running and
   ``Develop > Allow JavaScript from Apple Events`` to be enabled.

Nothing here touches audio, the launchd service, or private ``state/`` content.
Typed text and screenshot pixels are never logged. Navigating is restricted to
``http(s)`` URLs; selectors are validated and JSON-encoded before they reach the
page, so neither can inject JavaScript.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "Availability",
    "Browser",
    "BrowserError",
    "BrowserPolicyError",
    "BrowserScriptError",
    "BrowserTimeout",
    "BrowserUnavailable",
    "Page",
    "SelectorError",
    "Snapshot",
    "availability",
    "available",
    "find_browser",
    "session",
    "validate_selector",
    "validate_url",
]

DEFAULT_TIMEOUT = 10.0
LAUNCH_TIMEOUT = 20.0
SELECTOR_MAX = 512
SCRIPT_MAX = 100_000
TEXT_MAX = 12_000
HTML_MAX = 200_000
ELEMENTS_MAX = 80
SCREENSHOT_MAX_HEIGHT = 20_000
VIEWPORT = (1280, 800)

OSASCRIPT = "/usr/bin/osascript"
SAFARI_APP = "/Applications/Safari.app"

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
PATH_BINARIES = ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")

_SELECTOR_FORBIDDEN = set(";{}\n\r\t\0")


class BrowserError(RuntimeError):
    """A browser action failed."""


class BrowserUnavailable(BrowserError):
    """No usable browser or transport exists on this machine."""


class BrowserTimeout(BrowserError):
    """A browser action did not finish inside its time budget."""


class BrowserPolicyError(BrowserError):
    """The caller asked for something Talos refuses to do."""


class SelectorError(BrowserPolicyError):
    """A selector is malformed or matches nothing."""


class BrowserScriptError(BrowserError):
    """Page JavaScript raised or returned something unusable."""


@dataclass(frozen=True)
class Availability:
    """What browser transport, if any, this machine can offer."""

    available: bool
    channel: str
    message: str
    binary: str | None = None

    def __bool__(self) -> bool:
        return self.available


@dataclass(frozen=True)
class Snapshot:
    """A compact, model-readable view of the open page."""

    url: str
    title: str
    ready_state: str
    text: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    html: str | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "ready_state": self.ready_state,
            "text": self.text,
            "elements": self.elements,
            "truncated": self.truncated,
        }


def _usable(path: str | os.PathLike[str]) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def find_browser() -> str | None:
    """Return the Chrome-family binary Talos should drive, if one exists."""
    explicit = os.environ.get("TALOS_BROWSER_BINARY")
    if explicit:
        return explicit if _usable(explicit) else None
    for candidate in CHROME_CANDIDATES:
        if _usable(candidate):
            return candidate
    for name in PATH_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _has_websockets() -> bool:
    return importlib.util.find_spec("websockets") is not None


def availability(executable: str | None = None) -> Availability:
    """Report whether CDP or the Safari fallback can be used.

    Cheap: this never launches a browser and never opens a socket.
    """
    if executable is not None and not _usable(executable):
        return Availability(False, "none", f"no browser executable at {executable}", None)

    explicit = executable if executable is not None else os.environ.get("TALOS_BROWSER_BINARY")
    if explicit and not _usable(explicit):
        # An explicit override is a decision, not a hint: report it instead of
        # silently switching to a different browser.
        return Availability(False, "none", f"no browser executable at {explicit}", None)

    binary = explicit or find_browser()
    if binary and _has_websockets():
        return Availability(True, "cdp", f"Chrome DevTools Protocol via {binary}", binary)

    if binary and not _has_websockets():
        detail = f"websockets is required for CDP (pip install websockets); found {binary}"
    else:
        searched = ", ".join(CHROME_CANDIDATES)
        detail = (
            f"no Chrome/Chromium binary found (looked in {searched}; "
            "set TALOS_BROWSER_BINARY to override)"
        )

    if sys.platform == "darwin" and Path(SAFARI_APP).exists():
        return Availability(
            True,
            "safari-applescript",
            f"{detail}; falling back to Safari AppleScript, which needs Safari running and "
            "'Develop > Allow JavaScript from Apple Events' enabled",
            SAFARI_APP,
        )
    return Availability(False, "none", detail, None)


def available() -> bool:
    """True when any browser transport is usable."""
    return availability().available


def validate_url(url: str) -> str:
    """Return a normalized http(s) URL or raise :class:`BrowserPolicyError`."""
    if not isinstance(url, str):
        raise BrowserPolicyError(f"url must be a string, got {type(url).__name__}")
    candidate = url.strip()
    if not candidate:
        raise BrowserPolicyError("url must not be empty")
    if len(candidate) > 4096:
        raise BrowserPolicyError("url is longer than 4096 characters")
    if any(character in candidate for character in "\r\n\t\0"):
        raise BrowserPolicyError("url must not contain control characters")
    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise BrowserPolicyError(
            f"refusing to navigate to scheme {scheme or '(none)'!r}; only http and https are allowed"
        )
    if not parsed.hostname:
        raise BrowserPolicyError(f"url has no host: {candidate!r}")
    if parsed.username or parsed.password:
        raise BrowserPolicyError("url must not embed credentials")
    return candidate


def validate_selector(selector: str) -> str:
    """Return a selector safe to hand to ``document.querySelector``."""
    if not isinstance(selector, str):
        raise SelectorError(f"selector must be a string, got {type(selector).__name__}")
    if not selector.strip():
        raise SelectorError("selector must not be empty")
    if len(selector) > SELECTOR_MAX:
        raise SelectorError(f"selector is longer than {SELECTOR_MAX} characters")
    bad = sorted({character for character in selector if character in _SELECTOR_FORBIDDEN})
    if bad:
        printable = ", ".join(repr(character) for character in bad)
        raise SelectorError(f"selector contains characters that are not valid CSS: {printable}")
    return selector


def _write_private(path: str | os.PathLike[str], data: bytes) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chmod(target, 0o600)
    return target


def _tail(path: Path, limit: int = 600) -> str:
    try:
        return path.read_text(errors="replace")[-limit:].strip()
    except OSError:
        return ""


class _Transport:
    """Primitives a page needs from whichever browser channel is active."""

    channel = "none"

    def start(self) -> None:
        raise NotImplementedError

    def new_page(self, width: int, height: int) -> str:
        raise NotImplementedError

    def close_page(self, page: str) -> None:
        raise NotImplementedError

    def evaluate(self, expression: str, page: str, timeout: float | None = None) -> Any:
        raise NotImplementedError

    def navigate(self, url: str, page: str, timeout: float | None = None) -> None:
        raise NotImplementedError

    def click_at(self, x: float, y: float, page: str) -> None:
        raise NotImplementedError

    def insert_text(self, text: str, page: str) -> None:
        raise NotImplementedError

    def screenshot(self, page: str, full_page: bool) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _CDPTransport(_Transport):
    channel = "cdp"

    def __init__(self, binary: str, *, headless: bool, timeout: float, cdp_port: int | None = None):
        self.binary = binary
        self.headless = headless
        self.timeout = timeout
        self.cdp_port = cdp_port
        self._process: subprocess.Popen[bytes] | None = None
        self._profile: Path | None = None
        self._stderr: Path | None = None
        self._socket = None
        self._module = None
        self._ids = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=256)
        self._pages: list[str] = []
        self._targets: dict[str, str] = {}

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        try:
            from websockets.exceptions import ConnectionClosed
            from websockets.sync.client import connect
        except ImportError as error:  # pragma: no cover - availability() checks this first
            raise BrowserUnavailable(
                "websockets is required for Chrome DevTools Protocol: pip install websockets"
            ) from error
        self._module = (connect, ConnectionClosed)
        descriptor, name = tempfile.mkstemp(prefix="talos-browser-stderr-")
        os.close(descriptor)
        self._stderr = Path(name)

        port = self.cdp_port
        if port is None:
            port = self._running_port()
        if port is not None and not self._probe(port):
            port = None
        if port is None:
            port = self._launch()
        self._connect(port)

    def _running_port(self) -> int | None:
        raw = os.environ.get("TALOS_CDP_PORT")
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as error:
            raise BrowserUnavailable(f"TALOS_CDP_PORT must be an integer, got {raw!r}") from error

    @staticmethod
    def _probe(port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0):
                return True
        except (OSError, urllib.error.URLError):
            return False

    def _launch(self) -> int:
        profile = Path(tempfile.mkdtemp(prefix="talos-browser-"))
        os.chmod(profile, 0o700)
        self._profile = profile
        argv = [
            self.binary,
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-popup-blocking",
            "--hide-scrollbars",
        ]
        argv.append("--headless=new" if self.headless else "--new-window")
        argv.append("about:blank")
        assert self._stderr is not None
        stderr = self._stderr.open("wb")
        try:
            self._process = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as error:
            raise BrowserUnavailable(f"could not start {self.binary}: {error}") from error
        finally:
            stderr.close()

        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            if port_file.is_file():
                try:
                    port = int(port_file.read_text().split()[0])
                except (IndexError, ValueError):
                    port = 0
                if port and self._probe(port):
                    return port
            if self._process.poll() is not None:
                detail = _tail(self._stderr) if self._stderr else "no stderr"
                raise BrowserUnavailable(
                    f"{self.binary} exited during startup (code {self._process.returncode}): {detail}"
                )
            time.sleep(0.05)
        raise BrowserUnavailable(f"{self.binary} did not expose a DevTools port within {LAUNCH_TIMEOUT}s")

    def _connect(self, port: int) -> None:
        assert self._module is not None
        connect, _ = self._module
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2.0) as response:
                version = json.loads(response.read())
            endpoint = version["webSocketDebuggerUrl"]
        except (OSError, ValueError, KeyError) as error:
            raise BrowserUnavailable(f"could not read the DevTools endpoint on port {port}: {error}") from error
        try:
            self._socket = connect(endpoint, open_timeout=self.timeout, max_size=64 * 1024 * 1024)
        except Exception as error:
            raise BrowserUnavailable(f"could not connect to the DevTools websocket: {error}") from error

    def close(self) -> None:
        for target in list(self._pages):
            with contextlib.suppress(BrowserError):
                self.call("Target.closeTarget", {"targetId": target})
        self._pages.clear()
        self._targets.clear()
        if self._socket is not None:
            with contextlib.suppress(Exception):
                self._socket.close()
            self._socket = None
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._process.wait(5)
            self._process = None
        if self._profile is not None:
            root = Path(tempfile.gettempdir())
            if self._profile.parent == root and self._profile.name.startswith("talos-browser-"):
                shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None
        if self._stderr is not None:
            with contextlib.suppress(OSError):
                self._stderr.unlink()
            self._stderr = None

    # -- protocol ------------------------------------------------------
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one CDP command and return its result, routing around events."""
        if self._socket is None or self._module is None:
            raise BrowserError(f"browser transport is not running; cannot call {method}")
        _, connection_closed = self._module
        self._ids += 1
        identifier = self._ids
        message: dict[str, Any] = {"id": identifier, "method": method}
        if params:
            message["params"] = params
        if session:
            message["sessionId"] = session
        budget = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        try:
            self._socket.send(json.dumps(message))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                reply = json.loads(self._socket.recv(timeout=remaining))
                if reply.get("id") == identifier:
                    error = reply.get("error")
                    if error:
                        detail = error.get("message") if isinstance(error, dict) else str(error)
                        raise BrowserError(f"{method} failed: {detail}")
                    return reply.get("result") or {}
                if reply.get("method"):
                    self._events.append(reply)
        except TimeoutError as error:
            raise BrowserTimeout(f"{method} timed out after {budget:g}s") from error
        except connection_closed as error:
            raise BrowserError(f"browser connection closed during {method}: {error}") from error

    def _saw(self, method: str, session: str) -> bool:
        return any(
            event.get("method") == method and event.get("sessionId") == session
            for event in list(self._events)
        )

    def _forget_events(self) -> None:
        self._events.clear()

    # -- transport primitives -----------------------------------------
    def new_page(self, width: int, height: int) -> str:
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        attached = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})
        page = attached["sessionId"]
        self._pages.append(target)
        self.call("Page.enable", session=page)
        self.call("Runtime.enable", session=page)
        self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
            session=page,
        )
        # The target id is what we close; keep the mapping for close_page.
        self._targets[page] = target
        return page

    def close_page(self, page: str) -> None:
        target = self._targets.pop(page, None)
        if target is None:
            return
        if target in self._pages:
            self._pages.remove(target)
        with contextlib.suppress(BrowserError):
            self.call("Target.closeTarget", {"targetId": target})

    def navigate(self, url: str, page: str, timeout: float | None = None) -> None:
        self._forget_events()
        result = self.call("Page.navigate", {"url": url}, session=page, timeout=timeout)
        failure = result.get("errorText")
        if failure and failure != "net::ERR_ABORTED":
            raise BrowserError(f"navigation to {url} failed: {failure}")
        self._wait_loaded(page, url, self.timeout if timeout is None else timeout)

    def _wait_loaded(self, page: str, url: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        previous = None
        target = urlparse(url)
        while time.monotonic() < deadline:
            if self._saw("Page.loadEventFired", page):
                return
            try:
                state = self.evaluate(
                    "({ready: document.readyState, href: location.href})",
                    page,
                    timeout=max(0.5, min(2.0, deadline - time.monotonic())),
                )
            except BrowserTimeout:
                state = None
            except BrowserError as error:
                if "context" not in str(error).lower() and "destroyed" not in str(error).lower():
                    raise
                state = None
            if isinstance(state, dict):
                if previous is None:
                    previous = state.get("href")
                href = state.get("href") or ""
                same_document = urlparse(href).path == target.path and urlparse(href).netloc == target.netloc
                if state.get("ready") == "complete" and (href != previous or same_document):
                    return
            time.sleep(0.05)
        raise BrowserTimeout(f"page did not finish loading {url} within {timeout:g}s")

    def evaluate(self, expression: str, page: str, timeout: float | None = None) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            session=page,
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            raise BrowserScriptError(_exception_text(result["exceptionDetails"]))
        value = result.get("result", {})
        if "value" in value:
            return value["value"]
        if "unserializableValue" in value:
            return value["unserializableValue"]
        return None

    def click_at(self, x: float, y: float, page: str) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, session=page)
        self.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base}, session=page)
        self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base}, session=page)

    def insert_text(self, text: str, page: str) -> None:
        self.call("Input.insertText", {"text": text}, session=page)

    def screenshot(self, page: str, full_page: bool) -> bytes:
        params: dict[str, Any] = {"format": "png", "fromSurface": True, "captureBeyondViewport": full_page}
        if full_page:
            metrics = self.call("Page.getLayoutMetrics", session=page)["cssContentSize"]
            height = min(float(metrics.get("height") or 0), SCREENSHOT_MAX_HEIGHT)
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": float(metrics.get("width") or VIEWPORT[0]),
                "height": max(height, 1.0),
                "scale": 1,
            }
        data = self.call("Page.captureScreenshot", params, session=page)["data"]
        return base64.b64decode(data)


class _SafariTransport(_Transport):
    """Last-resort Safari transport.

    Requires Safari to be running with JavaScript from Apple Events enabled.
    Navigation reuses the front document; screenshots are not supported here.
    """

    channel = "safari-applescript"

    def __init__(self, *, timeout: float):
        self.timeout = timeout
        self._started = False

    def _osascript(self, script: str, argument: str | None = None, timeout: float | None = None) -> str:
        argv = [OSASCRIPT, "-e", script]
        if argument is not None:
            argv.extend(["--", argument])
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout if timeout is None else timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise BrowserError(f"osascript failed: {error}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[:600]
            if "javascript" in detail.lower() and ("allow" in detail.lower() or "not allowed" in detail.lower()):
                raise BrowserUnavailable(
                    "Safari refuses JavaScript from Apple Events; enable "
                    "'Develop > Allow JavaScript from Apple Events' or install Chrome for CDP"
                )
            raise BrowserError(f"osascript exit {completed.returncode}: {detail}")
        return (completed.stdout or "").strip()

    def start(self) -> None:
        if sys.platform != "darwin":
            raise BrowserUnavailable("Safari AppleScript is only available on macOS")
        running = self._osascript('application "Safari" is running')
        if running.strip().lower() != "true":
            raise BrowserUnavailable(
                "Safari is not running and Talos will not launch it; install Chrome for CDP support"
            )
        self._started = True

    def new_page(self, width: int, height: int) -> str:
        if not self._started:
            raise BrowserError("Safari transport is not started")
        return "front"

    def close_page(self, page: str) -> None:
        return None

    def evaluate(self, expression: str, page: str, timeout: float | None = None) -> Any:
        return self._osascript(
            'on run argv\n'
            '  tell application "Safari" to do JavaScript (item 1 of argv) in front document\n'
            "end run",
            expression,
            timeout=timeout,
        )

    def navigate(self, url: str, page: str, timeout: float | None = None) -> None:
        self._osascript(
            'on run argv\n'
            '  tell application "Safari" to set URL of front document to (item 1 of argv)\n'
            "end run",
            url,
            timeout=timeout,
        )
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            state = self.evaluate("document.readyState", page)
            if state == "complete":
                return
            time.sleep(0.1)
        raise BrowserTimeout(f"Safari did not finish loading {url}")

    def click_at(self, x: float, y: float, page: str) -> None:
        raise BrowserError("the Safari fallback cannot synthesize trusted clicks; use CDP")

    def insert_text(self, text: str, page: str) -> None:
        raise BrowserError("the Safari fallback cannot synthesize keystrokes; use CDP")

    def screenshot(self, page: str, full_page: bool) -> bytes:
        raise BrowserError(
            "the Safari fallback cannot capture screenshots; use CDP or talos_core.mac.computer_use"
        )

    def close(self) -> None:
        # Never close tabs or quit Safari: the user owns that session.
        self._started = False


def _exception_text(details: dict[str, Any]) -> str:
    exception = details.get("exception") or {}
    description = exception.get("description") or details.get("text") or "page JavaScript raised"
    return str(description).strip()[:800]


def _json_expression(expression: str) -> str:
    """Wrap an expression so both transports return a JSON string."""
    return f"JSON.stringify((() => {{ {expression} }})())"


class Page:
    """One browser tab driven through a transport."""

    def __init__(self, transport: _Transport, session: str, url: str, timeout: float):
        self._transport = transport
        self.session = session
        self._timeout = timeout
        self._url = url
        self._closed = False

    def __repr__(self) -> str:
        return f"Page({self._url!r}, channel={self._transport.channel})"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def url(self) -> str:
        """The live page URL, falling back to the URL this page was opened with."""
        if self._closed:
            return self._url
        try:
            return str(self._eval_json("return location.href;") or self._url)
        except BrowserError:
            return self._url

    # -- internals -----------------------------------------------------
    def _require_open(self) -> None:
        if self._closed:
            raise BrowserError("this page is closed; call open_page() again")

    def _eval_json(self, expression: str, timeout: float | None = None) -> Any:
        self._require_open()
        raw = self._transport.evaluate(_json_expression(expression), self.session, timeout)
        if raw is None:
            return None
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except ValueError as error:
            raise BrowserScriptError(f"page returned malformed JSON: {raw[:200]!r}") from error

    def _selector_json(self, selector: str) -> str:
        return json.dumps(validate_selector(selector))

    def wait_for(self, selector: str, timeout: float | None = None) -> dict[str, Any]:
        """Wait until a selector matches an element; raise on malformed selectors."""
        selector = validate_selector(selector)
        budget = self._timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        encoded = json.dumps(selector)
        probe = (
            f"try {{ const el = document.querySelector({encoded});"
            " return el ? {found: true} : {found: false}; }"
            " catch (error) { return {error: String((error && error.message) || error)}; }"
        )
        while time.monotonic() < deadline:
            result = self._eval_json(probe) or {}
            if result.get("error"):
                raise SelectorError(f"invalid selector {selector!r}: {result['error']}")
            if result.get("found"):
                return {"selector": selector, "found": True}
            time.sleep(0.05)
        raise BrowserTimeout(f"no element matched {selector!r} within {budget:g}s")

    def goto(self, url: str, timeout: float | None = None) -> None:
        self._require_open()
        self._url = validate_url(url)
        self._transport.navigate(self._url, self.session, timeout)

    # -- reading -------------------------------------------------------
    def text(self, selector: str | None = None, limit: int = TEXT_MAX) -> str:
        """Visible text of the page, or of one element."""
        if selector is None:
            expression = "return (document.body ? document.body.innerText : '') || '';"
        else:
            self.wait_for(selector)
            encoded = self._selector_json(selector)
            expression = (
                f"const el = document.querySelector({encoded});"
                "if (!el) return null;"
                "return el.innerText || el.textContent || '';"
            )
        value = self._eval_json(expression)
        if value is None:
            raise SelectorError(f"no element matched {selector!r}")
        return str(value)[:limit]

    def html(self, limit: int = HTML_MAX) -> str:
        value = self._eval_json(
            "return document.documentElement ? document.documentElement.outerHTML : '';"
        )
        return str(value or "")[:limit]

    def snapshot(self, *, include_html: bool = False, include_elements: bool = True) -> Snapshot:
        """Return url, title, ready state, text and interactive elements."""
        state = self._eval_json(
            "return {url: location.href, title: document.title, ready: document.readyState,"
            " text: (document.body ? document.body.innerText : '') || ''};"
        ) or {}
        elements: list[dict[str, Any]] = []
        if include_elements:
            elements = self._elements()[:ELEMENTS_MAX]
        raw_text = str(state.get("text") or "")
        return Snapshot(
            url=str(state.get("url") or self._url),
            title=str(state.get("title") or ""),
            ready_state=str(state.get("ready") or "unknown"),
            text=raw_text[:TEXT_MAX],
            elements=elements,
            html=self.html() if include_html else None,
            truncated=len(raw_text) > TEXT_MAX,
        )

    def _elements(self) -> list[dict[str, Any]]:
        query = (
            "const nodes = Array.from(document.querySelectorAll("
            "'a[href],button,input,select,textarea,[role=button],[contenteditable=true]')).slice(0, 200);"
            "return nodes.map((el) => {"
            " const box = el.getBoundingClientRect();"
            " const label = (el.innerText || el.getAttribute('aria-label') || "
            "el.getAttribute('placeholder') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);"
            " return {tag: el.tagName.toLowerCase(),"
            " id: el.id || null,"
            " name: el.getAttribute('name') || null,"
            " type: el.getAttribute('type') || null,"
            " text: label,"
            " href: el.tagName === 'A' ? (el.getAttribute('href') || '').slice(0, 300) : null,"
            " rendered: !!(box.width && box.height)};"
            "});"
        )
        result = self._eval_json(query)
        return [item for item in (result or []) if isinstance(item, dict)]

    # -- acting --------------------------------------------------------
    def click(self, selector: str, timeout: float | None = None) -> dict[str, Any]:
        """Click an element with trusted mouse events, or JS click if it has no box."""
        self.wait_for(selector, timeout)
        encoded = self._selector_json(selector)
        rect = self._eval_json(
            f"const el = document.querySelector({encoded});"
            "if (!el) return null;"
            "el.scrollIntoView({block: 'center', inline: 'center'});"
            "const box = el.getBoundingClientRect();"
            "return {x: box.left + box.width / 2, y: box.top + box.height / 2,"
            " width: box.width, height: box.height};"
        )
        if not isinstance(rect, dict):
            raise SelectorError(f"no element matched {selector!r}")
        if rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            self._transport.click_at(float(rect["x"]), float(rect["y"]), self.session)
            return {"selector": selector, "method": "mouse"}
        self._eval_json(
            f"const el = document.querySelector({encoded}); if (el) el.click(); return true;"
        )
        return {"selector": selector, "method": "js"}

    def type(
        self,
        selector: str,
        text: str,
        *,
        clear: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Focus an element and type text through the browser input pipeline."""
        if not isinstance(text, str):
            raise BrowserError(f"text must be a string, got {type(text).__name__}")
        if len(text) > 20_000:
            raise BrowserPolicyError("text is longer than 20000 characters")
        self.wait_for(selector, timeout)
        encoded = self._selector_json(selector)
        clear_js = (
            "if (clear) {"
            " if (typeof el.select === 'function') el.select();"
            " else { const range = document.createRange(); range.selectNodeContents(el);"
            "  const selection = window.getSelection(); selection.removeAllRanges();"
            "  selection.addRange(range); }"
            "}"
        )
        focus = self._eval_json(
            f"const el = document.querySelector({encoded});"
            "if (!el) return {focused: false};"
            "el.scrollIntoView({block: 'center'});"
            "if (typeof el.focus === 'function') el.focus();"
            "const clear = " + ("true" if clear else "false") + ";"
            + clear_js
            + " if (!clear) { try {"
            " if (typeof el.setSelectionRange === 'function') "
            "{ const end = el.value.length; el.setSelectionRange(end, end); }"
            " } catch (error) {} }"
            "const active = document.activeElement;"
            "return {focused: !!el && (active === el || el.contains(active))};"
        ) or {}
        if not focus.get("focused"):
            raise BrowserError(f"element {selector!r} cannot take focus, so text was not typed")
        self._transport.insert_text(text, self.session)
        return {"selector": selector, "chars": len(text), "cleared": bool(clear)}

    def screenshot(self, path: str | os.PathLike[str] | None = None, *, full_page: bool = False) -> Path:
        """Capture a PNG; pixels are written to disk and never logged."""
        self._require_open()
        data = self._transport.screenshot(self.session, full_page)
        if path is None:
            descriptor, name = tempfile.mkstemp(prefix="talos-browser-shot-", suffix=".png")
            os.close(descriptor)
            target = Path(name)
        else:
            target = Path(path).expanduser()
        return _write_private(target, data)

    def eval_js(self, script: str) -> Any:
        """Evaluate JavaScript in the page and return its value.

        This runs with the page's own privileges, so callers should not paste
        untrusted strings into it. The Safari fallback returns raw AppleScript
        text rather than typed JSON values.
        """
        if not isinstance(script, str):
            raise BrowserScriptError(f"script must be a string, got {type(script).__name__}")
        if not script.strip():
            raise BrowserScriptError("script must not be empty")
        if len(script) > SCRIPT_MAX:
            raise BrowserScriptError(f"script is longer than {SCRIPT_MAX} characters")
        self._require_open()
        return self._transport.evaluate(script, self.session)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transport.close_page(self.session)


class Browser:
    """A local Chrome-family browser driven over CDP, with a Safari fallback.

    Headless by default so browsing never steals focus; pass ``headless=False``
    when a person should watch the window. Set ``TALOS_CDP_PORT`` to attach to
    an already-running Chrome instead of launching a throwaway profile.
    """

    def __init__(
        self,
        executable: str | None = None,
        *,
        headless: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        width: int = VIEWPORT[0],
        height: int = VIEWPORT[1],
        cdp_port: int | None = None,
    ):
        self.executable = executable
        self.headless = headless
        self.timeout = timeout
        self.width = width
        self.height = height
        self.cdp_port = cdp_port
        self.status = availability(executable)
        self._transport: _Transport | None = None
        self._page: Page | None = None
        self._closed = False

    def __enter__(self) -> Browser:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Browser(channel={self.status.channel!r}, available={self.status.available})"

    def available(self) -> bool:
        return self.status.available

    def _ensure_transport(self) -> _Transport:
        if self._closed:
            raise BrowserError("this browser is closed; create a new Browser()")
        if self._transport is not None:
            return self._transport
        status = availability(self.executable)
        self.status = status
        if not status:
            raise BrowserUnavailable(status.message)
        if status.channel == "cdp":
            transport: _Transport = _CDPTransport(
                status.binary or "", headless=self.headless, timeout=self.timeout, cdp_port=self.cdp_port
            )
        elif status.channel == "safari-applescript":
            transport = _SafariTransport(timeout=self.timeout)
        else:  # pragma: no cover - availability() only returns the two channels above
            raise BrowserUnavailable(status.message)
        try:
            transport.start()
        except BrowserUnavailable:
            transport.close()
            raise
        except BrowserError:
            transport.close()
            raise
        except Exception as error:
            transport.close()
            raise BrowserError(f"could not start the {status.channel} transport: {error}") from error
        self._transport = transport
        return transport

    def open_page(self, url: str, *, timeout: float | None = None) -> Page:
        """Open an http(s) URL and return the page once it has loaded."""
        target = validate_url(url)
        transport = self._ensure_transport()
        session = transport.new_page(self.width, self.height)
        page = Page(transport, session, target, self.timeout)
        try:
            page.goto(target, timeout)
        except Exception:
            page.close()
            raise
        self._page = page
        return page

    def _active(self) -> Page:
        if self._page is None or self._page.closed:
            raise BrowserError("no page is open; call open_page(url) first")
        return self._page

    def goto(self, url: str, *, timeout: float | None = None) -> Page:
        page = self._active()
        page.goto(url, timeout)
        return page

    def text(self, selector: str | None = None, limit: int = TEXT_MAX) -> str:
        return self._active().text(selector, limit)

    def snapshot(self, *, include_html: bool = False, include_elements: bool = True) -> Snapshot:
        return self._active().snapshot(include_html=include_html, include_elements=include_elements)

    def click(self, selector: str, timeout: float | None = None) -> dict[str, Any]:
        return self._active().click(selector, timeout)

    def type(self, selector: str, text: str, *, clear: bool = True, timeout: float | None = None) -> dict[str, Any]:
        return self._active().type(selector, text, clear=clear, timeout=timeout)

    def screenshot(self, path: str | os.PathLike[str] | None = None, *, full_page: bool = False) -> Path:
        return self._active().screenshot(path, full_page=full_page)

    def eval_js(self, script: str) -> Any:
        return self._active().eval_js(script)

    def wait_for(self, selector: str, timeout: float | None = None) -> dict[str, Any]:
        return self._active().wait_for(selector, timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None


@contextlib.contextmanager
def session(**options: Any):
    """Use a browser for the duration of a block and always shut it down."""
    browser = Browser(**options)
    try:
        yield browser
    finally:
        browser.close()
