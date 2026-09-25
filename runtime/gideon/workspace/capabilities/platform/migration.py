from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone

from gideon.cognition.knowledge.store import KnowledgeStore, _fts_tags, normalize_url
from gideon.engine.tasks.models import Project, Task, TaskPriority, TaskStatus
from gideon.workspace.capabilities.knowledge.reviews import digest as knowledge_digest
from gideon.workspace.capabilities.knowledge.capture import CaptureInbox
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks
from gideon.workspace.capabilities.communications.store import PeopleStore, person_values

FORMAT = "legacy_snapshot_v1"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
MAX_PEOPLE = 500
MAX_KNOWLEDGE = 500
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


class MigrationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _decode(content):
    if not isinstance(content, str) or len(content) > MAX_ARCHIVE_BYTES * 2:
        raise MigrationError("Archive must be base64 and at most 16 MiB")
    try:
        raw = base64.b64decode(content, validate=True)
    except (ValueError, TypeError):
        raise MigrationError("Archive is not valid base64") from None
    if not raw or len(raw) > MAX_ARCHIVE_BYTES:
        raise MigrationError("Archive must be base64 and at most 16 MiB")
    return raw


def _json(data, label):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MigrationError(f"{label} is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise MigrationError(f"{label} must be a JSON object")
    return value


def _members(raw):
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except (tarfile.TarError, OSError):
        raise MigrationError("Archive must be a readable gzip tar snapshot") from None
    with archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_FILES:
            raise MigrationError("Archive has no entries or exceeds 2000 entries")
        files, total, roots = {}, 0, set()
        for member in members:
            name = member.name.replace("\\", "/").rstrip("/")
            parts = name.split("/")
            if not name or name.startswith("/") or any(part in ("", ".", "..") for part in parts):
                raise MigrationError("Archive contains an unsafe path")
            roots.add(parts[0])
            if member.issym() or member.islnk() or member.isdev():
                raise MigrationError("Archive links and device entries are unsupported")
            if member.isdir():
                continue
            if not member.isfile() or name in files:
                raise MigrationError("Archive contains an unsupported or duplicate entry")
            total += member.size
            if member.size < 0 or total > MAX_EXPANDED_BYTES:
                raise MigrationError("Expanded archive exceeds 32 MiB")
            stream = archive.extractfile(member)
            if stream is None:
                raise MigrationError("Archive entry could not be read")
            files[name] = stream.read(MAX_EXPANDED_BYTES + 1)
            if len(files[name]) != member.size:
                raise MigrationError("Archive entry is truncated")
        if len(roots) != 1:
            raise MigrationError("Archive must contain exactly one snapshot directory")
        return next(iter(roots)), files


def _inspect(data):
    if not isinstance(data, dict) or set(data) != {"format", "content"} or data.get("format") != FORMAT:
        raise MigrationError(f"Migration format must be {FORMAT}")
    raw = _decode(data["content"])
    archive_digest = hashlib.sha256(raw).hexdigest()
    root, files = _members(raw)
    manifest_name = f"{root}/manifest.json"
    manifest_bytes = files.pop(manifest_name, None)
    if manifest_bytes is None:
        raise MigrationError("A checksum manifest is required")
    manifest = _json(manifest_bytes, "Manifest")
    if set(manifest) != {"generatedAt", "fileCount", "files"} or not isinstance(manifest["generatedAt"], str):
        raise MigrationError("Manifest version or shape is unsupported")
    declared = manifest["files"]
    if not isinstance(declared, dict) or type(manifest["fileCount"]) is not int or manifest["fileCount"] != len(declared):
        raise MigrationError("Manifest file count is invalid")
    normalized = {}
    for path, digest in declared.items():
        if not isinstance(path, str) or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise MigrationError("Manifest contains an invalid path or checksum")
        path = path.replace("\\", "/")
        parts = path.split("/")
        if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise MigrationError("Manifest contains an unsafe path")
        if path in normalized:
            raise MigrationError("Manifest contains duplicate normalized paths")
        normalized[path] = digest
    data_files = {name.removeprefix(f"{root}/data/"): content for name, content in files.items()
                  if name.startswith(f"{root}/data/")}
    other = set(files) - {f"{root}/data/{name}" for name in data_files}
    if other or set(data_files) != set(normalized):
        raise MigrationError("Archive and manifest file inventories differ")
    for name, expected in normalized.items():
        if hashlib.sha256(data_files[name]).hexdigest() != expected:
            raise MigrationError(f"Checksum mismatch for {name}", 409)
    allowed = re.compile(r"^brain/(?:people|projects|ideas|journals|memories|links|buckets|inbox|admin|threads)/(?:index\.json|[A-Za-z0-9_-]{1,128}/index\.json)$")
    unsupported = sorted(name for name in data_files if not allowed.fullmatch(name))
    if unsupported:
        domains = sorted({name.split("/", 1)[0] for name in unsupported})
        raise MigrationError(f"Archive contains unsupported domains: {', '.join(domains)}")
    domains = [domain for domain in ("people", "projects", "ideas", "journals", "memories", "links", "buckets", "inbox", "admin", "threads") if f"brain/{domain}/index.json" in data_files]
    if not domains:
        raise MigrationError("Archive contains no supported collection index")
    if any(domain in domains for domain in ("people", "projects", "admin", "threads")) and len(domains) > 1:
        raise MigrationError("People, projects, tasks and knowledge domains require separate atomic imports")
    for domain in domains:
        index = _json(data_files[f"brain/{domain}/index.json"], f"{domain.title()} collection index")
        if index.get("schemaVersion") != 1 or index.get("type") != domain:
            raise MigrationError(f"{domain.title()} collection schema version is unsupported")
    records = []
    for name in sorted(data_files):
        if name.endswith("/index.json") and name.count("/") == 2:
            continue
        domain = name.split("/")[1]
        record = _json(data_files[name], f"{domain.title()} record")
        record_id = name.split("/")[2]
        if record.get("id") != record_id or record.get("_deleted"):
            raise MigrationError(f"{domain.title()} record identity or tombstone is unsupported")
        if domain == "people":
            if set(record) - {"id", "name", "context", "followUps", "lastTouched", "tags", "createdAt", "updatedAt", "originInstanceId"}:
                raise MigrationError("People record contains unsupported fields")
            notes = str(record.get("context") or "").strip()
            details = []
            for field, label, separator in (("followUps", "Follow-ups", "; "), ("tags", "Tags", ", ")):
                if record.get(field):
                    if not isinstance(record[field], list) or not all(isinstance(v, str) for v in record[field]):
                        raise MigrationError(f"People record {field} are invalid")
                    details.append(f"{label}: {separator.join(record[field])}")
            if record.get("lastTouched"):
                details.append("Last touched: " + str(record["lastTouched"]))
            details.append("Legacy record: " + record_id)
            candidate = person_values({"name": record.get("name"), "notes": "\n".join(filter(None, [notes, *details])), "identities": []})
            records.append({"domain": domain, "source_id": record_id, "values": candidate})
            if len(records) > MAX_PEOPLE:
                raise MigrationError("Migration is limited to 500 people records")
            continue
        if domain == "projects":
            if not UUID.fullmatch(record_id) or set(record) - {"id", "name", "status", "nextAction", "notes", "tags", "createdAt", "updatedAt", "originInstanceId"}:
                raise MigrationError("Projects record identity or fields are unsupported")
            if record.get("status") not in ("active", "waiting", "blocked", "someday", "done"):
                raise MigrationError("Projects record status is unsupported")
            tags = record.get("tags", [])
            if not isinstance(record.get("name"), str) or not record["name"].strip() or len(record["name"]) > 200 or not isinstance(record.get("nextAction"), str) or not record["nextAction"].strip() or not isinstance(tags, list) or not all(isinstance(v, str) and len(v) <= 50 for v in tags):
                raise MigrationError("Projects record fields are invalid")
            for value in (record.get("createdAt"), record.get("updatedAt")):
                try:
                    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise MigrationError("Projects record timestamps are invalid") from None
            details = [record.get("notes", ""), "Next action: " + record["nextAction"], "Legacy status: " + record["status"]]
            if tags:
                details.append("Tags: " + ", ".join(tags))
            records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id, "name": record["name"].strip(),
                            "status": "archived" if record["status"] == "done" else "active", "brief": "\n".join(filter(None, details)),
                            "created_at": record.get("createdAt"), "updated_at": record.get("updatedAt")}})
            continue
        if domain in ("admin", "threads"):
            common = {"id", "title", "status", "notes", "createdAt", "updatedAt", "originInstanceId"}
            fields = common | ({"dueDate", "nextAction"} if domain == "admin" else
                               {"priority", "nextAction", "waitingOn", "dueAt", "tags", "pinned", "refs", "source", "externalState", "closedAt"})
            if (domain == "admin" and not UUID.fullmatch(record_id) or domain == "threads" and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record_id)
                    or set(record) - fields):
                raise MigrationError(f"{domain.title()} record identity or fields are unsupported")
            title, notes, next_action = record.get("title"), record.get("notes", ""), record.get("nextAction", "")
            statuses = ("open", "waiting", "done") if domain == "admin" else ("open", "waiting", "someday", "done", "archived")
            if (not isinstance(title, str) or not title.strip() or len(title) > 200 or record.get("status") not in statuses
                    or not isinstance(notes, str) or len(notes) > (5000 if domain == "admin" else 20000)
                    or not isinstance(next_action, str) or len(next_action) > 500):
                raise MigrationError(f"{domain.title()} record fields are invalid")
            created, updated = record.get("createdAt"), record.get("updatedAt")
            for value in (created, updated, record.get("dueDate") or record.get("dueAt"), record.get("closedAt")):
                if value is None:
                    continue
                try:
                    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise MigrationError(f"{domain.title()} record timestamps are invalid") from None
            tags = record.get("tags", []) if domain == "threads" else []
            refs = record.get("refs", []) if domain == "threads" else []
            if (not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(value, str) or not 0 < len(value) <= 50 for value in tags)
                    or not isinstance(refs, list) or len(refs) > 100):
                raise MigrationError("Threads tags or references are invalid")
            accepted_refs = {"brain.idea", "brain.project", "brain.person", "brain.admin", "brain.memory", "brain.link", "brain.journal",
                             "cos.task", "github.issue", "gitlab.issue", "jira.issue", "url"}
            for ref in refs:
                if (not isinstance(ref, dict) or set(ref) - {"kind", "id", "label"} or ref.get("kind") not in accepted_refs
                        or not isinstance(ref.get("id"), str) or not 0 < len(ref["id"]) <= 2000
                        or not isinstance(ref.get("label", ""), str) or len(ref.get("label", "")) > 300):
                    raise MigrationError("Threads reference is unsupported or invalid")
                if ref["kind"] in ("github.issue", "gitlab.issue", "jira.issue", "url") and not re.fullmatch(r"https?://[^\s]+", ref["id"]):
                    raise MigrationError("Threads external reference URL is invalid")
            mapped_status = TaskStatus.BLOCKED if record["status"] == "waiting" else TaskStatus.DONE if record["status"] == "done" else TaskStatus.CANCELLED if record["status"] == "archived" else TaskStatus.OPEN
            priority = {"low": TaskPriority.LOW, "normal": TaskPriority.MEDIUM, "high": TaskPriority.HIGH, "urgent": TaskPriority.CRITICAL}.get(record.get("priority", "normal"))
            if priority is None or "pinned" in record and type(record["pinned"]) is not bool:
                raise MigrationError("Threads priority or pinned state is invalid")
            source_state = record.get("source")
            if source_state is not None and (not isinstance(source_state, dict) or set(source_state) != {"kind", "key"}
                                             or source_state.get("kind") != "github.issue" or not isinstance(source_state.get("key"), str)
                                             or not 0 < len(source_state["key"]) <= 2000):
                raise MigrationError("Threads source provenance is invalid")
            if record.get("externalState", "unknown") not in ("unknown", "open", "closed"):
                raise MigrationError("Threads external state is invalid")
            labels = tags + (["someday"] if record["status"] == "someday" else []) + (["pinned"] if record.get("pinned") else [])
            records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id, "title": title.strip(),
                            "status": mapped_status.value, "description": notes, "priority": priority.value, "labels": list(dict.fromkeys(labels)),
                            "due": record.get("dueDate") or record.get("dueAt") or "", "action_plan": [{"content": next_action, "completed": mapped_status in (TaskStatus.DONE, TaskStatus.CANCELLED)}] if next_action else [],
                            "notes": [{"content": "Waiting on: " + record["waitingOn"], "timestamp": updated}] if record.get("waitingOn") else [],
                            "blocked_reason_kind": "external" if mapped_status == TaskStatus.BLOCKED else "",
                            "created_at": created, "updated_at": updated, "refs": refs,
                            "source_state": {key: record[key] for key in ("source", "externalState", "closedAt") if key in record}}})
            continue
        if domain == "buckets":
            fields = {"id", "name", "color", "icon", "order", "createdAt", "updatedAt", "originInstanceId"}
            if not UUID.fullmatch(record_id) or set(record) - fields:
                raise MigrationError("Buckets record identity or fields are unsupported")
            if (not isinstance(record.get("name"), str) or not record["name"].strip() or len(record["name"]) > 100
                    or record.get("color") not in (None, "accent", "success", "warning", "error", "purple", "pink", "cyan", "slate")
                    or not isinstance(record.get("icon", ""), str) or len(record.get("icon", "")) > 50
                    or type(record.get("order", 0)) is not int):
                raise MigrationError("Buckets record fields are invalid")
            for value in (record.get("createdAt"), record.get("updatedAt")):
                try:
                    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise MigrationError("Buckets record timestamps are invalid") from None
            records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id,
                            "name": record["name"].strip(), "icon": record.get("icon") or record.get("color") or "",
                            "position": record.get("order", 0), "created_at": record["createdAt"], "updated_at": record["updatedAt"]}})
            continue
        if domain == "inbox":
            fields = {"id", "capturedText", "capturedAt", "source", "ai", "classification", "status", "filed", "correction", "error", "creative", "sentToCatalogAt", "originInstanceId"}
            if not UUID.fullmatch(record_id) or set(record) - fields:
                raise MigrationError("Inbox record identity or fields are unsupported")
            text = record.get("capturedText")
            status = record.get("status")
            if (not isinstance(text, str) or not text.strip() or len(text) > 10000 or record.get("source") != "brain_ui"
                    or status not in ("classifying", "filed", "needs_review", "corrected", "done", "error")):
                raise MigrationError("Inbox record fields are invalid")
            try:
                captured = record["capturedAt"]
                if not isinstance(captured, str) or datetime.fromisoformat(captured.replace("Z", "+00:00")).utcoffset() is None:
                    raise ValueError()
            except (KeyError, ValueError, OverflowError):
                raise MigrationError("Inbox captured timestamp is invalid") from None
            filed = record.get("filed")
            if status in ("filed", "corrected", "done"):
                if (not isinstance(filed, dict) or set(filed) != {"destination", "destinationId"}
                        or filed.get("destination") not in ("ideas", "memories", "links") or not UUID.fullmatch(str(filed.get("destinationId", "")))):
                    raise MigrationError("Routed inbox records require a supported canonical destination")
            elif filed is not None:
                raise MigrationError("Unrouted inbox records cannot carry a filed destination")
            error = record.get("error")
            if error is not None and (not isinstance(error, dict) or set(error) - {"message", "stack"} or not isinstance(error.get("message"), str)):
                raise MigrationError("Inbox error details are invalid")
            ai = record.get("ai")
            if ai is not None and (not isinstance(ai, dict) or set(ai) - {"providerId", "modelId", "promptTemplateId", "temperature", "maxTokens"}
                                   or any(not isinstance(ai.get(key), str) for key in ("providerId", "modelId", "promptTemplateId"))
                                   or "temperature" in ai and (type(ai["temperature"]) not in (int, float) or not 0 <= ai["temperature"] <= 2)
                                   or "maxTokens" in ai and (type(ai["maxTokens"]) is not int or ai["maxTokens"] <= 0)):
                raise MigrationError("Inbox AI provenance is invalid")
            classification = record.get("classification")
            if classification is not None and (not isinstance(classification, dict)
                    or set(classification) - {"destination", "confidence", "title", "cleanedUp", "thoughts", "extracted", "reasons"}
                    or classification.get("destination") not in ("people", "projects", "ideas", "admin", "memories", "links", "unknown")
                    or type(classification.get("confidence")) not in (int, float) or not 0 <= classification["confidence"] <= 1
                    or not isinstance(classification.get("title"), str) or not 0 < len(classification["title"]) <= 200
                    or not isinstance(classification.get("extracted"), dict)):
                raise MigrationError("Inbox classification is invalid")
            correction = record.get("correction")
            if correction is not None and (not isinstance(correction, dict) or set(correction) - {"correctedAt", "previousDestination", "newDestination", "note"}
                    or correction.get("previousDestination") not in ("people", "projects", "ideas", "admin", "memories", "links", "unknown")
                    or correction.get("newDestination") not in ("people", "projects", "ideas", "admin", "memories")
                    or not isinstance(correction.get("correctedAt"), str)):
                raise MigrationError("Inbox correction is invalid")
            if "creative" in record and type(record["creative"]) is not bool:
                raise MigrationError("Inbox creative flag is invalid")
            for value in filter(None, [correction.get("correctedAt") if correction else None, record.get("sentToCatalogAt")]):
                try:
                    if datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                        raise ValueError()
                except (AttributeError, ValueError, OverflowError):
                    raise MigrationError("Inbox event timestamp is invalid") from None
            records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id,
                            "title": text.strip()[:200], "text": text, "captured_at": captured,
                            "status": "routed" if filed else "error" if status == "error" else "needs_review",
                            "destination_id": filed.get("destinationId") if filed else None,
                            "error": error.get("message") if error else None,
                            "event": {key: record[key] for key in ("ai", "classification", "filed", "correction", "creative", "sentToCatalogAt", "status") if key in record}}})
            continue
        if domain != "journals" and not UUID.fullmatch(record_id):
            raise MigrationError(f"{domain.title()} record identity must be a UUID")
        common = {"id", "title", "tags", "createdAt", "updatedAt", "originInstanceId"}
        allowed_fields = common | ({"content", "mood", "source", "sourceRef", "sourceCreatedAt", "sourceUpdatedAt"} if domain == "memories" else
                                   {"url", "description", "note", "linkType", "isRepo", "repoHost", "repoOwner", "repoName", "isGitHubRepo", "gitHubOwner", "gitHubRepo", "localPath", "cloneStatus", "cloneError", "cloneInstanceId", "cloneInterrupted", "malwareScan", "repoIntake", "repoStudy", "bucketId", "bucketOrder"} if domain == "links" else
                                   {"status", "oneLiner", "notes"} if domain == "ideas" else
                                   {"date", "content", "segments"})
        if set(record) - allowed_fields:
            raise MigrationError(f"{domain.title()} record contains unsupported fields")
        if domain == "journals" and (record_id != record.get("date") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record_id)):
            raise MigrationError("Journals record identity must match its ISO date")
        title = record.get("title") or (f"Journal for {record_id}" if domain == "journals" else None)
        content = record.get("content", "") if domain in ("memories", "journals") else "\n".join(filter(None, [record.get("oneLiner", ""), record.get("notes", "")])) if domain == "ideas" else "\n".join(filter(None, [record.get("description", ""), record.get("note", "")]))
        url = record.get("url", "") if domain == "links" else ""
        tags = record.get("tags", []) if domain != "journals" else []
        if domain == "ideas" and (record.get("status") not in ("active", "done") or not isinstance(record.get("oneLiner"), str) or not record["oneLiner"].strip()):
            raise MigrationError("Ideas record status or one-liner is invalid")
        content_limit = 100000 if domain == "journals" else 10000
        if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(content, str) or len(content) > content_limit:
            raise MigrationError(f"{domain.title()} record text is invalid")
        if not isinstance(tags, list) or len(tags) > 100 or not all(isinstance(v, str) and 0 < len(v) <= 50 for v in tags):
            raise MigrationError(f"{domain.title()} record tags are invalid")
        if domain == "links" and (not isinstance(url, str) or not re.fullmatch(r"https?://[^\s]+", url)):
            raise MigrationError("Links record URL is invalid")
        if domain == "links" and (record.get("bucketId") is not None and not UUID.fullmatch(str(record["bucketId"]))
                                  or record.get("bucketOrder") is not None and type(record["bucketOrder"]) is not int):
            raise MigrationError("Links bucket membership is invalid")
        created = record.get("sourceCreatedAt") or record.get("createdAt")
        updated = record.get("sourceUpdatedAt") or record.get("updatedAt") or created
        for value in (created, updated):
            try:
                if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                    raise ValueError()
            except (ValueError, OverflowError):
                raise MigrationError(f"{domain.title()} record timestamps are invalid") from None
        segments = record.get("segments", []) if domain == "journals" else []
        if domain == "journals" and (not isinstance(segments, list) or any(not isinstance(v, dict) or set(v) - {"text", "at", "source"} or not isinstance(v.get("text"), str) or not isinstance(v.get("at"), str) or v.get("source") not in ("text", "voice", "edit") for v in segments)):
            raise MigrationError("Journals record segments are invalid")
        metadata = {"migration_format": FORMAT, "source_collection": domain, "source_record_id": record_id,
                    "source": record.get("source"), "source_ref": record.get("sourceRef"), "mood": record.get("mood"),
                    "link_type": record.get("linkType"), "legacy_status": record.get("status"), "journal_date": record_id if domain == "journals" else None,
                    "journal_timezone": "UTC" if domain == "journals" else None, "journal_revision": 1 if domain == "journals" else None,
                    "segments": segments, "bucket_id": record.get("bucketId"), "bucket_order": record.get("bucketOrder"),
                    "repository": {key: record.get(key) for key in ("isRepo", "repoHost", "repoOwner", "repoName") if key in record}}
        records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id, "title": title.strip(), "content": content,
                        "item_type": "note" if domain == "memories" else "bookmark" if domain == "links" else "fleeting" if domain == "ideas" else "journal",
                        "summary": record.get("description", "") or record.get("oneLiner", ""), "url": normalize_url(url) if url else "",
                        "tags": tags, "provider": "legacy-migration", "source_id": "legacy-archive", "guid": f"{domain}:{record_id}",
                        "file_metadata": metadata, "created_at": created, "updated_at": updated,
                        "is_archived": 1 if domain == "ideas" and record.get("status") == "done" else 0}})
        if domain == "journals":
            records[-1]["values"]["guid"] = "date_journal:" + knowledge_digest([record_id, "UTC"])
        if len(records) > MAX_KNOWLEDGE:
            raise MigrationError("Migration is limited to 500 knowledge records")
    if not records:
        raise MigrationError("Archive contains no supported records")
    token = hashlib.sha256((archive_digest + json.dumps(records, sort_keys=True)).encode()).hexdigest()
    return archive_digest, token, records, manifest["generatedAt"], domains


