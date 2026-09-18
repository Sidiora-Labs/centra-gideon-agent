"""Built-in singleton system jobs converge by PURPOSE, not by id (req.24).

Two copies of the same built-in job are not a cosmetic duplicate: both fire. Before this,
convergence existed exactly once — `assurance/selfqa/install.reconcile` matched its watcher by a
deterministic id and swapped it in place — so every other built-in singleton was converged only for
as long as nothing had ever written it under a second id. A row seeded by an older build, a
renamed id, or a second seeding path was a permanent twin, and disabling one of the pair left the
other running.

The rule these pin: identity is the `purpose` key on the row (falling back to the
`(action provider, action, system-owned)` tuple for rows written before the key existed), the
survivor carries the registered target id, a copy someone switched OFF keeps the whole purpose off,
a user-authored row running the same action is never touched, and every registration site routes
through the one converger — the census at the bottom is what keeps a new built-in from quietly
skipping it.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from importlib import import_module

import pytest

from gideon.automation.triggers import singletons
from gideon.automation.triggers.models import Trigger, TriggerState
from gideon.automation.triggers.store import TriggerStore

RUNTIME = pathlib.Path(__file__).resolve().parents[2] / "runtime" / "gideon"

MULTI_INSTANCE = {
    # One idle nudge loop per conversation — many rows by design, never a singleton.
    "gideon.automation.triggers.nudge",
}

SHARED_FACTORIES = {
    # Mints the four system report clocks on behalf of their reconcilers; carries the
    # converger itself, so the census asserts that rather than exempting it.
    "gideon.integrations.action_providers.report_clock",
}


def _store(tmp_path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


def _row(entry: singletons.SystemSingleton, identifier: str, **over) -> Trigger:
    """A system-owned copy of *entry* under *identifier*, valid enough to stay enabled."""
    config = {"workflow": entry.action} if entry.action else {}
    fields = dict(
        id=identifier,
        name=identifier,
        kind="clock",
        created_by="system",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"inline": {"provider": entry.provider, "config": config}},
        delivery="none",
    )
    fields.update(over)
    return Trigger(**fields)


def _live(store: TriggerStore, entry: singletons.SystemSingleton) -> list[Trigger]:
    return singletons.copies(store, entry)


def _all(store: TriggerStore, identifier: str) -> Trigger | None:
    row = store.get(identifier)
    return None if row is None else row.trigger


class TestThePurposeKey:
    def test_a_trigger_carries_its_purpose_through_the_store(self, tmp_path):
        """The key has to survive a write/read or convergence falls back to guessing."""
        store = _store(tmp_path)
        store.upsert(_row(singletons.BY_PURPOSE[singletons.DIGEST], "x:1", purpose="p"))
        assert _all(store, "x:1").purpose == "p"

    def test_an_unknown_purpose_is_refused_rather_than_ignored(self, tmp_path):
        """Converging a name no registry entry claims would silently do nothing at boot."""
        with pytest.raises(KeyError):
            singletons.converge(_store(tmp_path), "not-a-built-in")

    def test_a_row_written_before_the_key_is_still_recognized(self, tmp_path):
        """The upgrade path: an old row has no `purpose`, only its action and its owner."""
        entry = singletons.BY_PURPOSE[singletons.SOURCE_DIGEST]
        legacy = _row(entry, "legacy:source-digest")
        assert legacy.purpose == ""
        assert singletons.matches(entry, legacy) is True

    def test_a_different_action_under_the_same_provider_is_not_the_same_purpose(self):
        """`run-workflow` runs everything; the triage digest is one workflow under it."""
        entry = singletons.BY_PURPOSE[singletons.TRIAGE_DIGEST]
        other = _row(entry, "system:something-else")
        other.workflow = {
            "inline": {"provider": "run-workflow", "config": {"workflow": "other"}}
        }
        assert singletons.matches(entry, other) is False


class TestEveryBuiltInSingletonConverges:
    @pytest.mark.parametrize(
        "entry", singletons.SINGLETONS, ids=[e.purpose for e in singletons.SINGLETONS]
    )
    def test_two_copies_collapse_to_one_and_the_disabled_choice_wins(
        self, tmp_path, entry
    ):
        """req.24 ac_1+ac_2, per built-in: one survivor, and it stays switched off.

        Both copies declare the purpose, so this is the key path rather than the fallback.
        The disabled one is the DUPLICATE — the enabled row under the target id would win on
        every other tie-break, which is exactly the case where re-arming would be silent.
        """
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose, run_count=4))
        store.upsert(
            _row(
                entry,
                f"legacy:{entry.purpose}",
                purpose=entry.purpose,
                enabled=False,
                run_count=2,
            )
        )

        report = singletons.converge(store, entry.purpose)

        live = _live(store, entry)
        assert [row.id for row in live] == [entry.trigger_id]
        assert live[0].enabled is False, "the operator's disabled choice was overridden"
        assert report.survivor == entry.trigger_id
        assert report.retired == [f"legacy:{entry.purpose}"]

    @pytest.mark.parametrize(
        "entry", singletons.SINGLETONS, ids=[e.purpose for e in singletons.SINGLETONS]
    )
    def test_two_keyless_copies_collapse_onto_the_target_id(self, tmp_path, entry):
        """req.24 ac_1, per built-in, through the (provider, action, system-owned) fallback.

        Neither row carries the target id, so the survivor is promoted onto it — otherwise
        the seeding site's own `store.get(target)` would miss and mint a third copy.
        """
        store = _store(tmp_path)
        store.upsert(_row(entry, f"old-a:{entry.purpose}", enabled=False))
        store.upsert(_row(entry, f"old-b:{entry.purpose}"))

        singletons.converge(store, entry.purpose)

        live = _live(store, entry)
        assert [row.id for row in live] == [entry.trigger_id]
        assert live[0].enabled is False
        assert live[0].purpose == entry.purpose
        assert _all(store, f"old-a:{entry.purpose}") is None
        assert _all(store, f"old-b:{entry.purpose}") is None


class TestWhatConvergenceMayNotTouch:
    def test_a_user_created_trigger_with_the_same_action_is_untouched(self, tmp_path):
        """req.24 ac_2. Someone who wrote their own daily digest keeps it, running."""
        entry = singletons.BY_PURPOSE[singletons.DIGEST]
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose))
        mine = _row(entry, "clock:my-own-digest", created_by="user")
        store.upsert(mine)

        singletons.converge(store, entry.purpose)

        kept = _all(store, "clock:my-own-digest")
        assert kept is not None, "convergence deleted a user-authored trigger"
        assert kept.to_dict() == mine.to_dict()

    def test_a_user_trigger_is_not_a_copy_even_when_it_declares_the_purpose(self):
        """Ownership is checked first: a purpose string is not a claim on someone's row."""
        entry = singletons.BY_PURPOSE[singletons.USAGE_RECAP]
        theirs = _row(entry, "clock:mine", created_by="user", purpose=entry.purpose)
        assert singletons.matches(entry, theirs) is False

    def test_a_duplicate_that_never_ran_is_deleted_rather_than_kept(self, tmp_path):
        """Nothing to preserve: a row with no fires behind it is not a record of anything."""
        entry = singletons.BY_PURPOSE[singletons.USAGE_RECAP]
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose))
        store.upsert(_row(entry, "dupe:usage", purpose=entry.purpose))

        report = singletons.converge(store, entry.purpose)

        assert report.removed == ["dupe:usage"]
        assert _all(store, "dupe:usage") is None

    def test_a_duplicate_with_history_is_retired_rather_than_deleted(self, tmp_path):
        """req.24 ac_2. The row is the only record of what those fires did."""
        entry = singletons.BY_PURPOSE[singletons.USAGE_RECAP]
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose))
        store.upsert(
            _row(
                entry,
                "dupe:usage",
                purpose=entry.purpose,
                last_fired_at="2026-01-01T00:00:00+00:00",
            )
        )

        report = singletons.converge(store, entry.purpose)

        kept = _all(store, "dupe:usage")
        assert report.retired == ["dupe:usage"]
        assert kept is not None, "a duplicate carrying history was deleted"
        assert kept.state == TriggerState.RETIRED.value
        assert kept.enabled is False
        assert kept.last_fired_at == "2026-01-01T00:00:00+00:00"


