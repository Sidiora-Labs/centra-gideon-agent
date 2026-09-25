"""Versioned encrypted identity groups with explicit per-store restore receipts."""

import base64
import json
import sqlite3

from gideon.automation.workflows.project_archive import (
    ArchiveRefused,
    decrypt_archive,
    encrypt_archive,
)
from gideon.workspace.capabilities.identity.bundle_inventory import (
    GROUPS,
    BundleInventory,
    digest,
)
from gideon.workspace.capabilities.identity.bundle_platform import group_available
from gideon.workspace.capabilities.identity.bundles import FORMAT as SLOT_FORMAT
from gideon.workspace.capabilities.identity.bundles import BundleService
from gideon.workspace.capabilities.identity.continuity import SLOTS
from gideon.workspace.capabilities.identity.store import ConflictError

FORMAT = "gideon.identity.bundle.v2"
MAX_ENCODED = 8 * 1024 * 1024


class ExtendedBundleService(BundleService):
    def __init__(self, home):
        super().__init__(home)
        self.inventory_store = BundleInventory(home)

    @staticmethod
    def _selected(groups):
        if (
            not isinstance(groups, list)
            or not groups
            or len(groups) > len(SLOTS + GROUPS)
            or any(not isinstance(g, str) or g not in SLOTS + GROUPS for g in groups)
            or len(set(groups)) != len(groups)
        ):
            raise ValueError("Select known identity groups exactly once")
        return sorted(groups)

    def inventory(self):
        result = super().inventory()
        for group in GROUPS:
            available = group_available(group)
            data = self.inventory_store.snapshot(group) if available else []
            count = (
                len(data)
                if isinstance(data, list)
                else len(data.get("documents", data.get("stories", [])))
            )
            result["groups"].append(
                {
                    "id": group,
                    "count": count,
                    "cap_chars": None,
                    "available": available,
                    "unavailable": (
                        "" if available else "Unavailable under destination policy"
                    ),
                }
            )
        result.update(
            format=FORMAT,
            exclusions=[
                "credentials",
                "provider_configuration",
                "account_identity",
                "runtime_authority",
                "activity_bindings",
            ],
            max_encoded_bytes=MAX_ENCODED,
        )
        return result

    def export_bundle(self, *, groups, passphrase):
        groups = self._selected(groups)
        if all(group in SLOTS for group in groups):
            return super().export_bundle(groups=groups, passphrase=passphrase)
        self._passphrase(passphrase)
        data = {}
        for group in groups:
            if not group_available(group):
                raise ValueError("Selected group is unavailable")
            data[group] = (
                self._decode(
                    super().export_bundle(groups=[group], passphrase=passphrase),
                    passphrase,
                )["groups"][group]
                if group in SLOTS
                else self.inventory_store.snapshot(group)
            )
        for group, value in data.items():
            if group not in SLOTS:
                self.inventory_store.validate(group, value)
        payload = {"format": FORMAT, "groups": data}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        if len(raw) > MAX_ENCODED * 3 // 4 - 100:
            raise ValueError("Selected identity bundle exceeds size limit")
        return {
            "format": FORMAT,
            "ciphertext": base64.b64encode(encrypt_archive(raw, passphrase)).decode(),
        }

    def _extended(self, bundle, passphrase):
        self._passphrase(passphrase)
        if (
            not isinstance(bundle, dict)
            or set(bundle) != {"format", "ciphertext"}
            or bundle["format"] != FORMAT
            or not isinstance(bundle["ciphertext"], str)
            or len(bundle["ciphertext"]) > MAX_ENCODED
        ):
            raise ValueError("Invalid extended identity envelope")
        try:
            data = json.loads(
                decrypt_archive(
                    base64.b64decode(bundle["ciphertext"], validate=True), passphrase
                )
            )
        except (ArchiveRefused, ValueError, TypeError):
            raise ValueError("Bundle authentication or format failed") from None
        if (
            not isinstance(data, dict)
            or set(data) != {"format", "groups"}
            or data["format"] != FORMAT
            or not isinstance(data["groups"], dict)
        ):
            raise ValueError("Invalid extended identity payload")
        self._selected(list(data["groups"]))
        for group, value in data["groups"].items():
            if group not in SLOTS:
                self.inventory_store.validate(group, value)
            elif not isinstance(value, list) or any(
                not isinstance(text, str) for text in value
            ):
                raise ValueError("Invalid continuity lines")
        return data

    def _slot_envelope(self, group, value, passphrase):
        raw = json.dumps({"format": SLOT_FORMAT, "groups": {group: value}}).encode()
        return {
            "format": SLOT_FORMAT,
            "ciphertext": base64.b64encode(encrypt_archive(raw, passphrase)).decode(),
        }

    def preview(self, *, bundle, passphrase, groups):
        if isinstance(bundle, dict) and bundle.get("format") == SLOT_FORMAT:
            return super().preview(bundle=bundle, passphrase=passphrase, groups=groups)
        selected = self._selected(groups)
        data = self._extended(bundle, passphrase)
        if any(group not in data["groups"] for group in selected):
            raise ValueError("Selected group is not present in bundle")
        plans, snapshots = [], {}
        for group in selected:
            if group in SLOTS:
                slot = self._slot_envelope(group, data["groups"][group], passphrase)
                plans.append(
                    super().preview(bundle=slot, passphrase=passphrase, groups=[group])[
                        "groups"
                    ][0]
                )
                snapshots[group] = self.continuity.status()["slots"][group]
            else:
                plans.append(self.inventory_store.plan(group, data["groups"][group]))
                snapshots[group] = (
                    self.inventory_store.snapshot(group)
                    if group_available(group)
                    else None
                )
        return {
            "preview_token": digest([bundle, selected, snapshots]),
            "groups": plans,
            "can_apply": not any(row["blocked"] for row in plans),
        }

    def apply_bundle(self, *, bundle, passphrase, groups, preview_token, request_id):
        if isinstance(bundle, dict) and bundle.get("format") == SLOT_FORMAT:
            return super().apply_bundle(
                bundle=bundle,
                passphrase=passphrase,
                groups=groups,
                preview_token=preview_token,
                request_id=request_id,
            )
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("request_id is required")
        preview = self.preview(bundle=bundle, passphrase=passphrase, groups=groups)
        data = self._extended(bundle, passphrase)
        fingerprint = digest([bundle, sorted(groups), preview_token])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT fingerprint,body FROM receipts WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError(
                        "Request identifier names another bundle application"
                    )
                return json.loads(prior[1])
            if preview_token != preview["preview_token"]:
                raise ConflictError("Destination identity changed; preview again")
            if not preview["can_apply"]:
                raise ValueError(
                    "Selected identity groups have conflicts or are unavailable"
                )
            snapshots = {
                group: digest(self.inventory_store.snapshot(group))
                for group in groups
                if group not in SLOTS
            }
            receipt = {
                "status": "applying",
                "applied": [],
                "skipped": [],
                "errors": [],
                "provenance": {"payload_hash": digest(data), "groups": sorted(groups)},
            }
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(receipt)),
            )
        for plan in preview["groups"]:
            group, count = plan["id"], 0
            try:
                if group in SLOTS:
                    for text in plan["new"]:
                        self.continuity.append_anchor(slot=group, text=text)
                        count += 1
                else:
                    count = self.inventory_store.apply(
                        group, data["groups"][group], snapshots[group]
                    )
            except Exception as error:
                receipt["errors"].append({"slot": group, "error": str(error)})
            receipt["applied"].append({"slot": group, "count": count})
            receipt["skipped"].append(
                {
                    "slot": group,
                    "count": len(plan["duplicates"]) + len(plan["tombstoned"]),
                }
            )
            receipt["status"] = "partial" if receipt["errors"] else "applying"
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "UPDATE receipts SET body=? WHERE id=?",
                    (json.dumps(receipt), request_id),
                )
        receipt["status"] = "partial" if receipt["errors"] else "applied"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE receipts SET body=? WHERE id=?",
                (json.dumps(receipt), request_id),
            )
        return receipt