def preview(data):
    digest, token, records, generated, domains = _inspect(data)
    return {"format": FORMAT, "archive_digest": digest, "review_token": token, "generated_at": generated,
            "coverage": {"supported": domains, "unsupported": "all other snapshot domains"},
            "records": [{"source_id": row["source_id"], "domain": row["domain"],
                         "name": row["values"].get("name") or row["values"]["title"]} for row in records]}


def commit(store: PeopleStore, data, knowledge: KnowledgeStore | None = None, projects: BoundHierarchy | None = None,
           tasks: BoundTasks | None = None):
    if not isinstance(data, dict) or set(data) != {"format", "content", "archive_digest", "review_token"}:
        raise MigrationError("Commit requires the reviewed archive, digest and review token")
    digest, token, records, generated, domains = _inspect({"format": data["format"], "content": data["content"]})
    if data["archive_digest"] != digest or data["review_token"] != token:
        raise MigrationError("Archive changed since preview", 409)
    if domains == ["projects"]:
        if projects is None:
            raise MigrationError("Canonical project store is unavailable", 503)
        return _commit_project(projects, digest, token, records, generated)
    if domains in (["admin"], ["threads"]):
        if tasks is None:
            raise MigrationError("Canonical task store is unavailable", 503)
        return _commit_task(tasks, store, knowledge, projects, digest, token, records, generated)
    if domains != ["people"]:
        if knowledge is None:
            raise MigrationError("Canonical knowledge store is unavailable", 503)
        return _commit_knowledge(knowledge, digest, token, records, generated)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS platform_migrations (archive_digest TEXT PRIMARY KEY, review_token TEXT NOT NULL, receipt TEXT NOT NULL)")
        prior = db.execute("SELECT review_token,receipt FROM platform_migrations WHERE archive_digest=?", (digest,)).fetchone()
        if prior:
            if prior[0] != token:
                raise MigrationError("Archive was already imported with different content", 409)
            return json.loads(prior[1]), False
        imported = []
        for row in records:
            source_id, values = row["source_id"], row["values"]
            person_id = "migration-" + hashlib.sha256((digest + ":" + source_id).encode()).hexdigest()[:32]
            if db.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            body = {**values, "id": person_id}
            db.execute("INSERT INTO people VALUES (?,?,?)", (person_id, json.dumps(body), 1))
            imported.append({"source_id": source_id, "person_id": person_id, "revision": 1})
        receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
                   "committed_at": datetime.now(timezone.utc).isoformat(), "domains": {"people": len(imported)}, "records": imported}
        db.execute("INSERT INTO platform_migrations VALUES (?,?,?)", (digest, token, json.dumps(receipt)))
    return receipt, True


