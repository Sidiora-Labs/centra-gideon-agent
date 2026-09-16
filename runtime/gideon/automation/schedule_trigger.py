"""Immediate scheduling requests go to the live runtime's authenticated route."""

from __future__ import annotations

import re

_JOB_ID_RE = re.compile(r"[a-f0-9]{6,16}")


def _trigger_receipt(response, job_id: str) -> tuple[bool, str]:
    if not isinstance(response, dict):
        return False, "unexpected response from gateway"
    if error := response.get("error"):
        return False, str(error)
    if response.get("ok"):
        return True, "triggered '%s'" % (response.get("name") or job_id)
    failure = "job is already running" if response.get("running") else "trigger failed"
    return False, failure


def trigger_schedule_job(job_id: str) -> tuple[bool, str]:
    identity = (job_id or "").strip()
    if _JOB_ID_RE.fullmatch(identity) is None:
        return False, f"invalid job id: {identity!r}"
    from gideon.integrations.mcp_core import _post

    response = _post("/api/triggers/schedule:%s/run" % identity, {})
    return _trigger_receipt(response, identity)
