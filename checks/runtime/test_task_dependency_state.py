from gideon.engine.tasks import reconcile
from gideon.engine.tasks.models import Task, TaskDependency, TaskStatus


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
    assert reconcile.ready_task_ids(tasks) == ["b", "c"]
    assert reconcile.analyze(tasks).completion_pct == 0
    assert reconcile.block_reason(tasks["d"], tasks)["blocking_task_ids"] == ["b"]
    tasks["b"].status = TaskStatus.DONE
    assert [row.id for row in reconcile.reconcile_blocked_status(tasks, "b")] == ["d"]
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
