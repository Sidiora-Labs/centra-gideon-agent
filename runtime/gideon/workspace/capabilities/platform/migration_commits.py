"""Canonical record and song migration commits, recovery and receipt inspection."""

from __future__ import annotations

from typing import Any

from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.platform.migration import (
    BoundHierarchy as BoundHierarchy,
)
from gideon.workspace.capabilities.platform.migration import BoundTasks as BoundTasks
from gideon.workspace.capabilities.platform.migration import (
    KnowledgeStore as KnowledgeStore,
)
from gideon.workspace.capabilities.platform.migration import (
    NativeArtifactProvider as NativeArtifactProvider,
)
from gideon.workspace.capabilities.platform.migration import PeopleStore as PeopleStore
from gideon.workspace.capabilities.platform.migration import (
    RepertoireStore as RepertoireStore,
)


def _collection_state(knowledge, step, journal):
    if knowledge is None:
        return "drift"
    db, values = knowledge.db, step["record"]["values"]
    row = db.execute(
        "SELECT id,name,kind,query,icon,position,created_at,updated_at FROM collections WHERE id=?",
        (values["id"],),
    ).fetchone()
    if row is None:
        return "absent"
    expected = {
        "id": values["id"],
        "name": values["name"],
        "kind": "manual",
        "query": "",
        "icon": values["icon"],
        "position": values["position"],
        "created_at": values["created_at"],
        "updated_at": values["updated_at"],
    }
    allowed = {
        candidate["record"]["values"]["id"]
        for candidate in journal["steps"]
        if candidate["domain"] == "links"
        and candidate["record"]["values"]["file_metadata"].get("bucket_id")
        == values["id"]
    }
    actual_members = {
        candidate[0]
        for candidate in db.execute(
            "SELECT item_id FROM collection_items WHERE collection_id=?",
            (values["id"],),
        )
    }
    return "owned" if dict(row) == expected and actual_members <= allowed else "drift"


def _canonical_state(step, journal, tasks, people, knowledge, projects):
    from gideon.workspace.capabilities.platform.migration import (
        _knowledge_state,
        _person_state,
        _project_state,
        _task_state,
    )

    if step["adapter"] == "person":
        return _person_state(people, step, journal)
    if step["adapter"] == "knowledge":
        return _knowledge_state(knowledge, step, journal)
    if step["adapter"] == "collection":
        return _collection_state(knowledge, step, journal)
    return (
        _project_state(projects, step["record"], journal)
        if step["adapter"] == "project"
        else _task_state(tasks, step["record"], journal)
    )


