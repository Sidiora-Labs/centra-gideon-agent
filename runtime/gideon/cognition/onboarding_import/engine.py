"""Scan → select → import. The orchestration a UI or CLI drives.

Two phases on purpose, mirroring the pack importer's inspect/commit split: a scan
writes nothing to our home and reads nothing from the foreign root twice, so the
onboarding step can show counts and let the user pick categories before a single
byte lands.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from gideon.cognition.onboarding_import.import_projection import SelectionPlan
from gideon.cognition.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    ScanResult,
    Plan,
)
from gideon.cognition.onboarding_import.registry import get_source, list_sources
from gideon.cognition.onboarding_import.writers import (
    import_report,
    imported_fingerprints,
    plan_item,
)


def scan_source(name: str, root: Path | str | None = None) -> ScanResult:
    """Scan one registered source. Never writes — to our home or theirs."""
    return get_source(name).scan(root)


def scan_all(*, roots: dict[str, Path | str] | None = None) -> list[ScanResult]:
    """Scan every registered source, resolving each root env-var-then-default.

    ``roots`` overrides a source's root by name (what a test fixture or a seeded
    dev home uses); an absent source yields ``present=False``, not an error.
    """
    return SelectionPlan.scan(sys.modules[__name__], roots)


def detected(results: Iterable[ScanResult]) -> list[ScanResult]:
    """Only the sources actually present on this machine, with something to offer."""
    return list(filter(lambda result: result.present and result.items, results))


def select_items(
    results: Iterable[ScanResult],
    *,
    categories: Iterable[ImportCategory | str] | None = None,
    sources: Iterable[str] | None = None,
    fingerprints: Iterable[str] | None = None,
) -> list[ImportItem]:
    """Flatten scan results into the items to import, honouring the user's picks.

    ``None`` means "everything" for that axis — the onboarding step passes the
    checked categories, the CLI defaults to all.
    """
    items = list(SelectionPlan(ImportCategory, categories, sources).items(results))
    wanted = None if fingerprints is None else set(fingerprints)
    return [item for item in items if wanted is None or item.fingerprint in wanted]


def run_import(
    results: Iterable[ScanResult],
    *,
    categories: Iterable[ImportCategory | str] | None = None,
    sources: Iterable[str] | None = None,
    fingerprints: Iterable[str] | None = None,
) -> ImportReport:
    """Import the selected items from an existing scan and report every outcome."""
    scans = list(results)
    wanted = None if fingerprints is None else list(dict.fromkeys(fingerprints))
    selected = select_items(scans, categories=categories, sources=sources, fingerprints=wanted)
    found = {item.fingerprint for scan in scans for item in scan.items}
    chosen = {item.fingerprint for item in selected}
    unselected = [(item, plan_item(item)) for scan in scans for item in scan.items if item.fingerprint not in chosen]
    admitted = {item.source for item in selected}
    withheld = sum(max(0, scan.secrets_skipped - sum(item.secrets_skipped for item in scan.items)) for scan in scans if scan.source in admitted)
    report = import_report(selected, secrets_skipped=withheld + sum(item.secrets_skipped for item in selected))
    report.unselected = unselected
    report.missing = [] if wanted is None else [fp for fp in wanted if fp not in found]
    return report


def already_imported(results: Iterable[ScanResult]) -> set[str]:
    """Fingerprints in the scan that this importer has already written — what the
    step marks as ``existing`` on re-entry, without writing anything."""
    return SelectionPlan.existing(sys.modules[__name__], results)


def plans(results: Iterable[ScanResult]) -> dict[str, Plan]:
    return {item.fingerprint: plan_item(item) for scan in results for item in scan.items}
