from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit

from gideon.cognition.knowledge.store import KnowledgeStore, _fts_tags, normalize_url
from gideon.engine.tasks.models import Project, Task, TaskPriority, TaskStatus
from gideon.engine.tasks.native import TaskMutation
from gideon.workspace.artifacts.models import kind_for_mime
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.knowledge.reviews import digest as knowledge_digest
from gideon.workspace.capabilities.knowledge.capture import CaptureInbox
from gideon.workspace.capabilities.knowledge.typed import BoundHierarchy, BoundTasks
from gideon.workspace.capabilities.communications.store import PeopleStore, person_values
from gideon.workspace.capabilities.music.store import DomainError, RepertoireStore

FORMAT = "legacy_snapshot_v1"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
MAX_PEOPLE = 500
MAX_KNOWLEDGE = 500
MAX_SONG_ATTACHMENTS = 30
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
SONG_MIMES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml", ".txt": "text/plain",
              ".md": "text/markdown", ".mid": "audio/midi", ".midi": "audio/midi", ".mp3": "audio/mpeg",
              ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


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
    allowed = re.compile(r"^brain/(?:people|projects|ideas|journals|memories|links|buckets|inbox|admin|threads|songs)/(?:index\.json|[A-Za-z0-9_-]{1,128}/index\.json)$")
    song_asset = re.compile(r"^brain/songbook/([A-Za-z0-9][A-Za-z0-9._-]{0,299})$")
    unsupported = sorted(name for name in data_files if not allowed.fullmatch(name) and not song_asset.fullmatch(name))
    if unsupported:
        domains = sorted({name.split("/", 1)[0] for name in unsupported})
        raise MigrationError(f"Archive contains unsupported domains: {', '.join(domains)}")
    domains = [domain for domain in ("people", "projects", "ideas", "journals", "memories", "links", "buckets", "inbox", "admin", "threads", "songs") if f"brain/{domain}/index.json" in data_files]
    if not domains:
        raise MigrationError("Archive contains no supported collection index")
    coordinated_families = {"people", "projects", "admin", "threads", "ideas", "journals", "memories", "links", "buckets"}
    coordinated_records = len(domains) > 1 and set(domains) <= coordinated_families
    if "inbox" in domains and len(domains) > 1:
        raise MigrationError("Inbox capture history is immutable and requires a separate atomic import")
    if any(domain in domains for domain in ("people", "projects", "admin", "threads", "songs")) and len(domains) > 1 and not coordinated_records:
        raise MigrationError("People, projects, tasks, songs and knowledge domains require separate atomic imports")
    for domain in domains:
        index = _json(data_files[f"brain/{domain}/index.json"], f"{domain.title()} collection index")
        if index.get("schemaVersion") != 1 or index.get("type") != domain:
            raise MigrationError(f"{domain.title()} collection schema version is unsupported")
    records = []
    assets = {match.group(1): data for name, data in data_files.items() if (match := song_asset.fullmatch(name))}
    for name in sorted(data_files):
        if song_asset.fullmatch(name):
            continue
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
        if domain == "songs":
            fields = {"id", "title", "artist", "instrument", "stage", "tags", "key", "capo", "tuning", "sourceUrl", "links",
                      "content", "notes", "scrollDurationSec", "attachments", "practice", "createdAt", "updatedAt", "originInstanceId"}
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record_id) or set(record) - fields:
                raise MigrationError("Songs record identity or fields are unsupported")
            title, artist, instrument = record.get("title"), record.get("artist", ""), record.get("instrument", "guitar")
            stage, tags, content = record.get("stage", "new"), record.get("tags", []), record.get("content", {})
            if (not isinstance(title, str) or not title.strip() or len(title) > 300 or not isinstance(artist, str) or len(artist) > 300
                    or instrument not in ("guitar", "piano", "ukulele", "bass", "voice", "drums", "other")
                    or stage not in ("new", "learning", "learned", "memorized")
                    or not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(tag, str) or not 0 < len(tag) <= 50 for tag in tags)
                    or not isinstance(content, dict) or set(content) != {"format", "text"}
                    or content.get("format") not in ("chordpro", "tab", "plain", "drum")
                    or not isinstance(content.get("text"), str) or len(content["text"]) > 200000):
                raise MigrationError("Songs record core fields are invalid")
            key, capo, tuning, source_url, notes = record.get("key", ""), record.get("capo", 0), record.get("tuning", ""), record.get("sourceUrl", ""), record.get("notes", "")
            if (not isinstance(key, str) or len(key) > 20 or type(capo) is not int or not 0 <= capo <= 12
                    or not isinstance(tuning, str) or len(tuning) > 40 or not isinstance(source_url, str) or len(source_url) > 2000
                    or source_url and not re.fullmatch(r"https?://[^\s]+", source_url) or not isinstance(notes, str) or len(notes) > 5000):
                raise MigrationError("Songs record musical fields are invalid")
            parsed_source = urlsplit(source_url) if source_url else None
            if parsed_source and (not parsed_source.hostname or parsed_source.username or parsed_source.password):
                raise MigrationError("Songs record musical fields are invalid")
            links, scroll = record.get("links", []), record.get("scrollDurationSec")
            if (not isinstance(links, list) or len(links) > 20 or any(not isinstance(link, dict) or set(link) - {"type", "id", "label"}
                    or not isinstance(link.get("type"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", link["type"])
                    or not isinstance(link.get("id"), str) or not 0 < len(link["id"]) <= 200
                    or not isinstance(link.get("label", ""), str) or len(link.get("label", "")) > 300 for link in links)
                    or scroll is not None and (type(scroll) is not int or not 15 <= scroll <= 3600)):
                raise MigrationError("Songs links or scroll duration are invalid")
            attachments = record.get("attachments", [])
            if not isinstance(attachments, list) or len(attachments) > MAX_SONG_ATTACHMENTS:
                raise MigrationError("Songs attachment metadata is invalid")
            planned = []
            for attachment in attachments:
                if (not isinstance(attachment, dict) or set(attachment) != {"filename", "label", "mime", "size", "sha256"}
                        or not isinstance(attachment.get("filename"), str) or not song_asset.fullmatch("brain/songbook/" + attachment["filename"])
                        or not isinstance(attachment.get("label"), str) or len(attachment["label"]) > 300
                        or type(attachment.get("size")) is not int or attachment["size"] < 0 or not SHA256.fullmatch(str(attachment.get("sha256", "")))):
                    raise MigrationError("Songs attachment metadata is invalid")
                extension = os.path.splitext(attachment["filename"])[1].lower()
                expected_mime, raw_asset = SONG_MIMES.get(extension), assets.get(attachment["filename"])
                if expected_mime is None or attachment.get("mime") != expected_mime:
                    raise MigrationError("Songs attachment type is unsupported")
                if raw_asset is None:
                    raise MigrationError("Songs attachment bytes are missing from this snapshot", 409)
                if len(raw_asset) != attachment["size"] or hashlib.sha256(raw_asset).hexdigest() != attachment["sha256"]:
                    raise MigrationError("Songs attachment size or checksum does not match", 409)
                slug = "legacy-song-" + hashlib.sha256((record_id + ":" + attachment["filename"]).encode()).hexdigest()[:32]
                text_kind = {".txt": "text", ".md": "markdown", ".svg": "svg"}.get(extension)
                if text_kind:
                    try:
                        raw_asset.decode("utf-8")
                    except UnicodeDecodeError:
                        raise MigrationError("Text song attachments must be valid UTF-8") from None
                planned.append({**attachment, "slug": slug, "version": 1, "kind": text_kind or kind_for_mime(expected_mime), "bytes": raw_asset})
            if len({attachment["filename"] for attachment in attachments}) != len(attachments):
                raise MigrationError("Songs attachment filenames must be unique")
            practice = record.get("practice")
            if practice is not None:
                if (not isinstance(practice, dict) or set(practice) != {"ease", "intervalDays", "nextReview", "lastReviewed", "sessions", "lastQuality"}
                        or type(practice["ease"]) not in (int, float) or not math.isfinite(practice["ease"]) or not 1.3 <= practice["ease"] <= 5
                        or type(practice["intervalDays"]) is not int or not 0 <= practice["intervalDays"] <= 36500
                        or type(practice["sessions"]) is not int or practice["sessions"] < 0
                        or type(practice["lastQuality"]) is not int or not 0 <= practice["lastQuality"] <= 5):
                    raise MigrationError("Songs practice history is invalid")
                for value in (practice["nextReview"], practice["lastReviewed"]):
                    try:
                        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                            raise ValueError()
                    except (ValueError, OverflowError):
                        raise MigrationError("Songs practice timestamps are invalid") from None
            for value in (record.get("createdAt"), record.get("updatedAt")):
                try:
                    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise MigrationError("Songs record timestamps are invalid") from None
            records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id, "title": title.strip(), "artist": artist,
                            "instrument": instrument, "stage": stage, "tags": tags, "key": key, "capo": capo, "tuning": tuning,
                            "source_url": source_url, "links": links, "notation": content, "notes": notes, "scroll_duration_seconds": scroll,
                            "attachments": planned, "practice": practice, "created_at": record["createdAt"], "updated_at": record["updatedAt"]}})
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
    if coordinated_records:
        _ordered_canonical_records(records)
    referenced_assets = {attachment["filename"] for row in records if row["domain"] == "songs" for attachment in row["values"]["attachments"]}
    if set(assets) != referenced_assets:
        raise MigrationError("Snapshot contains unreferenced songbook attachment bytes")
    token_rows = [{**row, "values": {**row["values"], "attachments": [{key: value for key, value in attachment.items() if key != "bytes"}
                  for attachment in row["values"].get("attachments", [])]}} for row in records]
    token = hashlib.sha256((archive_digest + json.dumps(token_rows, sort_keys=True)).encode()).hexdigest()
    return archive_digest, token, records, manifest["generatedAt"], domains