def _apply_canonical_step(step, journal, tasks, people, knowledge, projects):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        _commit_project,
        _commit_task,
        _coordinated_person_id,
        _fts_tags,
        closing,
        json,
    )

    common = (
        journal["archive_digest"],
        journal["review_token"],
        [step["record"]],
        journal["generated_at"],
        journal["receipt"],
    )
    if step["adapter"] == "person":
        person_id = _coordinated_person_id(journal, step)
        body = {**step["record"]["values"], "id": person_id}
        with closing(people.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute(
                "INSERT INTO people VALUES (?,?,1)", (person_id, json.dumps(body))
            )
    elif step["adapter"] == "collection":
        if knowledge is None:
            raise MigrationError("Canonical knowledge store is unavailable", 503)
        values, db = step["record"]["values"], knowledge.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute(
                "SELECT 1 FROM collections WHERE id=?", (values["id"],)
            ).fetchone():
                raise MigrationError("Migration target identity already exists", 409)
            db.execute(
                "INSERT INTO collections (id,name,kind,query,icon,position,created_at,updated_at) "
                "VALUES (?,?,'manual','',?,?,?,?)",
                (
                    values["id"],
                    values["name"],
                    values["icon"],
                    values["position"],
                    values["created_at"],
                    values["updated_at"],
                ),
            )
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
            bucket_id = (
                values["file_metadata"].get("bucket_id")
                if step["domain"] == "links"
                else None
            )
            if (
                bucket_id is not None
                and not db.execute(
                    "SELECT 1 FROM collections WHERE id=?", (bucket_id,)
                ).fetchone()
            ):
                raise MigrationError("Link references a missing canonical bucket", 409)
            db.execute(
                "INSERT INTO items (id,title,content,item_type,summary,status,url,word_count,provider,source_id,guid,file_metadata,is_archived,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?)",
                (
                    values["id"],
                    values["title"],
                    values["content"],
                    values["item_type"],
                    values["summary"],
                    values["url"],
                    len(values["content"].split()),
                    values["provider"],
                    values["source_id"],
                    values["guid"],
                    json.dumps(values["file_metadata"]),
                    values.get("is_archived", 0),
                    values["created_at"],
                    values["updated_at"],
                ),
            )
            knowledge._write_item_tags(
                values["id"], values["tags"], source="user", now=values["created_at"]
            )
            rowid = db.execute(
                "SELECT rowid FROM items WHERE id=?", (values["id"],)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO items_fts (rowid,title,content,tags) VALUES (?,?,?,?)",
                (rowid, values["title"], values["content"], _fts_tags(values["tags"])),
            )
            if bucket_id is not None:
                db.execute(
                    "INSERT INTO collection_items (collection_id,item_id,added_at) VALUES (?,?,?)",
                    (bucket_id, values["id"], values["created_at"]),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        from gideon.cognition.knowledge import maintenance

        maintenance.mark_dirty(reason="coordinated archive migration")
    elif step["adapter"] == "project":
        _commit_project(projects, *common, require_fingerprint=True)
    else:
        _commit_task(
            tasks, people, knowledge, projects, *common, require_fingerprint=True
        )


def _compensate_canonical_steps(journal, tasks, people, knowledge, projects):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        TaskMutation,
        _coordinated_person_id,
        _fts_tags,
        _knowledge_state,
        closing,
        json,
    )

    states = [
        (_canonical_state(step, journal, tasks, people, knowledge, projects), step)
        for step in journal["steps"]
    ]
    if any(state == "drift" for state, _ in states):
        raise MigrationError(
            "Coordinated migration identity changed; automatic compensation refused",
            409,
        )
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
                    (identity, expected, identity),
                )
                deleted = cursor.rowcount == 1
        elif step["adapter"] == "knowledge":
            if knowledge is None:
                raise MigrationError(
                    "Canonical migration compensation could not be completed", 409
                )
            values, db = step["record"]["values"], knowledge.db
            db.execute("BEGIN IMMEDIATE")
            try:
                if _knowledge_state(knowledge, step, journal) != "owned":
                    raise MigrationError(
                        "Canonical migration compensation could not be completed", 409
                    )
                row = db.execute(
                    "SELECT rowid,title,content FROM items WHERE id=?", (values["id"],)
                ).fetchone()
                tags = _fts_tags(values["tags"])
                db.execute(
                    "INSERT INTO items_fts (items_fts,rowid,title,content,tags) VALUES ('delete',?,?,?,?)",
                    (row["rowid"], row["title"], row["content"], tags),
                )
                db.execute(
                    "DELETE FROM collection_items WHERE item_id=?", (values["id"],)
                )
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
                raise MigrationError(
                    "Canonical migration compensation could not be completed", 409
                )
            values, db = step["record"]["values"], knowledge.db
            db.execute("BEGIN IMMEDIATE")
            try:
                current = _collection_state(knowledge, step, journal)
                remaining = db.execute(
                    "SELECT count(*) FROM collection_items WHERE collection_id=?",
                    (values["id"],),
                ).fetchone()[0]
                if current != "owned" or remaining:
                    raise MigrationError(
                        "Canonical migration compensation could not be completed", 409
                    )
                cursor = db.execute(
                    "DELETE FROM collections WHERE id=?", (values["id"],)
                )
                db.execute("COMMIT")
                deleted = cursor.rowcount == 1
            except Exception:
                db.execute("ROLLBACK")
                raise
            from gideon.cognition.knowledge import maintenance

            maintenance.mark_dirty(reason="coordinated archive compensation")
        else:
            identity = step["record"]["values"]["id"]
            deleted = (
                projects.delete_project(identity)
                if step["adapter"] == "project"
                else TaskMutation(tasks).delete(identity)
            )
        if not deleted:
            raise MigrationError(
                "Canonical migration compensation could not be completed", 409
            )


def _resume_canonical_records(path, journal, tasks, people, knowledge, projects):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
    )

    try:
        for step in journal["steps"]:
            state = _canonical_state(step, journal, tasks, people, knowledge, projects)
            if step["state"] == "applied":
                if state != "owned":
                    raise MigrationError(
                        "Applied canonical migration record has identity drift", 409
                    )
                continue
            if state == "drift":
                raise MigrationError(
                    "Pending canonical migration identity is occupied", 409
                )
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
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        _canonical_coordinator_root,
        _read_canonical_journal,
    )

    for path in _canonical_coordinator_root(tasks).glob("*.json"):
        journal = _read_canonical_journal(path)
        if journal["status"] == "complete":
            if any(
                _canonical_state(step, journal, tasks, people, knowledge, projects)
                != "owned"
                for step in journal["steps"]
            ):
                raise MigrationError(
                    "A completed canonical migration has identity drift", 409
                )
        else:
            _resume_canonical_records(path, journal, tasks, people, knowledge, projects)


