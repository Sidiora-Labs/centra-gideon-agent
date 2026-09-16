"""Track text shown to a session and validate the snapshot used for a write."""

from __future__ import annotations

import hashlib
import heapq
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

OBSERVATION_TTL_SECS = 3600.0
MAX_PATHS_PER_SESSION = 512
_NO_SESSION = "\x00no-session"


@dataclass(frozen=True)
class Observation:
    content_sha256: str
    complete: bool
    at: float
    fragments: tuple[str, ...] = ()

    def expired(self, *, now: float | None = None) -> bool:
        elapsed = (now or time.time()) - self.at
        return elapsed > OBSERVATION_TTL_SECS

    def covers(self, text: str) -> bool:
        return any(fragment.find(text) >= 0 for fragment in self.fragments)


@dataclass(frozen=True)
class Refusal:
    reason: str
    error: str
    hint: str


def _bucket(session_key: str) -> str:
    return session_key or _NO_SESSION


class _EvidenceIndex:
    def __init__(self) -> None:
        self.paths: dict[str, dict[str, Observation]] = {}
        self.references: dict[str, dict[str, tuple[str, str]]] = {}
        self.lock = threading.Lock()

    def lookup(self, session: str, path: str) -> Observation | None:
        with self.lock:
            return self.paths.get(session, {}).get(path)

    def publish(
        self,
        session: str,
        path: str,
        snapshot: Observation,
        *,
        bounded: bool,
        reference: str = "",
    ) -> None:
        with self.lock:
            entries = self.paths.setdefault(session, {})
            entries[path] = snapshot
            excess = len(entries) - MAX_PATHS_PER_SESSION if bounded else 0
            if excess > 0:
                oldest = heapq.nsmallest(
                    excess, entries, key=lambda key: entries[key].at
                )
                for key in oldest:
                    del entries[key]
            if reference:
                links = self.references.setdefault(session, {})
                links[reference] = (path, snapshot.content_sha256)
                if len(links) > MAX_PATHS_PER_SESSION:
                    links.clear()

    def extend(self, session: str, reference: str, fragment: str) -> None:
        with self.lock:
            link = self.references.get(session, {}).get(reference)
            if link is None:
                return
            path, digest = link
            entries = self.paths.get(session, {})
            snapshot = entries.get(path)
            if snapshot is not None and snapshot.content_sha256 == digest:
                entries[path] = replace(
                    snapshot, at=time.time(), fragments=snapshot.fragments + (fragment,)
                )

    def substitute(
        self, session: str, path: str, digest: str, old: str, new: str, count: int
    ) -> None:
        with self.lock:
            entries = self.paths.setdefault(session, {})
            snapshot = entries.get(path)
            if snapshot is not None:
                revised = tuple(
                    part.replace(old, new, count) for part in snapshot.fragments
                )
                entries[path] = replace(
                    snapshot, content_sha256=digest, at=time.time(), fragments=revised
                )

    def discard(self, session: str | None = None) -> None:
        with self.lock:
            for mapping in (self.paths, self.references):
                if session is None:
                    mapping.clear()
                else:
                    mapping.pop(session, None)


_EVIDENCE = _EvidenceIndex()
_LEDGER = _EVIDENCE.paths
_RAW_REFS = _EVIDENCE.references
_LOCK = _EVIDENCE.lock


def file_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def record_read(
    session_key: str,
    path: Path | str,
    *,
    observed_text: str,
    content_sha256: str,
    complete: bool,
    raw_ref: str = "",
) -> None:
    snapshot = Observation(content_sha256, complete, time.time(), (observed_text,))
    _EVIDENCE.publish(
        _bucket(session_key), str(Path(path)), snapshot, bounded=True, reference=raw_ref
    )


def record_retrieval(session_key: str, raw_ref: str, *, observed_text: str) -> None:
    if raw_ref and observed_text:
        _EVIDENCE.extend(_bucket(session_key), raw_ref, observed_text)


def record_overwrite(session_key: str, path: Path | str, *, content: str) -> None:
    target = Path(path)
    digest = file_sha256(target)
    if digest is not None:
        _EVIDENCE.publish(
            _bucket(session_key),
            str(target),
            Observation(digest, True, time.time(), (content,)),
            bounded=False,
        )


