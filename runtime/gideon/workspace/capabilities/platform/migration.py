from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
from contextlib import closing
from datetime import datetime, timezone

from gideon.cognition.knowledge.store import KnowledgeStore, _fts_tags, normalize_url
from gideon.workspace.capabilities.communications.store import PeopleStore, person_values

FORMAT = "portos_snapshot_v1"
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
    allowed = re.compile(r"^brain/(?:people|memories|links)/(?:index\.json|[A-Za-z0-9_-]{1,128}/index\.json)$")
    unsupported = sorted(name for name in data_files if not allowed.fullmatch(name))
    if unsupported:
        domains = sorted({name.split("/", 1)[0] for name in unsupported})
        raise MigrationError(f"Archive contains unsupported domains: {', '.join(domains)}")
    domains = [domain for domain in ("people", "memories", "links") if f"brain/{domain}/index.json" in data_files]
    if not domains:
        raise MigrationError("Archive contains no supported collection index")
    if "people" in domains and len(domains) > 1:
        raise MigrationError("People and knowledge domains require separate atomic imports")
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
        if not UUID.fullmatch(record_id):
            raise MigrationError(f"{domain.title()} record identity must be a UUID")
        common = {"id", "title", "tags", "createdAt", "updatedAt", "originInstanceId"}
        allowed_fields = common | ({"content", "mood", "source", "sourceRef", "sourceCreatedAt", "sourceUpdatedAt"} if domain == "memories" else
                                   {"url", "description", "note", "linkType", "isRepo", "repoHost", "repoOwner", "repoName", "isGitHubRepo", "gitHubOwner", "gitHubRepo", "localPath", "cloneStatus", "cloneError", "cloneInstanceId", "cloneInterrupted", "malwareScan", "repoIntake", "repoStudy", "bucketId", "bucketOrder"})
        if set(record) - allowed_fields:
            raise MigrationError(f"{domain.title()} record contains unsupported fields")
        title = record.get("title")
        content = record.get("content", "") if domain == "memories" else "\n".join(filter(None, [record.get("description", ""), record.get("note", "")]))
        url = record.get("url", "") if domain == "links" else ""
        tags = record.get("tags", [])
        if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(content, str) or len(content) > 10000:
            raise MigrationError(f"{domain.title()} record text is invalid")
        if not isinstance(tags, list) or len(tags) > 100 or not all(isinstance(v, str) and 0 < len(v) <= 50 for v in tags):
            raise MigrationError(f"{domain.title()} record tags are invalid")
        if domain == "links" and (not isinstance(url, str) or not re.fullmatch(r"https?://[^\s]+", url)):
            raise MigrationError("Links record URL is invalid")
        created = record.get("sourceCreatedAt") or record.get("createdAt")
        updated = record.get("sourceUpdatedAt") or record.get("updatedAt") or created
        for value in (created, updated):
            try:
                if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
                    raise ValueError()
            except (ValueError, OverflowError):
                raise MigrationError(f"{domain.title()} record timestamps are invalid") from None
        metadata = {"migration_format": FORMAT, "source_collection": domain, "source_record_id": record_id,
                    "source": record.get("source"), "source_ref": record.get("sourceRef"), "mood": record.get("mood"),
                    "link_type": record.get("linkType"), "repository": {key: record.get(key) for key in ("isRepo", "repoHost", "repoOwner", "repoName") if key in record}}
        records.append({"domain": domain, "source_id": record_id, "values": {"id": record_id, "title": title.strip(), "content": content,
                        "item_type": "note" if domain == "memories" else "bookmark", "summary": record.get("description", ""), "url": normalize_url(url) if url else "",
                        "tags": tags, "provider": "legacy-migration", "source_id": "legacy-archive", "guid": f"{domain}:{record_id}",
                        "file_metadata": metadata, "created_at": created, "updated_at": updated}})
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


def commit(store: PeopleStore, data, knowledge: KnowledgeStore | None = None):
    if not isinstance(data, dict) or set(data) != {"format", "content", "archive_digest", "review_token"}:
        raise MigrationError("Commit requires the reviewed archive, digest and review token")
    digest, token, records, generated, domains = _inspect({"format": data["format"], "content": data["content"]})
    if data["archive_digest"] != digest or data["review_token"] != token:
        raise MigrationError("Archive changed since preview", 409)
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
        for row in records:
            values = row["values"]
            if db.execute("SELECT 1 FROM items WHERE id=?", (values["id"],)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute("INSERT INTO items (id,title,content,item_type,summary,status,url,word_count,provider,source_id,guid,file_metadata,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?,?,?,?,?,?,?)",
                       (values["id"], values["title"], values["content"], values["item_type"], values["summary"], values["url"],
                        len(values["content"].split()), values["provider"], values["source_id"], values["guid"], json.dumps(values["file_metadata"]), values["created_at"], values["updated_at"]))
            store._write_item_tags(values["id"], values["tags"], source="user", now=values["created_at"])
            rowid = db.execute("SELECT rowid FROM items WHERE id=?", (values["id"],)).fetchone()[0]
            db.execute("INSERT INTO items_fts (rowid,title,content,tags) VALUES (?,?,?,?)", (rowid, values["title"], values["content"], _fts_tags(values["tags"])))
            imported.append({"source_id": row["source_id"], "item_id": values["id"], "domain": row["domain"]})
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


def receipts(store: PeopleStore, knowledge: KnowledgeStore | None = None):
    with closing(store.connect()) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'").fetchone()
        result = [] if not exists else [json.loads(row[0]) for row in db.execute("SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20")]
    if knowledge is not None and knowledge.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'").fetchone():
        result += [json.loads(row[0]) for row in knowledge.db.execute("SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20")]
    return sorted(result, key=lambda row: row["committed_at"], reverse=True)[:20]