def _commit_canonical_records(
    tasks, people, knowledge, projects, digest, token, records, generated
):
    from gideon.workspace.capabilities.platform.migration import (
        FORMAT,
        MigrationError,
        _canonical_coordinator_root,
        _canonical_step,
        _coordinated_person_id,
        _coordinator_root,
        _ordered_canonical_records,
        _read_canonical_journal,
        _read_coordinator_journal,
        _recover_coordinated_journals,
        closing,
        datetime,
        hashlib,
        json,
        timezone,
    )

    _recover_coordinated_journals(tasks, people, knowledge, projects)
    legacy = _coordinator_root(tasks) / (digest + ".json")
    if legacy.exists():
        journal = _read_coordinator_journal(legacy)
        if journal["review_token"] != token or journal["status"] != "complete":
            raise MigrationError(
                "Archive has an incompatible legacy recovery journal", 409
            )
        return journal["receipt"], False
    ordered = _ordered_canonical_records(records)
    path = _canonical_coordinator_root(tasks) / (digest + ".json")
    if path.exists():
        journal = _read_canonical_journal(path)
        if journal["review_token"] != token:
            raise MigrationError(
                "Archive was already imported with different content", 409
            )
        if journal["status"] != "complete":
            _resume_canonical_records(path, journal, tasks, people, knowledge, projects)
        return journal["receipt"], False
    names = set()
    for row in ordered:
        if row["domain"] == "people":
            identity = _coordinated_person_id(
                {"archive_digest": digest}, _canonical_step(row)
            )
            with closing(people.connect()) as db:
                occupied = db.execute(
                    "SELECT 1 FROM people WHERE id=?", (identity,)
                ).fetchone()
            if occupied:
                raise MigrationError("Migration target identity already exists", 409)
        elif row["domain"] == "projects":
            identity = row["values"]["id"]
            name = row["values"]["name"].casefold()
            if (
                name in names
                or (projects._projects_dir() / identity).exists()
                or projects.get_project_by_name(row["values"]["name"])
            ):
                raise MigrationError("Migration target identity already exists", 409)
            names.add(name)
        elif row["domain"] == "buckets":
            if (
                knowledge is None
                or knowledge.db.execute(
                    "SELECT 1 FROM collections WHERE id=?", (row["values"]["id"],)
                ).fetchone()
            ):
                raise MigrationError("Migration target identity already exists", 409)
        elif row["domain"] in ("ideas", "journals", "memories", "links"):
            if (
                knowledge is None
                or knowledge.db.execute(
                    "SELECT 1 FROM items WHERE id=?", (row["values"]["id"],)
                ).fetchone()
            ):
                raise MigrationError("Migration target identity already exists", 409)
        else:
            identity = row["values"]["id"]
            if tasks._task_path(identity).exists():
                raise MigrationError("Migration target identity already exists", 409)
    counts = {
        domain: sum(row["domain"] == domain for row in ordered)
        for domain in sorted({row["domain"] for row in ordered})
    }
    receipt_records = []
    for row in ordered:
        record = {"source_id": row["source_id"], "domain": row["domain"]}
        if row["domain"] == "people":
            record.update(
                person_id="migration-"
                + hashlib.sha256(
                    (digest + ":" + row["source_id"]).encode()
                ).hexdigest()[:32],
                revision=1,
            )
        elif row["domain"] == "buckets":
            record["collection_id"] = row["values"]["id"]
        elif row["domain"] in ("ideas", "journals", "memories", "links"):
            record["item_id"] = row["values"]["id"]
        else:
            record["project_id" if row["domain"] == "projects" else "task_id"] = row[
                "values"
            ]["id"]
        receipt_records.append(record)
    receipt = {
        "archive_digest": digest,
        "format": FORMAT,
        "generated_at": generated,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "domains": counts,
        "records": receipt_records,
    }
    journal = {
        "schema": 2,
        "kind": "canonical_records",
        "status": "prepared",
        "archive_digest": digest,
        "review_token": token,
        "generated_at": generated,
        "steps": [_canonical_step(row) for row in ordered],
        "receipt": receipt,
        "receipt_hash": hashlib.sha256(
            json.dumps(receipt, sort_keys=True).encode()
        ).hexdigest(),
    }
    _write_journal(path, journal)
    return (
        _resume_canonical_records(path, journal, tasks, people, knowledge, projects),
        True,
    )


def _journal_root(repertoire):
    root = repertoire.root.parent / "platform" / "migration-journals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_journal(path, body):
    from gideon.core.atomic_write import atomic_write
    from gideon.workspace.capabilities.platform.migration import (
        json,
        os,
    )

    atomic_write(path, json.dumps(body, sort_keys=True), fsync=True, mode=0o600)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _artifact_digest(artifacts, plan):
    from gideon.workspace.capabilities.platform.migration import (
        hashlib,
    )

    artifact = artifacts.get(plan["slug"], version=1)
    if artifact is None:
        return None
    if plan["kind"] in ("text", "markdown", "svg"):
        return hashlib.sha256((artifact.content or "").encode()).hexdigest()
    raw = artifacts.raw_bytes(plan["slug"], version=1)
    return hashlib.sha256(raw[0]).hexdigest() if raw else None


