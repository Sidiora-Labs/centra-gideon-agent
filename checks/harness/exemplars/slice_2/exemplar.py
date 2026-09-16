"""Slice 2 exemplar — engine-owned completion via the `required_artifacts` gate (WF2-R3).

A 3-node run whose middle node CLAIMS success but writes no file. The engine's artifact
gate (`apply_artifact_gate`, applied at the single dispatch seam so every node kind
inherits it) refuses the claimed completion and fails the node — so the run ends FAILED
rather than COMPLETE, and the third node never runs. This is the mechanism Slice 2 added:
completion is the engine's decision, checked against the filesystem, not the node's
self-report. "A node said it wrote the file" is a weaker claim than a passing node looks.

Runnable standalone: `python -m checks.harness.exemplars.slice_2.exemplar` (or `smoke.sh`, which
isolates GIDEON_HOME first). `main()` self-asserts and returns 0 on the expected
outcome, non-zero on a surprise — that return code is what the smoke script and the proving
test (`checks/runtime/test_harness_exemplars.py`) read.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gideon.automation.workflows import journal as J
from gideon.automation.workflows import store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import InstanceState, RunStatus, WorkflowRun

SPEC: dict[str, Any] = {
    "name": "slice2-required-artifacts",
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {
                "kind": "transform",
                "id": "seed",
                "config": {"expr": {"topic": "quarterly report"}},
            },
            {
                "kind": "infer",
                "id": "write",
                "config": {
                    "prompt": "write the report on {{nodes.seed.output.topic}} to report.md",
                    "required_artifacts": ["report.md"],
                },
            },
            {
                "kind": "transform",
                "id": "finalize",
                "config": {"expr": "published {{nodes.write.output}}"},
            },
        ],
    },
}


async def _claims_success_without_writing(
    prompt: str, *, use_case: str = "background", output_type: Any = None
) -> str:
    """The exact lie the gate exists to catch: a confident completion that produced no file."""
    return "Done — I wrote the report to report.md."


async def _run() -> tuple[str, RunStatus, dict[str, Any]]:
    run = store.create(WorkflowRun(id="", workflow_name=SPEC["name"]))
    store.write_spec(run.id, SPEC)
    services = EngineServices(
        completion=_claims_success_without_writing,
        cwd=str(store.run_dir(run.id)),
    )
    controller = RunController(run, SPEC, services=services)
    status = await controller.run_to_completion(timeout=20)
    return run.id, status, store.read_state(run.id)


def main() -> int:
    run_id, status, instances = asyncio.run(_run())

    if status is not RunStatus.FAILED:
        print(f"FAIL: expected run status FAILED, got {status}")
        return 1

    write = instances.get("root.children[1]")
    if write is None or write.state is not InstanceState.FAILED:
        print(f"FAIL: expected the `write` node FAILED, got {write and write.state}")
        return 1
    if (
        not write.failure
        or "required artifacts missing" not in write.failure.cause_plain
    ):
        print(
            f"FAIL: expected a required-artifacts failure, got {write and write.failure}"
        )
        return 1

    finalize = instances.get("root.children[2]")
    if finalize is not None and finalize.state is InstanceState.DONE:
        print("FAIL: `finalize` ran despite its predecessor failing its artifact gate")
        return 1

    failures = [r for r in J.ledger(run_id) if r["kind"] == J.STEP_FAILED]
    if not failures:
        print("FAIL: no STEP_FAILED record on the ledger")
        return 1

    print(
        "PASS slice_2: the artifact gate failed the claimed-but-unwritten node "
        f"({len(failures)} ledger failure record(s)); the run ended FAILED and `finalize` "
        "never ran."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
