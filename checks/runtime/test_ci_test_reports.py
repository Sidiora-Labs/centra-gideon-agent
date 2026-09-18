"""Every CI job that runs tests must leave a structured report behind, even when it fails.

**Measured gap (2026-09-18).** ``.github/workflows/ci.yml``'s ``test-shard`` matrix ran
``pytest --no-cov -q --splits 4 --group N`` with no ``--junitxml`` and no upload step, and so
did every other test job in ``ci.yml`` and ``full.yml`` apart from the two Playwright ones.
The entire record of a failing run was the step's log — which GitHub truncates, which is
exactly what happens on the runs worth reading, and which is gone once the retention window
closes. ``grep -c junitxml .github/workflows/*.yml`` returned **0**.

The two e2e jobs already had the shape (``if: always()`` + ``actions/upload-artifact``), so
this is a pattern that existed and was not applied, not one that had to be invented.

**What these rails assert, and why it is a PROPERTY rather than a list of steps.** Pinning
"job X has an upload named Y" would go stale the day a job is added — and a new test job with
no report is precisely the regression. So the jobs are DISCOVERED: any job with a step whose
``run`` invokes a test runner is a test job, and every one of them must

1. emit a structured report — a ``--junitxml``, a junit reporter env/flag, or (for the
   harness gate, which has no test-runner output format) captured output redirected into the
   uploaded directory;
2. upload it with ``if: always()``, because a guard of ``success()`` — the default — throws
   away the artifact in the only case it was for;
3. carry an artifact name unique within its workflow, and one that names every matrix
   dimension the job has. ``actions/upload-artifact@v4+`` REJECTS a duplicate name, so a
   matrix leg whose name omits a coordinate does not merely confuse: three of the four legs
   fail to upload anything at all.

Parsed with PyYAML, which is a hard runtime dependency of this project (``pyproject.toml``
``dependencies``), so this file does not need the line-scan workaround its older siblings
``test_ci_tier_enforcement.py`` and ``test_ci_concurrency_dedupe.py`` describe — those
predate the dependency and their comments about it are stale.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = {
    "ci": REPO_ROOT / ".github" / "workflows" / "ci.yml",
    "full": REPO_ROOT / ".github" / "workflows" / "full.yml",
}

UPLOAD_ACTION = "actions/upload-artifact"

TEST_RUNNERS = re.compile(
    r"(?:\buv run pytest\b|\bpython -m pytest\b|\bpytest\b"
    r"|\bnpm run test:[a-z]+\b|\bvitest\b"
    r"|\bplaywright test\b|\bnode --test\b"
    r"|\bchecks\.harness (?:validate|scan)\b)"
)

REPORT_EVIDENCE = re.compile(
    r"(?:--junitxml|--reporter=[^\s]*junit|--test-reporter=junit|reports/)"
)

JUNIT_ENV_KEYS = (
    "VITEST_JUNIT_OUTPUT_FILE",
    "PLAYWRIGHT_JUNIT_OUTPUT_FILE",
    "PLAYWRIGHT_JUNIT_OUTPUT_NAME",
)

# The console job reaches vitest and node --test through root scripts whose report wiring
# lives in the JS configs rather than the workflow, so those two are checked at their source
# by `test_the_javascript_tiers_emit_junit` instead of by the workflow scan.
DELEGATED_RUNNERS = ("npm run test:web", "npm run test:desktop", "npm run test:mobile")


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load(WORKFLOWS[name].read_text(encoding="utf-8"))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _run_text(step: dict[str, Any]) -> str:
    run = step.get("run")
    return run if isinstance(run, str) else ""


def _matrix_keys(job: dict[str, Any]) -> list[str]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    return sorted(k for k in matrix if k not in {"include", "exclude"})


def _test_jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Jobs with at least one step that runs a test runner."""
    return {
        name: job
        for name, job in (workflow.get("jobs") or {}).items()
        if any(TEST_RUNNERS.search(_run_text(s)) for s in _steps(job))
    }


def _uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if str(s.get("uses", "")).startswith(UPLOAD_ACTION)]


def _report_uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in _uploads(job)
        if "report" in str((s.get("with") or {}).get("name", ""))
    ]