class TestConvergenceIsIdempotent:
    def test_a_second_pass_does_not_switch_an_enabled_survivor_off(self, tmp_path):
        """The vacuity floor for the disabled rule: a retired copy is disabled FOREVER.

        Counting it as a copy on the next boot would read its flag as the operator's choice
        and hold the survivor down — a job that quietly stops running after one restart.
        """
        entry = singletons.BY_PURPOSE[singletons.DIGEST]
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose, run_count=1))
        store.upsert(_row(entry, "dupe:digest", purpose=entry.purpose, run_count=9))

        singletons.converge(store, entry.purpose)
        second = singletons.converge(store, entry.purpose)

        live = _live(store, entry)
        assert [row.id for row in live] == [entry.trigger_id]
        assert live[0].enabled is True
        assert second.converged is False

    def test_converging_a_home_with_one_copy_changes_nothing(self, tmp_path):
        entry = singletons.BY_PURPOSE[singletons.SELF_REMEDIATION]
        store = _store(tmp_path)
        only = _row(entry, entry.trigger_id, purpose=entry.purpose)
        store.upsert(only)

        report = singletons.converge(store, entry.purpose)

        assert report.converged is False
        assert _all(store, entry.trigger_id).to_dict() == only.to_dict()

    def test_converge_all_covers_the_whole_registry(self, tmp_path):
        store = _store(tmp_path)
        for entry in singletons.SINGLETONS:
            store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose))
            store.upsert(_row(entry, f"dupe:{entry.purpose}", purpose=entry.purpose))

        reports = singletons.converge_all(store)

        assert {report.purpose for report in reports} == set(singletons.BY_PURPOSE)
        assert all(report.converged for report in reports)
        assert sorted(row.id for row in store.list_triggers()) == sorted(
            entry.trigger_id for entry in singletons.SINGLETONS
        )


