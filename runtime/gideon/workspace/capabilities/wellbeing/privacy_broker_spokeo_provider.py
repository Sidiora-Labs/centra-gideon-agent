"""Native tools for the approved Spokeo privacy-case protocol."""

import json

from gideon.core.config.loader import config_dir
from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

from .privacy_broker_spokeo import SpokeoCaseAdapter, SpokeoProtocol
from .privacy_brokers import PrivacyBrokerStore
from .store import MeasurementError


class SpokeoPrivacyBrokerProvider(ToolProvider):
    name = "gideon-privacy-broker-spokeo"
    display_name = "Spokeo privacy broker"

    def __init__(self, service=None):
        self.service = service or SpokeoCaseAdapter(
            PrivacyBrokerStore(config_dir()), SpokeoProtocol()
        )

    async def list_tools(self):
        identity = {
            "case_id": {"type": "string"},
            "request_id": {"type": "string"},
            "revision": {"type": "integer", "minimum": 1},
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "state": {"type": "string", "pattern": "^[A-Za-z]{2}$"},
            "city": {"type": "string"},
        }
        optout = {
            "case_id": {"type": "string"},
            "request_id": {"type": "string"},
            "revision": {"type": "integer", "minimum": 1},
            "profile_url": {"type": "string"},
            "email": {"type": "string"},
        }
        return [
            ToolDefinition(
                name="privacy_broker_spokeo_scan",
                provider=self.name,
                description="Scan Spokeo for an explicitly consented privacy case and save the provider result.",
                parameters=self._schema(
                    identity,
                    (
                        "case_id",
                        "request_id",
                        "revision",
                        "first_name",
                        "last_name",
                        "state",
                    ),
                ),
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="privacy_broker_spokeo_prepare",
                provider=self.name,
                description="Inspect the current Spokeo opt-out form and save a preparation result without submitting it.",
                parameters=self._schema(optout, tuple(optout)),
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="privacy_broker_spokeo_submit",
                provider=self.name,
                description="Submit a prepared Spokeo opt-out after exact owner approval. Live submission is disabled unless the runtime gate is enabled.",
                parameters=self._schema(
                    {
                        **optout,
                        "approval": {
                            "type": "string",
                            "const": "SUBMIT SPOKEO OPT-OUT",
                        },
                    },
                    (*optout, "approval"),
                ),
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
            ToolDefinition(
                name="privacy_broker_spokeo_verify",
                provider=self.name,
                description="Re-scan a submitted Spokeo case and confirm removal only from a negative provider result.",
                parameters=self._schema(
                    identity,
                    (
                        "case_id",
                        "request_id",
                        "revision",
                        "first_name",
                        "last_name",
                        "state",
                    ),
                ),
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        methods = {
            "privacy_broker_spokeo_scan": self.service.scan,
            "privacy_broker_spokeo_prepare": self.service.prepare,
            "privacy_broker_spokeo_submit": self.service.submit,
            "privacy_broker_spokeo_verify": self.service.verify,
        }
        method = methods.get(tool_name)
        if (
            method is None
            or not isinstance(arguments, dict)
            or not isinstance(arguments.get("case_id"), str)
        ):
            return ToolResult(
                False,
                error="tool and arguments must match the declared Spokeo operation",
            )
        payload = {key: value for key, value in arguments.items() if key != "case_id"}
        try:
            value = await method(arguments["case_id"], payload)
            return ToolResult(True, output=json.dumps(value, sort_keys=True))
        except MeasurementError as exc:
            return ToolResult(
                False, error=str(exc), metadata={"code": exc.code, "status": exc.status}
            )

    @staticmethod
    def _schema(properties, required):
        return {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        }


def create_provider(config=None):
    config = config or {}
    if not isinstance(config, dict) or set(config) - {"origin"}:
        raise ValueError("Spokeo provider config accepts only origin")
    origin = config.get("origin", "https://www.spokeo.com")
    return SpokeoPrivacyBrokerProvider(
        SpokeoCaseAdapter(PrivacyBrokerStore(config_dir()), SpokeoProtocol(origin))
    )
