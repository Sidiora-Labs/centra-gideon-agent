from gideon.engine.tasks import reconcile
from gideon.engine.tasks.models import (
    HELD_STATUSES,
    STARTABLE_STATUSES,
    STATUS_PARTITION,
    TERMINAL_STATUSES,
    Task,
    TaskDependency,
    TaskStatus,
)


def task(identifier, *dependencies, status=TaskStatus.OPEN, reason=""):
    return Task(
        id=identifier,
        title=identifier,
        status=status,
        dependencies=[TaskDependency(key) for key in dependencies],
        blocked_reason_kind=reason,
    )


def test_deep_actual_task_graph_uses_iterative_walks_and_keeps_path_order():
    count = 1800
    tasks = {
        str(index): task(str(index), *([str(index - 1)] if index else []))
        for index in reversed(range(count))
    }
    assert reconcile.detect_cycle(tasks) == []
    analysis = reconcile.analyze(tasks)
    assert analysis.critical_path == [str(index) for index in range(count)]
    assert analysis.root_task_ids == ["0"]
    assert analysis.leaf_task_ids == [str(count - 1)]
    before = {key: row.to_dict() for key, row in tasks.items()}
    cycle = reconcile.would_create_cycle(tasks, "0", [str(count - 1)])
    assert cycle == ["0", *[str(index) for index in reversed(range(1, count))], "0"]
    assert before == {key: row.to_dict() for key, row in tasks.items()}


def test_manual_and_cancelled_statuses_keep_distinct_readiness_and_completion():
    tasks = {
        "a": task("a", status=TaskStatus.CANCELLED),
        "b": task("b", "a", status=TaskStatus.BLOCKED, reason="auto"),
        "c": task("c", "a", status=TaskStatus.BLOCKED, reason="manual"),
        "d": task("d", "b", "missing"),
    }
    assert [row.id for row in reconcile.reconcile_blocked_status(tasks, "a")] == [
        "b",
        "d",
    ]
    assert tasks["c"].blocked_reason_kind == "manual"
    assert tasks["d"].status == TaskStatus.BLOCKED
    assert reconcile.ready_task_ids(tasks) == ["b"]
    assert reconcile.analyze(tasks).completion_pct == 0
    assert reconcile.block_reason(tasks["d"], tasks)["blocking_task_ids"] == ["b"]
    tasks["b"].status = TaskStatus.DONE
    assert [row.id for row in reconcile.reconcile_blocked_status(tasks, "b")] == ["d"]
    assert reconcile.ready_task_ids(tasks) == ["d"]
    assert reconcile.analyze(tasks).to_dict()["completion_pct"] == 25.0


def test_ordered_cycles_duplicate_edges_and_stable_critical_ties():
    tasks = {"a": task("a"), "b": task("b", "a", "a"), "c": task("c", "a")}
    result = reconcile.analyze(tasks)
    assert result.critical_path == ["a", "b"]
    assert result.bottleneck_tasks == [{"id": "a", "dependents": 3}]
    tasks["a"].dependencies = [TaskDependency("c")]
    assert reconcile.detect_cycle(tasks) == ["a", "c", "a"]
    assert reconcile.analyze(tasks).critical_path == []
    assert reconcile.reconcile_blocked_status(tasks, "absent") == []
    assert reconcile.would_create_cycle(tasks, "absent", ["a"]) == ["a", "c", "a"]


def test_status_partition_covers_every_task_status_exactly_once():
    members = [status for group in STATUS_PARTITION for status in group]
    assert len(members) == len(set(members)), "a status sits in two partition groups"
    assert set(members) == set(TaskStatus), (
        "the startable/held/terminal partition does not cover TaskStatus: "
        f"{sorted(s.value for s in set(TaskStatus) ^ set(members))}"
    )
    assert STATUS_PARTITION == (STARTABLE_STATUSES, HELD_STATUSES, TERMINAL_STATUSES)


def test_held_work_is_never_offered_even_with_complete_dependencies():
    tasks = {
        "pre_done": task("pre_done", status=TaskStatus.DONE),
        "pre_cancelled": task("pre_cancelled", status=TaskStatus.CANCELLED),
    }
    for status in TaskStatus:
        tasks[status.value] = task(
            status.value, "pre_done", "pre_cancelled", status=status
        )
    offered = set(reconcile.ready_task_ids(tasks))
    assert offered == {status.value for status in STARTABLE_STATUSES}
    for status in HELD_STATUSES:
        assert status.value not in offered, f"{status.value} was offered as ready"
    for status in TERMINAL_STATUSES:
        assert status.value not in offered


def test_a_held_task_becomes_offerable_the_moment_it_is_unheld():
    tasks = {
        "a": task("a", status=TaskStatus.DONE),
        "b": task("b", "a", status=TaskStatus.BLOCKED, reason="manual"),
    }
    assert reconcile.ready_task_ids(tasks) == []
    tasks["b"].status, tasks["b"].blocked_reason_kind = TaskStatus.OPEN, ""
    assert reconcile.ready_task_ids(tasks) == ["b"]


def test_dependency_completion_rule_is_unchanged_by_the_partition():
    """A prerequisite counts as complete iff it is TERMINAL — held (blocked/skipped)
    prerequisites still hold their dependents, cancelled ones still release them."""
    for status in TaskStatus:
        tasks = {"pre": task("pre", status=status), "post": task("post", "pre")}
        waiting = reconcile._unfinished(tasks["post"], tasks) == ["pre"]
        assert waiting is (status not in TERMINAL_STATUSES), status
        assert reconcile.block_reason(tasks["post"], tasks)["is_blocked"] is waiting
        assert ("post" in reconcile.ready_task_ids(tasks)) is not waiting
    tasks = {"pre": task("pre", status=TaskStatus.SKIPPED), "post": task("post", "pre")}
    assert reconcile._unfinished(tasks["post"], tasks) == ["pre"]
