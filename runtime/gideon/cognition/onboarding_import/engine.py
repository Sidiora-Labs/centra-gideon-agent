"""Scan → select → import. The orchestration a UI or CLI drives.

Two phases on purpose, mirroring the pack importer's inspect/commit split: a scan
writes nothing to our home and reads nothing from the foreign root twice, so the
onboarding step can show counts and let the user pick categories before a single
byte lands.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from gideon.cognition.onboarding_import.import_projection import SelectionPlan
from gideon.cognition.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    ItemState,
    Plan,
    ScanResult,
    WriteResult,
)
from gideon.cognition.onboarding_import.registry import get_source, list_sources
from gideon.cognition.onboarding_import.writers import (
    import_report,
    imported_fingerprints,
    plan_item,
)


def scan_source(
    name: str, root: Path | str | None = None, *, look: bool = False
) -> ScanResult:
    """Scan one registered source. Never writes — to our home or theirs."""
    source = get_source(name)
    if look and name in {"claude_code", "codex"}:
        return source.scan(root, look=True)
    return source.scan(root)


def scan_all(
    *, roots: dict[str, Path | str] | None = None, look: bool = False
) -> list[ScanResult]:
    """Scan every registered source, resolving each root env-var-then-default.

    ``roots`` overrides a source's root by name (what a test fixture or a seeded
    dev home uses); an absent source yields ``present=False``, not an error.
    """
    locations = roots or {}
    return [
        scan_source(source.name, locations.get(source.name), look=look)
        for source in list_sources()
    ]


def read_unread(
    results: Iterable[ScanResult],
    *,
    stop: Callable[[], bool] = lambda: False,
    on_read: Callable[[], None] | None = None,
    wait: Callable[[], None] | None = None,
) -> int:
    """Complete provisional summaries one transcript at a time."""
    from importlib import import_module

    from gideon.cognition.onboarding_import.sources.common import giving_way

    done = 0

    with giving_way(wait or (lambda: None)):
        for result in results:
            module = import_module(get_source(result.source).scan.__module__)
            reader = getattr(module, "read_in_full", None)
            if reader is None:
                continue
            for path in result.unread:
                if stop():
                    return done
                reader(path)
                done += 1
                if on_read is not None:
                    on_read()
    return done


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
    accepted: Mapping[str, str] | None = None,
    on_result: Callable[[ImportItem, WriteResult], None] | None = None,
    stop_before: Callable[[ImportItem], bool] | None = None,
) -> ImportReport:
    """Import the selected items from an existing scan and report every outcome."""
    scans = list(results)
    wanted = None if fingerprints is None else list(dict.fromkeys(fingerprints))
    selected = [
        replace(item, accepted_warnings=(accepted or {}).get(item.fingerprint, ""))
        for item in select_items(
            scans, categories=categories, sources=sources, fingerprints=wanted
        )
    ]
    found = {item.fingerprint for scan in scans for item in scan.items}
    chosen = {item.fingerprint for item in selected}
    all_plans = plans(scans)
    unselected = [
        (item, all_plans[item.fingerprint])
        for scan in scans
        for item in scan.items
        if item.fingerprint not in chosen
    ]
    admitted = {item.source for item in selected}
    withheld = sum(
        max(0, scan.secrets_skipped - sum(item.secrets_skipped for item in scan.items))
        for scan in scans
        if scan.source in admitted
    )
    secrets_skipped = withheld + sum(item.secrets_skipped for item in selected)
    if on_result is None and stop_before is None:
        report = import_report(selected, secrets_skipped=secrets_skipped)
    else:
        from gideon.cognition.onboarding_import.model import withheld_notes
        from gideon.cognition.onboarding_import.writers import write_item

        selected.sort(key=lambda item: item.category is ImportCategory.CONVERSATIONS)
        report = ImportReport(
            secrets_skipped=secrets_skipped,
            redactions=sum(item.redactions for item in selected),
        )
        for index, item in enumerate(selected):
            if stop_before is not None and stop_before(item):
                report.not_reached = [row.fingerprint for row in selected[index:]]
                break
            result = write_item(item)
            report.results.append(result)
            if on_result is not None:
                on_result(item, result)
        report.notes.extend(
            withheld_notes(
                secrets_skipped=secrets_skipped, redactions=report.redactions
            )
        )
    report.unselected = unselected
    report.missing = [] if wanted is None else [fp for fp in wanted if fp not in found]
    return report


def already_imported(results: Iterable[ScanResult]) -> set[str]:
    """Fingerprints in the scan that this importer has already written — what the
    step marks as ``existing`` on re-entry, without writing anything."""
    return SelectionPlan.existing(sys.modules[__name__], results)


def plans(results: Iterable[ScanResult]) -> dict[str, Plan]:
    import json

    from gideon.cognition.history import ConversationLog
    from gideon.cognition.onboarding_import.writers import _rel_to_home

    planned: dict[str, Plan] = {}
    conversation_log = None
    for scan in results:
        for item in scan.items:
            if item.category is not ImportCategory.CONVERSATIONS:
                planned[item.fingerprint] = plan_item(item)
                continue
            if conversation_log is None:
                conversation_log = ConversationLog()
            path = conversation_log._path(f"imported_{item.source}_{item.fingerprint}")
            destination = _rel_to_home(path)
            state, detail = ItemState.NEW, ""
            if path.exists():
                try:
                    with path.open(encoding="utf-8") as stream:
                        header = json.loads(stream.readline())
                    same = (
                        isinstance(header, dict)
                        and header.get("import_source") == item.source
                        and header.get("import_key") == item.key
                    )
                except (OSError, ValueError):
                    same = False
                state = ItemState.EXISTING if same else ItemState.CONFLICT
                detail = (
                    "already imported"
                    if same
                    else "this imported conversation has changed; the existing session was kept"
                )
            planned[item.fingerprint] = Plan(state, destination, detail)
    return planned
