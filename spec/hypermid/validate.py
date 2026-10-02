"""Validate specification documents only; never invoke implementation gates."""

from pathlib import Path
import json
import sys
import tomllib
import warnings

import jsonschema


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def main() -> None:
    spec = tomllib.loads((ROOT / "spec.kvx").read_text())
    requirements = spec["req"]
    tasks = spec["task"]
    errors = []
    covered = set()
    visiting = set()
    visited = set()

    def visit(task_id):
        if task_id in visiting:
            errors.append(f"dependency cycle at {task_id}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        task = tasks[task_id]
        for dependency in task.get("requires", []):
            if dependency not in tasks:
                errors.append(f"{task_id}: missing dependency {dependency}")
                continue
            if tasks[dependency]["wave"] > task["wave"]:
                errors.append(f"{task_id}: depends on later wave {dependency}")
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id, task in tasks.items():
        for key in ("title", "status", "wave", "owner_lane", "requires", "reqs", "touches", "verify_cmd"):
            if key not in task:
                errors.append(f"{task_id}: missing {key}")
        if task.get("status") not in {"pending", "in_progress", "done", "blocked"}:
            errors.append(f"{task_id}: invalid task status")
        if task.get("status") == "done":
            for dependency in task.get("requires", []):
                if tasks.get(dependency, {}).get("status") != "done":
                    errors.append(f"{task_id}: completed before dependency {dependency}")
        for ref in task.get("reqs", []):
            requirement, clause = ref.rsplit(".", 1)
            if f"ac_{clause}" not in requirements.get(requirement, {}):
                errors.append(f"{task_id}: invalid acceptance reference {ref}")
            covered.add(ref)
        for path in task.get("touches", []):
            if Path(path).is_absolute() or ".." in Path(path).parts:
                errors.append(f"{task_id}: implementation path escapes OSS root: {path}")
        visit(task_id)

    for requirement_id, requirement in requirements.items():
        clauses = [key for key in requirement if key.startswith("ac_")]
        if len(clauses) < 2:
            errors.append(f"{requirement_id}: fewer than two acceptance clauses")
        for clause in clauses:
            reference = requirement_id + "." + clause.removeprefix("ac_")
            if reference not in covered:
                errors.append(f"unowned acceptance clause {reference}")

    capabilities = []
    for path in sorted((ROOT / "maps").glob("*.json")):
        data = json.loads(path.read_text())
        for item in data.get("capabilities", data.get("seams", [])):
            capabilities.append(item)
            for key in ("id", "requirement", "task", "acceptance", "disposition"):
                if not item.get(key):
                    errors.append(f"{path.name}: map record missing {key}")
            if item.get("requirement") not in requirements:
                errors.append(f"{path.name}: unknown requirement {item.get('requirement')}")
            if item.get("task") not in tasks:
                errors.append(f"{path.name}: unknown task {item.get('task')}")
            if "current_path" in item and not (REPO / item["current_path"]).exists():
                errors.append(f"{path.name}: existing integration path absent: {item['current_path']}")
    ids = [item["id"] for item in capabilities]
    if len(ids) != len(set(ids)):
        errors.append("duplicate capability identifiers")

    schema_paths = sorted((ROOT / "schemas").glob("*.schema.json"))
    schemas = {}
    for path in schema_paths:
        document = json.loads(path.read_text())
        jsonschema.Draft202012Validator.check_schema(document)
        schemas[document["$id"]] = document
    if len(schemas) != len(schema_paths):
        errors.append("duplicate schema identifiers")

    def reject_network(uri):
        raise ValueError(f"Unresolved schema reference; network access forbidden: {uri}")

    def references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value["$ref"]
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for uri, document in schemas.items():
            resolver = jsonschema.RefResolver(uri, document, store=schemas,
                handlers={"http": reject_network, "https": reject_network})
            for reference in references(document):
                try:
                    resolver.resolve(reference)
                except Exception as exc:
                    errors.append(f"{uri}: unresolved {reference}: {exc}")
        examples = ROOT / "examples" / "contracts.json"
        if examples.exists():
            for example in json.loads(examples.read_text())["examples"]:
                uri = example["schema"].split("#", 1)[0]
                document = schemas[uri]
                resolver = jsonschema.RefResolver(uri, document, store=schemas,
                    handlers={"http": reject_network, "https": reject_network})
                validator = jsonschema.Draft202012Validator({"$ref": example["schema"]}, resolver=resolver)
                valid = validator.is_valid(example["value"])
                if valid != example.get("valid", True):
                    errors.append(f"example does not match declared validity: {example['id']}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"kind": "document_validation", "requirements": len(requirements),
        "acceptance_clauses": len(covered), "tasks": len(tasks), "capabilities_and_seams": len(capabilities),
        "schemas": len(schemas), "runtime_tests_run": False}, indent=2))


if __name__ == "__main__":
    main()
