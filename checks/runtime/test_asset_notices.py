from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tooling/scripts/check_asset_notices.py"
SPEC = importlib.util.spec_from_file_location("check_asset_notices", CHECKER)
assert SPEC is not None and SPEC.loader is not None
ASSETS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ASSETS)


def test_every_checked_in_console_public_asset_has_a_valid_notice_record() -> None:
    assert ASSETS.violations() == []


def _copy_checker_inputs(root: Path) -> None:
    for rel in ("ASSET_SOURCES.json", "package-lock.json", "LICENSE", "apps/console/npm-license-records.json"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    shutil.copytree(ROOT / "apps/console/public", root / "apps/console/public")
    for name in ("format", "khroma", "measury", "rehype-katex", "remark-math", "use-composed-ref"):
        package = ROOT / "node_modules" / name
        if not package.is_dir():
            package = ROOT / "apps/console/node_modules" / name
        shutil.copytree(package, root / "node_modules" / name)


def test_changed_asset_bytes_fail_the_pinned_record(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _copy_checker_inputs(root)
    manifest = json.loads((root / "ASSET_SOURCES.json").read_text(encoding="utf-8"))
    entry = manifest["assets"][0]
    target = root / entry["path"]
    target.write_bytes(target.read_bytes() + b"changed")

    assert f"ASSET_SOURCES.json: SHA-256 mismatch for {entry['path']}" in ASSETS.violations(root)


def test_attribution_urls_stay_in_the_customer_notice(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _copy_checker_inputs(root)
    records_path = root / "apps/console/npm-license-records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["packages"]["khroma@2.1.0"] = {"license": "MIT", "note": "https://example.invalid/source"}
    records_path.write_text(json.dumps(records), encoding="utf-8")

    assert any("attribution URLs belong in the customer notice file" in item for item in ASSETS.violations(root))


def test_unbundled_license_files_require_complete_customer_notice_sections(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _copy_checker_inputs(root)
    notice_path = root / "apps/console/public/THIRD_PARTY_NOTICES.txt"
    notice = notice_path.read_text(encoding="utf-8")
    start = notice.index("NOTICE-SECTION: npm-measury-0.1.5")
    end = notice.index("NOTICE-END", start) + len("NOTICE-END")
    notice_path.write_text(notice[:start] + notice[end:], encoding="utf-8")

    assert any("measury@0.1.5 references missing notice section npm-measury-0.1.5" in item for item in ASSETS.violations(root))


def test_unrecorded_shipped_public_asset_fails_even_without_extra_inventory(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _copy_checker_inputs(root)
    asset = root / "apps/console/public/new-shipped-asset.txt"
    asset.write_text("shipped bytes", encoding="utf-8")

    assert (
        "ASSET_SOURCES.json: missing shipped public asset record for apps/console/public/new-shipped-asset.txt"
        in ASSETS.violations(root)
    )


def test_removed_shipped_asset_cannot_leave_a_stale_record(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _copy_checker_inputs(root)
    manifest = json.loads((root / "ASSET_SOURCES.json").read_text(encoding="utf-8"))
    entry = manifest["assets"][0]
    (root / entry["path"]).unlink()

    assert f"ASSET_SOURCES.json: stale public asset record for {entry['path']}" in ASSETS.violations(root)
