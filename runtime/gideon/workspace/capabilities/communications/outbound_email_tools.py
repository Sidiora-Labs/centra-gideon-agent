import json

from gideon.integrations.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

from .outbound_email import OutboundEmail
from .store import PeopleError, PeopleStore


class OutboundEmailTools(ToolProvider):
    @property
    def name(self):
        return "outbound_email"

    @property
    def display_name(self):
        return "Approved outbound email"

    async def list_tools(self):
        schema = {"type": "object", "additionalProperties": True}
        return [
            ToolDefinition(
                name="outbound_email_" + name,
                description=description,
                provider=self.name,
                parameters=schema,
                requires_approval=name in ("approve", "send"),
                risk_level=(
                    RiskLevel.DESTRUCTIVE
                    if name == "send"
                    else (
                        RiskLevel.CAUTION
                        if name in ("draft", "approve")
                        else RiskLevel.SAFE
                    )
                ),
            )
            for name, description in [
                ("list", "List durable outbound email records"),
                ("draft", "Create an account-bound email draft"),
                ("approve", "Approve the exact rendered email"),
                ("send", "Dispatch an approved email"),
                ("correlate", "Correlate an ingested reply without opening links"),
            ]
        ]

    async def invoke(self, tool_name, arguments):
        try:
            name = tool_name.removeprefix("outbound_email_")
            if name == "list":
                value = {"drafts": self.service.list()}
            elif name == "draft":
                value = {"draft": self.service.draft(arguments)[0]}
            else:
                draft_id = arguments.get("draft_id", "")
                data = {k: v for k, v in arguments.items() if k != "draft_id"}
                value = {
                    "draft": (
                        getattr(self.service, name)(draft_id, data)
                        if name in ("approve", "send")
                        else self.service.correlate(draft_id)
                    )
                }
            return ToolResult(success=True, output=json.dumps(value))
        except (PeopleError, KeyError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))

    def __init__(self, service=None):
        self.service = service or OutboundEmail(PeopleStore())


def create_provider(config=None):
    if config:
        raise ValueError(
            "Outbound email tools do not accept caller transport or credential configuration"
        )
    return OutboundEmailTools()
