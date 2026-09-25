from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from .store import PeopleError, fields, person_values, text

MAX_BYTES = 262144
MAX_ROWS = 500


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def csv_rows(content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
    if not reader.fieldnames or "name" not in reader.fieldnames:
        raise PeopleError("CSV requires a name column")
    if len(set(reader.fieldnames)) != len(reader.fieldnames) or set(
        reader.fieldnames
    ) - {"name", "email", "phone", "handle", "notes"}:
        raise PeopleError("CSV has duplicate or unknown columns")
    rows = []
    try:
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise PeopleError("CSV row has the wrong number of columns")
            identities = [
                {"kind": kind, "value": value.strip()}
                for kind in ("email", "phone", "handle")
                for value in row.get(kind, "").split("|")
                if value.strip()
            ]
            rows.append(
                {
                    "name": row["name"],
                    "notes": row.get("notes", ""),
                    "identities": identities,
                }
            )
            if len(rows) > MAX_ROWS:
                raise PeopleError("Import is limited to 500 contacts")
    except csv.Error:
        raise PeopleError("Malformed CSV") from None
    return rows


def unescape(value):
    return re.sub(
        r"\\([nN,;\\])", lambda m: "\n" if m[1].lower() == "n" else m[1], value
    )


def vcard_rows(content):
    lines = re.sub(r"\r?\n[ \t]", "", content).splitlines()
    rows, card = [], None
    for line in lines:
        if not line.strip():
            continue
        if ":" not in line:
            raise PeopleError("Malformed vCard property")
        header, value = line.split(":", 1)
        name = header.split(";")[0].split(".")[-1].upper()
        if name == "BEGIN" and value.upper() == "VCARD":
            if card is not None:
                raise PeopleError("Nested vCard")
            card = {"name": "", "identities": [], "notes": "", "version": ""}
        elif name == "END" and value.upper() == "VCARD":
            if card is None or card.pop("version") not in ("3.0", "4.0"):
                raise PeopleError("vCard requires version 3.0 or 4.0")
            rows.append(card)
            card = None
            if len(rows) > MAX_ROWS:
                raise PeopleError("Import is limited to 500 contacts")
        elif card is None:
            raise PeopleError("Property outside vCard")
        elif name == "VERSION":
            card["version"] = value
        elif name in ("FN", "NOTE", "EMAIL", "TEL"):
            if "ENCODING=" in header.upper():
                raise PeopleError(
                    "Encoded vCard fields are unsupported; export UTF-8 vCard"
                )
            value = unescape(value)
            if name in ("FN", "NOTE"):
                card["name" if name == "FN" else "notes"] = value
            else:
                card["identities"].append(
                    {
                        "kind": "email" if name == "EMAIL" else "phone",
                        "value": value.removeprefix("tel:") if name == "TEL" else value,
                    }
                )
    if card is not None:
        raise PeopleError("Unterminated vCard")
    return rows


def parse(data):
    fields(data, {"format", "content"})
    content = text(data.get("content"), "content", MAX_BYTES, True)
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise PeopleError("Import exceeds 256 KiB")
    kind = data.get("format")
    if kind not in ("csv", "vcard"):
        raise PeopleError("Import format must be csv or vcard")
    rows = csv_rows(content) if kind == "csv" else vcard_rows(content)
    if not rows:
        raise PeopleError("Import contains no contacts")
    return digest({"format": kind, "content": content}), rows


def preview(store, data):
    source_digest, rows = parse(data)
    people = store.people()
    result = []
    for index, raw in enumerate(rows):
        row = {
            "row_id": str(index + 1),
            "name": raw["name"],
            "candidate": None,
            "matches": [],
            "error": "",
        }
        try:
            row["candidate"] = person_values(raw)
            row["matches"] = [
                {"id": p["id"], "name": p["name"], "revision": p["revision"]}
                for p in people
                if any(i in p["identities"] for i in row["candidate"]["identities"])
            ]
        except PeopleError as exc:
            row["error"] = str(exc)
        result.append(row)
    return {"source_digest": source_digest, "rows": result}


def commit(store, data):
    fields(data, {"format", "content", "source_digest", "decisions"})
    source_digest, raw_rows = parse({k: data.get(k) for k in ("format", "content")})
    if data.get("source_digest") != source_digest:
        raise PeopleError("Source changed since preview", 409)
    decisions = data.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(raw_rows):
        raise PeopleError("One decision is required per contact")
    by_row = {}
    for choice in decisions:
        fields(choice, {"row_id", "action", "person_id", "revision"})
        row_id = choice.get("row_id")
        if (
            not isinstance(row_id, str)
            or row_id in by_row
            or row_id not in {str(i + 1) for i in range(len(raw_rows))}
        ):
            raise PeopleError("Invalid or duplicate row decision")
        if choice.get("action") not in ("create", "update", "skip"):
            raise PeopleError("Invalid import action")
        if choice["action"] != "update" and set(choice) - {"row_id", "action"}:
            raise PeopleError("Only updates accept a target and revision")
        by_row[row_id] = choice
    canonical = [by_row[str(i + 1)] for i in range(len(raw_rows))]
    decision_hash = digest(canonical)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS contact_imports (source_digest TEXT PRIMARY KEY, decision_hash TEXT NOT NULL, receipt TEXT NOT NULL)"
        )
        previous = db.execute(
            "SELECT decision_hash,receipt FROM contact_imports WHERE source_digest=?",
            (source_digest,),
        ).fetchone()
        if previous:
            if previous[0] != decision_hash:
                raise PeopleError(
                    "This source was already committed with different decisions", 409
                )
            return json.loads(previous[1]), False
        receipts, updated = [], set()
        for index, raw in enumerate(raw_rows):
            choice = by_row[str(index + 1)]
            if choice["action"] == "skip":
                receipts.append(
                    {"row_id": choice["row_id"], "action": "skip", "person_id": None}
                )
                continue
            values = person_values(raw)
            person_id = uuid4().hex
            revision = 1
            if choice["action"] == "update":
                person_id = text(choice.get("person_id"), "person_id", 64, True)
                revision = choice.get("revision")
                if type(revision) is not int or revision < 1 or person_id in updated:
                    raise PeopleError(
                        "Updates require a revision and one row per target person"
                    )
                found = db.execute(
                    "SELECT body,revision FROM people WHERE id=?", (person_id,)
                ).fetchone()
                if not found:
                    raise PeopleError("Target person not found", 404)
                if found[1] != revision:
                    raise PeopleError("Target person changed; preview again", 409)
                existing = json.loads(found[0])
                values = {
                    **existing,
                    "identities": existing["identities"]
                    + [
                        i
                        for i in values["identities"]
                        if i not in existing["identities"]
                    ],
                }
                values = person_values({k: v for k, v in values.items() if k != "id"})
                revision += 1
                updated.add(person_id)
            for body, other_id in db.execute("SELECT body,id FROM people"):
                if other_id != person_id and any(
                    i in json.loads(body)["identities"] for i in values["identities"]
                ):
                    raise PeopleError(
                        f"Contact row {index + 1} has an identity owned by another person",
                        409,
                    )
            values["id"] = person_id
            db.execute(
                "INSERT INTO people VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
                (person_id, json.dumps(values), revision),
            )
            receipts.append(
                {
                    "row_id": choice["row_id"],
                    "action": choice["action"],
                    "person_id": person_id,
                    "revision": revision,
                }
            )
        receipt = {
            "source_digest": source_digest,
            "format": data["format"],
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "rows": receipts,
            "source_rows": raw_rows,
            "decisions": canonical,
        }
        db.execute(
            "INSERT INTO contact_imports VALUES (?,?,?)",
            (source_digest, decision_hash, json.dumps(receipt)),
        )
    return receipt, True