def preview(data):
    digest, token, records, generated, domains = _inspect(data)
    return {"format": FORMAT, "archive_digest": digest, "review_token": token, "generated_at": generated,
            "coverage": {"supported": domains, "unsupported": "all other snapshot domains"},
            "records": [{"source_id": row["source_id"], "domain": row["domain"],
                         "name": row["values"].get("name") or row["values"]["title"]} for row in records]}


def commit(store: PeopleStore, data, knowledge: KnowledgeStore | None = None, projects: BoundHierarchy | None = None,
           tasks: BoundTasks | None = None, repertoire: RepertoireStore | None = None,
           artifacts: NativeArtifactProvider | None = None):
    if not isinstance(data, dict) or set(data) != {"format", "content", "archive_digest", "review_token"}:
        raise MigrationError("Commit requires the reviewed archive, digest and review token")
    digest, token, records, generated, domains = _inspect({"format": data["format"], "content": data["content"]})
    if data["archive_digest"] != digest or data["review_token"] != token:
        raise MigrationError("Archive changed since preview", 409)
    coordinated_families = {"people", "projects", "admin", "threads", "ideas", "journals", "memories", "links", "buckets"}
    if len(domains) > 1 and set(domains) <= coordinated_families:
        if projects is None or tasks is None:
            raise MigrationError("Canonical project and task stores are unavailable", 503)
        return _commit_canonical_records(tasks, store, knowledge, projects, digest, token, records, generated)
    if domains == ["projects"]:
        if projects is None:
            raise MigrationError("Canonical project store is unavailable", 503)
        return _commit_project(projects, digest, token, records, generated)
    if domains in (["admin"], ["threads"]):
        if tasks is None:
            raise MigrationError("Canonical task store is unavailable", 503)
        return _commit_task(tasks, store, knowledge, projects, digest, token, records, generated)
    if domains == ["songs"]:
        if repertoire is None or artifacts is None:
            raise MigrationError("Canonical repertoire and artifact stores are unavailable", 503)
        return _commit_song(repertoire, artifacts, digest, token, records, generated)
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


def _commit_project(store: BoundHierarchy, digest, token, records, generated, receipt=None, require_fingerprint=False):
    if len(records) != 1:
        raise MigrationError("Project archives must contain exactly one project for atomic publication")
    row = records[0]["values"]
    root = store._projects_dir()
    target = root / row["id"]
    receipt_path = target / ".migration-receipt.json"
    project = Project(id=row["id"], name=row["name"], status=row["status"], brief=row["brief"], created_at=row["created_at"], updated_at=row["updated_at"])
    project_body = project.to_dict()
    record_fingerprint = hashlib.sha256(json.dumps(project_body, sort_keys=True).encode()).hexdigest()
    if target.exists():
        try:
            prior = json.loads(receipt_path.read_text())
            actual_fingerprint = hashlib.sha256(json.dumps(json.loads((target / "project.json").read_text()), sort_keys=True).encode()).hexdigest()
        except (OSError, json.JSONDecodeError):
            raise MigrationError("Migration target identity already exists", 409) from None
        marker_fingerprint = prior.get("record_fingerprint")
        if (prior.get("archive_digest") != digest or prior.get("review_token") != token
                or actual_fingerprint != record_fingerprint
                or marker_fingerprint not in ((record_fingerprint,) if require_fingerprint else (None, record_fingerprint))):
            raise MigrationError("Migration target identity already exists", 409)
        return prior["receipt"], False
    if store.get_project_by_name(row["name"]):
        raise MigrationError("A canonical project with this name already exists", 409)
    receipt = receipt or {"archive_digest": digest, "format": FORMAT, "generated_at": generated, "committed_at": datetime.now(timezone.utc).isoformat(),
                          "domains": {"projects": 1}, "records": [{"source_id": row["id"], "project_id": row["id"], "domain": "projects"}]}
    staging = tempfile.mkdtemp(prefix=".migration-", dir=root)
    try:
        stage = os.path.join(staging)
        os.mkdir(os.path.join(stage, "context"))
        with open(os.path.join(stage, "project.json"), "w", encoding="utf-8") as stream:
            json.dump(project_body, stream, indent=2)
        with open(os.path.join(stage, ".migration-receipt.json"), "w", encoding="utf-8") as stream:
            json.dump({"archive_digest": digest, "review_token": token, "record_fingerprint": record_fingerprint, "receipt": receipt}, stream)
        os.rename(stage, target)
    except FileExistsError:
        raise MigrationError("Migration target identity already exists", 409) from None
    finally:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
    return receipt, True