def record_edit(
    session_key: str,
    path: Path | str,
    *,
    old: str,
    new: str,
    replace_all: bool = False,
) -> None:
    target = Path(path)
    digest = file_sha256(target)
    if digest is not None:
        _EVIDENCE.substitute(
            _bucket(session_key),
            str(target),
            digest,
            old,
            new,
            -1 if replace_all else 1,
        )


def begin_turn(session_key: str) -> None:
    _EVIDENCE.discard(_bucket(session_key))


def forget_session(session_key: str) -> None:
    _EVIDENCE.discard(_bucket(session_key))


def reset_all() -> None:
    _EVIDENCE.discard()


def observation(session_key: str, path: Path | str) -> Observation | None:
    return _EVIDENCE.lookup(_bucket(session_key), str(Path(path)))


@dataclass(frozen=True)
class _WriteRequest:
    operation: str
    display: str
    required: str | None

    def refusal(self, reason: str) -> Refusal:
        name = self.display
        read = f"Call read_file with path={name!r}"
        messages = {
            "state_unknown": (
                "undetermined",
                f"cannot determine the current state of {name} — refusing to write it",
                f"{read} to establish what is there now, then retry the write.",
            ),
            "not_observed": (
                "not_observed",
                f"{name} has not been read in this turn — refusing to "
                f"{'edit' if self.operation == 'edit' else 'overwrite'} a file whose current "
                "content was never observed",
                f"{read} first, then retry this "
                f"{'edit' if self.operation == 'edit' else 'write'} against the text you just read.",
            ),
            "expired": (
                "expired",
                f"the read of {name} is too old to rely on — refusing to write it",
                f"{read} again, then retry the write.",
            ),
            "unreadable": (
                "undetermined",
                f"cannot read {name} to confirm it is unchanged — refusing to write it",
                f"{read} to see its current content, then retry.",
            ),
            "changed_on_disk": (
                "changed_on_disk",
                f"{name} changed on disk since you read it — refusing to write "
                "an edit computed against the old content (it would revert that change)",
                f"{read} to see the new content, then recompute the edit against it.",
            ),
            "partial_observation": (
                "partial_observation",
                f"only part of {name} was shown to you (the read was truncated), "
                "so an overwrite would discard content you never saw — refusing",
                f"Use edit_file for a targeted change, or read the whole of {name!r} "
                "(tool_result_get on the projected read) before overwriting it.",
            ),
            "region_not_observed": (
                "region_not_observed",
                f"the text you are replacing is not in the part of {name} you were "
                "shown (that read was truncated) — refusing an edit to a region you never "
                "observed",
                f"Observe the region first: grep for it in {name!r}, or call "
                "tool_result_get with the read's result_id and a grep/line range, then "
                "retry the edit.",
            ),
            "unknown_operation": (
                "unknown_operation",
                f"unrecognized write operation {self.operation!r} for {name} — refusing",
                f"{read} and use write_file or edit_file.",
            ),
        }
        return Refusal(*messages[reason])

    def uncovered_region(self, snapshot: Observation) -> str | None:
        if self.operation not in {"edit", "overwrite"}:
            return "unknown_operation"
        if snapshot.complete:
            return None
        if self.operation == "overwrite":
            return "partial_observation"
        if self.required and not snapshot.covers(self.required):
            return "region_not_observed"
        return None


def _snapshot_problem(snapshot: Observation | None, path: Path) -> str | None:
    if snapshot is None:
        return "not_observed"
    if snapshot.expired():
        return "expired"
    digest = file_sha256(path)
    if digest is None:
        return "unreadable"
    return "changed_on_disk" if digest != snapshot.content_sha256 else None


def admit_write(
    session_key: str,
    path: Path,
    *,
    operation: str,
    display_path: str,
    required_text: str | None = None,
) -> Refusal | None:
    request = _WriteRequest(operation, display_path, required_text)
    try:
        existing_file = path.is_file()
    except OSError:
        return request.refusal("state_unknown")
    if not existing_file:
        return None
    snapshot = observation(session_key, path)
    problem = _snapshot_problem(snapshot, path)
    if problem is None:
        assert snapshot is not None
        problem = request.uncovered_region(snapshot)
    return request.refusal(problem) if problem else None
