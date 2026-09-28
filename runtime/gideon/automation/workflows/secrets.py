"""Credential presence projection, id-based restoration and workflow literal screening."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "private_key",
    "privatekey",
    "auth",
    "bearer",
    "session_key",
    "access_key",
    "refresh",
    "signing",
    "webhook",
)

SECRET_KEY_WORD_HINTS = ("pat",)

_WORD_SEP_RE = re.compile(r"[^a-z0-9]+")

_WORD_HINT_RES = tuple(re.compile(rf"{re.escape(h)}\d*") for h in SECRET_KEY_WORD_HINTS)

SECRET_REF_KEYS = frozenset({"credential_ref", "secret_ref", "auth_mode", "auth_type"})

SECRET_BINDING_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")

_INLINE_SECRET_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
)


def matches_secret_hint(name: str) -> bool:
    candidate = str(name or "").lower()
    for hint in SECRET_KEY_HINTS:
        if hint in candidate:
            return True
    for segment in _WORD_SEP_RE.split(candidate):
        if any(pattern.fullmatch(segment) for pattern in _WORD_HINT_RES):
            return True
    return False


def is_secret_key(key: str) -> bool:
    """Does this config key hold a credential VALUE (not a reference)?"""
    low = str(key or "").lower()
    if low in SECRET_REF_KEYS:
        return False
    return matches_secret_hint(low)


def has_flag_name(key: str) -> str:
    """`api_key` → `_has_api_key`. The presence flag a GET ships instead of the value."""
    return f"_has_{str(key or '').lstrip('_')}"


def is_secret_binding(value: Any) -> bool:
    """True when the value is already the sanctioned `{{secret:KEY}}` indirection."""
    return isinstance(value, str) and bool(SECRET_BINDING_RE.search(value))


def strip_secrets(spec: Any) -> Any:
    from gideon.security.security import redact_values_for_display
    return redact_values_for_display(_CredentialDocument().read(spec))


def _by_node_id(
    spec: Any, into: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    index = {} if into is None else into
    for value in _document_values(spec):
        if not isinstance(value, dict):
            continue
        identity, configuration = value.get("id"), value.get("config")
        if isinstance(identity, str) and identity and isinstance(configuration, dict):
            index[identity] = configuration
    return index


def reinject_secrets(incoming: Any, stored: Any) -> Any:
    return restore_hidden_values(incoming, stored)[0]


def restore_hidden_values(incoming: Any, stored: Any) -> tuple[Any, list[dict[str, str]]]:
    """Restore presence flags against the exact named base document, at every depth.

    Paths use the workflow engine's root/children/body/cases/default grammar where possible.
    A true flag with no matching stored value is returned as an issue; it is never written.
    """
    from gideon.security.security import keep_masked_values
    incoming = keep_masked_values(incoming, stored)
    unmatched: list[dict[str, str]] = []

    def step_path(path: str, key: str, index: int | None = None) -> str:
        if path == "root":
            if key == "children" and index is not None: return f"root.children[{index}]"
            if key in {"body", "default"}: return f"root.{key}"
            if key == "cases" and index is not None: return f"root.cases[{index}]"
        if path.startswith("root"):
            if key == "children" and index is not None: return f"{path}.children[{index}]"
            if key in {"body", "default"}: return f"{path}.{key}"
            if key == "cases" and index is not None: return f"{path}.cases[{index}]"
        return path

    def visit(value: Any, base: Any, path: str = "") -> Any:
        if isinstance(value, dict):
            base_map = base if isinstance(base, dict) else {}
            result: dict[str, Any] = {}
            for key, child in value.items():
                if key.startswith("_has_"):
                    original_key = key[5:]
                    if child is True:
                        if original_key in base_map:
                            result[original_key] = base_map[original_key]
                        else:
                            node_path = path or "root"
                            unmatched.append({"path": node_path, "key": original_key})
                    continue
                if key == "children" and isinstance(child, list):
                    result[key] = [visit(item, (base_map.get(key) or [])[idx] if isinstance(base_map.get(key), list) and idx < len(base_map[key]) else None,
                                         step_path(path or "root", key, idx)) for idx, item in enumerate(child)]
                elif key in {"body", "default"}:
                    result[key] = visit(child, base_map.get(key), step_path(path or "root", key))
                elif key == "cases" and isinstance(child, dict):
                    result[key] = {label: visit(item, (base_map.get(key) or {}).get(label) if isinstance(base_map.get(key), dict) else None,
                                                step_path(path or "root", key, label)) for label, item in child.items()}
                elif key == "root":
                    result[key] = visit(child, base_map.get(key), "root")
                elif key in {"inputs", "defaults"}:
                    result[key] = visit(child, base_map.get(key), key)
                else:
                    result[key] = visit(child, base_map.get(key), path)
            return result
        if isinstance(value, list):
            base_list = base if isinstance(base, list) else []
            return [visit(child, base_list[idx] if idx < len(base_list) else None, path) for idx, child in enumerate(value)]
        return value

    return visit(incoming, stored), unmatched


def _reinject(node: Any, stored_configs: dict[str, dict[str, Any]]) -> Any:
    return _CredentialDocument(stored_configs).write(node)


def _merge_config(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    result = incoming.copy()
    flags = list(filter(lambda key: key.startswith("_has_"), incoming))
    for marker in flags:
        key = marker[5:]
        preserve = bool(result.pop(marker))
        if key not in incoming and preserve and key in stored:
            result.update({key: stored[key]})
    return result


@dataclass
class InlineSecret:
    """One flagged literal. `node_id` and `key` locate it; the value is NEVER carried —
    an error message that quotes the credential leaks it into the logs that render it.
    """

    node_id: str = ""
    key: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "key": self.key, "hint": self.hint}


def find_inline_secrets(spec: Any) -> list[InlineSecret]:
    findings: list[InlineSecret] = []
    _scan(spec, "", findings)
    return findings


def _scan(node: Any, node_id: str, found: list[InlineSecret]) -> None:
    for owner, key, value in _inspection_strings(node, node_id):
        if is_secret_binding(value):
            continue
        reason = ""
        if is_secret_key(key) and value.strip():
            reason = "secret-named field holds a literal; use {{secret:KEY}}"
        elif looks_like_credential(value):
            reason = "value matches a known credential shape"
        if reason:
            found.append(
                InlineSecret(node_id=str(owner or ""), key=str(key), hint=reason)
            )


def looks_like_credential(text: str) -> bool:
    """Does this string match a known credential shape?

    The ONE list of vendor key shapes in the workflows package — `validator.py`'s
    save-time lint reads it from here rather than keeping a second copy. Recognizing a
    vendor's key SHAPE is secret-DETECTION data, not vendor logic (the same judgment
    `security.py`'s token regexes carry); narrowing it would silently stop catching those
    providers' keys.
    """
    return any(rx.search(text) for rx in _INLINE_SECRET_RES)


def secret_keys_referenced(spec: Any) -> list[str]:
    names = set()
    for value in _document_values(spec):
        if isinstance(value, str):
            names.update(SECRET_BINDING_RE.findall(value))
    return sorted(names)


def _document_values(root: Any):
    pending = [iter((root,))]
    while pending:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        yield value
        if isinstance(value, dict):
            pending.append(iter(value.values()))
        elif isinstance(value, list):
            pending.append(iter(value))


def _inspection_strings(node: Any, owner: str):
    if isinstance(node, dict):
        current = node.get("id") if isinstance(node.get("id"), str) else owner
        for key, value in node.items():
            if isinstance(value, str):
                yield current, key, value
            else:
                yield from _inspection_strings(value, str(current or ""))
    elif isinstance(node, list):
        for child in node:
            yield from _inspection_strings(child, owner)


class _CredentialDocument:
    def __init__(self, stored: dict[str, dict[str, Any]] | None = None):
        self.stored = stored if stored is not None else {}

    def read(self, value: Any) -> Any:
        if isinstance(value, list):
            return list(map(self.read, value))
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            hidden = (
                is_secret_key(key)
                and child not in (None, "")
                and not is_secret_binding(child)
            )
            destination, projected = (
                (has_flag_name(key), True) if hidden else (key, self.read(child))
            )
            result[destination] = projected
        return result

    def write(self, value: Any) -> Any:
        if isinstance(value, list):
            return list(map(self.write, value))
        if not isinstance(value, dict):
            return value
        result = dict((key, self.write(child)) for key, child in value.items())
        identity = value.get("id")
        configuration = result.get("config")
        if isinstance(identity, str) and identity and isinstance(configuration, dict):
            result.update(
                config=_merge_config(configuration, self.stored.get(identity) or {})
            )
        return result
