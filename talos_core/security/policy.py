"""Shared approval and policy engine for Talos actions.

The engine follows the shape of Codex-style sandbox profiles:

* ``read-only`` (the default) permits observation and interface actions but
  refuses filesystem writes;
* ``workspace-write`` permits writes only below configured roots;
* ``full`` permits everything except explicit denials and destructive actions,
  which still require confirmation.

Policy is deny-first: an unreadable or unparseable document, an unknown profile,
an unknown action, or a malformed rule all fail closed. ``full`` is never the
default and cannot be reached by falling back from a bad document.

Every decision carries a content-free audit record: payloads and command
arguments are reduced to a length and a SHA-256 digest, never stored as text.
Per-app "always allow" grants live in ``state/policy-grants.json`` with mode
0600 and are loaded on the next run so an approved app is not re-prompted.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

CONFIG_FILE = "policy.json"
GRANTS_FILE = "policy-grants.json"
AUDIT_FILE = "policy-audit.jsonl"

ALLOW = "allow"
DENY = "deny"
NEEDS_CONFIRMATION = "needs-confirmation"


class Profile(StrEnum):
    """Named permission profiles, ordered from least to most permissive."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL = "full"


DEFAULT_PROFILE = Profile.READ_ONLY

READ_ACTIONS = frozenset({
    "read", "list", "observe", "query", "screenshot", "clipboard.read",
})
WRITE_ACTIONS = frozenset({
    "write", "delete", "move", "mkdir", "truncate", "clipboard.write",
})
INTERFACE_ACTIONS = frozenset({
    "click", "key", "type", "menu", "scroll", "drag", "hover", "window", "open",
})
COMMAND_ACTIONS = frozenset({
    "run", "exec", "command", "external-command", "media",
})
KNOWN_ACTIONS = READ_ACTIONS | WRITE_ACTIONS | INTERFACE_ACTIONS | COMMAND_ACTIONS

_CONFIG_KEYS = frozenset({"profile", "roots", "commands"})