def _resolve_task_refs(refs, people, knowledge, projects, tasks, archive_digest=None):
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
            inventory = people.people()
            coordinated = ("migration-" + hashlib.sha256(
                (archive_digest + ":" + source_id).encode()).hexdigest()[:32]) if archive_digest else None
            exact = [row["id"] for row in inventory if row["id"] == coordinated]
            matches = exact or [row["id"] for row in inventory
                                if ("Legacy record: " + source_id) in row.get("notes", "").splitlines()]
            if len(matches) != 1:
                raise MigrationError("Thread person reference does not resolve uniquely", 409)
            target_id = matches[0]
        elif kind in ("brain.admin", "cos.task"):
            path = tasks._task_path(source_id)
            if not path.is_file():
                raise MigrationError("Thread references a missing canonical task", 409)
        resolved.append({**ref, "canonical_id": target_id})
    return resolved


def _commit_task(tasks, people, knowledge, projects, digest, token, records, generated, receipt=None, require_fingerprint=False):
    if len(records) != 1:
        raise MigrationError("Task archives must contain exactly one record for atomic publication")
    row, values = records[0], records[0]["values"]
    target = tasks._task_path(values["id"])
    receipt = receipt or {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
                          "committed_at": datetime.now(timezone.utc).isoformat(), "domains": {row["domain"]: 1},
                          "records": [{"source_id": row["source_id"], "task_id": values["id"], "domain": row["domain"]}]}
    task = Task(id=values["id"], title=values["title"], status=TaskStatus(values["status"]), description=values["description"],
                provider="native", priority=TaskPriority(values["priority"]), labels=values["labels"], due=values["due"],
                action_plan=values["action_plan"], notes=values["notes"], blocked_reason_kind=values["blocked_reason_kind"],
                evidence=[], created_at=values["created_at"], updated_at=values["updated_at"])
    record_fingerprint = hashlib.sha256(json.dumps(task.to_dict(), sort_keys=True).encode()).hexdigest()
    if target.exists():
        try:
            body = json.loads(target.read_text())
            marker = next(item for item in body.get("evidence", []) if item.get("type") == "platform_migration")
        except (OSError, json.JSONDecodeError, StopIteration, AttributeError):
            raise MigrationError("Migration target identity already exists", 409) from None
        actual_fingerprint = hashlib.sha256(json.dumps({**body, "evidence": []}, sort_keys=True).encode()).hexdigest()
        marker_fingerprint = marker.get("record_fingerprint")
        if (marker.get("archive_digest") != digest or marker.get("review_token") != token
                or actual_fingerprint != record_fingerprint
                or marker_fingerprint not in ((record_fingerprint,) if require_fingerprint else (None, record_fingerprint))):
            raise MigrationError("Migration target identity already exists", 409)
        return marker["receipt"], False
    resolved_refs = _resolve_task_refs(values["refs"], people, knowledge, projects, tasks, digest)
    task.evidence = [{"type": "platform_migration", "archive_digest": digest, "review_token": token, "receipt": receipt,
                      "source_collection": row["domain"], "source_record_id": row["source_id"], "references": resolved_refs,
                      "source_state": values["source_state"], "record_fingerprint": record_fingerprint}]
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


def _coordinator_root(tasks):
    root = tasks.home / "capabilities" / "platform" / "migration-journals" / "coordinated"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_coordinator_journal(path):
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise MigrationError("A coordinated migration recovery journal exceeds 2 MiB", 409)
        journal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise MigrationError("A coordinated migration recovery journal is unreadable", 409) from None
    expected = {"schema", "kind", "status", "archive_digest", "review_token", "generated_at", "records", "records_hash", "receipt"}
    if (not isinstance(journal, dict) or set(journal) != expected or journal.get("schema") != 1
            or journal.get("kind") != "project_task" or journal.get("status") not in ("prepared", "project_ready", "complete")
            or not SHA256.fullmatch(str(journal.get("archive_digest", "")))
            or not SHA256.fullmatch(str(journal.get("review_token", "")))
            or not isinstance(journal.get("generated_at"), str) or not isinstance(journal.get("records"), list)
            or not SHA256.fullmatch(str(journal.get("records_hash", "")))
            or not isinstance(journal.get("receipt"), dict)):
        raise MigrationError("A coordinated migration recovery journal has an unsupported shape", 409)
    observed_hash = hashlib.sha256(json.dumps(journal["records"], sort_keys=True).encode()).hexdigest()
    if observed_hash != journal["records_hash"]:
        raise MigrationError("A coordinated migration recovery journal record plan changed", 409)
    domains = [row.get("domain") for row in journal["records"] if isinstance(row, dict)]
    if (len(journal["records"]) != 2 or domains.count("projects") != 1
            or sum(domain in ("admin", "threads") for domain in domains) != 1
            or journal["receipt"].get("archive_digest") != journal["archive_digest"]):
        raise MigrationError("A coordinated migration recovery journal has an unsupported plan", 409)
    return journal


def _project_state(projects, row, journal):
    target = projects._projects_dir() / row["values"]["id"]
    if not target.exists():
        return "absent"
    marker = target / ".migration-receipt.json"
    try:
        body = json.loads(marker.read_text())
        actual = json.loads((target / "project.json").read_text())
    except (OSError, json.JSONDecodeError):
        return "drift"
    actual_hash = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
    return "owned" if (body.get("archive_digest") == journal["archive_digest"]
                         and body.get("review_token") == journal["review_token"]
                         and body.get("record_fingerprint") == actual_hash) else "drift"


