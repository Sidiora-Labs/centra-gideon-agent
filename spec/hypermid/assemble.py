"""Assemble the Hypermid specification and render only its CG projections."""

from pathlib import Path
import argparse
import json
import re
import shutil
import subprocess
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parent
ORDER = ("foundation", "fabric", "context", "memory", "surfaces", "integration", "security", "waves")


def assemble() -> str:
    parts = [ROOT / "overview.kvx"] + [ROOT / "fragments" / f"{name}.kvx" for name in ORDER]
    text = "\n\n".join(path.read_text().strip() for path in parts) + "\n"
    data = tomllib.loads(text)
    tasks = data["task"]
    sections = []
    for section in text.split("\n["):
        normalized = section if not sections else "[" + section
        if normalized.startswith("[task."):
            task_id = normalized.split("]", 1)[0][6:]
            task = tasks[task_id]
            wave = task["wave"]
            dependencies = list(task.get("requires", []))
            if task_id.startswith("wave-"):
                dependencies.extend(key for key, value in tasks.items()
                    if not key.startswith("wave-") and value["wave"] == wave)
            if wave > 1:
                dependency = f"wave-{wave - 1}-close"
                if dependency not in tasks:
                    raise ValueError(f"Missing prior wave closure: {dependency}")
                dependencies.append(dependency)
            if dependencies:
                dependencies = list(dict.fromkeys(dependencies))
                lines = normalized.splitlines()
                rewritten = False
                for index, line in enumerate(lines):
                    if line.startswith("requires ="):
                        lines[index] = "requires = " + json.dumps(dependencies)
                        rewritten = True
                        break
                if not rewritten:
                    lines.insert(1, "requires = " + json.dumps(dependencies))
                normalized = "\n".join(lines)
        sections.append(normalized)
    result = "\n".join(sections) + "\n"
    integrated = tomllib.loads(result)["task"]
    reachable = {}
    active = set()

    def ancestors(task_id):
        if task_id in active:
            raise ValueError(f"Task dependency cycle at {task_id}")
        if task_id in reachable:
            return reachable[task_id]
        active.add(task_id)
        result = set()
        for dependency in integrated[task_id].get("requires", []):
            result.add(dependency)
            result.update(ancestors(dependency))
        active.remove(task_id)
        reachable[task_id] = result
        return result

    for task_id in integrated:
        ancestors(task_id)
    blocks = re.split(r"(?=^\[task\.)", result, flags=re.M)
    for index, block in enumerate(blocks):
        match = re.match(r"\[task\.([^\]]+)\]", block)
        if not match:
            continue
        dependencies = integrated[match[1]].get("requires", [])
        minimal = [dependency for dependency in dependencies
            if not any(dependency in reachable[other] for other in dependencies if other != dependency)]
        blocks[index] = re.sub(r"^requires = .*$", "requires = " + json.dumps(minimal), block, flags=re.M)
    result = "".join(blocks)
    tomllib.loads(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    output = assemble()
    target = ROOT / "spec.kvx"
    if args.check:
        if not target.exists() or target.read_text() != output:
            raise SystemExit("spec.kvx differs from its authoring inputs")
    else:
        target.write_text(output)
    if args.render:
        with tempfile.TemporaryDirectory(prefix="hypermid-spec-") as temporary:
            isolated = Path(temporary)
            feature = isolated / "spec" / "hypermid"
            feature.mkdir(parents=True)
            shutil.copy2(ROOT.parent / "workflow.kvx", isolated / "spec" / "workflow.kvx")
            shutil.copy2(target, feature / "spec.kvx")
            subprocess.run(["cg", "spec", "render", "--root", str(isolated)], check=True)
            for name in ("requirements.md", "design.md", "tasks.md"):
                generated = feature / name
                destination = ROOT / name
                if args.check:
                    if not destination.exists() or destination.read_bytes() != generated.read_bytes():
                        raise SystemExit(f"Stale CG projection: {name}")
                else:
                    shutil.copy2(generated, destination)
    print("Hypermid specification " + ("current" if args.check else "assembled"))


if __name__ == "__main__":
    main()
