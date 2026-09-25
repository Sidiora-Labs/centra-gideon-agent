"""Parent editorial acceptance across revisions, continuity evidence and sibling issues."""

import asyncio

from gideon.workspace.capabilities.creative.continuity import ContinuityStore
from gideon.workspace.capabilities.creative.editorial import EditorialStore
from gideon.workspace.capabilities.creative.series import SeriesStore

FIRST = "Time stood still in the station. Mara kept the last sentence unchanged."
SECOND = "Rain crossed the observatory windows. The brass key stayed with Mara."


def test_multi_issue_anchored_repair_undo_restores_continuity_source(tmp_path):
    series = SeriesStore(tmp_path)
    parent = series.create(
        {
            "request_id": "closure-series",
            "title": "Continuity closure",
            "synopsis": "Mara follows the key.",
            "volumes": [
                {
                    "id": "volume-one",
                    "title": "Volume one",
                    "chapters": [
                        {
                            "id": "issue-one",
                            "title": "Station",
                            "prompt": "Open at the station.",
                        },
                        {
                            "id": "issue-two",
                            "title": "Observatory",
                            "prompt": "Continue at the observatory.",
                        },
                    ],
                }
            ],
            "arcs": [
                {
                    "id": "key-arc",
                    "title": "Key arc",
                    "summary": "Follow the key.",
                    "chapter_ids": ["issue-one", "issue-two"],
                }
            ],
        }
    )
    first = series.prepare(parent["id"], "issue-one", {"revision": parent["revision"]})
    second = series.prepare(parent["id"], "issue-two", {"revision": parent["revision"]})
    first = series.works.draft(
        first["id"],
        {
            "request_id": "closure-first-draft",
            "revision": first["revision"],
            "text": FIRST,
        },
    )["work"]
    second = series.works.draft(
        second["id"],
        {
            "request_id": "closure-second-draft",
            "revision": second["revision"],
            "text": SECOND,
        },
    )["work"]

    continuity = ContinuityStore(series.works)
    mara_start = FIRST.index("Mara")
    proposal = asyncio.run(
        continuity.propose(
            first["id"],
            {
                "request_id": "closure-continuity",
                "work_revision": first["revision"],
                "start": 0,
                "end": len(FIRST),
                "mode": "authored",
                "instruction": "Record only the cited manuscript fact.",
                "outline": [],
                "facts": [
                    {
                        "subject": "Mara",
                        "predicate": "location",
                        "value": "station",
                        "start": mara_start,
                        "end": mara_start + len("Mara"),
                        "quote": "Mara",
                    }
                ],
            },
        )
    )
    accepted = continuity.accept(
        first["id"],
        proposal["id"],
        {
            "revision": 0,
            "work_revision": first["revision"],
        },
    )
    before_ledger = continuity.get(first["id"])
    assert accepted["revision"] == 1
    assert before_ledger["facts"][0]["quote"] == "Mara"
    assert before_ledger["facts"][0]["stale"] is False

    editorial = EditorialStore(series.works)
    run = editorial.run(
        first["id"],
        {
            "request_id": "closure-run",
            "work_revision": first["revision"],
            "check_ids": ["prose.cliches"],
            "start": 0,
            "end": len(FIRST),
        },
    )
    finding = run["findings"][0]
    assert finding["quote"] == "Time stood still"
    assert FIRST[finding["start"] : finding["end"]] == finding["quote"]
    assert run["work_revision"] == first["revision"]
    assert run["draft_id"] == first["active_draft_id"]
    source_artifact = series.works.artifacts.get(
        run["artifact_id"], version=run["artifact_version"]
    )
    assert source_artifact is not None
    assert source_artifact.content == FIRST

    repair = asyncio.run(
        editorial.repair(
            first["id"],
            run["id"],
            finding["id"],
            {
                "request_id": "closure-repair",
                "work_revision": first["revision"],
                "replacement": "The station clock stopped",
            },
        )
    )
    candidate = editorial.polishing.get(first["id"], repair["proposal"]["id"])
    assert candidate["original"] == "Time stood still"
    assert candidate["replacement"] == "The station clock stopped"
    assert series.works.get(first["id"])["text"] == FIRST

    promoted = editorial.polishing.promote(
        first["id"], candidate["id"], {"revision": first["revision"]}
    )
    changed = series.works.get(first["id"])
    assert changed["revision"] == first["revision"] + 1
    assert changed["text"] == FIRST.replace(
        "Time stood still", "The station clock stopped"
    )
    assert changed["text"].endswith("Mara kept the last sentence unchanged.")
    assert series.works.get(second["id"])["text"] == SECOND
    assert promoted["draft"]["artifact_id"] == candidate["artifact_id"]
    assert editorial.get(first["id"])["runs"][0]["stale"] is True
    assert continuity.get(first["id"])["facts"][0]["stale"] is True

    restored = series.works.restore(
        first["id"],
        {
            "revision": changed["revision"],
            "target_revision": first["revision"],
        },
    )
    assert restored["revision"] == changed["revision"] + 1
    assert restored["active_draft_id"] == first["active_draft_id"]
    assert series.works.get(first["id"])["text"] == FIRST
    assert series.works.get(second["id"])["text"] == SECOND
    after_ledger = continuity.get(first["id"])
    assert after_ledger["revision"] == 1
    assert after_ledger["facts"][0]["stale"] is False
    assert after_ledger["facts"][0]["artifact_id"] == proposal["artifact_id"]
    historical = editorial.get(first["id"])["runs"][0]
    assert historical["stale"] is True
    assert historical["findings"][0] == finding
    assert (
        series.works.artifacts.get(
            run["artifact_id"], version=run["artifact_version"]
        ).content
        == FIRST
    )