def _commit_knowledge(store: KnowledgeStore, digest, token, records, generated):
    db = store.db
    if any(row["domain"] == "inbox" for row in records):
        CaptureInbox(store)
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS platform_migrations (archive_digest TEXT PRIMARY KEY, review_token TEXT NOT NULL, receipt TEXT NOT NULL)")
        prior = db.execute("SELECT review_token,receipt FROM platform_migrations WHERE archive_digest=?", (digest,)).fetchone()
        if prior:
            if prior["review_token"] != token:
                raise MigrationError("Archive was already imported with different content", 409)
            db.execute("ROLLBACK")
            return json.loads(prior["receipt"]), False
        imported = []
        for row in (candidate for candidate in records if candidate["domain"] not in ("buckets", "inbox")):
            values = row["values"]
            if db.execute("SELECT 1 FROM items WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute("INSERT INTO items (id,title,content,item_type,summary,status,url,word_count,provider,source_id,guid,file_metadata,is_archived,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?)",
                       (values["id"], values["title"], values["content"], values["item_type"], values["summary"], values["url"],
                        len(values["content"].split()), values["provider"], values["source_id"], values["guid"], json.dumps(values["file_metadata"]), values.get("is_archived", 0), values["created_at"], values["updated_at"]))
            store._write_item_tags(values["id"], values["tags"], source="user", now=values["created_at"])
            rowid = db.execute("SELECT rowid FROM items WHERE id=?", (values["id"],)).fetchone()[0]
            db.execute("INSERT INTO items_fts (rowid,title,content,tags) VALUES (?,?,?,?)", (rowid, values["title"], values["content"], _fts_tags(values["tags"])))
            imported.append({"source_id": row["source_id"], "item_id": values["id"], "domain": row["domain"]})
        for row in (candidate for candidate in records if candidate["domain"] == "buckets"):
            values = row["values"]
            if db.execute("SELECT 1 FROM collections WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute("INSERT INTO collections (id,name,kind,query,icon,position,created_at,updated_at) VALUES (?,?,'manual','',?,?,?,?)",
                       (values["id"], values["name"], values["icon"], values["position"], values["created_at"], values["updated_at"]))
            imported.append({"source_id": row["source_id"], "collection_id": values["id"], "domain": "buckets"})
        for row in (candidate for candidate in records if candidate["domain"] == "links"):
            values = row["values"]
            bucket_id = values["file_metadata"].get("bucket_id")
            if bucket_id is None:
                continue
            if not db.execute("SELECT 1 FROM collections WHERE id=?", (bucket_id,)).fetchone():
                raise MigrationError("Link references a missing canonical bucket", 409)
            db.execute("INSERT INTO collection_items (collection_id,item_id,added_at) VALUES (?,?,?)",
                       (bucket_id, values["id"], values["created_at"]))
        for row in (candidate for candidate in records if candidate["domain"] == "inbox"):
            values = row["values"]
            if db.execute("SELECT 1 FROM capability_knowledge_captures WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            if values["destination_id"] is not None and not db.execute("SELECT 1 FROM items WHERE id=?", (values["destination_id"],)).fetchone():
                raise MigrationError("Inbox record references a missing canonical destination", 409)
            request_id = "migration-inbox-" + values["id"]
            db.execute("INSERT INTO capability_knowledge_captures (id,request_id,input_origin,original_text,captured_at,status,error,revision,destination_id) VALUES (?,?,?,?,?,?,?,?,?)",
                       (values["id"], request_id, "text", values["text"], values["captured_at"], values["status"], values["error"], 1, values["destination_id"]))
            db.execute("INSERT INTO capability_knowledge_capture_events (capture_id,request_id,event,payload,happened_at) VALUES (?,?,?,?,?)",
                       (values["id"], "migration-event-" + values["id"], "migration_snapshot", json.dumps(values["event"], sort_keys=True), values["captured_at"]))
            imported.append({"source_id": row["source_id"], "capture_id": values["id"], "domain": "inbox"})
        counts = {domain: sum(row["domain"] == domain for row in records) for domain in sorted(set(row["domain"] for row in records))}
        receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated, "committed_at": datetime.now(timezone.utc).isoformat(), "domains": counts, "records": imported}
        db.execute("INSERT INTO platform_migrations VALUES (?,?,?)", (digest, token, json.dumps(receipt)))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    from gideon.cognition.knowledge import maintenance
    maintenance.mark_dirty(reason="archive migration")
    return receipt, True