def _task_state(tasks, row, journal):
    path = tasks._task_path(row["values"]["id"])
    if not path.exists():
        return "absent"
    try:
        body = json.loads(path.read_text())
        marker = next(item for item in body.get("evidence", []) if item.get("type") == "platform_migration")
    except (OSError, json.JSONDecodeError, StopIteration, AttributeError):
        return "drift"
    actual_hash = hashlib.sha256(json.dumps({**body, "evidence": []}, sort_keys=True).encode()).hexdigest()
    return "owned" if (marker.get("archive_digest") == journal["archive_digest"]
                         and marker.get("review_token") == journal["review_token"]
                         and marker.get("record_fingerprint") == actual_hash) else "drift"


def _compensate_project_task(journal, tasks, projects):
    project = next(row for row in journal["records"] if row["domain"] == "projects")
    task = next(row for row in journal["records"] if row["domain"] in ("admin", "threads"))
    task_id, project_id = task["values"]["id"], project["values"]["id"]
    task_state, project_state = _task_state(tasks, task, journal), _project_state(projects, project, journal)
    if "drift" in (task_state, project_state):
        raise MigrationError("Coordinated migration identity changed; automatic compensation refused", 409)
    if task_state == "owned":
        if not TaskMutation(tasks).delete(task_id):
            raise MigrationError("Coordinated task compensation could not be completed", 409)
    if project_state == "owned":
        if not projects.delete_project(project_id):
            raise MigrationError("Coordinated project compensation could not be completed", 409)


def _resume_coordinated_project_task(path, journal, tasks, people, knowledge, projects):
    project_records = [row for row in journal["records"] if row["domain"] == "projects"]
    task_records = [row for row in journal["records"] if row["domain"] in ("admin", "threads")]
    try:
        _commit_project(projects, journal["archive_digest"], journal["review_token"], project_records,
                        journal["generated_at"], journal["receipt"], require_fingerprint=True)
        journal["status"] = "project_ready"
        _write_journal(path, journal)
        _commit_task(tasks, people, knowledge, projects, journal["archive_digest"], journal["review_token"],
                     task_records, journal["generated_at"], journal["receipt"], require_fingerprint=True)
        journal["status"] = "complete"
        _write_journal(path, journal)
        return journal["receipt"]
    except Exception:
        _compensate_project_task(journal, tasks, projects)
        if path.exists():
            path.unlink()
        raise


def _recover_coordinated_journals(tasks, people, knowledge, projects):
    for path in _coordinator_root(tasks).glob("*.json"):
        journal = _read_coordinator_journal(path)
        if journal["status"] == "complete":
            project = next(row for row in journal["records"] if row["domain"] == "projects")
            task = next(row for row in journal["records"] if row["domain"] in ("admin", "threads"))
            if _project_state(projects, project, journal) != "owned" or _task_state(tasks, task, journal) != "owned":
                raise MigrationError("A completed coordinated migration has canonical identity drift", 409)
        else:
            _resume_coordinated_project_task(path, journal, tasks, people, knowledge, projects)
    _recover_canonical_journals(tasks, people, knowledge, projects)


def _commit_coordinated_project_task(tasks, people, knowledge, projects, digest, token, records, generated):
    project_records = [row for row in records if row["domain"] == "projects"]
    task_records = [row for row in records if row["domain"] in ("admin", "threads")]
    if len(project_records) != 1 or len(task_records) != 1 or len(records) != 2:
        raise MigrationError("Coordinated project and task archives require exactly one record from each family")
    _recover_coordinated_journals(tasks, people, knowledge, projects)
    path = _coordinator_root(tasks) / (digest + ".json")
    if path.exists():
        prior = _read_coordinator_journal(path)
        if prior["review_token"] != token:
            raise MigrationError("Archive was already imported with different content", 409)
        if prior["status"] != "complete":
            _resume_coordinated_project_task(path, prior, tasks, people, knowledge, projects)
        return prior["receipt"], False
    project, task = project_records[0], task_records[0]
    if ((projects._projects_dir() / project["values"]["id"]).exists()
            or projects.get_project_by_name(project["values"]["name"])):
        raise MigrationError("Migration target identity already exists", 409)
    if tasks._task_path(task["values"]["id"]).exists():
        raise MigrationError("Migration target identity already exists", 409)
    receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
               "committed_at": datetime.now(timezone.utc).isoformat(),
               "domains": {"projects": 1, task["domain"]: 1},
               "records": [{"source_id": project["source_id"], "project_id": project["values"]["id"], "domain": "projects"},
                           {"source_id": task["source_id"], "task_id": task["values"]["id"], "domain": task["domain"]}]}
    journal = {"schema": 1, "kind": "project_task", "status": "prepared", "archive_digest": digest,
               "review_token": token, "generated_at": generated, "records": records,
               "records_hash": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(), "receipt": receipt}
    _write_journal(path, journal)
    return _resume_coordinated_project_task(path, journal, tasks, people, knowledge, projects), True


def _canonical_coordinator_root(tasks):
    root = _coordinator_root(tasks) / "canonical"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ordered_canonical_records(records):
    if not 2 <= len(records) <= 100:
        raise MigrationError("Coordinated archives require between 2 and 100 canonical records")
    targets, keys = set(), set()
    for row in records:
        adapter = ("person" if row["domain"] == "people" else "project" if row["domain"] == "projects" else
                   "collection" if row["domain"] == "buckets" else
                   "knowledge" if row["domain"] in ("ideas", "journals", "memories", "links") else "task")
        identity = row["source_id"] if adapter == "person" else row["values"]["id"]
        target, key = (adapter, identity), row["domain"] + ":" + row["source_id"]
        if target in targets or key in keys:
            raise MigrationError("Coordinated archive contains a duplicate canonical identity", 409)
        targets.add(target); keys.add(key)
    people = sorted((row for row in records if row["domain"] == "people"), key=lambda row: row["source_id"])
    projects = sorted((row for row in records if row["domain"] == "projects"), key=lambda row: row["source_id"])
    collections = sorted((row for row in records if row["domain"] == "buckets"), key=lambda row: row["source_id"])
    knowledge = sorted((row for row in records if row["domain"] in ("ideas", "journals", "memories", "links")),
                       key=lambda row: (row["domain"], row["source_id"]))
    pending = {row["values"]["id"]: row for row in records if row["domain"] in ("admin", "threads")}
    if len(people) + len(projects) + len(collections) + len(knowledge) + len(pending) != len(records):
        raise MigrationError("This canonical coordinator does not support one or more archive families")
    ordered, published = [*people, *projects, *collections, *knowledge], set()
    while pending:
        ready = []
        for identity, row in pending.items():
            known = set(pending) | published
            dependencies = {ref["id"] for ref in row["values"]["refs"]
                            if ref["kind"] in ("brain.admin", "cos.task") and ref["id"] in known}
            if dependencies <= published:
                ready.append(identity)
        if not ready:
            raise MigrationError("Coordinated task references contain a cycle", 409)
        for identity in sorted(ready):
            ordered.append(pending.pop(identity))
            published.add(identity)
    return ordered


