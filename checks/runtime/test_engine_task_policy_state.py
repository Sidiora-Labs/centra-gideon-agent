"""Real lifecycle and registry state, plus shell-policy boundary vectors."""

import time
from dataclasses import asdict

import pytest

from gideon.engine import session_restrictions as restrictions
from gideon.engine.task import InvalidTransition, Task, TaskState
from gideon.engine.task_modes import (
    MUTATING,
    UNCLASSIFIED,
    classify_invocation,
    extract_bash_command,
    is_read_only_bash,
    resolve_effective_risk,
    task_mode_denies,
)

_ALLOWED_EDGES = {
    ("pending", "in_progress"),
    ("pending", "cancelled"),
    ("in_progress", "awaiting_approval"),
    ("in_progress", "completed"),
    ("in_progress", "failed"),
    ("in_progress", "cancelled"),
    ("awaiting_approval", "in_progress"),
    ("awaiting_approval", "completed"),
    ("awaiting_approval", "failed"),
    ("awaiting_approval", "cancelled"),
}


@pytest.mark.parametrize("source", list(TaskState))
@pytest.mark.parametrize("destination", list(TaskState))
def test_lifecycle_accepts_only_declared_edges(source, destination):
    record = Task("message", state=source)
    before = asdict(record)
    began = time.monotonic()
    if (source.value, destination.value) not in _ALLOWED_EDGES:
        with pytest.raises(
            InvalidTransition, match=f"{source.value} -> {destination.value}"
        ):
            record.transition(destination)
        assert asdict(record) == before
        return
    record.transition(destination)
    assert record.state is destination
    if destination is TaskState.IN_PROGRESS:
        assert began <= record.started_at <= time.monotonic()
    if destination.value in ("completed", "failed", "cancelled"):
        assert record.is_terminal
        assert began <= record.finished_at <= time.monotonic()
    else:
        assert not record.is_terminal and record.finished_at is None


def test_approval_round_trip_keeps_original_start_and_records_completion():
    record = Task("approval")
    record.start()
    began = record.started_at
    record.await_approval()
    assert record.state is TaskState.AWAITING_APPROVAL
    record.resume()
    assert record.started_at == began
    record.complete()
    assert record.created_at <= began <= record.finished_at
    assert record.error is None


def test_failure_records_error_only_after_successful_transition():
    record = Task("failure")
    with pytest.raises(InvalidTransition):
        record.fail("not running")
    assert record.error is None
    record.start()
    record.await_approval()
    record.fail("permission expired")
    snapshot = asdict(record)
    with pytest.raises(InvalidTransition):
        record.fail("replacement")
    assert asdict(record) == snapshot
    assert record.error == "permission expired" and record.is_terminal


def test_pending_cancellation_has_no_start_timestamp():
    record = Task("cancel")
    record.cancel()
    assert record.is_terminal and record.started_at is None
    assert record.finished_at >= record.created_at


@pytest.fixture
def registry_state():
    before = (
        list(restrictions._temporary.items()),
        list(restrictions._incognito.items()),
        restrictions._MAX,
    )
    restrictions._temporary.clear()
    restrictions._incognito.clear()
    restrictions._MAX = 2
    try:
        yield
    finally:
        restrictions._MAX = before[2]
        restrictions._temporary.clear()
        restrictions._temporary.update(before[0])
        restrictions._incognito.clear()
        restrictions._incognito.update(before[1])


def test_refresh_eviction_and_membership_reads_have_independent_order(registry_state):
    restrictions.mark_temporary("first")
    restrictions.mark_temporary("second")
    assert restrictions.is_temporary("first")
    restrictions.mark_temporary("third")
    assert not restrictions.is_temporary("first")
    restrictions.mark_temporary("second")
    restrictions.mark_temporary("fourth")
    assert list(restrictions._temporary) == ["second", "fourth"]
    restrictions.mark_incognito("third")
    restrictions.mark_incognito("first")
    assert list(restrictions._temporary) == ["second", "fourth"]
    assert restrictions.is_incognito("first") and not restrictions.is_temporary("first")
    restrictions.mark_incognito("second")
    assert not restrictions.is_restricted("third")
    restrictions.clear("second")
    assert not restrictions.is_restricted("second")
    assert restrictions.is_temporary("fourth") and restrictions.is_incognito("first")
    restrictions.clear("missing")


@pytest.mark.parametrize(
    "command,expected",
    [
        ("", False),
        (" ls -la && git status | head -5", True),
        ("ls || pwd; git diff\nwhoami", True),
        ("cat a | sort | uniq | wc -l", True),
        ("cat a | python3 -c 'print(1)'", False),
        ("cat a | HEAD -1", False),
        ("ls\t-la", False),
        ("rm --help", True),
        ("unknown --version", True),
        ("cat a > b", False),
        ("echo $(pwd)", False),
        ("echo `pwd`", False),
        ("cat <(pwd)", False),
        ("ls & pwd", False),
        ("git status && python --version", True),
        ("ls |", True),
        ("|", False),
    ],
)
def test_shell_policy_vectors(command, expected):
    assert is_read_only_bash(command) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"command": "pwd"}, "pwd"),
        ({"command": 123}, ""),
        ('{"command":"pwd"}', "pwd"),
        ('{"command":null}', ""),
        ("not json", "not json"),
        ('["pwd"]', '["pwd"]'),
        ('"pwd"', '"pwd"'),
        (None, ""),
    ],
)
def test_raw_command_input_contract(raw, expected):
    assert extract_bash_command(raw) == expected


def test_shell_absence_and_decoy_classification_stay_separate_from_risk():
    assert classify_invocation("bash", "", {}) == UNCLASSIFIED
    assert resolve_effective_risk(None, "bash", "", {}) == "caution"
    assert task_mode_denies("ask", "bash", "", {})
    assert classify_invocation("memory_recall", "read", {"command": "pwd"}) == MUTATING
    assert task_mode_denies("plan", "memory_recall", "read", {"command": "pwd"})
    assert (
        resolve_effective_risk(
            "destructive", "memory_recall", "read", {"command": "pwd"}
        )
        == "destructive"
    )
    assert task_mode_denies("unrecognized-mode", "write_file", "edit", {}) == ""
