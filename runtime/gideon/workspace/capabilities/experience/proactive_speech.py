"""Speech derives only from the existing persisted proactive digest producer."""

import asyncio
import json
from uuid import uuid4

from gideon.core.config.loader import config_dir

from .narration import digest
from .store import Conflict


def latest_digest(home):
    if config_dir().resolve() != home.resolve():
        raise Conflict("digest scope differs from registered runtime home")
    from gideon.cognition.proactive.surface import build_digest_view
    from gideon.extensions.providers.entity_routes import notification_posture
    from gideon.interfaces.dashboard.handlers.proactive import (
        _install_state,
        _latest_digest,
    )

    state = _install_state()
    if not state["installed"] or not state["enabled"]:
        return {
            "state": "unavailable",
            "reason": "Enable and install the existing proactive digest first.",
        }
    if notification_posture("info") != "allowed":
        return {
            "state": "deferred",
            "reason": "Notification delivery settings currently defer speech.",
        }
    run, output, events = _latest_digest()
    view = build_digest_view(
        enabled=True, installed=True, run=run, output=output, events=events
    )
    if (
        view["state"] != "ready"
        or not view.get("body")
        or view.get("status") != "complete"
    ):
        return {
            "state": "waiting",
            "reason": "No completed proactive digest is available.",
        }
    prose = "\n\n".join(
        value for value in [view.get("title", ""), view["body"]] if value
    )
    if len(prose) > 12000:
        return {
            "state": "unavailable",
            "reason": "Digest exceeds the speech size limit.",
        }
    return {
        "state": "ready",
        "run_id": view["run_id"],
        "source_hash": digest({"run_id": view["run_id"], "text": prose}),
        "text": prose,
    }


def start_digest(jobs, owner, body, retry=False):
    owner.verify(body, proactive=True)
    source = latest_digest(jobs.store.path.parent.parent)
    if source["state"] != "ready":
        return {"source": source, "narration": None}
    key = "digest:" + source["source_hash"]
    with jobs.store.connection() as db:
        previous = db.execute(
            "SELECT body FROM narrations WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
            (key,),
        ).fetchone()
        if previous:
            prior = json.loads(previous[0])
            if not retry or prior["status"] not in (
                "unavailable",
                "failed",
                "cancelled",
                "interrupted",
            ):
                return {"source": source, "narration": prior}
        if len(jobs.tasks) >= 4:
            raise Conflict("four speech jobs are already active")
        job = {
            "id": uuid4().hex,
            "session_id": key,
            "source_kind": "proactive_digest",
            "run_id": source["run_id"],
            "story_id": "",
            "story_revision": 0,
            "node_id": "digest",
            "source_hash": source["source_hash"],
            "voice_hash": "",
            "status": "queued",
            "artifact_slug": "",
            "artifact_version": None,
            "audio_hash": "",
            "error": "",
        }
        job["source_description"] = (
            f"Proactive digest run {source['run_id']}; source {source['source_hash']}"
        )
        job["audio_url"] = (
            "/api/capabilities/experience/narrations/" + job["id"] + "/audio"
        )
        db.execute(
            "INSERT INTO narrations VALUES(?,?,?,?,?)",
            (job["id"], key, job["id"], source["source_hash"], json.dumps(job)),
        )
    task = asyncio.create_task(jobs.produce(job["id"], source["text"]))
    jobs.tasks[job["id"]] = task
    task.add_done_callback(lambda _: jobs.tasks.pop(job["id"], None))
    return {"source": source, "narration": job}