ALL_TEST_JOBS = [
    (wf, name) for wf in WORKFLOWS for name in sorted(_test_jobs(_load(wf)))
]


def test_the_workflow_scan_is_not_vacuous() -> None:
    """🪤 The floor. Every rail below iterates the discovered jobs, so a scan that found
    none — a renamed runner, a YAML shape change — would pass all of them silently.

    The named jobs are the long-standing ones; the counts stop a scan that finds only those.
    """
    ci = _test_jobs(_load("ci"))
    full = _test_jobs(_load("full"))
    assert {"test-shard", "web", "e2e-pwa", "e2e-a11y", "browse-live", "rails"} <= set(
        ci
    ), f"ci.yml test-job discovery broke: {sorted(ci)}"
    assert {"matrix-shard", "coverage", "security-corpus"} <= set(
        full
    ), f"full.yml test-job discovery broke: {sorted(full)}"
    assert len(ci) >= 8 and len(full) >= 3


@pytest.mark.parametrize("workflow,job_name", ALL_TEST_JOBS)
def test_every_test_job_uploads_a_report_after_failure(
    workflow: str, job_name: str
) -> None:
    """🔴 ac 23.1. A test job with no always()-guarded report upload is undiagnosable red."""
    job = _load(workflow)["jobs"][job_name]
    reports = _report_uploads(job)
    assert reports, (
        f"{workflow}.yml job {job_name!r} runs tests but uploads no report artifact — a "
        "failing run leaves only a log GitHub may have truncated"
    )
    for step in reports:
        guard = str(step.get("if", "")).strip()
        assert "always()" in guard, (
            f"{workflow}.yml job {job_name!r} guards its report upload with {guard!r}; "
            "without always() the artifact is discarded in the only case it is for"
        )


@pytest.mark.parametrize("workflow,job_name", ALL_TEST_JOBS)
def test_every_test_job_actually_emits_a_report(workflow: str, job_name: str) -> None:
    """An upload step is not a report. Something in the job must WRITE one.

    Accepted evidence: a ``--junitxml``/junit reporter flag, a junit output env var, or a
    redirect into the uploaded ``reports/`` directory (the harness gate, whose ``validate``
    and ``scan`` have no test-runner output format, captures its output that way).
    """
    job = _load(workflow)["jobs"][job_name]
    job_env = " ".join(str(k) for k in (job.get("env") or {}))
    evidence = [job_env]
    for step in _steps(job):
        evidence.append(_run_text(step))
        evidence.append(" ".join(str(k) for k in (step.get("env") or {})))
    blob = "\n".join(evidence)

    delegated = any(d in _run_text(s) for s in _steps(job) for d in DELEGATED_RUNNERS)
    has_flag = bool(REPORT_EVIDENCE.search(blob))
    has_env = any(key in blob for key in JUNIT_ENV_KEYS)
    assert has_flag or has_env or delegated, (
        f"{workflow}.yml job {job_name!r} uploads a report it never writes — no junit flag, "
        "no junit output variable, nothing written into reports/"
    )


def test_report_artifact_names_are_unique_within_a_workflow() -> None:
    """🔴 ac 23.2. upload-artifact v4+ REFUSES a duplicate name: colliding legs upload nothing."""
    for workflow in WORKFLOWS:
        seen: dict[str, str] = {}
        for job_name, job in _test_jobs(_load(workflow)).items():
            for step in _report_uploads(job):
                name = str(step["with"]["name"])
                assert name not in seen, (
                    f"{workflow}.yml: jobs {seen[name]!r} and {job_name!r} both upload an "
                    f"artifact named {name!r}; the second upload is rejected outright"
                )
                seen[name] = job_name
        assert seen, f"{workflow}.yml produced no report artifacts at all"


