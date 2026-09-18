"""#2720 — the sharded test matrix, and the four state leaks deterministic sharding exposed.

Two halves, matching the requirement's two criteria.

**The matrix.** Four groups, in parallel, under ONE required check. Completeness and
disjointness are not asserted by re-deriving the partition here — they are `pytest-split`'s
contract: ``--splits N --group i`` partitions the *collected* test set into N groups, each
test in exactly one group, so every collected test runs in exactly one shard by
construction. What a rail CAN protect is the invocation that buys that guarantee: a
``--splits`` that stops matching the matrix (a fifth shard added to ``matrix.shard`` alone
would silently never run its tests, and a ``--splits 5`` against four jobs would silently
drop a fifth of the suite), the balancing algorithm, and the aggregator whose NAME is what
branch protection requires.

**The leaks.** ``conftest`` carries four autouse guards whose reason for existing is that
sharding moved a leaker and its victim onto the same worker. Each is driven here as the
generator it is — real setup, a real leak, real teardown — because a guard that stopped
restoring would be invisible: the suite would simply start failing somewhere else, in a
file with nothing to do with the leak.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import conftest as suite_conftest
import pytest

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

SHARDS = 4


def _ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


class TestTheShardMatrixCoversTheWholeSuite:
    def test_pytest_split_is_a_declared_dependency(self) -> None:
        """The partition guarantee is a library's, so the library has to be installed."""
        pyproject = (CI_YML.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        assert "pytest-split" in pyproject
        assert pytest.importorskip("pytest_split") is not None

    def test_the_matrix_declares_exactly_four_groups(self) -> None:
        matrix = re.search(r"^\s*shard:\s*\[([^\]]*)\]", _ci_text(), re.MULTILINE)
        assert matrix, "the test-shard matrix is gone"
        groups = [part.strip() for part in matrix.group(1).split(",")]
        assert groups == [str(n) for n in range(1, SHARDS + 1)], groups

    def test_the_split_count_matches_the_matrix_and_balances_by_duration(self) -> None:
        """``--splits`` disagreeing with the matrix silently drops or duplicates tests."""
        command = next(
            line
            for line in _ci_text().splitlines()
            if "--splits" in line and "pytest" in line
        )
        assert f"--splits {SHARDS}" in command, command
        assert "--group ${{ matrix.shard }}" in command, command
        assert "--splitting-algorithm least_duration" in command, command

    def test_the_shards_carry_no_coverage(self) -> None:
        """Coverage is its own job (``full.yml``'s ``coverage``), measured over the whole
        suite in one process — a per-shard fraction would report four partial numbers.
        """
        command = next(
            line
            for line in _ci_text().splitlines()
            if "--splits" in line and "pytest" in line
        )
        assert "--no-cov" in command, command
        full_yml = CI_YML.with_name("full.yml").read_text(encoding="utf-8")
        assert "\n  coverage:\n" in full_yml
        assert "--cov=gideon" in full_yml

    def test_one_stable_required_check_aggregates_the_four(self) -> None:
        """Branch protection names ``test``. Sharding must not have renamed the check to
        four matrix job names, and the aggregator must FAIL on a red shard rather than be
        skipped with it."""
        text = _ci_text()
        assert re.search(r"^  test:$", text, re.MULTILINE), "the `test` job was renamed"
        assert "needs: [test-shard]" in text
        assert "if: ${{ always() }}" in text
        assert "exit 1" in text.split("  test:", 1)[1].split("\n  web:", 1)[0]


class TestEachIsolationGuardRemovesItsLeak:
    """One test per autouse guard, driving the guard's own generator.

    ``next(gen)`` runs its setup, the body plants the exact leak the guard's docstring
    records, and exhausting the generator runs its teardown. A guard that stopped
    restoring reds HERE, next to the reason it exists.
    """

    @staticmethod
    def _drive(fixture: object):
        return fixture.__wrapped__()  # type: ignore[attr-defined]

    def test_provider_registry_entries_do_not_outlive_a_test(self) -> None:
        from gideon.integrations.llm import registry as registry_mod

        gen = self._drive(suite_conftest._restore_provider_registry)
        next(gen)
        original = registry_mod.get_default_registry()
        before = set(original._entries)
        assert "leaked-chat-provider" not in before

        original._entries["leaked-chat-provider"] = object()
        with pytest.raises(StopIteration):
            next(gen)

        assert set(registry_mod.get_default_registry()._entries) == before

    def test_a_swapped_provider_registry_singleton_is_put_back(self) -> None:
        """The sharding-specific half: a test that RESETS the singleton leaves a typeless
        registry behind, and the next test on the worker cannot resolve ``acp_agent``.
        """
        from gideon.integrations.llm import registry as registry_mod

        gen = self._drive(suite_conftest._restore_provider_registry)
        next(gen)
        original = registry_mod.get_default_registry()

        registry_mod.reset_default_registry()
        assert registry_mod.get_default_registry() is not original
        with pytest.raises(StopIteration):
            next(gen)

        assert registry_mod.get_default_registry() is original

    def test_knowledge_source_providers_do_not_outlive_a_test(self) -> None:
        from gideon.integrations.knowledge_providers import registry as kp_registry

        gen = self._drive(suite_conftest._restore_knowledge_provider_registry)
        next(gen)
        before = dict(kp_registry._providers)

        kp_registry._providers["watched-leak"] = object()
        with pytest.raises(StopIteration):
            next(gen)

        assert kp_registry._providers == before

    def test_session_restrictions_do_not_outlive_a_test(self) -> None:
        import gideon.engine.session_restrictions as sr

        gen = self._drive(suite_conftest._reset_session_restrictions)
        next(gen)
        sr.mark_incognito("k")
        sr.mark_temporary("k2")
        assert sr.is_restricted("k")

        with pytest.raises(StopIteration):
            next(gen)

        assert not sr.is_restricted("k")
        assert not sr.is_restricted("k2")
        assert not sr._incognito and not sr._temporary

    def test_a_pinned_gideon_log_level_does_not_outlive_a_test(self) -> None:
        """``cli.main`` pins ``gideon`` to WARNING and appends a file handler; neither
        restores, so a later test's own DEBUG records vanish on that worker."""
        parent = logging.getLogger("gideon")
        child = logging.getLogger("gideon.test_shard_guard")
        before_level = parent.level
        before_handlers = list(parent.handlers)

        gen = self._drive(suite_conftest._restore_gideon_logging)
        next(gen)
        parent.setLevel(logging.WARNING)
        child.setLevel(logging.ERROR)
        leaked = logging.NullHandler()
        parent.addHandler(leaked)

        with pytest.raises(StopIteration):
            next(gen)

        assert parent.level == before_level
        assert parent.handlers == before_handlers
        assert leaked not in parent.handlers
        assert child.level == logging.NOTSET
