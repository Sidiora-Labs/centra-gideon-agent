"""Emit persisted native attention rows for display consumer checks."""

import json

from gideon.automation.workflows.attention import raise_gate_item
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manifest import AppManifest
from gideon.integrations.inbox import InboxStore, emit_attention_item, redact_item
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.notification_kinds import NotificationKind, register

state = ConsoleState(ConversationDirectory(AppConfig()), 0)
store = InboxStore()
manifest = AppManifest.from_dict(
    {"name": "approval-demo", "displayName": "Release Companion", "version": "1.0.0"}
)
register(
    NotificationKind(
        "app:approval-demo", "proposal:approve", "Release approval", attention=True
    )
)
emit_attention_item(
    state,
    source="app:approval-demo",
    kind="proposal:approve",
    title="Approve release",
    item_kind="proposal",
    store=store,
    refs={
        "app": manifest.name,
        "app_display_name": manifest.displayName or manifest.name,
    },
)
raise_gate_item(
    state,
    run_id="run-display",
    workflow="Release preparation",
    node_id="check",
    instance_path="check",
    epoch=0,
    resume_token="fixture-resume",
    ask={"kind": "text", "prompt": "Which branch?"},
)
loaded = InboxStore()
loaded.load()
print(json.dumps([redact_item(item.to_dict()) for item in loaded.items.values()]))
