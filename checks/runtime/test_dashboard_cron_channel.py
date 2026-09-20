"""Channel validation on schedule-trigger create (the unified /api/triggers facade)."""

import json
from unittest.mock import MagicMock

import pytest

from gideon.automation.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    make_agent_action,
)
from gideon.interfaces.dashboard.handlers.triggers import api_trigger_create


def _real_job(**over):
    """A real ScheduleJob so the handler's full serialization doesn't choke on a mock."""
    base = dict(
        id="abc",
        name="t",
        action=make_agent_action(message="m"),
        schedule=ScheduleDefinition(kind="every", every_secs=300),
    )
    base.update(over)
    return ScheduleJob(**base)


def _schedule_body(**extra):
    body = {
        "trigger_type": "schedule",
        "name": "test",
        "every": 300,
        "action": {"provider": "invoke-agent", "config": {"task_template": "msg"}},
    }
    body.update(extra)
    return body


def _request(state, body: dict):
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request(
        "POST", "/api/triggers", headers={"Content-Type": "application/json"}
    )
    request.app["state"] = state
    request._read_bytes = json.dumps(body).encode()
    return request


class TestScheduleTriggerChannel:
    @pytest.mark.asyncio
    async def test_valid_channel_accepted(self):
        mock_state = MagicMock()
        mock_state.crons.add_job.return_value = _real_job()
        mock_state.crons.is_running.return_value = False
        mock_state.crons.running_since.return_value = None
        mock_state._sessions = {}
        resp = await api_trigger_create(
            _request(mock_state, _schedule_body(channel="C0AP77JJSN6"))
        )
        assert resp.status == 200
        from gideon.interfaces.dashboard.handlers.triggers import _trigger_store

        trigger = _trigger_store().get("clock:test").trigger
        assert trigger.delivery == "channel:C0AP77JJSN6"

    @pytest.mark.asyncio
    async def test_invalid_channel_rejected(self):
        mock_state = MagicMock()
        resp = await api_trigger_create(
            _request(mock_state, _schedule_body(channel="not-valid"))
        )
        assert resp.status == 400
        mock_state.crons.add_job.assert_not_called()