def _commit_project(store: BoundHierarchy, digest, token, records, generated):
    if len(records) != 1:
        raise MigrationError("Project archives must contain exactly one project for atomic publication")
    row = records[0]["values"]
    root = store._projects_dir()
    target = root / row["id"]
    receipt_path = target / ".migration-receipt.json"
    if target.exists():
        try:
            prior = json.loads(receipt_path.read_text())
        except (OSError, json.JSONDecodeError):
            raise MigrationError("Migration target identity already exists", 409) from None
        if prior.get("archive_digest") != digest or prior.get("review_token") != token:
            raise MigrationError("Migration target identity already exists", 409)
        return prior["receipt"], False
    if store.get_project_by_name(row["name"]):
        raise MigrationError("A canonical project with this name already exists", 409)
    receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated, "committed_at": datetime.now(timezone.utc).isoformat(),
               "domains": {"projects": 1}, "records": [{"source_id": row["id"], "project_id": row["id"], "domain": "projects"}]}
    staging = tempfile.mkdtemp(prefix=".migration-", dir=root)
    try:
        stage = os.path.join(staging)
        os.mkdir(os.path.join(stage, "context"))
        project = Project(id=row["id"], name=row["name"], status=row["status"], brief=row["brief"], created_at=row["created_at"], updated_at=row["updated_at"])
        with open(os.path.join(stage, "project.json"), "w", encoding="utf-8") as stream:
            json.dump(project.to_dict(), stream, indent=2)
        with open(os.path.join(stage, ".migration-receipt.json"), "w", encoding="utf-8") as stream:
            json.dump({"archive_digest": digest, "review_token": token, "receipt": receipt}, stream)
        os.rename(stage, target)
    except FileExistsError:
        raise MigrationError("Migration target identity already exists", 409) from None
    finally:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
    return receipt, True


