import json

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)
from gideon.workspace.capabilities.communications import PeopleStore

from .privacy_broker_whitepages import WhitepagesCaseAdapter
from .privacy_brokers import PrivacyBrokerStore
from .store import MeasurementError


class WhitepagesPrivacyBrokerProvider(ToolProvider):
    name = "gideon-privacy-broker-whitepages"
    display_name = "Whitepages approved email opt-out"

    def __init__(self, service=None):
        self.service = service or WhitepagesCaseAdapter(
            PrivacyBrokerStore(config_dir()), PeopleStore()
        )

    async def list_tools(self):
        common = {
            "case_id": {"type": "string"},
            "revision": {"type": "integer", "minimum": 1},
        }
        definitions = {
            "prepare": (
                {
                    **common,
                    "request_id": {"type": "string"},
                    "account_id": {"type": "string"},
                    "full_name": {"type": "string"},
                    "contact_email": {"type": "string"},
                    "profile_url": {"type": "string"},
                    "jurisdiction": {"type": "string", "enum": ["US-CA", "US-OTHER"]},
                },
                [
                    "case_id",
                    "request_id",
                    "revision",
                    "account_id",
                    "full_name",
                    "contact_email",
                    "profile_url",
                    "jurisdiction",
                ],
            ),
            "approve": (
                {
                    **common,
                    "draft_revision": {"type": "integer"},
                    "content_sha256": {"type": "string"},
                    "confirm_exact": {"const": True},
                },
                [
                    "case_id",
                    "revision",
                    "draft_revision",
                    "content_sha256",
                    "confirm_exact",
                ],
            ),
            "send": (
                {
                    **common,
                    "request_id": {"type": "string"},
                    "draft_revision": {"type": "integer"},
                    "content_sha256": {"type": "string"},
                    "confirm_send": {"const": True},
                },
                [
                    "case_id",
                    "request_id",
                    "revision",
                    "draft_revision",
                    "content_sha256",
                    "confirm_send",
                ],
            ),
            "correlate": (
                {
                    **common,
                    "manual_code": {"type": "string", "pattern": "^[0-9]{4,8}$"},
                },
                ["case_id", "revision"],
            ),
        }
        descriptions = {
            "prepare": "Create a canonical account-bound Whitepages email draft without sending it.",
            "approve": "Approve the exact rendered Whitepages recipient and content through canonical outbound email.",
            "send": "Dispatch the approved draft; SMTP acceptance remains distinct from delivery.",
            "correlate": "Correlate an ingested reply or manually match its code without opening links or confirming removal.",
        }
        return [
            ToolDefinition(
                name="privacy_broker_whitepages_" + name,
                provider=self.name,
                description=descriptions[name],
                parameters={
                    "type": "object",
                    "properties": schema,
                    "required": required,
                    "additionalProperties": False,
                },
                requires_approval=True,
                risk_level=(
                    RiskLevel.DESTRUCTIVE if name == "send" else RiskLevel.CAUTION
                ),
            )
            for name, (schema, required) in definitions.items()
        ]

    async def invoke(self, tool_name, arguments):
        name = tool_name.removeprefix("privacy_broker_whitepages_")
        method = getattr(self.service, name, None)
        if (
            method is None
            or not isinstance(arguments, dict)
            or not isinstance(arguments.get("case_id"), str)
        ):
            return ToolResult(
                False,
                error="tool and arguments must match the declared Whitepages operation",
            )
        try:
            value = method(
                arguments["case_id"],
                {key: value for key, value in arguments.items() if key != "case_id"},
            )
            return ToolResult(True, output=json.dumps(value, sort_keys=True))
        except MeasurementError as exc:
            return ToolResult(
                False, error=str(exc), metadata={"code": exc.code, "status": exc.status}
            )


def create_provider(config=None):
    if config:
        raise ValueError(
            "Whitepages provider does not accept caller storage, transport or credential configuration"
        )
    return WhitepagesPrivacyBrokerProvider()
