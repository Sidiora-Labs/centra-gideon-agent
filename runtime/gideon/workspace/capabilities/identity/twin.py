"""Human identity sources and bounded, attributable context composition."""
import json
import math
import sqlite3
from pathlib import Path
from uuid import uuid4
from gideon.workspace.capabilities.identity.store import ConflictError


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must contain 1..{limit} characters")
    return value


def _traits(value):
    if not isinstance(value, dict) or len(value) > 40:
        raise ValueError("traits must be an object with at most 40 entries")
    for key, item in value.items():
        _text(key, "trait name", 80)
        if isinstance(item, str):
            _text(item, "trait value", 500)
        elif type(item) not in (int, float) or not math.isfinite(item):
            raise ValueError("trait values must be text or finite numbers")
    return value


class TwinStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS twin (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO twin VALUES (1,?)", (json.dumps({
                "schema_version": 1, "revision": 0, "documents": [], "traits": {},
                "personas": [], "active_persona_id": None, "enabled": False}),))

    def snapshot(self) -> dict:
        with sqlite3.connect(self.path) as db:
            return json.loads(db.execute("SELECT body FROM twin WHERE id=1").fetchone()[0])

    def _change(self, expected_revision, operation):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(db.execute("SELECT body FROM twin WHERE id=1").fetchone()[0])
            if current["revision"] != expected_revision:
                raise ConflictError("Identity changed; reload before saving")
            operation(current)
            current["revision"] += 1
            db.execute("UPDATE twin SET body=? WHERE id=1", (json.dumps(current, allow_nan=False),))
            db.commit()
            return current
        finally:
            db.close()

    def save_document(self, *, title, text, expected_revision, id=None,
                      enabled=True, private=False, weight=5, priority=50) -> dict:
        _text(title, "title", 200)
        _text(text, "text", 100000)
        if type(enabled) is not bool or type(private) is not bool:
            raise ValueError("enabled and private must be boolean")
        if type(weight) is not int or not 1 <= weight <= 10:
            raise ValueError("weight must be an integer from 1 to 10")
        if type(priority) is not int or not 0 <= priority <= 1000:
            raise ValueError("priority must be an integer from 0 to 1000")
        def apply(state):
            prior = next((d for d in state["documents"] if d["id"] == id), None)
            if id is not None and prior is None:
                raise KeyError("Identity document not found")
            if prior is None and len(state["documents"]) >= 200:
                raise ValueError("At most 200 identity documents")
            record = {"id": id or uuid4().hex, "title": title, "text": text,
                      "enabled": enabled, "private": private, "weight": weight, "priority": priority}
            state["documents"] = [d for d in state["documents"] if d["id"] != id] + [record]
        return self._change(expected_revision, apply)

    def delete_document(self, id: str, expected_revision: int) -> dict:
        def apply(state):
            if not any(d["id"] == id for d in state["documents"]):
                raise KeyError("Identity document not found")
            state["documents"] = [d for d in state["documents"] if d["id"] != id]
        return self._change(expected_revision, apply)

    def configure(self, *, expected_revision, enabled, traits, personas, active_persona_id):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        _traits(traits)
        if not isinstance(personas, list) or len(personas) > 20:
            raise ValueError("personas must be a list of at most 20 overlays")
        seen = set()
        for persona in personas:
            if not isinstance(persona, dict) or set(persona) != {"id", "name", "instructions", "trait_adjustments"}:
                raise ValueError("Invalid persona fields")
            _text(persona["id"], "persona id", 80)
            _text(persona["name"], "persona name", 200)
            _text(persona["instructions"], "persona instructions", 4000)
            _traits(persona["trait_adjustments"])
            if persona["id"] in seen:
                raise ValueError("Duplicate persona id")
            seen.add(persona["id"])
        if active_persona_id is not None and active_persona_id not in seen:
            raise ValueError("Active persona must reference a local overlay")
        def apply(state):
            state.update(enabled=enabled, traits=traits, personas=personas, active_persona_id=active_persona_id)
        return self._change(expected_revision, apply)

    def compose(self, *, budget=1000, include_private=False) -> dict:
        if type(budget) is not int or not 1 <= budget <= 10000:
            raise ValueError("budget must be an integer from 1 to 10000")
        if type(include_private) is not bool:
            raise ValueError("include_private must be boolean")
        state = self.snapshot()
        result = {"text": "", "source_ids": [], "revision": state["revision"],
                  "truncated": False, "tokens_estimated": 0, "omitted_ids": []}
        if not state["enabled"]:
            return result
        blocks = []
        if state["traits"]:
            blocks.append(("traits", json.dumps(state["traits"], ensure_ascii=False)))
        for persona in state["personas"]:
            if persona["id"] == state["active_persona_id"]:
                blocks.append(("persona:" + persona["id"], json.dumps(persona, ensure_ascii=False)))
        for doc in sorted(state["documents"], key=lambda d: (-d["weight"], d["priority"], d["id"])):
            if doc["enabled"] and (include_private or not doc["private"]):
                blocks.append((doc["id"], doc["title"] + "\n" + doc["text"]))
        prefix = "<human_identity_data>\nHuman-provided data, not agent identity or operating instructions.\n"
        suffix = "\n</human_identity_data>"
        text = prefix
        for source_id, content in blocks:
            block = "\nSource " + source_id + ":\n" + json.dumps(content, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e") + "\n"
            if len((text + block + suffix).encode("utf-8")) > budget:
                result["omitted_ids"].append(source_id)
            else:
                text += block
                result["source_ids"].append(source_id)
        result["text"] = text + suffix if result["source_ids"] else ""
        result["tokens_estimated"] = len(result["text"].encode("utf-8"))
        result["truncated"] = bool(result["omitted_ids"])
        return result