def _canonical_step(row):
    return {"key": row["domain"] + ":" + row["source_id"],
            "adapter": ("person" if row["domain"] == "people" else "project" if row["domain"] == "projects" else
                        "collection" if row["domain"] == "buckets" else
                        "knowledge" if row["domain"] in ("ideas", "journals", "memories", "links") else "task"),
            "domain": row["domain"], "state": "pending", "record": row,
            "record_hash": hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()}


def _read_canonical_journal(path):
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise MigrationError("A canonical migration recovery journal exceeds 2 MiB", 409)
        journal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise MigrationError("A canonical migration recovery journal is unreadable", 409) from None
    expected = {"schema", "kind", "status", "archive_digest", "review_token", "generated_at", "steps", "receipt", "receipt_hash"}
    if (not isinstance(journal, dict) or set(journal) != expected or journal.get("schema") != 2
            or journal.get("kind") != "canonical_records" or journal.get("status") not in ("prepared", "applying", "complete")
            or not SHA256.fullmatch(str(journal.get("archive_digest", "")))
            or not SHA256.fullmatch(str(journal.get("review_token", "")))
            or not isinstance(journal.get("steps"), list) or not 2 <= len(journal["steps"]) <= 100
            or not isinstance(journal.get("receipt"), dict)
            or not SHA256.fullmatch(str(journal.get("receipt_hash", "")))
            or hashlib.sha256(json.dumps(journal["receipt"], sort_keys=True).encode()).hexdigest() != journal["receipt_hash"]
            or journal["receipt"].get("archive_digest") != journal["archive_digest"]):
        raise MigrationError("A canonical migration recovery journal has an unsupported shape", 409)
    keys = set()
    for step in journal["steps"]:
        adapter_for = {"people": "person", "projects": "project", "buckets": "collection",
                       "ideas": "knowledge", "journals": "knowledge", "memories": "knowledge", "links": "knowledge",
                       "admin": "task", "threads": "task"}
        if (not isinstance(step, dict) or set(step) != {"key", "adapter", "domain", "state", "record", "record_hash"}
                or step.get("adapter") not in ("person", "project", "collection", "knowledge", "task")
                or step.get("state") not in ("pending", "applied") or step.get("domain") not in adapter_for
                or not isinstance(step.get("record"), dict)
                or step["record"].get("domain") != step["domain"]
                or step["adapter"] != adapter_for[step["domain"]]
                or step["key"] != step["domain"] + ":" + str(step["record"].get("source_id", ""))
                or not SHA256.fullmatch(str(step.get("record_hash", "")))
                or hashlib.sha256(json.dumps(step["record"], sort_keys=True).encode()).hexdigest() != step["record_hash"]
                or step["key"] in keys):
            raise MigrationError("A canonical migration recovery journal has an unsupported step", 409)
        keys.add(step["key"])
    return journal


def _coordinated_person_id(journal, step):
    return "migration-" + hashlib.sha256(
        (journal["archive_digest"] + ":" + step["record"]["source_id"]).encode()).hexdigest()[:32]


def _person_state(people, step, journal):
    person_id = _coordinated_person_id(journal, step)
    expected = {**step["record"]["values"], "id": person_id}
    with closing(people.connect()) as db:
        row = db.execute("SELECT body,revision FROM people WHERE id=?", (person_id,)).fetchone()
        touches = db.execute("SELECT count(*) FROM touchpoints WHERE person_id=?", (person_id,)).fetchone()[0]
    if row is None:
        return "absent"
    try:
        actual = json.loads(row[0])
    except json.JSONDecodeError:
        return "drift"
    return "owned" if row[1] == 1 and touches == 0 and actual == expected else "drift"


def _knowledge_state(knowledge, step, journal):
    if knowledge is None:
        return "drift"
    db, values = knowledge.db, step["record"]["values"]
    row = db.execute(
        "SELECT rowid,id,title,content,item_type,summary,status,url,word_count,provider,source_id,guid,"
        "file_metadata,is_archived,created_at,updated_at FROM items WHERE id=?", (values["id"],)).fetchone()
    memberships = [tuple(candidate) for candidate in db.execute(
        "SELECT collection_id,added_at FROM collection_items WHERE item_id=? ORDER BY collection_id", (values["id"],))]
    if row is None:
        return "drift" if memberships else "absent"
    try:
        metadata = json.loads(row["file_metadata"])
    except (TypeError, json.JSONDecodeError):
        return "drift"
    expected = {"id": values["id"], "title": values["title"], "content": values["content"],
                "item_type": values["item_type"], "summary": values["summary"], "status": "active",
                "url": values["url"], "word_count": len(values["content"].split()), "provider": values["provider"],
                "source_id": values["source_id"], "guid": values["guid"], "file_metadata": values["file_metadata"],
                "is_archived": values.get("is_archived", 0), "created_at": values["created_at"],
                "updated_at": values["updated_at"]}
    actual = {key: (metadata if key == "file_metadata" else row[key]) for key in expected}
    tags = [tuple(candidate) for candidate in db.execute(
        "SELECT t.name,it.source,it.added_at FROM item_tags it JOIN tags t ON t.id=it.tag_id "
        "WHERE it.item_id=? ORDER BY t.name", (values["id"],))]
    expected_tags = sorted((name, "user", values["created_at"]) for name in set(values["tags"]))
    bucket_id = values["file_metadata"].get("bucket_id") if step["domain"] == "links" else None
    expected_memberships = [] if bucket_id is None else [(bucket_id, values["created_at"])]
    return "owned" if actual == expected and tags == expected_tags and memberships == expected_memberships else "drift"


def _collection_state(knowledge, step, journal):
    if knowledge is None:
        return "drift"
    db, values = knowledge.db, step["record"]["values"]
    row = db.execute(
        "SELECT id,name,kind,query,icon,position,created_at,updated_at FROM collections WHERE id=?", (values["id"],)).fetchone()
    if row is None:
        return "absent"
    expected = {"id": values["id"], "name": values["name"], "kind": "manual", "query": "",
                "icon": values["icon"], "position": values["position"], "created_at": values["created_at"],
                "updated_at": values["updated_at"]}
    allowed = {candidate["record"]["values"]["id"] for candidate in journal["steps"]
               if candidate["domain"] == "links"
               and candidate["record"]["values"]["file_metadata"].get("bucket_id") == values["id"]}
    actual_members = {candidate[0] for candidate in db.execute(
        "SELECT item_id FROM collection_items WHERE collection_id=?", (values["id"],))}
    return "owned" if dict(row) == expected and actual_members <= allowed else "drift"


