"""Publish proposal attention, synchronize decisions and record security events."""

from __future__ import annotations

from .proposal_queue import QueueBindings


class ProposalSignals(QueueBindings):
    def dashboard(self, *, report_missing=False):
        try:
            from gideon.integrations.inbox_providers.native_source import (
                get_dashboard_state,
            )

            return get_dashboard_state()
        except Exception:
            if report_missing:
                self.api.logger.debug(
                    "proposal inbox surface: no dashboard state", exc_info=True
                )
            return None

    def surface(self, proposal):
        try:
            self.emit(proposal)
        except Exception:
            self.api.logger.debug("proposal inbox surface failed", exc_info=True)

    def emit(self, proposal):
        from gideon.integrations.inbox import ItemKind, emit_attention_item

        state = self.dashboard(report_missing=True)
        label = self.api._KIND_LABELS.get(proposal.kind, "Proposal")
        from gideon.cognition.proposals_contract import REFS_KEY, Proposal

        review = Proposal(
            title=proposal.title or label,
            preview=proposal.body or proposal.title or "",
            preview_kind="text",
            provenance="learning",
            editable=False,
            apply={"skill_promotion": {"pid": proposal.id}},
        )
        pointers = {
            "learning_proposal": proposal.id,
            "session": proposal.session_key,
            REFS_KEY: review.to_dict(),
        }
        wire = dict(
            source="learning",
            kind="proposal",
            item_kind=ItemKind.PROPOSAL.value,
            title=label,
            body=f"{proposal.title}",
            refs=pointers,
            dedup_key=f"learning_proposal:{proposal.id}",
        )
        emit_attention_item(state, **wire)

    def resolve(self, identifier, status):
        try:
            self.synchronize(identifier, status)
        except Exception:
            self.api.logger.debug("proposal inbox resolve failed", exc_info=True)

    def synchronize(self, identifier, status):
        from gideon.integrations.inbox import InboxStore, live_store

        state = self.dashboard()
        target = live_store(state) if state is not None else None
        live = target is not None
        if not live:
            target = InboxStore()
            target.load()
        assert target is not None
        updates = 0
        for item in target.items.values():
            if item.refs.get("learning_proposal") != identifier:
                continue
            if item.status == status:
                continue
            item.status = status
            updates += 1
        if not updates:
            return
        target.save()
        if live:
            self.api.logger.debug("resolved proposal inbox item in the live store")

    def audit(self, operation, proposal, outcome):
        try:
            self.security_event(operation, proposal, outcome)
        except Exception:
            self.api.logger.debug("proposal SEL audit failed", exc_info=True)

    def security_event(self, operation, proposal, outcome):
        from gideon.security.sel import SecurityEvent, sel

        api = self.api
        ledger = sel()
        attributes = dict(
            event_id=api.os.urandom(8).hex(),
            timestamp=api._now(),
            event_type="api_access",
            caller_identity=proposal.session_key or api.os.environ.get("USER", "owner"),
            agent="gideon",
            source="dashboard",
            operation=operation,
            outcome=outcome,
            resources=f"{proposal.kind}:{proposal.id} target={proposal.target or '-'}",
            metadata={
                key: getattr(proposal, key)
                for key in ("kind", "provenance", "reinforcements", "fingerprint")
            },
        )
        ledger.log(SecurityEvent(**attributes))