def _read_song_journal(path):
    from gideon.workspace.capabilities.platform.migration import (
        SHA256,
        MigrationError,
        json,
    )

    try:
        journal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise MigrationError(
            "A song migration recovery journal is unreadable", 409
        ) from None
    common = {
        "schema",
        "status",
        "archive_digest",
        "review_token",
        "import_id",
        "song_id",
        "artifacts",
    }
    expected = common | ({"receipt"} if journal.get("status") == "complete" else set())
    if (
        not isinstance(journal, dict)
        or set(journal) != expected
        or journal.get("schema") != 1
        or journal.get("status") not in ("prepared", "artifacts_ready", "complete")
        or not SHA256.fullmatch(str(journal.get("archive_digest", "")))
        or not SHA256.fullmatch(str(journal.get("review_token", "")))
        or not isinstance(journal.get("import_id"), str)
        or not isinstance(journal.get("song_id"), str)
        or not isinstance(journal.get("artifacts"), list)
    ):
        raise MigrationError(
            "A song migration recovery journal has an unsupported shape", 409
        )
    for plan in journal["artifacts"]:
        if (
            not isinstance(plan, dict)
            or not isinstance(plan.get("slug"), str)
            or not SHA256.fullmatch(str(plan.get("sha256", "")))
            or type(plan.get("owned")) is not bool
        ):
            raise MigrationError(
                "A song migration recovery journal has an unsupported shape", 409
            )
        fingerprint = plan.get("ownership_fingerprint")
        if (
            journal["status"] == "artifacts_ready"
            and plan["owned"]
            and not SHA256.fullmatch(str(fingerprint or ""))
        ):
            raise MigrationError(
                "A song migration recovery journal has ambiguous artifact ownership",
                409,
            )
        if fingerprint is not None and not SHA256.fullmatch(str(fingerprint)):
            raise MigrationError(
                "A song migration recovery journal has an unsupported shape", 409
            )
    if journal["status"] == "complete" and not isinstance(journal["receipt"], dict):
        raise MigrationError(
            "A song migration recovery journal has an unsupported shape", 409
        )
    return journal


def _recover_song_journals(repertoire, artifacts):
    root = _journal_root(repertoire)
    for path in root.glob("*.json"):
        journal = _read_song_journal(path)
        if journal.get("status") == "complete":
            continue
        _rollback_song_state(
            repertoire,
            artifacts,
            journal["import_id"],
            journal["review_token"],
            journal["artifacts"],
        )
        path.unlink()


def _rollback_song_state(repertoire, artifacts, import_id, token, plans):
    """Preflight every owned artifact, then retain that exact state lock through rollback."""
    from gideon.workspace.capabilities.platform.migration import SHA256, MigrationError

    with artifacts.mutation_lock:
        for plan in plans:
            if not plan.get("owned"):
                continue
            expected = plan.get("ownership_fingerprint")
            try:
                observed = artifacts.state_fingerprint(plan["slug"])
            except (OSError, ValueError) as exc:
                raise MigrationError(
                    "Interrupted song attachment cannot be rolled back safely", 409
                ) from exc
            if expected is None and observed is None:
                continue
            if not SHA256.fullmatch(str(expected or "")) or observed != expected:
                raise MigrationError(
                    "Interrupted song attachment cannot be rolled back safely", 409
                )
        try:
            repertoire.rollback_import(import_id, token)
        except DomainError as exc:
            if exc.code != "import_not_found":
                raise MigrationError(
                    "Interrupted song import changed and cannot be rolled back automatically",
                    409,
                ) from exc
        for plan in plans:
            expected = plan.get("ownership_fingerprint")
            if (
                plan.get("owned")
                and expected is not None
                and not artifacts.delete_if_state(plan["slug"], expected)
            ):
                raise MigrationError(
                    "Interrupted song attachment cannot be rolled back safely", 409
                )


def _materialize_attachment(artifacts, plan, archive_digest):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
    )

    existing = artifacts.get(plan["slug"], version=1)
    if existing is not None:
        if _artifact_digest(artifacts, plan) != plan["sha256"]:
            raise MigrationError(
                "Canonical attachment slug already contains different bytes", 409
            )
        return
    common = {
        "name": plan["label"] or plan["filename"],
        "slug": plan["slug"],
        "source": "import",
        "description": "Restored song attachment: " + plan["filename"],
        "tags": ["archive-migration", "song-attachment"],
        "event_metadata": {
            "archive_digest": archive_digest,
            "source_filename": plan["filename"],
        },
    }
    if plan["kind"] in ("text", "markdown", "svg"):
        artifact = artifacts.create(
            content=plan["bytes"].decode("utf-8"), kind=plan["kind"], **common
        )
    else:
        artifact = artifacts.create_binary(
            data=plan["bytes"], mime=plan["mime"], kind=plan["kind"], **common
        )
    if (
        artifact.slug != plan["slug"]
        or artifact.version != 1
        or _artifact_digest(artifacts, plan) != plan["sha256"]
    ):
        artifacts.delete(artifact.slug)
        raise MigrationError(
            "Canonical attachment publication did not preserve its identity or checksum",
            409,
        )


