"""The shared-store conformance kit's own tests — the bundled stores pass, and each
deliberately-broken store or binding fails for the RIGHT named clause.

Two halves, and neither is sufficient alone:

* **Against real code.** ``TriggerStore`` (the in-tree trigger row store, TSE-4's
  ``trigger`` provider type) and ``NativeTaskProvider`` (the in-tree task store) are
  driven through the whole contract with the bundled bindings. They are real stores
  writing real files under ``tmp_path``; nothing here substitutes for them.
* **Against mutants.** A kit whose failure path is untested is the "test exercises the
  mechanism, not its use" trap: every assertion could be inverted, or silently vacuous,
  and a green suite would still say nothing. So each clause gets a violator — a real
  ``TriggerStore`` subclass, or a binding that answers one of the contract's questions
  wrongly — and the test pins which clause name the failure carries.

Imported the way an APP imports it (``gideon.sdk.store``), not by the
``gideon.assurance.testing`` path, so the export path apps depend on is the one core
exercises.
"""

from __future__ import annotations

import itertools
import pathlib
from dataclasses import replace

import pytest

from gideon.automation.triggers import registry as TREG
from gideon.automation.triggers.store import TriggerStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.sdk.store import (
    WRITE_CONFLICT_CHECKED,
    SharedStoreContractError,
    assert_shared_store_contract,
    task_provider_binding,
    trigger_store_binding,
)

OWNER = "conformance-owner"
COLLEAGUE = "conformance-colleague"


@pytest.fixture(autouse=True)
def _empty_trigger_registry():
    """The provider-store registry is process-global; a leftover store would intercept
    every upsert below and the kit would grade someone else's backend."""
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    yield
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


@pytest.fixture
def binding():
    return trigger_store_binding()


@pytest.fixture
def tasks(monkeypatch):
    """A real task store on the per-test home, with no configured owner identity.

    The kit passes both usernames in explicitly, so pinning ``current_username`` to ``""``
    only stops a developer's own configured handle from being stamped as the author of the
    row the contract needs UNATTRIBUTED.
    """
    monkeypatch.setattr("gideon.cognition.identity.current_username", lambda: "")
    return NativeTaskProvider()


class TestBundledStores:
    """The contract, end to end, against the two shared stores that ship in-tree."""

    def test_the_trigger_store_honours_the_contract(self, store, binding):
        assert (
            assert_shared_store_contract(store, binding, owner=OWNER, foreign=COLLEAGUE)
            is None
        )

    def test_the_native_task_provider_honours_the_contract(self, tasks):
        assert (
            assert_shared_store_contract(
                tasks, task_provider_binding(), owner=OWNER, foreign=COLLEAGUE
            )
            is None
        )

    def test_the_contract_is_rerunnable_against_one_live_store(self, store, binding):
        """Two runs against the same instance: the kit seeds fresh rows each time rather
        than assuming an empty store, which is what an app suite does across tests."""
        assert_shared_store_contract(store, binding, owner=OWNER, foreign=COLLEAGUE)
        assert_shared_store_contract(store, binding, owner=OWNER, foreign=COLLEAGUE)


class TestDeclaration:
    def test_an_undeclared_write_policy_is_refused(self, store, binding):
        broken = replace(binding, write_semantics="whatever-the-backend-does")
        with pytest.raises(SharedStoreContractError, match=r"\[declaration\]"):
            assert_shared_store_contract(store, broken, owner=OWNER, foreign=COLLEAGUE)

    def test_a_missing_binding_answer_is_refused(self, store, binding):
        broken = replace(binding, transfer=None)
        with pytest.raises(SharedStoreContractError, match=r"binding.transfer"):
            assert_shared_store_contract(store, broken, owner=OWNER, foreign=COLLEAGUE)

    def test_one_identity_cannot_play_both_parts(self, store, binding):
        with pytest.raises(SharedStoreContractError, match=r"\[declaration\]"):
            assert_shared_store_contract(store, binding, owner=OWNER, foreign=OWNER)


