"""Validate authored plan structure; never execute a product acceptance command."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path


def validate(root: Path) -> dict:
    errors: list[str] = []
    native = root / "spec.kvx"
    raw = native.read_bytes()
    data = tomllib.loads(raw.decode())
    tasks = data.get("task", {})
    requirements = data.get("req", {})
    coverage: dict[str, list[str]] = {}
    for name, req in requirements.items():
        for field in req:
            if re.fullmatch(r"ac_\d+", field):
                coverage[f"{name}.{field[3:]}"] = []
    ancestors: dict[str, set[str]] = {}
    active: set[str] = set()

    def walk(name: str) -> set[str]:
        if name in ancestors:
            return ancestors[name]
        if name in active:
            errors.append(f"Dependency cycle through {name}")
            return set()
        active.add(name)
        found: set[str] = set()
        for dep in tasks[name].get("requires", []):
            if dep not in tasks:
                errors.append(f"{name}: unknown dependency {dep}")
                continue
            found.add(dep)
            found.update(walk(dep))
            if tasks[dep]["wave"] > tasks[name]["wave"]:
                errors.append(f"{name}: depends on later wave {dep}")
        active.remove(name)
        ancestors[name] = found
        return found

    for name, task in tasks.items():
        for field in ("title", "status", "wave", "owner_lane", "requires", "reqs", "touches", "verify_cmd"):
            if field not in task:
                errors.append(f"{name}: missing {field}")
        if task.get("status") != "pending":
            errors.append(f"{name}: authoring draft unexpectedly claims {task.get('status')}")
        if not task.get("reqs"):
            errors.append(f"{name}: no acceptance clause ownership")
        for ref in task.get("reqs", []):
            if ref not in coverage:
                errors.append(f"{name}: unknown criterion {ref}")
            else:
                coverage[ref].append(name)
        if task.get("packet_kind") == "vertical":
            for field in ("parent_task", "contract_inputs", "contract_outputs", "consumer", "stop_condition", "positive_case", "negative_case", "recovery_case"):
                if not task.get(field):
                    errors.append(f"{name}: missing vertical packet field {field}")
            if len([k for k in task if re.fullmatch(r"do_\d+", k)]) < 4:
                errors.append(f"{name}: fewer than four concrete implementation instructions")
            for path in task.get("touches", []):
                if any(c in path for c in "*?[") or path.endswith("/"):
                    errors.append(f"{name}: non-exact owned path {path}")
                if Path(path).is_absolute() or ".." in Path(path).parts:
                    errors.append(f"{name}: unsafe owned path {path}")
            parent = task.get("parent_task")
            if parent not in tasks:
                errors.append(f"{name}: unknown parent journey {parent}")
        if task.get("max_minutes", 30) > 30 or task.get("max_repair_cycles", 2) > 2:
            errors.append(f"{name}: task execution limits exceed programme policy")
        walk(name)
    for ref, owners in coverage.items():
        if not owners:
            errors.append(f"Uncovered acceptance clause {ref}")
    for name, task in tasks.items():
        if task.get("packet_kind") == "vertical":
            parent = task.get("parent_task")
            if parent in tasks and name not in ancestors.get(parent, set()):
                errors.append(f"{name}: parent journey does not require this packet")
        wave = task.get("wave", 0)
        if wave and f"wave-{wave - 1}-close" not in ancestors.get(name, set()):
            errors.append(f"{name}: lacks preceding wave barrier")
        close = f"wave-{wave}-close"
        if close in tasks and close != name and name not in ancestors.get(close, set()):
            errors.append(f"{name}: wave close does not depend on task")
    packets = [(name, task) for name, task in tasks.items() if task.get("packet_kind") == "vertical"]
    conflicts = []
    all_writers = list(tasks.items())
    for index, (left, a) in enumerate(all_writers):
        for right, b in all_writers[index + 1:]:
            if a["wave"] != b["wave"] or left in ancestors.get(right, set()) or right in ancestors.get(left, set()):
                continue
            overlap = sorted(set(a["touches"]) & set(b["touches"]))
            if overlap:
                conflicts.append({"left": left, "right": right, "paths": overlap})
                errors.append(f"Concurrent ownership collision {left}/{right}: {overlap}")
    for doc in sorted(root.glob("*.md")):
        content = doc.read_text()
        for dest in re.findall(r"\]\(([^)]+)\)", content):
            dest = dest.strip("<>").split("#", 1)[0]
            if not dest or "://" in dest or dest.startswith("mailto:"):
                continue
            dest = re.sub(r":\d+$", "", dest)
            target = (doc.parent / dest).resolve()
            if not target.exists():
                errors.append(f"{doc.name}: broken local document link {dest}")
    waves = {}
    for wave in sorted({t["wave"] for t in tasks.values()}):
        levels: dict[str, int] = {}
        remaining = {name for name, task in tasks.items() if task["wave"] == wave}
        while remaining:
            ready = [name for name in remaining if all(dep in levels or tasks[dep]["wave"] < wave for dep in tasks[name]["requires"] if dep in tasks)]
            if not ready:
                break
            for name in ready:
                levels[name] = 1 + max((levels[dep] for dep in tasks[name]["requires"] if dep in levels), default=0)
                remaining.remove(name)
        widths = {}
        for name, level in levels.items():
            if tasks[name].get("packet_kind") == "vertical":
                widths[level] = widths.get(level, 0) + 1
        waves[str(wave)] = {"tasks": len(levels), "packet_frontier_widths": widths}
    sources = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "evidence" not in path.parts and "__pycache__" not in path.parts:
            sources[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "scope": "Specification authoring only; no product build, acceptance command, provider call or runtime test executed",
        "native_spec_sha256": hashlib.sha256(raw).hexdigest(),
        "requirements": len(requirements),
        "acceptance_clauses": len(coverage),
        "tasks": len(tasks),
        "vertical_packets": len(packets),
        "runtime_scenario_status": "UNRUN",
        "coverage": coverage,
        "waves": waves,
        "unordered_write_conflicts": conflicts,
        "sources_sha256": sources,
        "errors": errors,
        "status": "pass" if not errors else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        report = validate(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Authoring validation failed: {exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "requirements", "acceptance_clauses", "tasks", "vertical_packets", "runtime_scenario_status", "errors")}, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