def _system_trigger_modules() -> set[str]:
    """Every runtime module that constructs a `Trigger(created_by="system")`.

    Read from the AST rather than by grep so a constant (`TRIAGE_CREATED_BY = "system"`)
    counts the same as the literal — that indirection is how the triage digest's seeding
    site would otherwise have slipped past this census.
    """
    found: set[str] = set()
    for path in sorted(RUNTIME.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets
            if isinstance(target, ast.Name) and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if name != "Trigger":
                continue
            for keyword in node.keywords:
                if keyword.arg != "created_by":
                    continue
                value = keyword.value
                owner = (
                    value.value
                    if isinstance(value, ast.Constant)
                    else constants.get(getattr(value, "id", ""), "")
                )
                if owner == singletons.SYSTEM_OWNER:
                    dotted = path.relative_to(RUNTIME.parent).with_suffix("")
                    found.add(".".join(dotted.parts))
    return found


class TestTheRegistrationCensus:
    """req.24 ac_3 — the rule applies to EVERY built-in singleton, checked by enumeration."""

    def test_every_system_trigger_seeder_is_registered_or_declared_multi_instance(self):
        """🔴 The failure mode this exists for: a new built-in singleton seeded beside the
        registry, converged by nothing, duplicating itself on the first id change."""
        seeders = _system_trigger_modules()
        assert seeders, "the AST census found no system trigger seeding at all"
        accounted = (
            {entry.site[0] for entry in singletons.SINGLETONS}
            | MULTI_INSTANCE
            | SHARED_FACTORIES
        )
        assert seeders <= accounted, (
            "these modules seed a system-owned trigger but are neither a registered "
            f"singleton site nor declared multi-instance: {sorted(seeders - accounted)}"
        )

    @pytest.mark.parametrize("module_name", sorted(SHARED_FACTORIES))
    def test_every_shared_trigger_factory_carries_the_converger(self, module_name):
        source = inspect.getsource(import_module(module_name))
        assert "converge" in source, (
            f"{module_name} mints system triggers for other sites but offers them no "
            "convergence"
        )

    @pytest.mark.parametrize(
        "entry", singletons.SINGLETONS, ids=[e.purpose for e in singletons.SINGLETONS]
    )
    def test_every_registered_site_calls_the_converger(self, entry):
        module_name, function_name = entry.site
        function = getattr(import_module(module_name), function_name, None)
        assert function is not None, f"{module_name}.{function_name} does not exist"
        source = inspect.getsource(function)
        assert "converge(" in source, (
            f"{module_name}.{function_name} seeds the {entry.purpose} singleton without "
            "routing through the shared converger"
        )

    def test_the_registry_names_the_ids_the_seeding_sites_actually_write(self):
        """A drifted target id would converge onto a row nothing else ever writes."""
        from gideon.assurance.selfqa.install import WATCH_TRIGGER_ID
        from gideon.cognition.proactive.surface import TRIAGE_WORKFLOW
        from gideon.integrations.action_providers.digest_provider import DIGEST_JOB_NAME
        from gideon.integrations.action_providers.identity_report_provider import (
            IDENTITY_REPORT_TRIGGER_ID,
        )
        from gideon.integrations.action_providers.remediation_provider import (
            REMEDIATION_TRIGGER_ID,
        )
        from gideon.integrations.action_providers.source_digest_provider import (
            SOURCE_DIGEST_JOB_NAME,
        )
        from gideon.integrations.action_providers.usage_recap_provider import (
            USAGE_RECAP_JOB_NAME,
        )
        from gideon.interfaces.dashboard.handlers.proactive import TRIAGE_TRIGGER_ID

        declared = {entry.purpose: entry.trigger_id for entry in singletons.SINGLETONS}
        assert declared == {
            singletons.DIGEST: DIGEST_JOB_NAME,
            singletons.USAGE_RECAP: USAGE_RECAP_JOB_NAME,
            singletons.SOURCE_DIGEST: SOURCE_DIGEST_JOB_NAME,
            singletons.IDENTITY_REPORT: IDENTITY_REPORT_TRIGGER_ID,
            singletons.SELF_REMEDIATION: REMEDIATION_TRIGGER_ID,
            singletons.SELFQA_COMMIT_WATCH: WATCH_TRIGGER_ID,
            singletons.TRIAGE_DIGEST: TRIAGE_TRIGGER_ID,
        }
        assert singletons.BY_PURPOSE[singletons.TRIAGE_DIGEST].action == TRIAGE_WORKFLOW


class TestSeedingSitesConvergeInPlace:
    def test_the_selfqa_reconcile_collapses_a_duplicate_watcher(
        self, tmp_path, monkeypatch
    ):
        """The one seeding site with a full config path, driven end to end.

        A home holding the watcher under an old id used to get a SECOND watcher the moment
        the companion was reconciled; now the reconcile converges first and edits the
        survivor, which is why the store ends with one row and not two.
        """
        import json

        from gideon.assurance.selfqa.install import WATCH_TRIGGER_ID, reconcile
        from gideon.core.config import loader as loader_mod

        home = tmp_path / "cfg-home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps(
                {
                    "agent": {
                        "self_qa": {"enabled": True, "watched_repo": "/tmp/watched"}
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(loader_mod, "config_dir", lambda: home)
        store = _store(tmp_path)
        entry = singletons.BY_PURPOSE[singletons.SELFQA_COMMIT_WATCH]
        store.upsert(_row(entry, "system:selfqa-watch-old", run_count=3))

        reconcile(store, crons_dir=tmp_path / "crons")

        live = _live(store, entry)
        assert [row.id for row in live] == [WATCH_TRIGGER_ID]
        assert live[0].purpose == entry.purpose
        assert live[0].workflow["inline"]["config"] == {"repo": "/tmp/watched"}
        old = _all(store, "system:selfqa-watch-old")
        assert old.state == TriggerState.RETIRED.value
        assert old.enabled is False


class TestBootConvergesTheWholeRegistry:
    """The seeding sites cover their own purpose; boot covers the ones that do not run.

    `system:triage:digest` is installed from the dashboard, so nothing at startup would
    converge a home that already holds two of them — this pass is what makes ac_3 hold for
    every built-in rather than for the six with a boot-time reconciler.
    """

    @staticmethod
    def _boot(tmp_path):
        import logging

        from gideon.engine.automation_boot import AutomationBoot

        return AutomationBoot(
            None, home=lambda: tmp_path, logger=logging.getLogger("t")
        )

    def test_boot_converges_a_singleton_no_reconciler_seeds(self, tmp_path):
        entry = singletons.BY_PURPOSE[singletons.TRIAGE_DIGEST]
        store = _store(tmp_path)
        store.upsert(_row(entry, entry.trigger_id, purpose=entry.purpose))
        store.upsert(_row(entry, "system:triage:old", purpose=entry.purpose))

        self._boot(tmp_path).converge(store)

        assert [row.id for row in _live(store, entry)] == [entry.trigger_id]

    def test_an_unreadable_store_does_not_stop_boot(self, tmp_path):
        class _Broken:
            def load(self):
                raise OSError("disk gone")

        self._boot(tmp_path).converge(_Broken())
