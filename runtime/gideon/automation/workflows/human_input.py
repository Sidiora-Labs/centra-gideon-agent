from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.automation.workflows import store
from gideon.core.atomic_write import atomic_write

logger = logging.getLogger(__name__)
CONTINUATION_DIR = "continuations"
CLAIMED_DIR = "claimed"
DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS = 45
DEFAULT_BLOCKING_GATE_TIMEOUT_SECS = 1800
DEFAULT_RESUME_TTL_SECS = 7 * 24 * 3600


class AskKind(str, Enum):
    APPROVAL = "approval"
    CHOICE = "choice"
    TEXT = "text"
    FORM = "form"


@dataclass
class AskField:
    name: str
    type: str = "string"
    label: str = ""
    required: bool = False
    default: Any = None
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        values = (
            self.name,
            self.type,
            self.label or self.name,
            self.required,
            self.default,
            list(self.choices),
        )
        return dict(
            zip(("name", "type", "label", "required", "default", "choices"), values)
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AskField:
        record = d or {}
        values = {
            name: str(record.get(name, fallback) or fallback)
            for name, fallback in (("name", ""), ("type", "string"), ("label", ""))
        }
        values.update(
            required=bool(record.get("required", False)),
            default=record.get("default"),
            choices=list(map(str, record.get("choices") or [])),
        )
        return cls(**values)


@dataclass
class Ask:
    kind: AskKind = AskKind.APPROVAL
    prompt: str = ""
    node_id: str = ""
    fields: list[AskField] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    unattended_suppress: bool = False

    def to_dict(self) -> dict[str, Any]:
        values = (
            self.kind.value,
            self.prompt,
            self.node_id,
            [entry.to_dict() for entry in self.fields],
            list(self.choices),
            self.unattended_suppress,
        )
        return dict(
            zip(
                (
                    "kind",
                    "prompt",
                    "node_id",
                    "fields",
                    "choices",
                    "unattended_suppress",
                ),
                values,
            )
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ask:
        record = d or {}
        raw_kind = str(record.get("kind", "approval") or "approval")
        try:
            kind = AskKind(raw_kind)
        except ValueError:
            kind = AskKind.APPROVAL
        values = dict(
            kind=kind,
            prompt=str(record.get("prompt", "") or ""),
            node_id=str(record.get("node_id", "") or ""),
        )
        values["fields"] = [
            AskField.from_dict(entry)
            for entry in record.get("fields") or []
            if isinstance(entry, dict)
        ]
        values["choices"] = list(map(str, record.get("choices") or []))
        values["unattended_suppress"] = bool(record.get("unattended_suppress", False))
        return cls(**values)

    def validate_answer(self, answer: Any) -> str:
        return _AnswerRules(self).validate(answer)

    def apply_defaults(self, answer: Any) -> Any:
        if self.kind == AskKind.FORM and isinstance(answer, dict):
            result = dict(answer)
            for entry in self.fields:
                if entry.default is not None:
                    result.setdefault(entry.name, entry.default)
            return result
        return answer


class _AnswerRules:
    def __init__(self, ask: Ask) -> None:
        self.ask = ask

    def approval(self, answer: Any) -> str:
        if isinstance(answer, bool):
            return ""
        if isinstance(answer, dict):
            if isinstance(answer.get("approved"), bool):
                return ""
        return "approval expects a boolean (or {approved: bool})"

    def choice(self, answer: Any) -> str:
        value = answer.get("choice") if isinstance(answer, dict) else answer
        if isinstance(value, str):
            if not self.ask.choices or value in self.ask.choices:
                return ""
            return f"{value!r} is not one of: {', '.join(self.ask.choices)}"
        return "choice expects a string"

    def text(self, answer: Any) -> str:
        value = answer.get("text") if isinstance(answer, dict) else answer
        accepted = isinstance(value, str) and value.strip()
        return "" if accepted else "text expects a non-empty string"

    def form(self, answer: Any) -> str:
        if not isinstance(answer, dict):
            return "form expects an object of field values"
        for entry in self.ask.fields:
            if entry.name in answer:
                problem = _check_field(entry, answer[entry.name])
            else:
                problem = (
                    f"missing required field {entry.name!r}"
                    if entry.required and entry.default is None
                    else ""
                )
            if problem:
                return problem
        return ""

    def validate(self, answer: Any) -> str:
        routes = (
            (AskKind.APPROVAL, self.approval),
            (AskKind.CHOICE, self.choice),
            (AskKind.TEXT, self.text),
        )
        for kind, handler in routes:
            if self.ask.kind == kind:
                return handler(answer)
        return self.form(answer)


def _check_field(spec: AskField, value: Any) -> str:
    rules = (
        ("number", (int, float), "number"),
        ("boolean", bool, "boolean"),
        ("choice", str, "string"),
        ("string", str, "string"),
    )
    for kind, accepted, label in rules:
        if spec.type == kind:
            if not isinstance(value, accepted):
                return f"field {spec.name!r} expects a {label}"
            if kind == "choice" and spec.choices and value not in spec.choices:
                return f"field {spec.name!r} must be one of: {', '.join(spec.choices)}"
            break
    return (
        f"field {spec.name!r} is required"
        if spec.required and value in (None, "")
        else ""
    )


def gate_timeout_secs(
    node_config: dict[str, Any],
    *,
    mode: str = "background",
    background_default: int = DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS,
    blocking_default: int = DEFAULT_BLOCKING_GATE_TIMEOUT_SECS,
) -> int:
    configured = (node_config or {}).get("timeout_secs")
    if isinstance(configured, (int, float)) and configured >= 0:
        selected = configured
    else:
        selected = blocking_default if str(mode) == "blocking" else background_default
    return int(selected)


@dataclass
class Continuation:
    token: str
    run_id: str
    node_id: str
    instance_path: str
    epoch: int = 0
    resolved_inputs: dict[str, Any] = field(default_factory=dict)
    ask: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0
    handoff: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        names = (
            "token",
            "run_id",
            "node_id",
            "instance_path",
            "epoch",
            "resolved_inputs",
            "ask",
            "created_at",
            "expires_at",
            "handoff",
        )
        copies = {"resolved_inputs", "ask", "handoff"}
        return {
            name: dict(getattr(self, name)) if name in copies else getattr(self, name)
            for name in names
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Continuation:
        record = d or {}
        identity = {
            name: str(record.get(name, "") or "")
            for name in ("token", "run_id", "node_id", "instance_path")
        }
        identity["epoch"] = int(record.get("epoch", 0) or 0)
        for name in ("resolved_inputs", "ask"):
            identity[name] = dict(record.get(name) or {})
        for name in ("created_at", "expires_at"):
            identity[name] = float(record.get(name, 0.0) or 0.0)
        identity["handoff"] = dict(record.get("handoff") or {})
        return cls(**identity)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def handoff_bundle(
    *,
    scope: str,
    status: str,
    outstanding: list[str] | None = None,
    checks_run: list[str] | None = None,
    next_steps: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    bundle: dict[str, Any] = {"scope": scope, "status": status}
    for name, entries in (
        ("outstanding", outstanding),
        ("checks_run", checks_run),
        ("next_steps", next_steps),
        ("risks", risks),
    ):
        bundle[name] = list(entries or [])
    return bundle


def _dir(run_id: str):
    return store.run_dir(run_id) / CONTINUATION_DIR


def _claimed_dir(run_id: str):
    return _dir(run_id) / CLAIMED_DIR


class _ContinuationFiles:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    @staticmethod
    def bare_token(token: str) -> bool:
        return bool(token) and not any(part in token for part in ("/", "\\", ".."))

    def pending_path(self, token: str):
        return _dir(self.run_id) / f"{token}.json"

    def write(self, continuation: Continuation) -> None:
        target = self.pending_path(continuation.token)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(continuation.to_dict(), indent=2, ensure_ascii=False)
        atomic_write(target, encoded)

    @staticmethod
    def read_path(path) -> Continuation:
        return Continuation.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def read(self, token: str) -> Continuation | None:
        if self.bare_token(token):
            source = self.pending_path(token)
            if source.is_file():
                try:
                    return self.read_path(source)
                except (OSError, ValueError):
                    logger.warning(
                        "run %s: unreadable continuation %s", self.run_id, token
                    )
        return None

    def pending(self) -> list[Continuation]:
        directory = _dir(self.run_id)
        result = []
        if directory.is_dir():
            for source in sorted(directory.glob("*.json")):
                try:
                    result.append(self.read_path(source))
                except (OSError, ValueError):
                    logger.debug(
                        "run %s: skipping unreadable continuation %s",
                        self.run_id,
                        source.name,
                    )
        return result

    def claim_path(self, token: str):
        source = self.pending_path(token)
        target = _claimed_dir(self.run_id) / f"{token}.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)
        except OSError:
            return None
        return target

    def consume(self, token: str) -> Continuation | None:
        if not self.bare_token(token):
            return None
        owned = self.claim_path(token)
        if owned is None:
            return None
        try:
            raw = owned.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "run %s: claimed continuation %s but could not read it",
                self.run_id,
                token,
            )
            return None
        try:
            parsed = json.loads(raw)
            return Continuation.from_dict(parsed)
        except ValueError:
            return None

    def remove(self, token: str) -> bool:
        target = self.pending_path(token)
        try:
            target.unlink()
        except OSError:
            logger.debug("run %s: could not drop continuation %s", self.run_id, token)
            return False
        return True


def save_continuation(cont: Continuation) -> Continuation:
    _ContinuationFiles(cont.run_id).write(cont)
    return cont


def create_continuation(
    run_id: str,
    *,
    node_id: str,
    instance_path: str,
    epoch: int,
    resolved_inputs: dict[str, Any] | None = None,
    ask: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    ttl_secs: int = DEFAULT_RESUME_TTL_SECS,
    now: float = 0.0,
) -> Continuation:
    clock = now or time.time()
    values = dict(
        token=new_token(),
        run_id=run_id,
        node_id=node_id,
        instance_path=instance_path,
        epoch=epoch,
        resolved_inputs=dict(resolved_inputs or {}),
        ask=dict(ask or {}),
        created_at=clock,
        expires_at=clock + max(0, int(ttl_secs)) if ttl_secs else 0.0,
        handoff=dict(handoff or {}),
    )
    return save_continuation(Continuation(**values))


def load_continuation(run_id: str, token: str) -> Continuation | None:
    return _ContinuationFiles(run_id).read(token)


def list_continuations(run_id: str) -> list[Continuation]:
    return _ContinuationFiles(run_id).pending()


def consume_continuation(run_id: str, token: str) -> Continuation | None:
    return _ContinuationFiles(run_id).consume(token)


def drop_continuations(run_id: str, *, instance_prefix: str = "") -> int:
    files = _ContinuationFiles(run_id)
    removed = 0
    for record in list_continuations(run_id):
        selected = not instance_prefix or record.instance_path.startswith(
            instance_prefix
        )
        if selected and files.remove(record.token):
            removed += 1
    return removed


def expired_item(cont: Continuation) -> dict[str, Any]:
    result = dict(
        kind="resume_expired",
        run_id=cont.run_id,
        node_id=cont.node_id,
        instance_path=cont.instance_path,
    )
    result.update(
        prompt=f"The approval link for {cont.node_id or cont.instance_path!r} expired before it was answered.",
        remediation="re-run the workflow from this node to ask again",
        expired_at=cont.expires_at,
    )
    return result