def _commit_song(repertoire, artifacts, digest, token, records, generated):
    from gideon.workspace.capabilities.platform.migration import (
        FORMAT,
        SHA256,
        MigrationError,
        datetime,
        timezone,
    )

    if len(records) != 1:
        raise MigrationError(
            "Song archives must contain exactly one song for recoverable publication"
        )
    _recover_song_journals(repertoire, artifacts)
    journal_path = _journal_root(repertoire) / (digest + ".json")
    if journal_path.exists():
        prior = _read_song_journal(journal_path)
        if prior.get("status") != "complete" or prior.get("review_token") != token:
            raise MigrationError(
                "Archive was already imported with different content", 409
            )
        return prior["receipt"], False
    row, values = records[0], records[0]["values"]
    plans = []
    for attachment in values["attachments"]:
        plan = {key: value for key, value in attachment.items() if key != "bytes"}
        existing = artifacts.get(plan["slug"], version=1)
        if existing is not None and _artifact_digest(artifacts, plan) != plan["sha256"]:
            raise MigrationError(
                "Canonical attachment slug already contains different bytes", 409
            )
        plan["owned"] = existing is None
        plan["ownership_fingerprint"] = (
            artifacts.state_fingerprint(plan["slug"]) if existing is not None else None
        )
        plans.append(plan)
    import_id = "platform-song-" + digest
    journal = {
        "schema": 1,
        "status": "prepared",
        "archive_digest": digest,
        "review_token": token,
        "import_id": import_id,
        "song_id": values["id"],
        "artifacts": plans,
    }
    _write_journal(journal_path, journal)
    try:
        for attachment, plan in zip(values["attachments"], plans, strict=True):
            _materialize_attachment(artifacts, attachment, digest)
            if plan["owned"]:
                plan["ownership_fingerprint"] = artifacts.state_fingerprint(
                    plan["slug"]
                )
                if not SHA256.fullmatch(str(plan["ownership_fingerprint"] or "")):
                    raise MigrationError(
                        "Canonical attachment ownership could not be recorded", 409
                    )
        journal["status"] = "artifacts_ready"
        _write_journal(journal_path, journal)
        practice = values["practice"]
        schedule = {
            "stage": values["stage"],
            "ease": practice["ease"] if practice else 2.5,
            "interval": practice["intervalDays"] if practice else 0,
            "repetitions": practice["sessions"] if practice else 0,
            "due_at": practice["nextReview"] if practice else None,
            "last_practiced_at": practice["lastReviewed"] if practice else None,
            "last_grade": practice["lastQuality"] if practice else None,
        }
        try:
            result = repertoire.import_song(
                import_id=import_id,
                source_fingerprint=token,
                item_id=values["id"],
                created_at=values["created_at"],
                updated_at=values["updated_at"],
                schedule=schedule,
                data={
                    "title": values["title"],
                    "artist": values["artist"],
                    "instrument": values["instrument"],
                    "body": values["notes"],
                    "tags": values["tags"],
                    "key": values["key"],
                    "capo": values["capo"],
                    "tuning": values["tuning"],
                    "notation": values["notation"],
                    "source_url": values["source_url"],
                    "links": values["links"],
                    "scroll_duration_seconds": values["scroll_duration_seconds"],
                    "attachment_refs": [
                        {"slug": plan["slug"], "version": 1} for plan in plans
                    ],
                },
            )
        except DomainError as exc:
            raise MigrationError(str(exc), exc.status) from exc
        receipt = {
            "archive_digest": digest,
            "format": FORMAT,
            "generated_at": generated,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "domains": {"songs": 1},
            "records": [
                {
                    "source_id": row["source_id"],
                    "song_id": values["id"],
                    "domain": "songs",
                    "attachment_refs": result["item"]["attachment_refs"],
                }
            ],
        }
        journal.update(status="complete", receipt=receipt)
        _write_journal(journal_path, journal)
        return receipt, True
    except Exception:
        _rollback_song_state(repertoire, artifacts, import_id, token, plans)
        journal_path.unlink(missing_ok=True)
        raise


