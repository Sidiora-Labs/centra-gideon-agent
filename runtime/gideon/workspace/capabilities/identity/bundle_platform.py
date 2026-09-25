"""Destination-owned model selector admission for portable identity data."""
import json
from pathlib import Path


def group_available(group):
    return True


def require_model_policy(home: Path, records):
    path = Path(home) / "active_models.json"
    active = json.loads(path.read_text()) if path.exists() else {}
    refs = {ref for values in active.values() for ref in (values if isinstance(values, list) else [values]) if isinstance(ref, str)}
    if any(row["model"] not in refs for row in records):
        raise ValueError("Model policy requires selectors already bound at the destination")
