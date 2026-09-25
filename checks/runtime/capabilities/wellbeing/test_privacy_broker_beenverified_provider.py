import json

import pytest

from gideon.workspace.capabilities.wellbeing.privacy_broker_beenverified_provider import (
    BeenVerifiedPrivacyBrokerProvider,
    create_provider,
)


class Service:
    def __init__(self):
        self.calls = []

    def _run(self, name, identity, payload):
        self.calls.append((name, identity, payload))
        return {
            "case": {
                "id": identity,
                "revision": payload["revision"],
                "state": "submitted",
            },
            "draft": {"id": "draft", "state": name},
        }

    def prepare(self, identity, payload):
        return self._run("prepare", identity, payload)

    def approve(self, identity, payload):
        return self._run("approve", identity, payload)

    def send(self, identity, payload):
        return self._run("send", identity, payload)

    def correlate(self, identity, payload):
        return self._run("correlate", identity, payload)


@pytest.mark.asyncio
async def test_native_tools_declare_exact_canonical_email_operations_and_approval_boundaries():
    service = Service()
    provider = BeenVerifiedPrivacyBrokerProvider(service)
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert set(tools) == {
        "privacy_broker_beenverified_prepare",
        "privacy_broker_beenverified_approve",
        "privacy_broker_beenverified_send",
        "privacy_broker_beenverified_correlate",
    }
    assert all(tool.requires_approval for tool in tools.values())
    assert tools["privacy_broker_beenverified_send"].risk_level.value == "destructive"
    assert tools["privacy_broker_beenverified_approve"].parameters["properties"][
        "confirm_exact"
    ] == {"const": True}
    assert tools["privacy_broker_beenverified_send"].parameters["properties"][
        "confirm_send"
    ] == {"const": True}
    assert (
        tools["privacy_broker_beenverified_prepare"].parameters["additionalProperties"]
        is False
    )
    result = await provider.invoke(
        "privacy_broker_beenverified_prepare",
        {
            "case_id": "case-1",
            "request_id": "request-1",
            "revision": 2,
            "account_id": "account-1",
            "full_name": "Jane Doe",
            "contact_email": "owner@example.com",
            "profile_url": "https://www.beenverified.com/name/jane",
            "jurisdiction": "US-CA",
        },
    )
    assert (
        result.success is True
        and json.loads(result.output)["draft"]["state"] == "prepare"
    )
    assert service.calls == [
        (
            "prepare",
            "case-1",
            {
                "request_id": "request-1",
                "revision": 2,
                "account_id": "account-1",
                "full_name": "Jane Doe",
                "contact_email": "owner@example.com",
                "profile_url": "https://www.beenverified.com/name/jane",
                "jurisdiction": "US-CA",
            },
        )
    ]


@pytest.mark.asyncio
async def test_native_provider_routes_send_and_correlation_without_transport_overrides():
    service = Service()
    provider = BeenVerifiedPrivacyBrokerProvider(service)
    sent = await provider.invoke(
        "privacy_broker_beenverified_send",
        {
            "case_id": "case-1",
            "request_id": "send-1",
            "revision": 3,
            "draft_revision": 2,
            "content_sha256": "a" * 64,
            "confirm_send": True,
        },
    )
    assert sent.success is True and json.loads(sent.output)["draft"]["state"] == "send"
    correlated = await provider.invoke(
        "privacy_broker_beenverified_correlate",
        {"case_id": "case-1", "revision": 4, "manual_code": "482913"},
    )
    assert (
        correlated.success is True
        and json.loads(correlated.output)["draft"]["state"] == "correlate"
    )
    assert all(
        "credential" not in payload
        and "sender" not in payload
        and "transport" not in payload
        for _, _, payload in service.calls
    )
    unknown = await provider.invoke(
        "privacy_broker_beenverified_delete", {"case_id": "case-1"}
    )
    assert (
        unknown.success is False and "declared BeenVerified operation" in unknown.error
    )


def test_factory_rejects_all_caller_storage_transport_and_credential_configuration():
    for config in (
        {"home": "/tmp/other"},
        {"smtp_host": "127.0.0.1"},
        {"credential": "secret"},
        {"sender": "other@example.com"},
    ):
        with pytest.raises(ValueError, match="does not accept caller"):
            create_provider(config)