def _grouped_root(repertoire):
    root = _journal_root(repertoire) / "grouped"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_grouped_journal(path):
    from gideon.workspace.capabilities.platform.migration import (
        SHA256,
        MigrationError,
        hashlib,
        json,
    )

    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise MigrationError("A grouped migration journal exceeds 2 MiB", 409)
        journal = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise MigrationError("A grouped migration journal is unreadable", 409) from None
    if (
        not isinstance(journal, dict)
        or set(journal)
        != {
            "schema",
            "kind",
            "archive_digest",
            "review_token",
            "generated_at",
            "plan_hash",
            "groups",
            "receipt",
            "receipt_hash",
        }
        or journal.get("schema") != 1
        or journal.get("kind") != "independent_groups"
        or not SHA256.fullmatch(str(journal.get("archive_digest", "")))
        or not SHA256.fullmatch(str(journal.get("review_token", "")))
        or not SHA256.fullmatch(str(journal.get("plan_hash", "")))
        or not isinstance(journal.get("groups"), list)
        or (journal["receipt"] is None) != (journal["receipt_hash"] is None)
        or journal["receipt"] is not None
        and (
            not isinstance(journal["receipt"], dict)
            or not SHA256.fullmatch(str(journal["receipt_hash"]))
            or hashlib.sha256(
                json.dumps(journal["receipt"], sort_keys=True).encode()
            ).hexdigest()
            != journal["receipt_hash"]
        )
    ):
        raise MigrationError(
            "A grouped migration journal has an unsupported shape", 409
        )
    group_ids = [group.get("id") for group in journal["groups"]]
    expected_order = [
        name for name in ("canonical", "inbox", "song") if name in group_ids
    ]
    if (
        group_ids != expected_order
        or len(group_ids) < 2
        or not set(group_ids) & {"inbox", "song"}
        or len(group_ids) != len(set(group_ids))
    ):
        raise MigrationError(
            "A grouped migration journal has an unsupported group order", 409
        )
    for group in journal["groups"]:
        if (
            set(group) != {"id", "state", "receipt", "error"}
            or group["state"] not in ("pending", "complete")
            or group["receipt"] is not None
            and not isinstance(group["receipt"], dict)
            or group["error"] is not None
            and not isinstance(group["error"], str)
        ):
            raise MigrationError(
                "A grouped migration journal has an unsupported group", 409
            )
    return journal


def _grouped_receipt(journal):
    from gideon.workspace.capabilities.platform.migration import (
        FORMAT,
        datetime,
        timezone,
    )

    completed = [group for group in journal["groups"] if group["state"] == "complete"]
    domains: dict[str, int] = {}
    records = []
    for group in completed:
        receipt = group["receipt"]
        for domain, count in receipt["domains"].items():
            domains[domain] = domains.get(domain, 0) + count
        records.extend(receipt["records"])
    return {
        "archive_digest": journal["archive_digest"],
        "format": FORMAT,
        "generated_at": journal["generated_at"],
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if len(completed) == len(journal["groups"]) else "partial",
        "domains": domains,
        "records": records,
        "groups": [
            {"id": group["id"], "status": group["state"], "error": group["error"]}
            for group in journal["groups"]
        ],
    }


def _preflight_grouped(
    tasks, people, knowledge, projects, repertoire, artifacts, digest, records
):
    from gideon.workspace.capabilities.platform.migration import (
        CaptureInbox,
        MigrationError,
        _canonical_step,
        _coordinated_person_id,
        _ordered_canonical_records,
        closing,
    )

    canonical_rows = [row for row in records if row["domain"] not in ("songs", "inbox")]
    canonical = _ordered_canonical_records(canonical_rows) if canonical_rows else []
    names = set()
    for row in canonical:
        if row["domain"] == "people":
            identity = _coordinated_person_id(
                {"archive_digest": digest}, _canonical_step(row)
            )
            with closing(people.connect()) as db:
                occupied = db.execute(
                    "SELECT 1 FROM people WHERE id=?", (identity,)
                ).fetchone()
            if occupied:
                raise MigrationError("Migration target identity already exists", 409)
        elif row["domain"] == "projects":
            name = row["values"]["name"].casefold()
            if (
                name in names
                or (projects._projects_dir() / row["values"]["id"]).exists()
                or projects.get_project_by_name(row["values"]["name"])
            ):
                raise MigrationError("Migration target identity already exists", 409)
            names.add(name)
        elif (
            row["domain"] in ("admin", "threads")
            and tasks._task_path(row["values"]["id"]).exists()
        ):
            raise MigrationError("Migration target identity already exists", 409)
        elif (
            row["domain"] == "buckets"
            and knowledge.db.execute(
                "SELECT 1 FROM collections WHERE id=?", (row["values"]["id"],)
            ).fetchone()
        ):
            raise MigrationError("Migration target identity already exists", 409)
        elif (
            row["domain"] in ("ideas", "journals", "memories", "links")
            and knowledge.db.execute(
                "SELECT 1 FROM items WHERE id=?", (row["values"]["id"],)
            ).fetchone()
        ):
            raise MigrationError("Migration target identity already exists", 409)
    inbox_rows = [row for row in records if row["domain"] == "inbox"]
    if inbox_rows:
        CaptureInbox(knowledge)
        planned_items = {
            row["values"]["id"]
            for row in canonical
            if row["domain"] in ("ideas", "journals", "memories", "links")
        }
        for row in inbox_rows:
            values = row["values"]
            if (
                knowledge.db.execute(
                    "SELECT 1 FROM capability_knowledge_captures WHERE id=? OR request_id=?",
                    (values["id"], "migration-inbox-" + values["id"]),
                ).fetchone()
                or knowledge.db.execute(
                    "SELECT 1 FROM capability_knowledge_capture_events WHERE request_id=?",
                    ("migration-event-" + values["id"],),
                ).fetchone()
            ):
                raise MigrationError("Migration target identity already exists", 409)
            if (
                values["destination_id"] is not None
                and values["destination_id"] not in planned_items
                and not knowledge.db.execute(
                    "SELECT 1 FROM items WHERE id=?", (values["destination_id"],)
                ).fetchone()
            ):
                raise MigrationError(
                    "Inbox record references a missing canonical destination", 409
                )
    songs = [row for row in records if row["domain"] == "songs"]
    if not songs:
        return
    song = songs[0]
    with repertoire._db() as db:
        if db.execute(
            "SELECT 1 FROM items WHERE id=?", (song["values"]["id"],)
        ).fetchone():
            raise MigrationError("Migration target identity already exists", 409)
    with artifacts.mutation_lock:
        for attachment in song["values"]["attachments"]:
            try:
                observed = artifacts.state_fingerprint(attachment["slug"])
            except (OSError, ValueError) as exc:
                raise MigrationError(
                    "Canonical attachment target has ambiguous filesystem state", 409
                ) from exc
            existing = artifacts.get(attachment["slug"], version=1)
            if observed is None:
                if existing is not None:
                    raise MigrationError(
                        "Canonical attachment target has ambiguous filesystem state",
                        409,
                    )
                continue
            if (
                existing is None
                or _artifact_digest(artifacts, attachment) != attachment["sha256"]
            ):
                raise MigrationError(
                    "Canonical attachment slug already contains different bytes", 409
                )


