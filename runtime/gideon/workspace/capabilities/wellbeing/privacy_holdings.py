"""Subject-scoped organization holdings and recorded change dispositions."""

import json
from urllib.parse import urlsplit
from uuid import uuid4

from .privacy import PrivacyStore, fields, now
from .store import MeasurementError, text


class OrgHoldingsStore(PrivacyStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS privacy_orgs(id TEXT NOT NULL,revision INTEGER NOT NULL,subject_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS privacy_holdings(id TEXT NOT NULL,revision INTEGER NOT NULL,org_id TEXT NOT NULL,fact_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS privacy_changes(id TEXT PRIMARY KEY,subject_id TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS privacy_change_targets(change_id TEXT NOT NULL,org_id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(change_id,org_id,revision));"""
            )

    def _org(self, db, identity):
        row = db.execute(
            "SELECT data FROM privacy_orgs WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if row is None:
            raise MeasurementError("Privacy organization not found", 404, "not_found")
        return json.loads(row[0])

    def _holdings(self, db, org=None, fact=None):
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT h.data FROM privacy_holdings h WHERE h.revision=(SELECT MAX(s.revision) FROM privacy_holdings s WHERE s.id=h.id) AND (? IS NULL OR h.org_id=?) AND (? IS NULL OR h.fact_id=?) ORDER BY h.rowid",
                (org, org, fact, fact),
            )
        ]

    def _append_holding(self, db, row):
        db.execute(
            "INSERT INTO privacy_holdings VALUES(?,?,?,?,?)",
            (
                row["id"],
                row["revision"],
                row["org_id"],
                row["fact_id"],
                json.dumps(row),
            ),
        )

    def _fact_at(self, db, identity, revision):
        if type(revision) is not int or revision < 1:
            raise MeasurementError("Fact revision must be a positive integer")
        row = db.execute(
            "SELECT data FROM privacy_facts WHERE id=? AND revision=?",
            (identity, revision),
        ).fetchone()
        if row is None:
            raise MeasurementError("Private fact revision not found", 404, "not_found")
        return json.loads(row[0])

    def _mutate(self, kind, identity, payload, execute):
        text(payload.get("request_id"), "request_id", 128)
        fingerprint = dict(identity=identity, payload=payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._prior(db, payload["request_id"], kind, fingerprint)
            if prior:
                return prior
            result = execute(db)
            return self._receipt(db, payload["request_id"], kind, fingerprint, result)

    def create_org(self, subject, payload):
        fields(
            payload, ("request_id", "name", "category", "website", "contact", "source")
        )
        return self._org_write(subject, None, payload)

    def update_org(self, identity, payload):
        fields(
            payload,
            ("request_id", "revision"),
            ("name", "category", "website", "contact", "archived"),
        )
        return self._org_write(None, identity, payload)

    def _org_write(self, subject, identity, payload):
        def execute(db):
            row = (
                self._org(db, identity)
                if identity
                else dict(
                    id=str(uuid4()),
                    subject_id=subject,
                    revision=0,
                    source=payload["source"],
                    archived=False,
                    created_at=now(),
                )
            )
            self._require(db, row["subject_id"], "vault")
            if identity and (
                type(payload["revision"]) is not int
                or payload["revision"] != row["revision"]
            ):
                raise MeasurementError("Organization changed; reload", 409, "conflict")
            row.update(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in ("request_id", "revision")
                }
            )
            row.update(revision=row["revision"] + 1, updated_at=now())
            for key, limit in (
                ("name", 200),
                ("category", 100),
                ("source", 256),
                ("contact", 256),
                ("website", 1000),
            ):
                text(row[key], key, limit, key in ("website", "contact"))
            if row["website"]:
                address = urlsplit(row["website"])
                if (
                    address.scheme not in ("http", "https")
                    or not address.hostname
                    or address.username
                    or address.password
                ):
                    raise MeasurementError(
                        "Organization website must be an HTTP(S) address without credentials"
                    )
            if type(row["archived"]) is not bool:
                raise MeasurementError("Archived must be a boolean")
            db.execute(
                "INSERT INTO privacy_orgs VALUES(?,?,?,?)",
                (row["id"], row["revision"], row["subject_id"], json.dumps(row)),
            )
            self._audit(
                db,
                row["subject_id"],
                "organization_recorded",
                org_id=row["id"],
                revision=row["revision"],
            )
            return row

        return self._mutate("privacy-org", identity or subject, payload, execute)

    def get_org(self, identity):
        with self.connection() as db:
            return self._org(db, identity)

    def list_orgs(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT o.data FROM privacy_orgs o WHERE subject_id=? AND o.revision=(SELECT MAX(s.revision) FROM privacy_orgs s WHERE s.id=o.id) ORDER BY o.rowid",
                    (subject,),
                )
            ]

    def history_org(self, identity):
        with self.connection() as db:
            self._org(db, identity)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM privacy_orgs WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]

    def set_holding(self, org, payload):
        fields(
            payload,
            ("request_id", "revision", "fact_id", "fact_revision", "status", "source"),
        )

        def execute(db):
            owner = self._org(db, org)
            self._require(db, owner["subject_id"], "vault")
            record = self._fact_at(db, payload["fact_id"], payload["fact_revision"])
            if (
                owner["archived"]
                or record["archived"]
                or record["subject_id"] != owner["subject_id"]
            ):
                raise MeasurementError(
                    "Holding requires active organization and matching subject fact",
                    409,
                    "conflict",
                )
            prior = self._holdings(db, org, payload["fact_id"])
            row = (
                prior[0]
                if prior
                else dict(
                    id=str(uuid4()),
                    revision=0,
                    org_id=org,
                    subject_id=owner["subject_id"],
                    fact_id=record["id"],
                    created_at=now(),
                )
            )
            if row.get("status") == "update_pending":
                raise MeasurementError(
                    "Resolve the pending change before editing this holding",
                    409,
                    "conflict",
                )
            if (
                type(payload["revision"]) is not int
                or payload["revision"] != row["revision"]
            ):
                raise MeasurementError("Holding changed; reload", 409, "conflict")
            if payload["status"] not in ("held", "unknown", "removed"):
                raise MeasurementError("Invalid holding status")
            text(payload["source"], "source", 256)
            row.update(
                revision=row["revision"] + 1,
                fact_revision=payload["fact_revision"],
                status=payload["status"],
                source=payload["source"],
                updated_at=now(),
                change_id=None,
            )
            self._append_holding(db, row)
            self._audit(
                db,
                row["subject_id"],
                "holding_recorded",
                holding_id=row["id"],
                revision=row["revision"],
            )
            return row

        return self._mutate("privacy-holding", org, payload, execute)

    def list_holdings(self, org):
        with self.connection() as db:
            self._org(db, org)
            return self._holdings(db, org)

    def history_holding(self, identity):
        with self.connection() as db:
            rows = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM privacy_holdings WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]
            if not rows:
                raise MeasurementError("Holding not found", 404, "not_found")
            return rows

    def _change(self, db, identity):
        row = db.execute(
            "SELECT data FROM privacy_changes WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise MeasurementError("Privacy change not found", 404, "not_found")
        result = json.loads(row[0])
        targets = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT t.data FROM privacy_change_targets t WHERE change_id=? AND t.revision=(SELECT MAX(s.revision) FROM privacy_change_targets s WHERE s.change_id=t.change_id AND s.org_id=t.org_id) ORDER BY t.org_id",
                (identity,),
            )
        ]
        progress = {
            state: sum(target["status"] == state for target in targets)
            for state in ("pending", "updated", "removed")
        }
        return dict(
            result, targets=targets, progress=dict(progress, total=len(targets))
        )

    def declare_change(self, subject, payload):
        fields(
            payload, ("request_id", "fact_id", "from_revision", "to_revision", "source")
        )

        def execute(db):
            self._require(db, subject, "vault")
            old = self._fact_at(db, payload["fact_id"], payload["from_revision"])
            new = self._fact_at(db, payload["fact_id"], payload["to_revision"])
            latest, _ = self._fact(db, payload["fact_id"])
            if (
                old["subject_id"] != subject
                or new["archived"]
                or new["revision"] != latest["revision"]
                or old["revision"] >= new["revision"]
            ):
                raise MeasurementError(
                    "Change requires an older and current active revision of the subject fact",
                    409,
                    "conflict",
                )
            text(payload["source"], "source", 256)
            holdings = self._holdings(db, fact=payload["fact_id"])
            if any(row["status"] == "update_pending" for row in holdings):
                raise MeasurementError(
                    "A change is already pending for this fact", 409, "conflict"
                )
            row = dict(
                id=str(uuid4()),
                subject_id=subject,
                fact_id=old["id"],
                from_revision=old["revision"],
                to_revision=new["revision"],
                source=payload["source"],
                created_at=now(),
            )
            db.execute(
                "INSERT INTO privacy_changes VALUES(?,?,?)",
                (row["id"], subject, json.dumps(row)),
            )
            for holding in holdings:
                if (
                    holding["status"] not in ("held", "unknown")
                    or holding["fact_revision"] > old["revision"]
                    or self._org(db, holding["org_id"])["archived"]
                ):
                    continue
                holding.update(
                    revision=holding["revision"] + 1,
                    status="update_pending",
                    change_id=row["id"],
                    updated_at=now(),
                )
                self._append_holding(db, holding)
                target = dict(
                    change_id=row["id"],
                    org_id=holding["org_id"],
                    revision=1,
                    status="pending",
                    evidence="",
                    attestation="user_reported",
                    updated_at=now(),
                )
                db.execute(
                    "INSERT INTO privacy_change_targets VALUES(?,?,?,?)",
                    (row["id"], holding["org_id"], 1, json.dumps(target)),
                )
            self._audit(
                db,
                subject,
                "change_declared",
                change_id=row["id"],
                fact_id=old["id"],
                from_revision=old["revision"],
                to_revision=new["revision"],
            )
            return self._change(db, row["id"])

        return self._mutate("privacy-change", subject, payload, execute)

    def get_change(self, identity):
        with self.connection() as db:
            return self._change(db, identity)

    def list_changes(self, subject):
        with self.connection() as db:
            self._subject(db, subject)
            return [
                self._change(db, row[0])
                for row in db.execute(
                    "SELECT id FROM privacy_changes WHERE subject_id=? ORDER BY rowid",
                    (subject,),
                ).fetchall()
            ]

    def history_target(self, identity, org):
        with self.connection() as db:
            change = self._change(db, identity)
            if not any(row["org_id"] == org for row in change["targets"]):
                raise MeasurementError(
                    "Organization is not part of this change", 404, "not_found"
                )
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM privacy_change_targets WHERE change_id=? AND org_id=? ORDER BY revision",
                    (identity, org),
                )
            ]

    def settle(self, identity, org, payload):
        fields(payload, ("request_id", "revision", "status", "evidence"))

        def execute(db):
            change = self._change(db, identity)
            self._require(db, change["subject_id"], "vault")
            target = next(
                (row for row in change["targets"] if row["org_id"] == org), None
            )
            if target is None:
                raise MeasurementError(
                    "Organization is not part of this change", 404, "not_found"
                )
            if (
                type(payload["revision"]) is not int
                or payload["revision"] != target["revision"]
                or target["status"] != "pending"
            ):
                raise MeasurementError(
                    "Change disposition is no longer pending; reload", 409, "conflict"
                )
            if payload["status"] not in ("updated", "removed"):
                raise MeasurementError("Disposition must be updated or removed")
            text(payload["evidence"], "evidence", 2000)
            holding = self._holdings(db, org, change["fact_id"])[0]
            if holding["change_id"] != identity:
                raise MeasurementError(
                    "Holding no longer matches this change", 409, "conflict"
                )
            target.update(
                revision=target["revision"] + 1,
                status=payload["status"],
                evidence=payload["evidence"],
                updated_at=now(),
            )
            holding.update(
                revision=holding["revision"] + 1,
                status="held" if target["status"] == "updated" else "removed",
                fact_revision=(
                    change["to_revision"]
                    if target["status"] == "updated"
                    else holding["fact_revision"]
                ),
                change_id=None,
                updated_at=now(),
            )
            db.execute(
                "INSERT INTO privacy_change_targets VALUES(?,?,?,?)",
                (identity, org, target["revision"], json.dumps(target)),
            )
            self._append_holding(db, holding)
            self._audit(
                db,
                change["subject_id"],
                "change_disposition",
                change_id=identity,
                org_id=org,
                status=target["status"],
                attestation="user_reported",
            )
            return self._change(db, identity)

        return self._mutate("privacy-settle", [identity, org], payload, execute)
