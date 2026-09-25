import json

import pytest

from gideon.workspace.capabilities.wellbeing.privacy_broker_spokeo_provider import (
    SpokeoPrivacyBrokerProvider,
    create_provider,
)


class Service:
    def __init__(self):
        self.calls = []

    async def scan(self, identity, payload):
        self.calls.append(("scan", identity, payload))
        return {"id": identity, "state": "found", "revision": payload["revision"] + 1}

    async def prepare(self, identity, payload):
        self.calls.append(("prepare", identity, payload))
        return {
            "case": {"id": identity, "state": "optout_in_progress"},
            "plan": {"live_submission_enabled": False},
        }

    async def submit(self, identity, payload):
        self.calls.append(("submit", identity, payload))
        return {"id": identity, "state": "submitted"}

    async def verify(self, identity, payload):
        self.calls.append(("verify", identity, payload))
        return {"id": identity, "state": "confirmed_removed"}


@pytest.mark.asyncio
async def test_native_provider_declares_exact_approved_operations_and_invokes_service():
    service = Service()
    provider = SpokeoPrivacyBrokerProvider(service)
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert set(tools) == {
        "privacy_broker_spokeo_scan",
        "privacy_broker_spokeo_prepare",
        "privacy_broker_spokeo_submit",
        "privacy_broker_spokeo_verify",
    }
    assert all(tool.requires_approval for tool in tools.values())
    assert (
        tools["privacy_broker_spokeo_submit"].parameters["properties"]["approval"][
            "const"
        ]
        == "SUBMIT SPOKEO OPT-OUT"
    )
    assert (
        tools["privacy_broker_spokeo_submit"].parameters["additionalProperties"]
        is False
    )

    result = await provider.invoke(
        "privacy_broker_spokeo_submit",
        {
            "case_id": "case-1",
            "request_id": "request-1",
            "revision": 3,
            "profile_url": "https://www.spokeo.com/Jane-Doe/CA/id",
            "email": "owner@example.test",
            "approval": "SUBMIT SPOKEO OPT-OUT",
        },
    )
    assert result.success is True
    assert json.loads(result.output)["state"] == "submitted"
    assert service.calls == [
        (
            "submit",
            "case-1",
            {
                "request_id": "request-1",
                "revision": 3,
                "profile_url": "https://www.spokeo.com/Jane-Doe/CA/id",
                "email": "owner@example.test",
                "approval": "SUBMIT SPOKEO OPT-OUT",
            },
        )
    ]


@pytest.mark.asyncio
async def test_native_provider_rejects_unknown_tool_and_factory_rejects_override_shapes():
    provider = SpokeoPrivacyBrokerProvider(Service())
    result = await provider.invoke("privacy_broker_spokeo_other", {"case_id": "case-1"})
    assert result.success is False
    assert "declared Spokeo operation" in result.error
    with pytest.raises(ValueError, match="accepts only origin"):
        create_provider({"home": "/tmp/other"})
    with pytest.raises(Exception, match="requires https"):
        create_provider({"origin": "http://127.0.0.1:9"})