@pytest.mark.parametrize("workflow,job_name", ALL_TEST_JOBS)
def test_a_matrix_jobs_report_name_carries_every_coordinate(
    workflow: str, job_name: str
) -> None:
    """🔴 ac 23.2, the half that only matrices can break.

    One name for N legs is not N artifacts with the last one winning — it is one upload and
    N-1 hard failures. So the name must interpolate every matrix dimension: os, python
    version and shard for ``full.yml``'s matrix, the shard for ``ci.yml``'s.
    """
    job = _load(workflow)["jobs"][job_name]
    keys = _matrix_keys(job)
    if not keys:
        pytest.skip(f"{job_name} is not a matrix job")
    for step in _report_uploads(job):
        name = str(step["with"]["name"])
        missing = [k for k in keys if f"matrix.{k}" not in name]
        assert not missing, (
            f"{workflow}.yml job {job_name!r} names its report artifact {name!r}, which "
            f"does not distinguish {missing} — every leg would upload under one name and "
            "all but the first would be rejected"
        )


def test_the_python_test_steps_ask_for_junit_xml() -> None:
    """Every pytest invocation in either workflow writes JUnit XML.

    Asserted over ALL pytest steps rather than one per job: a job can run pytest twice, and
    the second command silently producing nothing is the same gap on a smaller scale.
    """
    seen = 0
    for workflow in WORKFLOWS:
        for job_name, job in (_load(workflow)["jobs"] or {}).items():
            for step in _steps(job):
                # Shell line continuations first: a flag on the next physical line is on the
                # same command, and the lint job's pytest call is written exactly that way.
                run = _run_text(step).replace("\\\n", " ")
                for line in run.splitlines():
                    if not re.search(r"\bpytest\b", line):
                        continue
                    seen += 1
                    assert "--junitxml" in line, (
                        f"{workflow}.yml job {job_name!r} runs pytest without "
                        f"--junitxml: {' '.join(line.split())[:120]}"
                    )
    assert seen >= 7, f"only {seen} pytest steps found — the scan broke"


def test_the_playwright_steps_write_junit_to_the_uploaded_directory() -> None:
    """Playwright's junit reporter prints to stdout unless it is told where to write."""
    config = (REPO_ROOT / "apps/console" / "playwright.config.ts").read_text(
        encoding="utf-8"
    )
    assert "'junit'" in config, "the playwright config declares no junit reporter"

    seen = 0
    for workflow in WORKFLOWS:
        for job_name, job in (_load(workflow)["jobs"] or {}).items():
            for step in _steps(job):
                if "playwright test" not in _run_text(step):
                    continue
                seen += 1
                env = step.get("env") or {}
                target = str(env.get("PLAYWRIGHT_JUNIT_OUTPUT_FILE", ""))
                assert target.endswith(".xml") and "reports/" in target, (
                    f"{workflow}.yml job {job_name!r} runs playwright without pointing "
                    f"PLAYWRIGHT_JUNIT_OUTPUT_FILE into reports/: {env}"
                )
    assert seen >= 5, f"only {seen} playwright steps found — the scan broke"


def test_the_javascript_tiers_emit_junit() -> None:
    """The console job's three tiers write their reports from config, not from the workflow.

    ``npm run test:web`` has to stay exactly that string (``test_ci_tier_enforcement`` pins
    it, because npm runs from the repo root), so the wiring lives where the runner reads it:
    vitest's config for the web tier, the workspace ``test`` scripts for the node tiers.
    """
    vitest = (REPO_ROOT / "apps/console" / "vitest.config.ts").read_text(
        encoding="utf-8"
    )
    assert (
        "VITEST_JUNIT_OUTPUT_FILE" in vitest and "junit" in vitest
    ), "apps/console/vitest.config.ts no longer emits a junit report when asked"

    for workspace in ("apps/desktop", "apps/mobile"):
        manifest = (REPO_ROOT / workspace / "package.json").read_text(encoding="utf-8")
        assert (
            "--test-reporter=junit" in manifest
        ), f"{workspace}/package.json's test script emits no junit report"
        assert (
            "../../reports/" in manifest
        ), f"{workspace}/package.json writes its report outside the uploaded directory"


def test_contributing_explains_how_to_retrieve_the_reports() -> None:
    """🔴 ac 23.3. An artifact nobody knows the name of is an artifact nobody downloads."""
    doc = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert (
        "test-report-" in doc
    ), "CONTRIBUTING.md does not name the artifact convention"
    assert "gh run download" in doc, "CONTRIBUTING.md does not say how to fetch one"
    assert (
        "Artifacts" in doc
    ), "CONTRIBUTING.md does not point at the run's Artifacts list"
