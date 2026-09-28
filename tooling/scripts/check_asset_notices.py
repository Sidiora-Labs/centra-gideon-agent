#!/usr/bin/env python3
"""Read-only checks for shipped console asset and customer notice records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "apps/console/public/THIRD_PARTY_NOTICES.txt"
RECORDS = "apps/console/npm-license-records.json"
ASSET_FIELDS = {"id", "path", "source", "version", "sha256", "license", "notice"}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected an object")
    return value


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(key)
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    else:
        yield value


def _license_of(package: dict[str, Any]) -> str | None:
    declared = package.get("license")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    licenses = package.get("licenses")
    if isinstance(licenses, list):
        values = [entry.get("type", "").strip() for entry in licenses if isinstance(entry, dict)]
        values = [value for value in values if value]
        if len(values) == 1:
            return values[0]
        if len(values) > 1:
            return f"({' OR '.join(values)})"
    return None


def violations(root: Path = ROOT) -> list[str]:
    """Return missing, stale, unsafe, or changed asset/dependency notice records."""
    root = root.resolve()
    problems: list[str] = []
    try:
        manifest = _json(root / "ASSET_SOURCES.json")
        notice = (root / NOTICE).read_text(encoding="utf-8")
    except (ValueError, OSError) as exc:
        return [str(exc)]
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("assets"), list):
        return ["ASSET_SOURCES.json: expected schema_version 1 and an assets array"]
    exclusions = manifest.get("generated_exclusions")
    expected_exclusions = {
        "apps/console/public/THIRD_PARTY_NOTICES.txt",
        "apps/console/dist/assets/*",
        "apps/console/dist/sw.js",
        "apps/console/dist/THIRD_PARTY_NOTICES.txt",
    }
    if not isinstance(exclusions, list):
        problems.append("ASSET_SOURCES.json: generated_exclusions must be an array")
    else:
        found = set()
        for entry in exclusions:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ("path", "source", "reason")):
                problems.append("ASSET_SOURCES.json: generated exclusions need path, source, and reason")
                continue
            found.add(entry["path"])
        if found != expected_exclusions:
            problems.append("ASSET_SOURCES.json: generated exclusions do not match the console build outputs")

    records_by_path: dict[str, dict[str, Any]] = {}
    for entry in manifest["assets"]:
        if not isinstance(entry, dict):
            problems.append("ASSET_SOURCES.json: each asset record must be an object")
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            problems.append(f"ASSET_SOURCES.json: unsafe product-relative asset path {path!r}")
            continue
        if path in records_by_path:
            problems.append(f"ASSET_SOURCES.json: duplicate asset record for {path}")
        records_by_path[path] = entry
        if set(entry) != ASSET_FIELDS:
            problems.append(f"ASSET_SOURCES.json: {path} must have exactly the declared asset fields")
        if not all(isinstance(entry.get(field), str) and entry[field].strip() for field in ASSET_FIELDS):
            problems.append(f"ASSET_SOURCES.json: {path} needs non-empty asset metadata")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            problems.append(f"ASSET_SOURCES.json: {path} needs a lowercase SHA-256 digest")
        else:
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root)
                actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except (OSError, ValueError):
                problems.append(f"ASSET_SOURCES.json: listed asset is missing or unsafe: {path}")
            else:
                if actual != digest:
                    problems.append(f"ASSET_SOURCES.json: SHA-256 mismatch for {path}")
        ref = entry.get("notice")
        if isinstance(ref, str) and ref.startswith(NOTICE + "#"):
            slug = ref.removeprefix(NOTICE + "#")
            if f"NOTICE-SECTION: {slug}" not in notice and f"Path: {path}" not in notice:
                problems.append(f"{NOTICE}: {path} references missing notice section {slug}")
        elif isinstance(ref, str) and ref == "LICENSE":
            if not (root / "LICENSE").is_file():
                problems.append(f"ASSET_SOURCES.json: {path} references a missing product LICENSE")
        else:
            problems.append(f"ASSET_SOURCES.json: {path} needs a customer notice or product license reference")

    if any(isinstance(value, str) and re.match(r"https?://", value) for value in _walk(manifest)):
        problems.append("ASSET_SOURCES.json: attribution URLs belong in the customer notice file")
    if str(root) in notice:
        problems.append(f"{NOTICE}: contains a machine-local path")

    public_root = root / "apps/console/public"
    shipped = {
        f"apps/console/public/{path.relative_to(public_root).as_posix()}"
        for path in public_root.rglob("*")
        if path.is_file() and path.name != "THIRD_PARTY_NOTICES.txt"
    }
    recorded_paths = set(records_by_path)
    for path in shipped - recorded_paths:
        problems.append(f"ASSET_SOURCES.json: missing shipped public asset record for {path}")
    for path in recorded_paths - shipped:
        if path.startswith("apps/console/public/"):
            problems.append(f"ASSET_SOURCES.json: stale public asset record for {path}")

    try:
        lock = _json(root / "package-lock.json").get("packages", {})
        npm_records = _json(root / RECORDS).get("packages", {})
    except ValueError as exc:
        return problems + [str(exc)]
    if not isinstance(lock, dict) or not isinstance(npm_records, dict):
        return problems + ["package-lock.json or npm-license-records.json has an invalid packages map"]
    used: set[str] = set()
    for lock_path, locked in lock.items():
        if not isinstance(lock_path, str) or "node_modules/" not in lock_path or not isinstance(locked, dict) or locked.get("link"):
            continue
        package_file = root / lock_path / "package.json"
        if not package_file.is_file():
            continue
        try:
            package = _json(package_file)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if package.get("version") != locked.get("version") or not isinstance(package.get("name"), str):
            continue
        label = f"{package['name']}@{package['version']}"
        record = npm_records.get(label)
        license_id = _license_of(package)
        if not record:
            continue
        used.add(label)
        if any(isinstance(value, str) and re.match(r"https?://", value) for value in _walk(record)):
            problems.append(f"{RECORDS}: attribution URLs belong in the customer notice file")
        if not isinstance(record, dict) or set(record) - {"license", "notice", "note"}:
            problems.append(f"{RECORDS}: {label} has unsupported fields")
            continue
        if license_id and record.get("license"):
            problems.append(f"{RECORDS}: {label} already declares a license in package metadata")
        if not license_id and (not isinstance(record.get("license"), str) or not record["license"].strip()):
            problems.append(f"{RECORDS}: {label} needs a license identifier")
        if not isinstance(record.get("note"), str) or not record["note"].strip():
            problems.append(f"{RECORDS}: {label} needs a review note")
        has_license_file = any(
            p.is_file() and re.match(r"^(?:license|licence|copying|unlicense|notice)(?:[._-].*)?$", p.name, re.I)
            for p in package_file.parent.iterdir()
        )
        if not has_license_file:
            section = record.get("notice")
            if not isinstance(section, str) or not section.strip():
                problems.append(f"{RECORDS}: {label} has no installed license file or customer notice section")
            else:
                try:
                    notice_text = (root / NOTICE).read_text(encoding="utf-8")
                except OSError:
                    notice_text = ""
                escaped = re.escape(section)
                match = re.search(rf"^NOTICE-SECTION: {escaped}\r?\n([\s\S]*?)^NOTICE-END$", notice_text, re.M)
                if not match:
                    problems.append(f"{RECORDS}: {label} references missing notice section {section}")
                else:
                    section_text = match.group(1)
                    declared = license_id or record.get("license")
                    required_terms = (f"Package: {label}", f"License: {declared}", "Permission is hereby granted", "THE SOFTWARE IS PROVIDED")
                    if any(term not in section_text for term in required_terms):
                        problems.append(f"{NOTICE}: {label} notice section is missing package, license, or full license terms")
    for label in npm_records:
        if label not in used:
            problems.append(f"{RECORDS}: {label} is stale or no longer matches an installed package")

    return sorted(problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    problems = violations(args.root)
    if problems:
        print("\n".join(problems))
        return 1
    print("shipped console assets and notice records are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
