"""Selective canonical slot bundles using the existing authenticated archive envelope."""

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

from gideon.automation.workflows.project_archive import (
    ArchiveRefused,
    decrypt_archive,
    encrypt_archive,
)
from gideon.cognition import memory_slots
from gideon.workspace.capabilities.identity.continuity import SLOTS, ContinuityStore
from gideon.workspace.capabilities.identity.store import ConflictError

FORMAT = "gideon.identity.bundle.v1"
MAX_ENCODED = 65536


class BundleService:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.continuity = ContinuityStore(home)
        self.path = self.home / "capabilities/identity/bundles.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,body TEXT NOT NULL)"
            )

    @staticmethod
    def _groups(groups):
        if (
            not isinstance(groups, list)
            or not 1 <= len(groups) <= 2
            or any(not isinstance(group, str) or group not in SLOTS for group in groups)
            or len(set(groups)) != len(groups)
        ):
            raise ValueError("Select persona and/or self_notes exactly once")
        return sorted(groups)

    @staticmethod
    def _passphrase(passphrase):
        if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024:
            raise ValueError("Passphrase must contain 12..1024 characters")

    def inventory(self):
        state = self.continuity.status()
        return {
            "groups": [
                {
                    "id": slot,
                    "count": sum(not row["tombstoned"] for row in state["slots"][slot]),
                    "cap_chars": memory_slots.cap_for(slot),
                }
                for slot in SLOTS
            ],
            "exclusions": [
                "credentials",
                "provider_configuration",
                "heartbeat_policy",
                "human_identity",
                "agent_definitions",
                "avatars",
            ],
            "format": FORMAT,
        }

    def export_bundle(self, *, groups, passphrase):
        groups = self._groups(groups)
        self._passphrase(passphrase)
        state = self.continuity.status()
        data = {
            "format": FORMAT,
            "groups": {
                slot: [
                    row["text"] for row in state["slots"][slot] if not row["tombstoned"]
                ]
                for slot in groups
            },
        }
        encrypted = encrypt_archive(
            json.dumps(data, ensure_ascii=False).encode(), passphrase
        )
        return {"format": FORMAT, "ciphertext": base64.b64encode(encrypted).decode()}

    def _decode(self, bundle, passphrase):
        self._passphrase(passphrase)
        if (
            not isinstance(bundle, dict)
            or set(bundle) != {"format", "ciphertext"}
            or bundle["format"] != FORMAT
            or not isinstance(bundle["ciphertext"], str)
            or len(bundle["ciphertext"]) > MAX_ENCODED
        ):
            raise ValueError("Invalid identity bundle envelope")
        try:
            raw = decrypt_archive(
                base64.b64decode(bundle["ciphertext"], validate=True), passphrase
            )
            data = json.loads(raw)
        except (ArchiveRefused, ValueError, TypeError):
            raise ValueError("Bundle authentication or format failed") from None
        if (
            not isinstance(data, dict)
            or set(data) != {"format", "groups"}
            or data["format"] != FORMAT
            or not isinstance(data["groups"], dict)
        ):
            raise ValueError("Invalid identity bundle payload")
        self._groups(list(data["groups"]))
        for slot, lines in data["groups"].items():
            if (
                not isinstance(lines, list)
                or len(lines) > 500
                or any(
                    not isinstance(text, str)
                    or not text.strip()
                    or text != text.strip()
                    or len(text) > memory_slots.cap_for(slot)
                    for text in lines
                )
                or len(set(lines)) != len(lines)
            ):
                raise ValueError("Invalid bundle continuity lines")
            if sum(map(len, lines)) + max(0, len(lines) - 1) > memory_slots.cap_for(
                slot
            ):
                raise ValueError("Bundle group exceeds its canonical slot cap")
        return data

    def preview(self, *, bundle, passphrase, groups):
        selected = self._groups(groups)
        data = self._decode(bundle, passphrase)
        if any(slot not in data["groups"] for slot in selected):
            raise ValueError("Selected group is not present in this bundle")
        state = self.continuity.status()["slots"]
        token_data = [bundle, selected, {slot: state[slot] for slot in selected}]
        token = hashlib.sha256(
            json.dumps(token_data, sort_keys=True).encode()
        ).hexdigest()
        result = []
        for slot in selected:
            live = {row["text"] for row in state[slot] if not row["tombstoned"]}
            tombstones = {
                row["text"]
                for row in state[slot]
                if row["tombstoned"] and row["tombstoned_by"] == "human"
            }
            lines = data["groups"][slot]
            new = [
                text for text in lines if text not in live and text not in tombstones
            ]
            all_live = live | set(new)
            result.append(
                {
                    "id": slot,
                    "new": new,
                    "duplicates": [text for text in lines if text in live],
                    "tombstoned": [text for text in lines if text in tombstones],
                    "blocked": sum(map(len, all_live)) + max(0, len(all_live) - 1)
                    > memory_slots.cap_for(slot),
                }
            )
        return {
            "preview_token": token,
            "groups": result,
            "can_apply": not any(row["blocked"] for row in result),
        }

    def apply_bundle(self, *, bundle, passphrase, groups, preview_token, request_id):
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("request_id is required")
        preview = self.preview(bundle=bundle, passphrase=passphrase, groups=groups)
        fingerprint = hashlib.sha256(
            json.dumps([bundle, sorted(groups), preview_token], sort_keys=True).encode()
        ).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT fingerprint,body FROM receipts WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError(
                        "request_id already names a different bundle application"
                    )
                return json.loads(prior[1])
            if preview["preview_token"] != preview_token:
                raise ConflictError("Destination continuity changed; preview again")
            if not preview["can_apply"]:
                raise ValueError("Selected groups exceed destination slot capacity")
            receipt = {"status": "applying", "applied": [], "skipped": [], "errors": []}
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(receipt)),
            )
        for group in preview["groups"]:
            count = 0
            try:
                for text in group["new"]:
                    result = self.continuity.append_anchor(slot=group["id"], text=text)
                    if not any(
                        row["text"] == text and not row["tombstoned"]
                        for row in result["lines"]
                    ):
                        raise ValueError("Destination changed while importing")
                    count += 1
            except Exception:
                receipt["errors"].append(
                    {
                        "slot": group["id"],
                        "error": "Canonical memory update failed; inspect destination before a new preview",
                    }
                )
            receipt["applied"].append({"slot": group["id"], "count": count})
            receipt["skipped"].append(
                {
                    "slot": group["id"],
                    "count": len(group["duplicates"]) + len(group["tombstoned"]),
                }
            )
        receipt["status"] = "partial" if receipt["errors"] else "applied"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE receipts SET body=? WHERE id=?",
                (json.dumps(receipt), request_id),
            )
        return receipt