def _validate_inbox_group(knowledge, records):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        json,
    )

    db = knowledge.db
    for record in records:
        values = record["values"]
        row = db.execute(
            "SELECT * FROM capability_knowledge_captures WHERE id=?", (values["id"],)
        ).fetchone()
        events = list(
            db.execute(
                "SELECT request_id,event,payload,happened_at FROM capability_knowledge_capture_events WHERE capture_id=?",
                (values["id"],),
            )
        )
        expected = {
            "id": values["id"],
            "request_id": "migration-inbox-" + values["id"],
            "input_origin": "text",
            "original_text": values["text"],
            "audio_item_id": None,
            "audio_sha256": None,
            "captured_at": values["captured_at"],
            "status": values["status"],
            "transcript": None,
            "error": values["error"],
            "revision": 1,
            "destination_id": values["destination_id"],
            "pending": None,
        }
        expected_event = (
            "migration-event-" + values["id"],
            "migration_snapshot",
            json.dumps(values["event"], sort_keys=True),
            values["captured_at"],
        )
        if (
            row is None
            or dict(row) != expected
            or [tuple(event) for event in events] != [expected_event]
        ):
            raise MigrationError(
                "A completed inbox migration group has canonical identity drift", 409
            )


def _commit_grouped_records(
    tasks,
    people,
    knowledge,
    projects,
    repertoire,
    artifacts,
    digest,
    token,
    records,
    generated,
):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        _commit_knowledge,
        hashlib,
        json,
    )

    canonical = [row for row in records if row["domain"] not in ("songs", "inbox")]
    inbox = [row for row in records if row["domain"] == "inbox"]
    songs = [row for row in records if row["domain"] == "songs"]
    plan = [
        {
            **row,
            "values": {
                **row["values"],
                "attachments": [
                    {key: value for key, value in attachment.items() if key != "bytes"}
                    for attachment in row["values"].get("attachments", [])
                ],
            },
        }
        for row in records
    ]
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    path = _grouped_root(repertoire) / (digest + ".json")
    created = False
    if path.exists():
        journal = _read_grouped_journal(path)
        if journal["review_token"] != token or journal["plan_hash"] != plan_hash:
            raise MigrationError(
                "Archive was already grouped with different reviewed content", 409
            )
        if all(group["state"] == "complete" for group in journal["groups"]):
            if canonical:
                _recover_canonical_journals(tasks, people, knowledge, projects)
            if inbox:
                _validate_inbox_group(knowledge, inbox)
            if songs:
                _recover_song_journals(repertoire, artifacts)
            return journal["receipt"], False
    else:
        _preflight_grouped(
            tasks, people, knowledge, projects, repertoire, artifacts, digest, records
        )
        groups = (
            [{"id": "canonical", "state": "pending", "receipt": None, "error": None}]
            if canonical
            else []
        )
        groups += (
            [{"id": "inbox", "state": "pending", "receipt": None, "error": None}]
            if inbox
            else []
        )
        groups += (
            [{"id": "song", "state": "pending", "receipt": None, "error": None}]
            if songs
            else []
        )
        journal = {
            "schema": 1,
            "kind": "independent_groups",
            "archive_digest": digest,
            "review_token": token,
            "generated_at": generated,
            "plan_hash": plan_hash,
            "groups": groups,
            "receipt": None,
            "receipt_hash": None,
        }
        _write_journal(path, journal)
    for group in journal["groups"]:
        if group["state"] == "complete":
            if group["id"] == "canonical":
                _recover_canonical_journals(tasks, people, knowledge, projects)
            elif group["id"] == "inbox":
                _validate_inbox_group(knowledge, inbox)
            else:
                _recover_song_journals(repertoire, artifacts)
            continue
        try:
            if group["id"] == "canonical":
                receipt, group_created = _commit_canonical_records(
                    tasks,
                    people,
                    knowledge,
                    projects,
                    digest,
                    token,
                    canonical,
                    generated,
                )
            elif group["id"] == "inbox":
                receipt, group_created = _commit_knowledge(
                    knowledge, digest, token, inbox, generated
                )
            else:
                receipt, group_created = _commit_song(
                    repertoire, artifacts, digest, token, songs, generated
                )
        except MigrationError as error:
            group["error"] = str(error)
            journal["receipt"] = _grouped_receipt(journal)
            journal["receipt_hash"] = hashlib.sha256(
                json.dumps(journal["receipt"], sort_keys=True).encode()
            ).hexdigest()
            _write_journal(path, journal)
            if any(candidate["state"] == "complete" for candidate in journal["groups"]):
                return journal["receipt"], created
            raise
        group.update(state="complete", receipt=receipt, error=None)
        created = created or group_created
        journal["receipt"] = _grouped_receipt(journal)
        journal["receipt_hash"] = hashlib.sha256(
            json.dumps(journal["receipt"], sort_keys=True).encode()
        ).hexdigest()
        _write_journal(path, journal)
    return journal["receipt"], created