class TestOwnerScoping:
    def test_an_unfiltered_scope_fails(self, store, binding):
        """The regression this clause exists for: a store that hands the arm path every
        row it holds, foreign ones included."""
        broken = replace(
            binding, owner_scope=lambda provider, owner: binding.read_all(provider)
        )
        with pytest.raises(SharedStoreContractError, match=r"\[owner-scoping\]"):
            assert_shared_store_contract(store, broken, owner=OWNER, foreign=COLLEAGUE)

    def test_dropping_unattributed_rows_fails(self, store, binding):
        """Requiring an exact author match reads every pre-attribution row as foreign and
        empties the owner's working set on upgrade."""

        def strict(provider, owner):
            rows = binding.read_all(provider)
            return [
                row
                for row in rows
                if binding.owner_of(row).lower() == owner.strip().lower()
            ]

        with pytest.raises(SharedStoreContractError, match=r"UNATTRIBUTED"):
            assert_shared_store_contract(
                store,
                replace(binding, owner_scope=strict),
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_case_sensitive_comparison_fails(self, store, binding):
        def exact(provider, owner):
            rows = binding.read_all(provider)
            keep = owner.strip()
            return [
                row
                for row in rows
                if not keep
                or not binding.owner_of(row)
                or binding.owner_of(row) == keep
            ]

        with pytest.raises(
            SharedStoreContractError, match=r"case- or whitespace-sensitive"
        ):
            assert_shared_store_contract(
                store,
                replace(binding, owner_scope=exact),
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_listing_that_hides_foreign_rows_fails(self, store, binding):
        """The store is SHARED: its listing view must show other people's rows even though
        the owner-scoped view must not."""

        def mine_only(provider):
            return binding.owner_scope(provider, OWNER)

        with pytest.raises(SharedStoreContractError, match=r"listing view lost rows"):
            assert_shared_store_contract(
                store,
                replace(binding, read_all=mine_only),
                owner=OWNER,
                foreign=COLLEAGUE,
            )


class TestPromptFencing:
    def test_raw_foreign_content_in_the_projection_fails(self, store, binding):
        def leaky(provider, owner):
            return "\n".join(binding.text_of(row) for row in binding.read_all(provider))

        with pytest.raises(
            SharedStoreContractError, match=r"no untrusted-content fence"
        ):
            assert_shared_store_contract(
                store, replace(binding, surface=leaky), owner=OWNER, foreign=COLLEAGUE
            )

    def test_fencing_the_foreign_row_is_the_other_accepted_arm(self, store, binding):
        """Surfacing a colleague's row is allowed — surfacing it RAW is not."""
        from gideon.security.security import fence_untrusted

        def fenced(provider, owner):
            lines = []
            for row in binding.read_all(provider):
                text, author = binding.text_of(row), binding.owner_of(row)
                if author and author.strip().lower() != owner.strip().lower():
                    text = fence_untrusted(
                        text,
                        source=f"shared store: {author}",
                        source_type="shared_store",
                    )
                lines.append(text)
            return "\n".join(lines)

        assert_shared_store_contract(
            store, replace(binding, surface=fenced), owner=OWNER, foreign=COLLEAGUE
        )

    def test_a_fence_around_a_neighbouring_block_fails(self, store, binding):
        """One fence around a composed block lets a crafted row read as the note."""
        from gideon.security.security import fence_untrusted

        def misplaced(provider, owner):
            note = fence_untrusted("an unrelated note", source="elsewhere")
            body = "\n".join(binding.text_of(row) for row in binding.read_all(provider))
            return f"{note}\n{body}"

        with pytest.raises(SharedStoreContractError, match=r"sits OUTSIDE"):
            assert_shared_store_contract(
                store,
                replace(binding, surface=misplaced),
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_an_empty_projection_cannot_pass_vacuously(self, store, binding):
        """Without this guard every fencing assertion is satisfied by returning ''."""
        with pytest.raises(SharedStoreContractError, match=r"prompt-facing projection"):
            assert_shared_store_contract(
                store,
                replace(binding, surface=lambda provider, owner: ""),
                owner=OWNER,
                foreign=COLLEAGUE,
            )


class DuplicatingTriggerStore(TriggerStore):
    """A real store that APPENDS instead of replacing — one entity, two rows, one id."""

    def upsert(self, trigger):
        self.save_all([*self.list_triggers(include_broken=False), trigger])
        return trigger


class StampingTriggerStore(TriggerStore):
    """A real store that touches the row on every write, while claiming idempotency."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stamps = itertools.count(1)

    def upsert(self, trigger):
        return super().upsert(replace(trigger, last_alert_at=float(next(self._stamps))))


class VersionedTriggerStore(TriggerStore):
    """A real conflict-checked store: a write built from a stale read is refused.

    ``run_count`` carries the version, which is the shape a networked store with an etag
    has once the counter lives in the row rather than in a header.
    """

    def upsert(self, trigger):
        current = self.get(trigger.id)
        if current is not None and current.trigger.run_count > trigger.run_count:
            return None
        return super().upsert(replace(trigger, run_count=trigger.run_count + 1))


class LastWriterTriggerStore(VersionedTriggerStore):
    """Conflict-checked in its declaration, last-write-wins in its behaviour."""

    def upsert(self, trigger):
        return TriggerStore.upsert(
            self, replace(trigger, run_count=trigger.run_count + 1)
        )


class TestWriteBehaviour:
    def test_a_forked_row_fails_whatever_the_declared_policy(self, tmp_path, binding):
        with pytest.raises(SharedStoreContractError, match=r"rows with that id"):
            assert_shared_store_contract(
                DuplicatingTriggerStore(base_dir=tmp_path),
                binding,
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_store_that_mutates_on_replay_cannot_claim_idempotency(
        self, tmp_path, binding
    ):
        with pytest.raises(SharedStoreContractError, match=r"changed its content sha"):
            assert_shared_store_contract(
                StampingTriggerStore(base_dir=tmp_path),
                binding,
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_conflict_checked_store_honours_the_contract(self, tmp_path, binding):
        assert_shared_store_contract(
            VersionedTriggerStore(base_dir=tmp_path),
            replace(binding, write_semantics=WRITE_CONFLICT_CHECKED),
            owner=OWNER,
            foreign=COLLEAGUE,
        )

    def test_a_stale_write_that_wins_fails_the_conflict_checked_claim(
        self, tmp_path, binding
    ):
        with pytest.raises(SharedStoreContractError, match=r"STALE read overwrote"):
            assert_shared_store_contract(
                LastWriterTriggerStore(base_dir=tmp_path),
                replace(binding, write_semantics=WRITE_CONFLICT_CHECKED),
                owner=OWNER,
                foreign=COLLEAGUE,
            )


class TestOwnershipTransfer:
    def test_a_transfer_that_blanks_attribution_fails(self, store, binding):
        def blanking(provider, record_id, new_owner):
            loaded = provider.get(record_id)
            return provider.upsert(replace(loaded.trigger, author=""))

        with pytest.raises(SharedStoreContractError, match=r"nobody is accountable"):
            assert_shared_store_contract(
                store,
                replace(binding, transfer=blanking),
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_delete_then_recreate_transfer_fails(self, store, binding):
        def recreated(provider, record_id, new_owner):
            loaded = provider.get(record_id)
            provider.delete(record_id)
            moved = replace(loaded.trigger, id=f"{record_id}-moved", author=new_owner)
            return provider.upsert(moved)

        with pytest.raises(SharedStoreContractError, match=r"GONE from the store"):
            assert_shared_store_contract(
                store,
                replace(binding, transfer=recreated),
                owner=OWNER,
                foreign=COLLEAGUE,
            )

    def test_a_transfer_that_rewrites_siblings_fails(self, store, binding):
        def bulk(provider, record_id, new_owner):
            for row in binding.read_all(provider):
                provider.upsert(replace(row, author=new_owner))
            return provider.get(record_id).trigger

        with pytest.raises(SharedStoreContractError, match=r"rewrote other rows"):
            assert_shared_store_contract(
                store, replace(binding, transfer=bulk), owner=OWNER, foreign=COLLEAGUE
            )

    def test_a_transfer_that_never_moves_the_attribution_fails(self, store, binding):
        """A no-op transfer leaves both people counting the same row as theirs."""

        def inert(provider, record_id, new_owner):
            return provider.get(record_id).trigger

        with pytest.raises(SharedStoreContractError, match=r"\[transfer\]"):
            assert_shared_store_contract(
                store, replace(binding, transfer=inert), owner=OWNER, foreign=COLLEAGUE
            )


class TestSdkExport:
    def test_the_kit_is_reachable_on_the_app_surface(self):
        import gideon.sdk.store as surface

        for name in surface.__all__:
            assert hasattr(surface, name), name
        assert not [n for n in surface.__all__ if n.startswith("_")]

    def test_the_kit_adds_no_second_conflict_resolver(self):
        """ac_2: the contract rides the existing durability machinery. The kit imports the
        repo's content sha and owns no merge, no queue and no resolution of its own."""
        import gideon.assurance.testing.store_conformance as kit

        source = pathlib.Path(kit.__file__).read_text(encoding="utf-8")
        assert "from gideon.operations.durability.conflicts import row_sha" in source
        assert "def _resolve" not in source
        assert "ConflictQueue" not in source