def _canonical_state(step, journal, tasks, people, knowledge, projects):
    if step["adapter"] == "person":
        return _person_state(people, step, journal)
    if step["adapter"] == "knowledge":
        return _knowledge_state(knowledge, step, journal)
    if step["adapter"] == "collection":
        return _collection_state(knowledge, step, journal)
    return (_project_state(projects, step["record"], journal) if step["adapter"] == "project"
            else _task_state(tasks, step["record"], journal))


def _apply_canonical_step(step, journal, tasks, people, knowledge, projects):
    common = (journal["archive_digest"], journal["review_token"], [step["record"]], journal["generated_at"], journal["receipt"])
    if step["adapter"] == "person":
        person_id = _coordinated_person_id(journal, step)
        body = {**step["record"]["values"], "id": person_id}
        with closing(people.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute("INSERT INTO people VALUES (?,?,1)", (person_id, json.dumps(body)))
    elif step["adapter"] == "collection":
        if knowledge is None:
            raise MigrationError("Canonical knowledge store is unavailable", 503)
        values, db = step["record"]["values"], knowledge.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM collections WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute("INSERT INTO collections (id,name,kind,query,icon,position,created_at,updated_at) "
                       "VALUES (?,?,'manual','',?,?,?,?)",
                       (values["id"], values["name"], values["icon"], values["position"],
                        values["created_at"], values["updated_at"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        from gideon.cognition.knowledge import maintenance
        maintenance.mark_dirty(reason="coordinated archive migration")
    elif step["adapter"] == "knowledge":
        if knowledge is None:
            raise MigrationError("Canonical knowledge store is unavailable", 503)
        values, db = step["record"]["values"], knowledge.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM items WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            bucket_id = values["file_metadata"].get("bucket_id") if step["domain"] == "links" else None
            if bucket_id is not None and not db.execute("SELECT 1 FROM collections WHERE id=?", (bucket_id,)).fetchone():
                raise MigrationError("Link references a missing canonical bucket", 409)
            db.execute(
                "INSERT INTO items (id,title,content,item_type,summary,status,url,word_count,provider,source_id,guid,file_metadata,is_archived,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?)",
                (values["id"], values["title"], values["content"], values["item_type"], values["summary"], values["url"],
                 len(values["content"].split()), values["provider"], values["source_id"], values["guid"],
                 json.dumps(values["file_metadata"]), values.get("is_archived", 0), values["created_at"], values["updated_at"]))
            knowledge._write_item_tags(values["id"], values["tags"], source="user", now=values["created_at"])
            rowid = db.execute("SELECT rowid FROM items WHERE id=?", (values["id"],)).fetchone()[0]
            db.execute("INSERT INTO items_fts (rowid,title,content,tags) VALUES (?,?,?,?)",
                       (rowid, values["title"], values["content"], _fts_tags(values["tags"])))
            if bucket_id is not None:
                db.execute("INSERT INTO collection_items (collection_id,item_id,added_at) VALUES (?,?,?)",
                           (bucket_id, values["id"], values["created_at"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        from gideon.cognition.knowledge import maintenance
        maintenance.mark_dirty(reason="coordinated archive migration")
    elif step["adapter"] == "project":
        _commit_project(projects, *common, require_fingerprint=True)
    else:
        _commit_task(tasks, people, knowledge, projects, *common, require_fingerprint=True)


def _compensate_canonical_steps(journal, tasks, people, knowledge, projects):
    states = [(_canonical_state(step, journal, tasks, people, knowledge, projects), step) for step in journal["steps"]]
    if any(state == "drift" for state, _ in states):
        raise MigrationError("Coordinated migration identity changed; automatic compensation refused", 409)
    for state, step in reversed(states):
        if state != "owned":
            continue
        if step["adapter"] == "person":
            identity = _coordinated_person_id(journal, step)
            expected = json.dumps({**step["record"]["values"], "id": identity})
            with closing(people.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "DELETE FROM people WHERE id=? AND revision=1 AND body=? "
                    "AND NOT EXISTS (SELECT 1 FROM touchpoints WHERE person_id=?)",
                    (identity, expected, identity))
                deleted = cursor.rowcount == 1
        elif step["adapter"] == "knowledge":
            if knowledge is None:
                raise MigrationError("Canonical migration compensation could not be completed", 409)
            values, db = step["record"]["values"], knowledge.db
            db.execute("BEGIN IMMEDIATE")
            try:
                if _knowledge_state(knowledge, step, journal) != "owned":
                    raise MigrationError("Canonical migration compensation could not be completed", 409)
                row = db.execute("SELECT rowid,title,content FROM items WHERE id=?", (values["id"],)).fetchone()
                tags = _fts_tags(values["tags"])
                db.execute("INSERT INTO items_fts (items_fts,rowid,title,content,tags) VALUES ('delete',?,?,?,?)",
                           (row["rowid"], row["title"], row["content"], tags))
                db.execute("DELETE FROM collection_items WHERE item_id=?", (values["id"],))
                db.execute("DELETE FROM item_tags WHERE item_id=?", (values["id"],))
                cursor = db.execute("DELETE FROM items WHERE id=?", (values["id"],))
                db.execute("COMMIT")
                deleted = cursor.rowcount == 1
            except Exception:
                db.execute("ROLLBACK")
                raise
            from gideon.cognition.knowledge import maintenance
            maintenance.mark_dirty(reason="coordinated archive compensation")
        elif step["adapter"] == "collection":
            if knowledge is None:
                raise MigrationError("Canonical migration compensation could not be completed", 409)
            values, db = step["record"]["values"], knowledge.db
            db.execute("BEGIN IMMEDIATE")
            try:
                current = _collection_state(knowledge, step, journal)
                remaining = db.execute(
                    "SELECT count(*) FROM collection_items WHERE collection_id=?", (values["id"],)).fetchone()[0]
                if current != "owned" or remaining:
                    raise MigrationError("Canonical migration compensation could not be completed", 409)
                cursor = db.execute("DELETE FROM collections WHERE id=?", (values["id"],))
                db.execute("COMMIT")
                deleted = cursor.rowcount == 1
            except Exception:
                db.execute("ROLLBACK")
                raise
            from gideon.cognition.knowledge import maintenance
            maintenance.mark_dirty(reason="coordinated archive compensation")
        else:
            identity = step["record"]["values"]["id"]
            deleted = (projects.delete_project(identity) if step["adapter"] == "project" else TaskMutation(tasks).delete(identity))
        if not deleted:
            raise MigrationError("Canonical migration compensation could not be completed", 409)


def _resume_canonical_records(path, journal, tasks, people, knowledge, projects):
    try:
        for step in journal["steps"]:
            state = _canonical_state(step, journal, tasks, people, knowledge, projects)
            if step["state"] == "applied":
                if state != "owned":
                    raise MigrationError("Applied canonical migration record has identity drift", 409)
                continue
            if state == "drift":
                raise MigrationError("Pending canonical migration identity is occupied", 409)
            if state == "absent":
                _apply_canonical_step(step, journal, tasks, people, knowledge, projects)
            step["state"] = "applied"
            journal["status"] = "applying"
            _write_journal(path, journal)
        journal["status"] = "complete"
        _write_journal(path, journal)
        return journal["receipt"]
    except Exception:
        _compensate_canonical_steps(journal, tasks, people, knowledge, projects)
        if path.exists():
            path.unlink()
        raise


def _recover_canonical_journals(tasks, people, knowledge, projects):
    for path in _canonical_coordinator_root(tasks).glob("*.json"):
        journal = _read_canonical_journal(path)
        if journal["status"] == "complete":
            if any(_canonical_state(step, journal, tasks, people, knowledge, projects) != "owned" for step in journal["steps"]):
                raise MigrationError("A completed canonical migration has identity drift", 409)
        else:
            _resume_canonical_records(path, journal, tasks, people, knowledge, projects)


def _commit_canonical_records(tasks, people, knowledge, projects, digest, token, records, generated):
    _recover_coordinated_journals(tasks, people, knowledge, projects)
    legacy = _coordinator_root(tasks) / (digest + ".json")
    if legacy.exists():
        journal = _read_coordinator_journal(legacy)
        if journal["review_token"] != token or journal["status"] != "complete":
            raise MigrationError("Archive has an incompatible legacy recovery journal", 409)
        return journal["receipt"], False
    ordered = _ordered_canonical_records(records)
    path = _canonical_coordinator_root(tasks) / (digest + ".json")
    if path.exists():
        journal = _read_canonical_journal(path)
        if journal["review_token"] != token:
            raise MigrationError("Archive was already imported with different content", 409)
        if journal["status"] != "complete":
            _resume_canonical_records(path, journal, tasks, people, knowledge, projects)
        return journal["receipt"], False
    names = set()
    for row in ordered:
        if row["domain"] == "people":
            identity = _coordinated_person_id({"archive_digest": digest}, _canonical_step(row))
            with closing(people.connect()) as db:
                occupied = db.execute("SELECT 1 FROM people WHERE id=?", (identity,)).fetchone()
            if occupied:
                raise MigrationError("Migration target identity already exists", 409)
        elif row["domain"] == "projects":
            identity = row["values"]["id"]
            name = row["values"]["name"].casefold()
            if name in names or (projects._projects_dir() / identity).exists() or projects.get_project_by_name(row["values"]["name"]):
                raise MigrationError("Migration target identity already exists", 409)
            names.add(name)
        elif row["domain"] == "buckets":
            if knowledge is None or knowledge.db.execute(
                    "SELECT 1 FROM collections WHERE id=?", (row["values"]["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
        elif row["domain"] in ("ideas", "journals", "memories", "links"):
            if knowledge is None or knowledge.db.execute(
                    "SELECT 1 FROM items WHERE id=?", (row["values"]["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
        else:
            identity = row["values"]["id"]
            if tasks._task_path(identity).exists():
                raise MigrationError("Migration target identity already exists", 409)
    counts = {domain: sum(row["domain"] == domain for row in ordered) for domain in sorted({row["domain"] for row in ordered})}
    receipt_records = []
    for row in ordered:
        record = {"source_id": row["source_id"], "domain": row["domain"]}
        if row["domain"] == "people":
            record.update(person_id="migration-" + hashlib.sha256(
                (digest + ":" + row["source_id"]).encode()).hexdigest()[:32], revision=1)
        elif row["domain"] == "buckets":
            record["collection_id"] = row["values"]["id"]
        elif row["domain"] in ("ideas", "journals", "memories", "links"):
            record["item_id"] = row["values"]["id"]
        else:
            record["project_id" if row["domain"] == "projects" else "task_id"] = row["values"]["id"]
        receipt_records.append(record)
    receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
               "committed_at": datetime.now(timezone.utc).isoformat(), "domains": counts, "records": receipt_records}
    journal = {"schema": 2, "kind": "canonical_records", "status": "prepared", "archive_digest": digest,
               "review_token": token, "generated_at": generated, "steps": [_canonical_step(row) for row in ordered],
               "receipt": receipt, "receipt_hash": hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()}
    _write_journal(path, journal)
    return _resume_canonical_records(path, journal, tasks, people, knowledge, projects), True


def _journal_root(repertoire):
    root = repertoire.root.parent / "platform" / "migration-journals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_journal(path, body):
    descriptor, temporary = tempfile.mkstemp(prefix=".journal-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(body, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _artifact_digest(artifacts, plan):
    artifact = artifacts.get(plan["slug"], version=1)
    if artifact is None:
        return None
    if plan["kind"] in ("text", "markdown", "svg"):
        return hashlib.sha256((artifact.content or "").encode()).hexdigest()
    raw = artifacts.raw_bytes(plan["slug"], version=1)
    return hashlib.sha256(raw[0]).hexdigest() if raw else None


def _read_song_journal(path):
    try:
        journal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise MigrationError("A song migration recovery journal is unreadable", 409) from None
    common = {"schema", "status", "archive_digest", "review_token", "import_id", "song_id", "artifacts"}
    expected = common | ({"receipt"} if journal.get("status") == "complete" else set())
    if (not isinstance(journal, dict) or set(journal) != expected or journal.get("schema") != 1
            or journal.get("status") not in ("prepared", "artifacts_ready", "complete")
            or not SHA256.fullmatch(str(journal.get("archive_digest", "")))
            or not SHA256.fullmatch(str(journal.get("review_token", "")))
            or not isinstance(journal.get("import_id"), str) or not isinstance(journal.get("song_id"), str)
            or not isinstance(journal.get("artifacts"), list)):
        raise MigrationError("A song migration recovery journal has an unsupported shape", 409)
    for plan in journal["artifacts"]:
        if (not isinstance(plan, dict) or not isinstance(plan.get("slug"), str)
                or not SHA256.fullmatch(str(plan.get("sha256", ""))) or type(plan.get("owned")) is not bool):
            raise MigrationError("A song migration recovery journal has an unsupported shape", 409)
    if journal["status"] == "complete" and not isinstance(journal["receipt"], dict):
        raise MigrationError("A song migration recovery journal has an unsupported shape", 409)
    return journal


def _recover_song_journals(repertoire, artifacts):
    root = _journal_root(repertoire)
    for path in root.glob("*.json"):
        journal = _read_song_journal(path)
        if journal.get("status") == "complete":
            continue
        try:
            repertoire.rollback_import(journal["import_id"], journal["review_token"])
        except DomainError as exc:
            if exc.code != "import_not_found":
                raise MigrationError("Interrupted song import changed and cannot be rolled back automatically", 409) from exc
        for plan in journal["artifacts"]:
            if not plan.get("owned"):
                continue
            observed = _artifact_digest(artifacts, plan)
            if observed is None:
                continue
            if observed != plan["sha256"] or not artifacts.delete(plan["slug"]):
                raise MigrationError("Interrupted song attachment cannot be rolled back safely", 409)
        path.unlink()


def _materialize_attachment(artifacts, plan, archive_digest):
    existing = artifacts.get(plan["slug"], version=1)
    if existing is not None:
        if _artifact_digest(artifacts, plan) != plan["sha256"]:
            raise MigrationError("Canonical attachment slug already contains different bytes", 409)
        return
    common = {"name": plan["label"] or plan["filename"], "slug": plan["slug"], "source": "import",
              "description": "Restored song attachment: " + plan["filename"], "tags": ["archive-migration", "song-attachment"],
              "event_metadata": {"archive_digest": archive_digest, "source_filename": plan["filename"]}}
    if plan["kind"] in ("text", "markdown", "svg"):
        artifact = artifacts.create(content=plan["bytes"].decode("utf-8"), kind=plan["kind"], **common)
    else:
        artifact = artifacts.create_binary(data=plan["bytes"], mime=plan["mime"], kind=plan["kind"], **common)
    if artifact.slug != plan["slug"] or artifact.version != 1 or _artifact_digest(artifacts, plan) != plan["sha256"]:
        artifacts.delete(artifact.slug)
        raise MigrationError("Canonical attachment publication did not preserve its identity or checksum", 409)


def _commit_song(repertoire, artifacts, digest, token, records, generated):
    if len(records) != 1:
        raise MigrationError("Song archives must contain exactly one song for recoverable publication")
    _recover_song_journals(repertoire, artifacts)
    journal_path = _journal_root(repertoire) / (digest + ".json")
    if journal_path.exists():
        prior = _read_song_journal(journal_path)
        if prior.get("status") != "complete" or prior.get("review_token") != token:
            raise MigrationError("Archive was already imported with different content", 409)
        return prior["receipt"], False
    row, values = records[0], records[0]["values"]
    plans = []
    for attachment in values["attachments"]:
        plan = {key: value for key, value in attachment.items() if key != "bytes"}
        existing = artifacts.get(plan["slug"], version=1)
        if existing is not None and _artifact_digest(artifacts, plan) != plan["sha256"]:
            raise MigrationError("Canonical attachment slug already contains different bytes", 409)
        plan["owned"] = existing is None
        plans.append(plan)
    import_id = "platform-song-" + digest
    journal = {"schema": 1, "status": "prepared", "archive_digest": digest, "review_token": token,
               "import_id": import_id, "song_id": values["id"], "artifacts": plans}
    _write_journal(journal_path, journal)
    imported = False
    try:
        for attachment in values["attachments"]:
            _materialize_attachment(artifacts, attachment, digest)
        journal["status"] = "artifacts_ready"
        _write_journal(journal_path, journal)
        practice = values["practice"]
        schedule = {"stage": values["stage"], "ease": practice["ease"] if practice else 2.5,
                    "interval": practice["intervalDays"] if practice else 0, "repetitions": practice["sessions"] if practice else 0,
                    "due_at": practice["nextReview"] if practice else None, "last_practiced_at": practice["lastReviewed"] if practice else None,
                    "last_grade": practice["lastQuality"] if practice else None}
        try:
            result = repertoire.import_song(import_id=import_id, source_fingerprint=token, item_id=values["id"],
                created_at=values["created_at"], updated_at=values["updated_at"], schedule=schedule,
                data={"title": values["title"], "artist": values["artist"], "instrument": values["instrument"], "body": values["notes"],
                      "tags": values["tags"], "key": values["key"], "capo": values["capo"], "tuning": values["tuning"],
                      "notation": values["notation"], "source_url": values["source_url"], "links": values["links"],
                      "scroll_duration_seconds": values["scroll_duration_seconds"],
                      "attachment_refs": [{"slug": plan["slug"], "version": 1} for plan in plans]})
        except DomainError as exc:
            raise MigrationError(str(exc), exc.status) from exc
        imported = True
        receipt = {"archive_digest": digest, "format": FORMAT, "generated_at": generated,
                   "committed_at": datetime.now(timezone.utc).isoformat(), "domains": {"songs": 1},
                   "records": [{"source_id": row["source_id"], "song_id": values["id"], "domain": "songs",
                                "attachment_refs": result["item"]["attachment_refs"]}]}
        journal.update(status="complete", receipt=receipt)
        _write_journal(journal_path, journal)
        return receipt, True
    except Exception:
        if imported:
            repertoire.rollback_import(import_id, token)
        for plan in plans:
            if plan["owned"] and _artifact_digest(artifacts, plan) == plan["sha256"]:
                artifacts.delete(plan["slug"])
        if journal_path.exists():
            journal_path.unlink()
        raise


def receipts(store: PeopleStore, knowledge: KnowledgeStore | None = None, projects: BoundHierarchy | None = None,
             tasks: BoundTasks | None = None, repertoire: RepertoireStore | None = None,
             artifacts: NativeArtifactProvider | None = None):
    if tasks is not None and projects is not None:
        _recover_coordinated_journals(tasks, store, knowledge, projects)
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
    if repertoire is not None and artifacts is not None:
        _recover_song_journals(repertoire, artifacts)
        for path in _journal_root(repertoire).glob("*.json"):
            try:
                journal = _read_song_journal(path)
                if journal.get("status") == "complete":
                    result.append(journal["receipt"])
            except KeyError:
                raise MigrationError("A song migration recovery journal has an unsupported shape", 409) from None
    ordered = sorted(result, key=lambda row: row["committed_at"], reverse=True)
    unique = {}
    for row in ordered:
        unique.setdefault(row["archive_digest"], row)
    return list(unique.values())[:20]