def receipts(
    store: PeopleStore,
    knowledge: KnowledgeStore | None = None,
    projects: BoundHierarchy | None = None,
    tasks: BoundTasks | None = None,
    repertoire: RepertoireStore | None = None,
    artifacts: NativeArtifactProvider | None = None,
):
    from gideon.workspace.capabilities.platform.migration import (
        MigrationError,
        _canonical_coordinator_root,
        _read_canonical_journal,
        _recover_coordinated_journals,
        closing,
        json,
    )

    if tasks is not None and projects is not None:
        _recover_coordinated_journals(tasks, store, knowledge, projects)
    with closing(store.connect()) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'"
        ).fetchone()
        result = (
            []
            if not exists
            else [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20"
                )
            ]
        )
    if tasks is not None and projects is not None:
        # Recovery above validates canonical ownership before a durable receipt is served.
        # Mixed knowledge-only plans have no project marker or task evidence copy.
        for path in _canonical_coordinator_root(tasks).glob("*.json"):
            journal = _read_canonical_journal(path)
            if journal["status"] == "complete":
                result.append(journal["receipt"])
    if (
        knowledge is not None
        and knowledge.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_migrations'"
        ).fetchone()
    ):
        result += [
            json.loads(row[0])
            for row in knowledge.db.execute(
                "SELECT receipt FROM platform_migrations ORDER BY rowid DESC LIMIT 20"
            )
        ]
    if projects is not None:
        for path in projects._projects_dir().glob("*/.migration-receipt.json"):
            try:
                result.append(json.loads(path.read_text())["receipt"])
            except (OSError, KeyError, json.JSONDecodeError):
                continue
    if tasks is not None:
        for task in tasks._all_tasks():
            result += [
                item["receipt"]
                for item in task.evidence
                if item.get("type") == "platform_migration"
                and isinstance(item.get("receipt"), dict)
            ]
    if repertoire is not None and artifacts is not None:
        _recover_song_journals(repertoire, artifacts)
        for path in _journal_root(repertoire).glob("*.json"):
            try:
                journal = _read_song_journal(path)
                if journal.get("status") == "complete":
                    result.append(journal["receipt"])
            except KeyError:
                raise MigrationError(
                    "A song migration recovery journal has an unsupported shape", 409
                ) from None
        for path in _grouped_root(repertoire).glob("*.json"):
            journal = _read_grouped_journal(path)
            if journal["receipt"] is not None:
                result.append(journal["receipt"])
    ordered = sorted(result, key=lambda row: row["committed_at"], reverse=True)
    unique: dict[str, dict[str, Any]] = {}
    for row in ordered:
        unique.setdefault(row["archive_digest"], row)
    return list(unique.values())[:20]
