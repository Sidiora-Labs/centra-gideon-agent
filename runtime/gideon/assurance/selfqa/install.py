"""Converging the Self-QA commit watch onto the vcs trigger (SELF-VERIFICATION SV-11).

The Wave-2 companion shipped an interim seam — a cron script materialized into
``~/.gideon/crons/`` on an interval trigger — because no vcs trigger existed yet and a
script job may only load from that fenced directory. AUTOMATION-SUBSTRATE's ``vcs`` preset
now exists (:func:`gideon.automation.triggers.file_watch.vcs_patterns`), so this module does what
the plan's §3.1 promised from the start: *"When AUTO-R12's vcs preset lands, the cron script
retires and the same template binds to the real trigger — the template is the durable half,
the trigger is a swap."*

:func:`reconcile` therefore converges a ``file``-kind trigger (the vcs preset over
``agent.self_qa.watched_repo``) whose action is the ``selfqa-commit-watch`` provider — the
retired script's delta logic, moved in-process (:mod:`gideon.assurance.selfqa.watch`). It also
REMOVES any interim artifacts a Wave-2 home still carries (the installed script, its config,
its state file beside them): pre-1.0 clean break, per CONTRIBUTING's breaking-changes
posture, and leaving a dead script in the crons dir would invite a user to schedule it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WATCH_TRIGGER_ID = "system:selfqa-commit-watch"

_RETIRED_CRON_FILES = (
    "selfqa_commit_watch.py",
    "selfqa_commit_watch.config.json",
    "selfqa_commit_watch.state.json",
)


def remove_retired_script(crons_dir: Path | None = None) -> list[str]:
    """Delete the interim commit-watch script artifacts, returning what was removed.

    Best-effort and idempotent: a fresh home removes nothing, an upgraded Wave-2 home
    removes up to three files. The state is NOT migrated — the new watcher's first fire
    records HEAD and stays quiet (first-sight rule), which is the same behaviour a fresh
    enable has always had.
    """
    if crons_dir is None:
        from gideon.core.config.loader import config_dir

        crons_dir = config_dir() / "crons"
    removed: list[str] = []
    for name in _RETIRED_CRON_FILES:
        target = crons_dir / name
        try:
            if target.is_file():
                target.unlink()
                removed.append(name)
        except OSError:
            logger.debug(
                "selfqa reconcile: could not remove retired %s", name, exc_info=True
            )
    if removed:
        logger.info(
            "selfqa: removed retired commit-watch artifacts: %s", ", ".join(removed)
        )
    return removed


def reconcile(store: Any, *, crons_dir: Path | None = None) -> None:
    """Make the commit watcher match `agent.self_qa`. Idempotent, best-effort.

    Converges rather than only creating, so turning the companion on in Settings takes
    effect without the user knowing a trigger exists to be registered — and so editing
    `watched_repo` re-points the existing watcher instead of leaving it on the old path.

    **A disabled companion DISABLES its trigger; it never deletes it.** Deleting the last
    entry has been observed to stop the scheduler outright, and a disabled row is also the
    more honest surface: the user sees the watcher they configured, switched off, rather
    than an empty list that looks like their setting did not save.
    """
    from gideon.automation.triggers import screen as _screen
    from gideon.automation.triggers.file_watch import vcs_patterns
    from gideon.automation.triggers.models import Trigger
    from gideon.core.config.loader import AppConfig

    try:
        cfg = AppConfig.load().agent.self_qa
    except Exception:
        logger.debug("selfqa reconcile: could not read the config", exc_info=True)
        return

    repo = (cfg.watched_repo or "").strip()
    active = bool(cfg.enabled and repo)

    remove_retired_script(crons_dir)

    try:
        existing = store.get(WATCH_TRIGGER_ID)
    except Exception:
        logger.debug(
            "selfqa reconcile: could not read the trigger store", exc_info=True
        )
        return

    if existing is None and not active:
        return

    try:
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=WATCH_TRIGGER_ID,
                name="Self-QA commit watch",
                kind="file",
                created_by="system",
                delivery="none",
            )
        )
        trigger.enabled = active
        trigger.kind = "file"
        trigger.spec = {"paths": vcs_patterns(repo or "."), "dedup": "content"}
        trigger.workflow = {
            "inline": {
                "provider": "selfqa-commit-watch",
                "config": {"repo": repo},
            }
        }
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        store.upsert(trigger)
        logger.info(
            "selfqa: commit watch %s (repo=%s, vcs preset)",
            "armed" if active else "disabled",
            repo or "<unset>",
        )
    except Exception:
        logger.warning("selfqa reconcile: registration failed", exc_info=True)
