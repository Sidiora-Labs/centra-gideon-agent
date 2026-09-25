"""Source-attributed model observations alongside recorded judge benchmarks."""

import json
import math
import re
from datetime import date
from urllib.parse import urlsplit
from uuid import uuid4

from gideon.assurance.evals import judge_bench
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir


class ComparisonError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _path():
    return config_dir() / "evals" / "model-comparisons.json"


def _records():
    try:
        if _path().stat().st_size > 2_000_000:
            raise ValueError("oversized")
        values = json.loads(_path().read_text())
        if not isinstance(values, list) or any(
            not isinstance(row, dict) for row in values
        ):
            raise ValueError("invalid")
        return values
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise ComparisonError("Comparison records are unreadable", 503)


def view(run_id=None):
    runs = judge_bench.list_bench_runs()[:100]
    selected = run_id or (runs[0] if runs else None)
    if selected and selected not in runs:
        raise ComparisonError("Unknown recorded benchmark", 404)
    table = judge_bench.read_table(selected) if selected else None
    rows = []
    for row in (table or {}).get("rows", []):
        rows.append(
            {
                key: row.get(key)
                for key in (
                    "rubric_class",
                    "tier",
                    "samples",
                    "agreement",
                    "cost_usd",
                    "wall_secs",
                    "scored_cells",
                    "verifier_absent",
                    "protocol_errors",
                )
            }
        )
    return {
        "version": 1,
        "imports": _records(),
        "runs": runs,
        "selected_run": selected,
        "benchmark": (
            {
                "origin": "recorded_judge_benchmark",
                "rows": rows,
                "served_model": None,
                "usage_basis": "not_recorded_in_table",
            }
            if table
            else None
        ),
    }


def add(body):
    keys = ("model", "corpus", "metric", "source", "observed_at", "methodology")
    if not isinstance(body, dict) or set(body) != {*keys, "score"}:
        raise ComparisonError(
            "Model, corpus, metric, score, source, observed date and methodology are required"
        )
    result = {}
    for key in keys:
        value = body[key]
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > (2000 if key == "methodology" else 500)
        ):
            raise ComparisonError("Observation fields must be bounded nonempty text")
        result[key] = value.strip()
    source = urlsplit(result["source"])
    if (
        source.scheme not in {"http", "https"}
        or not source.hostname
        or source.username
        or source.password
    ):
        raise ComparisonError("Source must be an HTTP link without credentials")
    try:
        date.fromisoformat(result["observed_at"])
    except ValueError:
        raise ComparisonError("Observed date must be YYYY-MM-DD")
    score = body["score"]
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        raise ComparisonError("Score must be a finite number")
    records = _records()
    if len(records) >= 1000:
        raise ComparisonError("Remove an observation before adding more", 409)
    result.update(
        id=uuid4().hex,
        score=score,
        origin="imported",
        verification="user_attributed",
        usage_basis="not_provided",
    )
    records.append(result)
    _path().parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_path(), json.dumps(records, allow_nan=False) + "\n")
    return result


def remove(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ComparisonError("Invalid observation ID")
    records = _records()
    remaining = [record for record in records if record.get("id") != identifier]
    if len(remaining) == len(records):
        raise ComparisonError("Unknown imported observation", 404)
    atomic_write(_path(), json.dumps(remaining) + "\n")
