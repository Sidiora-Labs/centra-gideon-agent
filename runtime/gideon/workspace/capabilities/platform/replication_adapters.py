"""Canonical SQLite row adapters for direct peer replication."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.operations.durability import conflicts, inventory, merge, reconcile, writeback
from gideon.workspace.capabilities.creative.authors import AuthorStore
from gideon.workspace.capabilities.creative.moodboards import BoardStore
from gideon.workspace.capabilities.creative.series import SeriesStore
from gideon.workspace.capabilities.creative.store import IngredientStore
from gideon.workspace.capabilities.creative.stories import StoryStore
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.creative.works import WorkStore
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.identity.progress import ProgressStore
from gideon.workspace.capabilities.identity.twin import TwinStore
from gideon.workspace.capabilities.communications.store import PeopleStore
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.listening import ListeningStore
from gideon.workspace.capabilities.music.store import RepertoireStore


@dataclass(frozen=True)
class ApplyResult:
    added: int
    updated: int
    removed: int
    conflicts: int
    new_ancestors: dict[str, str]

    @property
    def verdict(self) -> str:
        return "consumed"


CREATIVE_TABLES = {
    "creative.ingredients": "ingredients",
    "creative.moodboards": "boards",
    "creative.universes": "universes",
    "creative.authors": "authors",
    "creative.works": "works",
    "creative.stories": "stories",
    "creative.series": "series",
}
IDENTITY_TABLES = {
    "identity.goals": "goals",
    "identity.sessions": "sessions",
    "identity.goal_plans": "goal_plans",
    "identity.goal_checkins": "goal_checkins",
}
IDENTITY_ENTRIES = frozenset({*IDENTITY_TABLES, "identity.progress_profile", "identity.twin_profile", "identity.twin_documents"})
COMMUNICATION_TABLES = {"communications.people": "people", "communications.touchpoints": "touchpoints"}
MUSIC_ENTRIES = ("music.artists", "music.tracks", "music.albums", "music.songs", "music.playlists", "music.decks")
SQLITE_ENTRIES = frozenset({"knowledge.items", *CREATIVE_TABLES, *IDENTITY_ENTRIES, *COMMUNICATION_TABLES, *MUSIC_ENTRIES})


def _knowledge(home: Path) -> KnowledgeStore:
    return KnowledgeStore(str(knowledge_db_path(home)))


def _creative_path(home: Path) -> Path:
    IngredientStore(home); BoardStore(home); UniverseStore(home); AuthorStore(home)
    WorkStore(home); StoryStore(home); SeriesStore(home)
    return home / "capabilities/creative/catalog.sqlite3"


def _identity_paths(home: Path) -> tuple[Path, Path, Path]:
    root = home / "capabilities/identity"
    goals, progress, twin = root / "goals.sqlite3", root / "progress.sqlite3", root / "twin.sqlite3"
    GoalPlanStore(goals); ProgressStore(progress); TwinStore(twin)
    return goals, progress, twin


def _people(home: Path) -> PeopleStore:
    return PeopleStore(home / "capabilities/communications")


def _music_paths(home: Path) -> tuple[Path, Path, Path, Path]:
    root = home / "capabilities/music"
    artifacts = NativeArtifactProvider(home / "artifacts")
    catalog = MusicCatalog(root, artifacts).path
    songs = RepertoireStore(root, artifacts).path
    playlists = ListeningStore(home, artifacts).path
    decks = root / "decks.sqlite3"
    with sqlite3.connect(decks) as db:
        db.execute("CREATE TABLE IF NOT EXISTS decks(id TEXT PRIMARY KEY,payload TEXT)")
    return catalog, songs, playlists, decks


def _pinned(ref: object) -> bool:
    return isinstance(ref, dict) and set(ref) == {"slug", "version"} and isinstance(ref["slug"], str) and bool(ref["slug"]) and type(ref["version"]) is int and ref["version"] > 0


def _contains_private_music_key(value: object) -> bool:
    forbidden = {"credential_ref", "credential_name", "access_token", "refresh_token", "client_secret"}
    if isinstance(value, dict):
        return bool(forbidden & set(value)) or any(_contains_private_music_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_music_key(item) for item in value)
    return False


def validate_entries(scope: str, entries: list[dict]) -> None:
    if scope != "communications.contacts":
        return
    people = entries[0]["rows"]
    touchpoints = entries[1]["rows"]
    ids = {row.get("id") for row in people}
    identities: list[dict] = []
    for row in people:
        data = row.get("data")
        values = data.get("identities") if isinstance(data, dict) else None
        if not isinstance(row.get("id"), str) or not isinstance(values, list) or any(value in identities for value in values):
            raise ValueError("Invalid or ambiguous canonical contact identities")
        identities.extend(values)
    if any(not isinstance(row.get("data"), dict) or row["data"].get("person_id") not in ids for row in touchpoints):
        raise ValueError("Touchpoint coverage references a missing canonical person")


def validate_music_entries(entries: list[dict]) -> None:
    rows = {item["entry_id"]: item["rows"] for item in entries}
    artist_ids = [row.get("id") for row in rows["music.artists"]]
    track_ids = [row.get("id") for row in rows["music.tracks"]]
    album_ids = [row.get("id") for row in rows["music.albums"]]
    all_catalog = artist_ids + track_ids + album_ids
    if None in all_catalog or len(all_catalog) != len(set(all_catalog)):
        raise ValueError("Music catalog identities must be unique across kinds")
    artists, tracks = set(artist_ids), set(track_ids)
    for item in entries:
        for row in item["rows"]:
            data = row.get("data")
            if not isinstance(row.get("id"), str) or not isinstance(data, dict) or data.get("id") != row["id"] or _contains_private_music_key(data):
                raise ValueError("Invalid or private music metadata")
    for row in rows["music.tracks"]:
        data = row["data"]
        if data.get("artist_id") and data["artist_id"] not in artists:
            raise ValueError("Track references a missing artist")
        renders = data.get("renders", [])
        if not isinstance(renders, list) or any(not isinstance(render, dict) or not _pinned(render.get("artifact_ref")) for render in renders):
            raise ValueError("Track render references must pin artifact versions")
        if data.get("selected_render_id") is not None and data["selected_render_id"] not in {render.get("id") for render in renders}:
            raise ValueError("Selected track render is missing")
    for row in rows["music.albums"]:
        data = row["data"]
        if data.get("artist_id") and data["artist_id"] not in artists:
            raise ValueError("Album references a missing artist")
        if not isinstance(data.get("track_ids"), list) or len(set(data["track_ids"])) != len(data["track_ids"]) or any(track not in tracks for track in data["track_ids"]):
            raise ValueError("Album references missing or repeated tracks")
    for row in rows["music.songs"]:
        if any(not _pinned(ref) for ref in row["data"].get("attachment_refs", [])):
            raise ValueError("Song attachments must pin artifact versions")
    for row in rows["music.playlists"]:
        if row["data"].get("artifact_ref") is not None and not _pinned(row["data"]["artifact_ref"]):
            raise ValueError("Playlist provenance must pin an artifact version")
    for row in rows["music.decks"]:
        data = row["data"]
        cards = data.get("cards", [])
        if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards) or data.get("exports") not in (None, []):
            raise ValueError("Deck replication contains non-current or invalid metadata")
        refs = list(data.get("sample_refs", [])) + ([data["context_ref"]] if data.get("context_ref") else []) + [card["artifact_ref"] for card in cards if card.get("artifact_ref")]
        if any(not _pinned(ref) for ref in refs):
            raise ValueError("Deck media references must pin artifact versions")


def read_rows(home: Path, entry_id: str) -> list[dict]:
    if entry_id == "knowledge.items":
        store = _knowledge(home)
        try:
            rows = store.db.execute("SELECT id FROM items ORDER BY id").fetchall()
            return [{"id": row["id"], "data": store.get_item(row["id"])} for row in rows]
        finally:
            store.db.close()
    if entry_id in MUSIC_ENTRIES:
        catalog, songs, playlists, decks = _music_paths(home)
        if entry_id in {"music.artists", "music.tracks", "music.albums"}:
            kind = entry_id.removeprefix("music.")
            with sqlite3.connect(catalog) as db:
                return [{"id": row[0], "data": json.loads(row[1])} for row in db.execute("SELECT id,payload FROM catalog WHERE kind=? ORDER BY id", (kind,))]
        path, table = (songs, "items") if entry_id == "music.songs" else (playlists, "snapshots") if entry_id == "music.playlists" else (decks, "decks")
        with sqlite3.connect(path) as db:
            if entry_id == "music.playlists":
                query = "SELECT id,payload FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots GROUP BY playlist_id) ORDER BY id"
            else:
                query = f"SELECT id,payload FROM {table} ORDER BY id"
            rows = [{"id": row[0], "data": json.loads(row[1])} for row in db.execute(query)]
            if entry_id == "music.decks":
                for row in rows:
                    row["data"]["exports"] = []
            return rows
    if entry_id in COMMUNICATION_TABLES:
        store, table = _people(home), COMMUNICATION_TABLES[entry_id]
        with store.connect() as db:
            if table == "people":
                return [{"id": row[0], "data": {**json.loads(row[1]), "revision": row[2]}} for row in db.execute("SELECT id,body,revision FROM people ORDER BY id")]
            return [{"id": row[0], "data": json.loads(row[1])} for row in db.execute("SELECT id,body FROM touchpoints ORDER BY id")]
    if entry_id in IDENTITY_ENTRIES:
        goals, progress, twin = _identity_paths(home)
        if entry_id in IDENTITY_TABLES:
            table = IDENTITY_TABLES[entry_id]
            with sqlite3.connect(goals) as db:
                return [{"id": row[0], "data": json.loads(row[1])} for row in db.execute(f"SELECT id,body FROM {table} ORDER BY id")]
        if entry_id == "identity.progress_profile":
            with sqlite3.connect(progress) as db:
                row = db.execute("SELECT body FROM profile WHERE id=1").fetchone()
            return [{"id": "profile", "data": json.loads(row[0])}] if row else []
        state = TwinStore(twin).snapshot()
        if entry_id == "identity.twin_profile":
            data = {key: state[key] for key in ("schema_version", "enabled", "traits", "personas", "active_persona_id")}
            return [{"id": "profile", "data": data}]
        return [{"id": row["id"], "data": row} for row in sorted(state["documents"], key=lambda row: row["id"]) if not row["private"]]
    table = CREATIVE_TABLES[entry_id]
    path = _creative_path(home)
    with sqlite3.connect(path) as db:
        return [{"id": row[0], "data": json.loads(row[1])} for row in db.execute(f"SELECT id,record FROM {table} ORDER BY id")]


def _write_knowledge(home: Path, row: dict | None, entity_id: str) -> None:
    store = _knowledge(home)
    try:
        if row is None:
            store.delete_item(entity_id)
            return
        data = row["data"]
        current = store.get_item(entity_id)
        if current:
            values = {key: value for key, value in data.items() if key in store._ITEM_COLUMNS or key == "tags"}
            values.pop("embedding", None)
            store.update_item(entity_id, touch=False, **values)
            store.db.commit()
            return
        columns = {item[1] for item in store.db.execute("PRAGMA table_info(items)")}
        values = {key: value for key, value in data.items() if key in columns and key != "embedding"}
        values["id"] = entity_id
        values.setdefault("title", "")
        values.setdefault("content", "")
        values.setdefault("item_type", data.get("type") or "note")
        values.setdefault("created_at", datetime.now().isoformat())
        values.setdefault("updated_at", values["created_at"])
        encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else int(value) if isinstance(value, bool) else value for key, value in values.items()}
        store.db.execute("BEGIN")
        names = ",".join(encoded)
        store.db.execute(f"INSERT INTO items ({names}) VALUES ({','.join('?' for _ in encoded)})", tuple(encoded.values()))
        store._write_item_tags(entity_id, data.get("tags", []))
        dbrow = store.db.execute("SELECT rowid,title,content FROM items WHERE id=?", (entity_id,)).fetchone()
        store.db.execute("INSERT INTO items_fts(rowid,title,content,tags) VALUES(?,?,?,?)", (dbrow["rowid"], dbrow["title"], dbrow["content"], " ".join(data.get("tags", []))))
        store.db.execute("COMMIT")
    finally:
        store.db.close()


def _write_creative(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    table = CREATIVE_TABLES[entry_id]
    with sqlite3.connect(_creative_path(home)) as db:
        if row is None:
            db.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))
        else:
            data = dict(row["data"])
            data["id"] = entity_id
            db.execute(f"INSERT INTO {table}(id,record) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record", (entity_id, json.dumps(data, sort_keys=True)))


def _write_identity(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    goals, progress, twin = _identity_paths(home)
    if entry_id in IDENTITY_TABLES:
        table = IDENTITY_TABLES[entry_id]
        with sqlite3.connect(goals) as db:
            if row is None:
                db.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))
            elif table == "goal_checkins":
                data = row["data"]
                db.execute("INSERT INTO goal_checkins(id,goal_id,observed_at,body) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET goal_id=excluded.goal_id,observed_at=excluded.observed_at,body=excluded.body", (entity_id, data["goal_id"], data["observed_at"], json.dumps(data, sort_keys=True)))
            else:
                db.execute(f"INSERT INTO {table}(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (entity_id, json.dumps(row["data"], sort_keys=True)))
        return
    if entry_id == "identity.progress_profile":
        with sqlite3.connect(progress) as db:
            if row is None:
                db.execute("DELETE FROM profile WHERE id=1")
            else:
                db.execute("INSERT INTO profile(id,body) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (json.dumps(row["data"], sort_keys=True),))
        return
    with sqlite3.connect(twin) as db:
        state = json.loads(db.execute("SELECT body FROM twin WHERE id=1").fetchone()[0])
        if entry_id == "identity.twin_profile":
            if row is None:
                state.update(enabled=False, traits={}, personas=[], active_persona_id=None)
            else:
                state.update({key: row["data"][key] for key in ("enabled", "traits", "personas", "active_persona_id")})
        else:
            existing = next((item for item in state["documents"] if item["id"] == entity_id), None)
            if existing and existing["private"]:
                raise ValueError("Private identity documents are local and cannot be overwritten by replication")
            state["documents"] = [item for item in state["documents"] if item["id"] != entity_id]
            if row is not None:
                document = dict(row["data"]); document["id"] = entity_id; document["private"] = False
                state["documents"].append(document)
        state["revision"] += 1
        db.execute("UPDATE twin SET body=? WHERE id=1", (json.dumps(state, sort_keys=True),))


def _write_communications(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    store = _people(home)
    try:
        with store.connect() as db:
            if entry_id == "communications.people":
                if row is None:
                    db.execute("DELETE FROM touchpoints WHERE person_id=?", (entity_id,))
                    db.execute("DELETE FROM people WHERE id=?", (entity_id,))
                else:
                    data = dict(row["data"]); revision = data.pop("revision")
                    if type(revision) is not int or revision < 1 or data.get("id") != entity_id:
                        raise ValueError("Invalid canonical person row")
                    identities = data.get("identities", [])
                    for body, other_id in db.execute("SELECT body,id FROM people WHERE id<>?", (entity_id,)):
                        if any(identity in json.loads(body).get("identities", []) for identity in identities):
                            raise ValueError("Contact identity belongs to another canonical person")
                    db.execute("INSERT INTO people(id,body,revision) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision", (entity_id, json.dumps(data, sort_keys=True), revision))
            elif row is None:
                db.execute("DELETE FROM touchpoints WHERE id=?", (entity_id,))
            else:
                data = dict(row["data"]); data["id"] = entity_id
                if not db.execute("SELECT 1 FROM people WHERE id=?", (data.get("person_id"),)).fetchone():
                    raise ValueError("Touchpoint references a missing canonical person")
                db.execute("INSERT INTO touchpoints(id,person_id,source,external_id,body) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET person_id=excluded.person_id,source=excluded.source,external_id=excluded.external_id,body=excluded.body", (entity_id, data["person_id"], data["source"], data["external_id"], json.dumps(data, sort_keys=True)))
    except sqlite3.IntegrityError as error:
        raise ValueError("Communication record violates canonical identity constraints") from error


def _write_music(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    catalog, songs, playlists, decks = _music_paths(home)
    if entry_id in {"music.artists", "music.tracks", "music.albums"}:
        kind = entry_id.removeprefix("music.")
        with sqlite3.connect(catalog) as db:
            if row is None: db.execute("DELETE FROM catalog WHERE kind=? AND id=?", (kind, entity_id))
            else: db.execute("INSERT INTO catalog(kind,id,payload) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,payload=excluded.payload", (kind, entity_id, json.dumps(row["data"], sort_keys=True)))
        return
    path, table = (songs, "items") if entry_id == "music.songs" else (playlists, "snapshots") if entry_id == "music.playlists" else (decks, "decks")
    with sqlite3.connect(path) as db:
        if row is None:
            db.execute(f"DELETE FROM {table} WHERE id=?", (entity_id,))
        elif entry_id == "music.playlists":
            data = row["data"]
            db.execute("INSERT INTO snapshots(id,playlist_id,payload) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET playlist_id=excluded.playlist_id,payload=excluded.payload", (entity_id, data["playlist_id"], json.dumps(data, sort_keys=True)))
        else:
            data = dict(row["data"])
            if entry_id == "music.decks": data["exports"] = []
            db.execute(f"INSERT INTO {table}(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (entity_id, json.dumps(data, sort_keys=True)))


def write_row(home: Path, entry_id: str, row: dict | None, entity_id: str) -> None:
    if entry_id == "knowledge.items":
        _write_knowledge(home, row, entity_id)
    elif entry_id in CREATIVE_TABLES:
        _write_creative(home, entry_id, row, entity_id)
    elif entry_id in IDENTITY_ENTRIES:
        _write_identity(home, entry_id, row, entity_id)
    elif entry_id in COMMUNICATION_TABLES:
        _write_communications(home, entry_id, row, entity_id)
    else:
        _write_music(home, entry_id, row, entity_id)


def _conflict(entry_id: str, entity_id: str, ancestor: str, local: dict, remote: dict, now: str) -> conflicts.ConflictRecord:
    domain = inventory.DOMAIN_KNOWLEDGE if entry_id == "knowledge.items" else inventory.DOMAIN_MEMORY if entry_id in IDENTITY_ENTRIES else inventory.DOMAIN_WORK
    return conflicts.ConflictRecord(entry_id=entry_id, entity_id=entity_id, domain=domain,
        surface=conflicts.surface_for_domain(domain), ancestor_sha=ancestor,
        local_sha=conflicts.row_sha(local), remote_sha=conflicts.row_sha(remote),
        local_row=local, remote_row=remote, detected_at=now)


def apply_rows(home: Path, entry_id: str, remote_rows: list[dict], ancestors: dict[str, str], queue: conflicts.ConflictQueue, now: str) -> ApplyResult:
    local_rows = read_rows(home, entry_id)
    local = {row["id"]: row for row in local_rows}
    remote = {row.get("id"): row for row in remote_rows if isinstance(row.get("id"), str) and isinstance(row.get("data"), dict)}
    if len(remote) != len(remote_rows):
        raise ValueError("invalid canonical SQLite rows")
    added = updated = removed = conflict_count = 0
    for entity_id in sorted(set(local) | set(remote) | set(ancestors)):
        lrow, rrow, ancestor = local.get(entity_id), remote.get(entity_id), ancestors.get(entity_id, "")
        lsha = conflicts.row_sha(lrow) if lrow else ""
        rsha = conflicts.row_sha(rrow) if rrow else ""
        if lsha == rsha:
            continue
        if ancestor and lsha != ancestor and rsha != ancestor:
            marker_local = lrow or {"id": entity_id, "deleted_at": now}
            marker_remote = rrow or {"id": entity_id, "deleted_at": now}
            if queue.record(_conflict(entry_id, entity_id, ancestor, marker_local, marker_remote, now)):
                conflict_count += 1
            continue
        if rrow is None:
            if ancestor and lsha == ancestor:
                write_row(home, entry_id, None, entity_id); removed += 1
            continue
        if lrow is None:
            write_row(home, entry_id, rrow, entity_id); added += 1
        elif not ancestor or lsha == ancestor:
            write_row(home, entry_id, rrow, entity_id); updated += 1
    current = {row["id"]: conflicts.row_sha(row) for row in read_rows(home, entry_id)}
    return ApplyResult(added, updated, removed, conflict_count, current)


def apply_inventory_rows(home: Path, entry: inventory.StateEntry, remote_rows: list[dict], ancestors: dict[str, str], queue: conflicts.ConflictQueue, now: str) -> ApplyResult:
    local_rows = reconcile.read_local_rows(entry, home / entry.path)
    detected = conflicts.detect_conflicts(entry, local_rows, remote_rows, ancestors, now=now)
    recorded = sum(1 for item in detected if queue.record(item))
    held = queue.held_ids(entry.id)
    effective = [row for row in remote_rows if conflicts.row_id(row) not in held]
    result = merge.merge_rows(entry.merge, local_rows, effective, tombstones=entry.tombstones, dedup_key="id")
    local = {conflicts.row_id(row): row for row in local_rows if conflicts.row_id(row)}
    remote = {conflicts.row_id(row): row for row in effective if conflicts.row_id(row)}
    merged = {conflicts.row_id(row): row for row in result.rows if conflicts.row_id(row)}
    for entity_id, remote_row in remote.items():
        local_row, ancestor = local.get(entity_id), ancestors.get(entity_id, "")
        if local_row and ancestor and conflicts.row_sha(local_row) == ancestor:
            merged[entity_id] = remote_row
    rows = [merged[key] for key in sorted(merged)]
    applied = writeback.apply_rows(entry.kind, home / entry.path, rows)
    after = {key: conflicts.row_sha(row) for key, row in merged.items()}
    before = {key: conflicts.row_sha(row) for key, row in local.items()}
    agreed = {key: sha for key, sha in after.items() if key not in held and key in remote and sha == conflicts.row_sha(remote[key])}
    added = len(set(after) - set(before))
    updated = sum(before.get(key) != sha for key, sha in after.items() if key in before)
    return ApplyResult(added, updated, applied.removed, recorded, agreed)


def restore_fields(home: Path, record_id: str, fields: list[str], now: str) -> dict:
    queue = conflicts.ConflictQueue(home)
    record = queue.get(record_id)
    if record is None or record.status != conflicts.STATUS_NEEDS_REVIEW:
        raise ValueError("Conflict is not available for restoration")
    if record.entry_id not in SQLITE_ENTRIES or not fields or len(fields) > 50 or any(not isinstance(field, str) or not field or field in {"id", "created_at", "updated_at", "revision"} for field in fields):
        raise ValueError("Invalid field restoration")
    local_data, remote_data = record.local_row.get("data"), record.remote_row.get("data")
    if not isinstance(local_data, dict) or not isinstance(remote_data, dict) or any(field not in remote_data for field in fields):
        raise ValueError("Selected remote fields are unavailable")
    merged = dict(local_data)
    for field in fields:
        merged[field] = remote_data[field]
    if "revision" in merged:
        merged["revision"] = int(local_data.get("revision", 0)) + 1
    if "updated_at" in merged:
        merged["updated_at"] = now
    write_row(home, record.entry_id, {"id": record.entity_id, "data": merged}, record.entity_id)
    record.status = conflicts.STATUS_RESOLVED
    record.resolution = "merge_fields:" + ",".join(fields)
    record.resolved_at = now
    if not queue.update(record):
        raise RuntimeError("Conflict queue update failed")
    return {"resolved": True, "id": record.id, "entry_id": record.entry_id, "entity_id": record.entity_id, "fields": fields}