class PolicyDenied(RuntimeError):
    """An action was refused, or still needs confirmation, under policy."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(f"{decision.action} {decision.status}: {decision.reason}")


@dataclass(frozen=True)
class Decision:
    """The outcome of a policy check, plus its content-free audit record."""

    status: str
    action: str
    profile: str
    reason: str
    app: str | None = None
    confirmed: bool = False
    audit: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == ALLOW

    @property
    def denied(self) -> bool:
        return self.status == DENY

    @property
    def needs_confirmation(self) -> bool:
        return self.status == NEEDS_CONFIRMATION

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "action": self.action,
            "profile": self.profile,
            "reason": self.reason,
            "app": self.app,
            "confirmed": self.confirmed,
            "audit": dict(self.audit),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _normalize_app(app: str) -> str:
    return " ".join(str(app).split()).casefold()


def _resolve_root(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


class Policy:
    """An immutable profile plus mutable per-app grants backed by state."""

    def __init__(
        self,
        state: Path | str | None = None,
        *,
        profile: Profile | str = DEFAULT_PROFILE,
        roots: Sequence[str] = (),
        allow_commands: Sequence[str] = (),
        deny_commands: Sequence[str] = (),
        invalid_reason: str | None = None,
    ) -> None:
        self.state = Path(state) if state is not None else None
        error = invalid_reason
        try:
            self.profile = Profile(profile)
        except ValueError:
            self.profile = DEFAULT_PROFILE
            error = error or f"unknown profile {profile!r}"
        self.roots: tuple[Path, ...] = ()
        resolved: list[Path] = []
        for root in roots:
            try:
                resolved.append(_resolve_root(root))
            except (OSError, ValueError):
                error = error or f"unusable writable root {root!r}"
        if error is None:
            self.roots = tuple(resolved)
        self.allow_commands = tuple(str(pattern) for pattern in allow_commands)
        self.deny_commands = tuple(str(pattern) for pattern in deny_commands)
        self.error = error
        self._grants: dict[str, set[str]] = self._load_grants()

    # -- construction -----------------------------------------------------
    @classmethod
    def from_document(cls, document: object, *, state: Path | str | None = None) -> Policy:
        """Build a policy from a decoded JSON document, failing closed."""
        if not isinstance(document, dict):
            return cls(state, invalid_reason="policy document must be a JSON object")
        unknown = sorted(set(document) - _CONFIG_KEYS)
        if unknown:
            return cls(state, invalid_reason=f"unknown policy key(s): {', '.join(unknown)}")
        try:
            profile = Profile(document.get("profile", DEFAULT_PROFILE.value))
            roots = _string_list(document.get("roots"), "roots")
            commands = document.get("commands", {})
            if not isinstance(commands, dict):
                raise ValueError("commands must be an object")
            extra = sorted(set(commands) - {"allow", "deny"})
            if extra:
                raise ValueError(f"unknown commands key(s): {', '.join(extra)}")
            allow_commands = _string_list(commands.get("allow"), "commands.allow")
            deny_commands = _string_list(commands.get("deny"), "commands.deny")
        except (TypeError, ValueError) as error:
            return cls(state, invalid_reason=str(error))
        return cls(
            state,
            profile=profile,
            roots=roots,
            allow_commands=allow_commands,
            deny_commands=deny_commands,
        )

    @classmethod
    def load(cls, state: Path | str, *, profile: Profile | str | None = None) -> Policy:
        """Load ``state/policy.json``; a missing file means read-only.

        A malformed document, unreadable file, or unknown profile produces a
        policy that denies every action. It never falls through to ``full``.
        """
        state = Path(state)
        path = state / CONFIG_FILE
        if not path.exists():
            return cls(state, profile=profile or DEFAULT_PROFILE)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return cls(state, invalid_reason=f"policy unreadable: {type(error).__name__}")
        try:
            document = json.loads(text)
        except ValueError:
            return cls(state, invalid_reason="policy is not valid JSON")
        if not isinstance(document, dict):
            return cls(state, invalid_reason="policy document must be a JSON object")
        if profile is not None:
            document = {**document, "profile": str(profile)}
        return cls.from_document(document, state=state)

    # -- grants -----------------------------------------------------------
    def has_grant(self, app: str | None, action: str) -> bool:
        if not app:
            return False
        actions = self._grants.get(_normalize_app(app), set())
        return "*" in actions or action in actions

    def grant_app(self, app: str, action: str = "*") -> dict:
        """Persist an "always allow" grant for one app and action."""
        if not isinstance(app, str) or not app.strip():
            raise ValueError("An app name is required for a grant.")
        action = str(action or "").strip()
        if not action:
            raise ValueError("A grant action is required.")
        normalized = _normalize_app(app)
        self._grants.setdefault(normalized, set()).add(action)
        self._save_grants()
        return {"app": " ".join(app.split()), "action": action, "persisted": self.state is not None}

    def grants(self) -> dict[str, list[str]]:
        return {app: sorted(actions) for app, actions in sorted(self._grants.items())}

    # -- decisions --------------------------------------------------------
    def approve(
        self,
        action: str,
        *,
        app: str | None = None,
        target: Path | str | None = None,
        payload: str | None = None,
        command: Sequence[str] | None = None,
        destructive: bool = False,
        confirm: bool = False,
        grant: bool = False,
    ) -> Decision:
        """Return allow, deny, or needs-confirmation for one action."""
        action = str(action or "").strip()
        argv = [str(part) for part in command] if command is not None else None
        context = {
            "app": app,
            "target": str(target) if target is not None else None,
            "payload": payload,
            "command": argv,
        }
        if self.error:
            return self._finish(DENY, action, f"policy unavailable: {self.error}",
                                confirmed=False, **context)
        if action not in KNOWN_ACTIONS:
            return self._finish(DENY, action, "unknown action", confirmed=False, **context)
        if argv and self._matches(self.deny_commands, argv):
            return self._finish(DENY, action, "command matches the deny list",
                                confirmed=False, **context)
        if destructive and not confirm:
            return self._finish(NEEDS_CONFIRMATION, action,
                                "destructive action requires explicit confirmation",
                                confirmed=False, **context)
        if self.has_grant(app, action) and action not in WRITE_ACTIONS:
            return self._finish(ALLOW, action, "per-app grant", confirmed=confirm, **context)

        status, reason = self._resolve(action, target, argv)
        if status == NEEDS_CONFIRMATION and confirm:
            status, reason = ALLOW, "operator confirmed"
        if status == NEEDS_CONFIRMATION and grant and app and action not in WRITE_ACTIONS:
            self.grant_app(app, action)
            status, reason = ALLOW, "granted for app"
        return self._finish(status, action, reason, confirmed=confirm, **context)

    def require(
        self,
        action: str,
        *,
        app: str | None = None,
        target: Path | str | None = None,
        payload: str | None = None,
        command: Sequence[str] | None = None,
        destructive: bool = False,
        confirm: bool = False,
        grant: bool = False,
    ) -> Decision:
        """Like :meth:`approve`, but raise when the action is not allowed."""
        decision = self.approve(
            action,
            app=app,
            target=target,
            payload=payload,
            command=command,
            destructive=destructive,
            confirm=confirm,
            grant=grant,
        )
        if not decision.allowed:
            raise PolicyDenied(decision)
        return decision

    # -- internals --------------------------------------------------------
    def _resolve(
        self, action: str, target: Path | str | None, command: list[str] | None
    ) -> tuple[str, str]:
        if action in READ_ACTIONS:
            return ALLOW, "read action"
        if action in INTERFACE_ACTIONS:
            return ALLOW, "interface action"
        if action in WRITE_ACTIONS:
            if self.profile is Profile.FULL:
                return ALLOW, "full profile"
            if self.profile is Profile.READ_ONLY:
                return DENY, "read-only profile denies writes"
            if self._inside_roots(target):
                return ALLOW, "target is inside a writable root"
            return DENY, "target is outside the writable roots"
        if action in COMMAND_ACTIONS:
            if command and self._matches(self.allow_commands, command):
                return ALLOW, "command is on the allow list"
            if self.profile is Profile.FULL:
                return ALLOW, "full profile"
            return NEEDS_CONFIRMATION, "external commands need explicit confirmation"
        return DENY, "unknown action"

    def _inside_roots(self, target: Path | str | None) -> bool:
        if target is None:
            return False
        try:
            candidate = Path(target).expanduser().resolve(strict=False)
        except (OSError, ValueError):
            return False
        return any(candidate.is_relative_to(root) for root in self.roots)

    @staticmethod
    def _matches(patterns: Sequence[str], command: Sequence[str]) -> bool:
        if not patterns or not command:
            return False
        program = Path(str(command[0])).name
        joined = " ".join(str(part) for part in command)
        return any(
            fnmatch.fnmatchcase(program, pattern) or fnmatch.fnmatchcase(joined, pattern)
            for pattern in patterns
        )

    def _finish(
        self,
        status: str,
        action: str,
        reason: str,
        *,
        app: str | None,
        target: str | None,
        payload: str | None,
        command: list[str] | None,
        confirmed: bool,
    ) -> Decision:
        record = self._audit_record(
            status, action, reason, app=app, target=target, payload=payload,
            command=command, confirmed=confirmed,
        )
        decision = Decision(
            status=status,
            action=action,
            profile=str(self.profile),
            reason=reason,
            app=app,
            confirmed=confirmed,
            audit=record,
        )
        self._append_audit(record)
        return decision

    def _audit_record(
        self,
        status: str,
        action: str,
        reason: str,
        *,
        app: str | None,
        target: str | None,
        payload: str | None,
        command: list[str] | None,
        confirmed: bool,
    ) -> dict:
        record: dict = {
            "timestamp": _now(),
            "profile": str(self.profile),
            "action": action,
            "status": status,
            "reason": reason,
            "app": app,
            "confirmed": bool(confirmed),
        }
        if payload:
            record["payload_chars"] = len(payload)
            record["payload_sha256"] = _digest(payload)
        if command:
            joined = "\x00".join(command)
            record["command"] = Path(command[0]).name
            record["command_chars"] = len(joined)
            record["command_sha256"] = _digest(joined)
        if target:
            record["target"] = Path(target).name
            record["target_sha256"] = _digest(target)
        return record

    def _append_audit(self, record: dict) -> None:
        if self.state is None:
            return
        try:
            self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.state / AUDIT_FILE
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def _load_grants(self) -> dict[str, set[str]]:
        if self.state is None:
            return {}
        try:
            data = json.loads((self.state / GRANTS_FILE).read_text(encoding="utf-8"))
            grants = data.get("grants")
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(grants, dict):
            return {}
        loaded: dict[str, set[str]] = {}
        for app, entry in grants.items():
            if not isinstance(app, str) or not isinstance(entry, dict):
                continue
            actions = entry.get("actions")
            if isinstance(actions, list) and all(isinstance(item, str) for item in actions):
                loaded[_normalize_app(app)] = set(actions)
        return loaded

    def _save_grants(self) -> None:
        if self.state is None:
            return
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "grants": {
                app: {"actions": sorted(actions)}
                for app, actions in sorted(self._grants.items())
            },
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".policy-grants-", dir=self.state)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.state / GRANTS_FILE)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def _string_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return [item.strip() for item in value]