def _resolve_task_refs(refs, people, knowledge, projects, tasks):
    resolved = []
    for ref in refs:
        kind, source_id = ref["kind"], ref["id"]
        target_id = source_id
        if kind in ("github.issue", "gitlab.issue", "jira.issue", "url"):
            resolved.append({**ref, "canonical_id": source_id})
            continue
        if kind in ("brain.idea", "brain.memory", "brain.link", "brain.journal"):
            if knowledge is None or knowledge.get_item(source_id) is None:
                raise MigrationError("Thread references a missing canonical knowledge record", 409)
        elif kind == "brain.project":
            if projects is None or projects.get_project(source_id) is None:
                raise MigrationError("Thread references a missing canonical project", 409)
        elif kind == "brain.person":
            matches = [row["id"] for row in people.people() if ("Legacy record: " + source_id) in row.get("notes", "").splitlines()]
            if len(matches) != 1:
                raise MigrationError("Thread person reference does not resolve uniquely", 409)
            target_id = matches[0]
        elif kind in ("brain.admin", "cos.task"):
            path = tasks._task_path(source_id)
            if not path.is_file():
                raise MigrationError("Thread references a missing canonical task", 409)
        resolved.append({**ref, "canonical_id": target_id})
    return resolved


def _commit_task(tasks, people, knowledge, projects, digest, token, records, generated):
    if len(records) != 1:
        raise MigrationError("Task archives must contain exactly one record for atomic publication")
    row, values = records[0], records[0]["values"]
    target = tasks._task_path(values["id"])
    if target.exists():
        try:
            body = json.loads(target.read_text())
            marker = next(item for item in body.get("evidence", []) if item.get("type") == "platform_migration")
        except (OSError, json.JSONDecodeError, StopIteration, AttributeError):
            raise MigrationError("Migration target identity already exists", 409) from None
        if marker.get("archive_digest") != digest or marker.get("review_token") != token:
            raise MigrationError("Migration target identity already exists", 409)
        return marker["receipt"], False
    resolved_refs = _resolve_task_refs(values["refs"], people, knowledge, projects, tasks)
    receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
               "committed_at": datetime.now(timezone.utc).isoformat(), "domains": {row["domain"]: 1},
               "records": [{"source_id": row["source_id"], "task_id": values["id"], "domain": row["domain"]}]}
    evidence = {"type": "platform_migration", "archive_digest": digest, "review_token": token, "receipt": receipt,
                "source_collection": row["domain"], "source_record_id": row["source_id"], "references": resolved_refs,
                "source_state": values["source_state"]}
    task = Task(id=values["id"], title=values["title"], status=TaskStatus(values["status"]), description=values["description"],
                provider="native", priority=TaskPriority(values["priority"]), labels=values["labels"], due=values["due"],
                action_plan=values["action_plan"], notes=values["notes"], blocked_reason_kind=values["blocked_reason_kind"],
                evidence=[evidence], created_at=values["created_at"], updated_at=values["updated_at"])
    root = tasks._ensure_dir()
    descriptor, staging = tempfile.mkstemp(prefix=".migration-", suffix=".json", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(task.to_dict(), stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, target)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        raise MigrationError("Migration target identity already exists", 409) from None
    finally:
        try:
            os.unlink(staging)
        except FileNotFoundError:
            pass
    return receipt, True


def receipts(store: PeopleStore, knowledge: KnowledgeStore | None = None, projects: BoundHierarchy | None = None,
             tasks: BoundTasks | None = None):
    with closing(store.connect()) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'").fetchone()
        result = [] if not exists else [json.loads(row[0]) for row in db.execute("SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20")]
    if knowledge is not None and knowledge.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'").fetchone():
        result += [json.loads(row[0]) for row in knowledge.db.execute("SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20")]
    if projects is not None:
        for path in projects._projects_dir().glob("*/.migration-receipt.json"):
            try:
                result.append(json.loads(path.read_text())["receipt"])
            except (OSError, KeyError, json.JSONDecodeError):
                continue
    if tasks is not None:
        for task in tasks._all_tasks():
            result += [item["receipt"] for item in task.evidence
                       if item.get("type") == "platform_migration" and isinstance(item.get("receipt"), dict)]
    return sorted(result, key=lambda row: row["committed_at"], reverse=True)[:20]
