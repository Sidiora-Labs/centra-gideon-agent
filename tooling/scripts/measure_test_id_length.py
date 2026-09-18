#!/usr/bin/env python3
"""Measure what the short-id hook does to a collected run's total id length (req 86.2).

The claim short ids are supposed to buy is "the unsharded job's log stops being truncated".
That is a claim about BYTES, and sharding changes the same number for an unrelated reason —
four shards each print a quarter of the ids — so the two effects have to be measurable
apart. This script measures the id side alone: one collection with the hook off, one with it
on, the same target both times, nothing sharded.

    python tooling/scripts/measure_test_id_length.py                     # the whole suite
    python tooling/scripts/measure_test_id_length.py checks/runtime/test_x.py
    python tooling/scripts/measure_test_id_length.py --json out.json checks/runtime

``--collect-only -q`` is used rather than a real run because collection is what produces the
ids; executing them would add an hour and change nothing being counted. Each leg runs in its
own subprocess with ``addopts`` cleared, so a developer's local ``-n auto`` or coverage flags
cannot move the number.

Reported per leg: how many ids were collected, their total length in characters, the mean and
the longest. A leg that collects a different NUMBER of ids than the other is reported as a
failure rather than a saving — the hook must rename ids, never add or drop one.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_VAR = "GIDEON_SHORT_TEST_IDS"


@dataclass
class Leg:
    """One collection: the ids it produced and their sizes."""

    label: str
    count: int
    total_chars: int
    mean_chars: float
    max_chars: int
    returncode: int

    @classmethod
    def of(cls, label: str, ids: list[str], returncode: int) -> "Leg":
        total = sum(len(i) for i in ids)
        return cls(
            label=label,
            count=len(ids),
            total_chars=total,
            mean_chars=round(total / len(ids), 2) if ids else 0.0,
            max_chars=max((len(i) for i in ids), default=0),
            returncode=returncode,
        )


def collect(targets: list[str], *, short: bool, timeout: int) -> tuple[list[str], int]:
    """Collect ``targets`` in a fresh process and return (node ids, returncode)."""
    env = dict(os.environ)
    env.pop(ENV_VAR, None)
    if short:
        env[ENV_VAR] = "1"
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "runtime"))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *targets,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env=env,
    )
    ids = [ln.strip() for ln in proc.stdout.splitlines() if "::" in ln]
    return ids, proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets", nargs="*", help="pytest targets (default: testpaths)"
    )
    parser.add_argument("--json", dest="json_out", help="also write the numbers here")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    before_ids, before_rc = collect(args.targets, short=False, timeout=args.timeout)
    after_ids, after_rc = collect(args.targets, short=True, timeout=args.timeout)
    before = Leg.of("before (default ids)", before_ids, before_rc)
    after = Leg.of(f"after ({ENV_VAR}=1)", after_ids, after_rc)

    width = max(len(before.label), len(after.label))
    print(f"{'leg'.ljust(width)}  {'ids':>7}  {'total':>10}  {'mean':>7}  {'max':>5}")
    for leg in (before, after):
        print(
            f"{leg.label.ljust(width)}  {leg.count:>7}  {leg.total_chars:>10}  "
            f"{leg.mean_chars:>7}  {leg.max_chars:>5}"
        )

    saved = before.total_chars - after.total_chars
    pct = (saved / before.total_chars * 100) if before.total_chars else 0.0
    print(f"\nsaved: {saved} characters ({pct:.1f}% of {before.total_chars})")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "before": asdict(before),
                    "after": asdict(after),
                    "saved_chars": saved,
                    "saved_pct": round(pct, 2),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if before.count != after.count:
        print(
            f"MISMATCH: {before.count} ids collected before, {after.count} after — the hook "
            "must rename ids, never add or drop one",
            file=sys.stderr,
        )
        return 1
    if not before.count:
        print("nothing was collected; there is no measurement here", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
