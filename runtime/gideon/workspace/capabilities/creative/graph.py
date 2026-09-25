"""Universe relationship projection and optimistic, auditable universe merge."""

import json
from datetime import datetime, timezone

from .store import CatalogError, identifier, integer, keys
from .universes import UniverseStore


class UniverseGraph:
    def __init__(self, store: UniverseStore):
        self.store = store
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS universe_merges(request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, record TEXT NOT NULL)"
            )

    def graph(self, id):
        with self.store.connection() as db:
            universe = self.store._universe(db, id)
            members = set(universe["ingredient_ids"])
            nodes, edges = {}, []

            def node(id):
                if id not in nodes:
                    row = db.execute(
                        "SELECT record FROM ingredients WHERE id=?", (id,)
                    ).fetchone()
                    value = json.loads(row[0]) if row else None
                    nodes[id] = {
                        "id": id,
                        "title": value["title"] if value else id,
                        "type": value["type"] if value else None,
                        "missing": value is None,
                        "external": id not in members,
                    }
                    return value
                return None

            for member in universe["ingredient_ids"]:
                value = node(member)
                if value is None:
                    row = db.execute(
                        "SELECT record FROM ingredients WHERE id=?", (member,)
                    ).fetchone()
                    value = json.loads(row[0]) if row else None
                for relation in value["relations"] if value else []:
                    node(relation["target_id"])
                    edges.append(
                        {
                            "source": member,
                            "target": relation["target_id"],
                            "kind": relation["kind"],
                        }
                    )
            events = [
                json.loads(row[0])["merge"]
                for row in db.execute("SELECT record FROM universe_merges")
                if json.loads(row[0])["merge"]["target_id"] == id
            ]
            return {
                "universe_id": id,
                "revision": universe["revision"],
                "nodes": list(nodes.values()),
                "edges": edges,
                "merges": events,
            }

    def _preview(self, db, id, source_id):
        if identifier(id) == identifier(source_id):
            raise CatalogError("Choose a different source universe")
        target = self.store._universe(db, id)
        source = self.store._universe(db, source_id)
        target_entries = {entry["id"]: entry for entry in target["canon"]}
        conflicts = [
            {"id": entry["id"], "target": target_entries[entry["id"]], "source": entry}
            for entry in source["canon"]
            if entry["id"] in target_entries and entry != target_entries[entry["id"]]
        ]
        return {
            "target": target,
            "source": source,
            "canon_conflicts": conflicts,
            "identity_conflict": target["visual_identity"] != source["visual_identity"],
            "added_canon_ids": [
                entry["id"]
                for entry in source["canon"]
                if entry["id"] not in target_entries
            ],
        }

    def merge_preview(self, id, payload):
        keys(payload, {"source_id"})
        with self.store.connection() as db:
            return self._preview(db, id, payload.get("source_id"))

    def merge(self, id, payload):
        keys(
            payload,
            {
                "request_id",
                "source_id",
                "target_revision",
                "source_revision",
                "canon_choices",
                "identity_choice",
            },
        )
        request_id = identifier(payload.get("request_id"))
        encoded = json.dumps({"id": identifier(id), **payload}, sort_keys=True)
        with self.store.connection() as db:
            prior = db.execute(
                "SELECT payload,record FROM universe_merges WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise CatalogError(
                        "Merge request already used with different values", 409
                    )
                return json.loads(prior[1])
            preview = self._preview(db, id, payload.get("source_id"))
            target, source = preview["target"], preview["source"]
            if (
                integer(payload.get("target_revision")) != target["revision"]
                or integer(payload.get("source_revision")) != source["revision"]
            ):
                raise CatalogError("Universe changed; preview the merge again", 409)
            choices = payload.get("canon_choices", {})
            conflict_ids = {entry["id"] for entry in preview["canon_conflicts"]}
            keys(choices, conflict_ids)
            if set(choices) != conflict_ids or any(
                value not in ("target", "source") for value in choices.values()
            ):
                raise CatalogError("Resolve every canon conflict")
            identity = payload.get(
                "identity_choice",
                "target" if not preview["identity_conflict"] else None,
            )
            if identity not in ("target", "source"):
                raise CatalogError("Resolve the visual identity conflict")
            source_entries = {entry["id"]: entry for entry in source["canon"]}
            canon = [
                (
                    source_entries[entry["id"]]
                    if choices.get(entry["id"]) == "source"
                    else entry
                )
                for entry in target["canon"]
            ]
            canon.extend(
                entry
                for entry in source["canon"]
                if entry["id"] in preview["added_canon_ids"]
            )
            values = {
                **self.store.editable(target),
                "canon": canon,
                "visual_identity": (target if identity == "target" else source)[
                    "visual_identity"
                ],
                "ingredient_ids": list(
                    dict.fromkeys(target["ingredient_ids"] + source["ingredient_ids"])
                ),
                "board_refs": target["board_refs"]
                + [
                    ref
                    for ref in source["board_refs"]
                    if ref not in target["board_refs"]
                ],
            }
            values = self.store._values(db, values, [target, source])
            record = self.store._save(
                db,
                {
                    **target,
                    **values,
                    "revision": target["revision"] + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            result = {
                "universe": record,
                "merge": {
                    "request_id": request_id,
                    "source_id": source["id"],
                    "source_revision": source["revision"],
                    "target_id": id,
                    "target_revision": target["revision"],
                    "result_revision": record["revision"],
                    "canon_choices": choices,
                    "identity_choice": identity,
                },
            }
            db.execute(
                "INSERT INTO universe_merges VALUES(?,?,?)",
                (request_id, encoded, json.dumps(result)),
            )
            return result
