"""Append-only replication of WorkStore-authored text and immutable revisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from gideon.operations.durability import conflicts, inventory
from gideon.workspace.capabilities.creative.store import CatalogError, identifier
from gideon.workspace.capabilities.creative.works import FIELDS, WorkStore

SCOPE = "creative.work_documents"
VERSIONS_ENTRY = "creative.work_versions"
DRAFTS_ENTRY = "creative.work_drafts"
ENTRIES = (VERSIONS_ENTRY, DRAFTS_ENTRY)
_VERSION_FIELDS = FIELDS | {"id", "revision", "created_at", "updated_at"}
_DRAFT_FIELDS = {
    "id",
    "artifact_id",
    "artifact_version",
    "note",
    "created_at",
    "characters",
}
_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ApplyResult:
    added: int
    updated: int
    removed: int
    conflicts: int
    new_ancestors: dict[str, str]

    @property
    def verdict(self):
        return "consumed"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _version_id(work_id, revision):
    return hashlib.sha256(f"work-version\0{work_id}\0{revision}".encode()).hexdigest()


def _timestamp(value, label):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"Invalid {label}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid {label}") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"Invalid {label}")
    return parsed


def _version_row(row):
    if not isinstance(row, dict) or set(row) != {"id", "data"}:
        raise ValueError("Invalid work version row")
    data = row["data"]
    if not isinstance(data, dict) or set(data) != {
        "work_id",
        "revision",
        "record",
        "record_sha256",
    }:
        raise ValueError("Invalid work version data")
    work_id = identifier(data["work_id"])
    revision, record = data["revision"], data["record"]
    if (
        type(revision) is not int
        or not 1 <= revision <= 2**31
        or not isinstance(record, dict)
    ):
        raise ValueError("Invalid work version revision")
    if (
        set(record) != _VERSION_FIELDS
        or record.get("id") != work_id
        or record.get("revision") != revision
    ):
        raise ValueError("Work version record is not canonical")
    identifier(record["id"])
    if record["kind"] not in ("work", "exercise"):
        raise ValueError("Invalid work version kind")
    for field, limit in (("title", 200), ("prompt", 20000)):
        value = record[field]
        if (
            not isinstance(value, str)
            or len(value) > limit
            or (field == "title" and not value.strip())
        ):
            raise ValueError("Invalid work version text")
    for field in ("author_ref", "universe_ref"):
        ref = record[field]
        if ref is not None and (
            not isinstance(ref, dict)
            or set(ref) != {"id", "revision"}
            or type(ref["revision"]) is not int
            or not 1 <= ref["revision"] <= 2**31
        ):
            raise ValueError("Invalid work version pinned reference")
        if ref is not None:
            identifier(ref["id"])
    if record["active_draft_id"] is not None:
        identifier(record["active_draft_id"])
    created, updated = _timestamp(
        record["created_at"], "work creation timestamp"
    ), _timestamp(record["updated_at"], "work update timestamp")
    if updated < created:
        raise ValueError("Work version update predates creation")
    if row["id"] != _version_id(work_id, revision) or data["record_sha256"] != _sha(
        record
    ):
        raise ValueError("Work version identity or hash does not match")
    return data


def _draft_row(row):
    if not isinstance(row, dict) or set(row) != {"id", "data"}:
        raise ValueError("Invalid authored draft row")
    data = row["data"]
    expected = {"work_id", "draft", "text", "content_sha256", "artifact_description"}
    if (
        not isinstance(data, dict)
        or set(data) != expected
        or not isinstance(data["draft"], dict)
    ):
        raise ValueError("Invalid authored draft data")
    identifier(data["work_id"])
    draft = data["draft"]
    if (
        set(draft) != _DRAFT_FIELDS
        or row["id"] != draft.get("id")
        or _HEX.fullmatch(row["id"]) is None
    ):
        raise ValueError("Invalid authored draft identity")
    if (
        draft["artifact_id"] != "creative-draft-" + row["id"]
        or draft["artifact_version"] != 1
    ):
        raise ValueError("Invalid authored draft artifact reference")
    content = data["text"]
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > 1000000
        or draft["characters"] != len(content)
    ):
        raise ValueError("Invalid authored draft text")
    if data["content_sha256"] != hashlib.sha256(content.encode()).hexdigest():
        raise ValueError("Authored draft content hash does not match")
    if not isinstance(draft["note"], str) or len(draft["note"]) > 2000:
        raise ValueError("Invalid authored draft note")
    _timestamp(draft["created_at"], "authored draft timestamp")
    if _HEX.fullmatch(data["artifact_description"]) is None:
        raise ValueError("Invalid authored draft provenance")
    return data


def _validate_rows(entry_id, rows):
    if entry_id not in ENTRIES or not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid creative document row set")
    parser = _version_row if entry_id == VERSIONS_ENTRY else _draft_row
    result = {}
    for row in rows:
        data = parser(row)
        if row["id"] in result:
            raise ValueError("Duplicate creative document row")
        result[row["id"]] = data
    return result


def validate_entries(entries):
    if (
        not isinstance(entries, list)
        or len(entries) != 2
        or [
            item.get("entry_id") if isinstance(item, dict) else None for item in entries
        ]
        != list(ENTRIES)
        or any(set(item) != {"entry_id", "rows"} for item in entries)
    ):
        raise ValueError(
            "Creative document replication requires complete ordered coverage"
        )
    versions = _validate_rows(VERSIONS_ENTRY, entries[0]["rows"])
    drafts = _validate_rows(DRAFTS_ENTRY, entries[1]["rows"])
    by_work = {}
    for data in versions.values():
        by_work.setdefault(data["work_id"], []).append(data)
        active = data["record"]["active_draft_id"]
        if active is not None and (
            active not in drafts or drafts[active]["work_id"] != data["work_id"]
        ):
            raise ValueError("Work version references a missing authored draft")
    for work_id, records in by_work.items():
        revisions = sorted(item["revision"] for item in records)
        if revisions != list(range(1, revisions[-1] + 1)):
            raise ValueError("Work version history must be complete and contiguous")
        created = records[0]["record"]["created_at"]
        if any(item["record"]["created_at"] != created for item in records):
            raise ValueError("Work version creation timestamp changed")
    if any(data["work_id"] not in by_work for data in drafts.values()):
        raise ValueError("Authored draft references a missing work history")


def read_rows(home, entry_id):
    store = WorkStore(home)
    with store.connection() as database:
        if entry_id == VERSIONS_ENTRY:
            rows = database.execute(
                "SELECT id,revision,record FROM work_revisions ORDER BY id,revision"
            ).fetchall()
            result = []
            for work_id, revision, encoded in rows:
                record = json.loads(encoded)
                data = {
                    "work_id": work_id,
                    "revision": revision,
                    "record": record,
                    "record_sha256": _sha(record),
                }
                result.append({"id": _version_id(work_id, revision), "data": data})
        elif entry_id == DRAFTS_ENTRY:
            rows = database.execute(
                "SELECT id,work_id,record FROM work_drafts ORDER BY id"
            ).fetchall()
            result = []
            for draft_id, work_id, encoded in rows:
                draft = json.loads(encoded)
                artifact = store.artifacts.get(
                    draft["artifact_id"], version=draft["artifact_version"]
                )
                if (
                    artifact is None
                    or artifact.content is None
                    or not artifact.readonly
                    or artifact.kind != "markdown"
                ):
                    raise ValueError("Canonical authored draft artifact is unavailable")
                data = {
                    "work_id": work_id,
                    "draft": draft,
                    "text": artifact.content,
                    "content_sha256": hashlib.sha256(
                        artifact.content.encode()
                    ).hexdigest(),
                    "artifact_description": artifact.description,
                }
                result.append({"id": draft_id, "data": data})
        else:
            raise ValueError("Unknown creative document replication entry")
    _validate_rows(entry_id, result)
    return result


def _conflict(entry_id, entity_id, ancestor, local, remote, now):
    return conflicts.ConflictRecord(
        entry_id=entry_id,
        entity_id=entity_id,
        domain=inventory.DOMAIN_WORK,
        surface=conflicts.surface_for_domain(inventory.DOMAIN_WORK),
        ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local),
        remote_sha=conflicts.row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
    )


def _preflight(home, entry_id, rows):
    store = WorkStore(home)
    parsed = _validate_rows(entry_id, rows)
    with store.connection() as database:
        for data in parsed.values():
            work = database.execute(
                "SELECT record FROM works WHERE id=?", (data["work_id"],)
            ).fetchone()
            if not work:
                raise ValueError(
                    "Creative document requires its canonical current work"
                )
            if entry_id == VERSIONS_ENTRY:
                current = json.loads(work[0])
                if data["revision"] > current["revision"]:
                    raise ValueError("Work version exceeds the canonical current work")
                for field, table in (
                    ("author_ref", "author_revisions"),
                    ("universe_ref", "universe_revisions"),
                ):
                    ref = data["record"][field]
                    if (
                        ref is not None
                        and not database.execute(
                            f"SELECT 1 FROM {table} WHERE id=? AND revision=?",
                            (ref["id"], ref["revision"]),
                        ).fetchone()
                    ):
                        raise ValueError("Work version pinned context is unavailable")
            else:
                artifact = store.artifacts.get(data["draft"]["artifact_id"], version=1)
                if artifact is not None and (
                    artifact.content != data["text"]
                    or not artifact.readonly
                    or artifact.kind != "markdown"
                    or artifact.description != data["artifact_description"]
                ):
                    raise ValueError("Immutable authored draft artifact conflicts")


def apply_rows(home, entry_id, remote_rows, ancestors, queue, now):
    if entry_id not in ENTRIES or not isinstance(ancestors, dict):
        raise ValueError("Unknown creative document replication entry")
    _preflight(home, entry_id, remote_rows)
    local = {row["id"]: row for row in read_rows(home, entry_id)}
    remote = {row["id"]: row for row in remote_rows}
    store = WorkStore(home)
    added = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote)):
        local_row, remote_row = local.get(entity_id), remote.get(entity_id)
        if remote_row is None or (
            local_row is not None
            and conflicts.row_sha(local_row) == conflicts.row_sha(remote_row)
        ):
            continue
        if local_row is not None:
            if queue.record(
                _conflict(
                    entry_id,
                    entity_id,
                    ancestors.get(entity_id, ""),
                    local_row,
                    remote_row,
                    now,
                )
            ):
                conflict_count += 1
            continue
        data = remote_row["data"]
        try:
            if entry_id == VERSIONS_ENTRY:
                store.import_revision(data["record"])
            else:
                store.import_draft(
                    data["work_id"],
                    data["draft"],
                    data["text"],
                    data["artifact_description"],
                    data["content_sha256"],
                )
        except CatalogError as error:
            raise ValueError(str(error)) from error
        added += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, 0, 0, conflict_count, current)
