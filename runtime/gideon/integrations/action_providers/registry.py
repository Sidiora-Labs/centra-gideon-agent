"""Synchronized action directory with ordered native bootstrap groups."""

from importlib import import_module
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.action_providers.base import ActionProvider

_providers: "dict[str, ActionProvider]" = {}
_registration_lock = RLock()

_BUILTIN_GROUPS = (
    ("bash", (("bash_provider", "BashActionProvider"),)),
    ("run-script", (("run_script_provider", "RunScriptActionProvider"),)),
    ("notify", (("notify_provider", "NotifyActionProvider"),)),
    ("send-message", (("send_message_provider", "SendMessageActionProvider"),)),
    ("notification-digest", (("digest_provider", "NotificationDigestActionProvider"),)),
    ("usage-recap", (("usage_recap_provider", "UsageRecapActionProvider"),)),
    ("self-remediation", (("remediation_provider", "SelfRemediationActionProvider"),)),
    (
        "identity-report",
        (("identity_report_provider", "IdentityReportActionProvider"),),
    ),
    ("source-digest", (("source_digest_provider", "SourceDigestActionProvider"),)),
    ("create-task", (("create_task_provider", "CreateTaskActionProvider"),)),
    ("invoke-agent", (("invoke_agent_provider", "InvokeAgentActionProvider"),)),
    ("selfqa-triage", (("selfqa_triage_provider", "SelfQaTriageActionProvider"),)),
    (
        "selfqa-file-finding",
        (("selfqa_finding_provider", "SelfQaFindingActionProvider"),),
    ),
    (
        "selfqa-evidence",
        (("selfqa_evidence_provider", "SelfQaEvidenceActionProvider"),),
    ),
    (
        "selfqa-commit-watch",
        (("selfqa_watch_provider", "SelfQaCommitWatchActionProvider"),),
    ),
    ("triage-digest", (("triage_digest_provider", "TriageDigestActionProvider"),)),
    ("inbox-op", (("inbox_op_provider", "InboxOpActionProvider"),)),
    ("run-prompt", (("run_prompt_provider", "RunPromptActionProvider"),)),
    ("run-workflow", (("run_workflow_provider", "RunWorkflowActionProvider"),)),
    ("call-app-route", (("call_app_route_provider", "CallAppRouteActionProvider"),)),
    (
        "artifact-update",
        (("artifact_update_provider", "ArtifactUpdateActionProvider"),),
    ),
    (
        "knowledge-persist",
        (("knowledge_persist_provider", "KnowledgePersistActionProvider"),),
    ),
    (
        "knowledge-retrieve",
        (("knowledge_retrieve_provider", "KnowledgeRetrieveActionProvider"),),
    ),
    (
        "artifact_inspect",
        (("artifact_inspect_provider", "ArtifactInspectActionProvider"),),
    ),
    (
        "knowledge-health",
        (
            ("knowledge_maintain_provider", "KnowledgeHealthActionProvider"),
            ("knowledge_maintain_provider", "KnowledgeConsolidateActionProvider"),
            ("knowledge_maintain_provider", "KnowledgeGapsActionProvider"),
        ),
    ),
    (
        "render-report",
        (("knowledge_render_provider", "KnowledgeRenderReportActionProvider"),),
    ),
    (
        "knowledge-propose",
        (("knowledge_propose_provider", "KnowledgeProposeActionProvider"),),
    ),
    (
        "knowledge-report",
        (("knowledge_report_provider", "KnowledgeReportActionProvider"),),
    ),
    ("browse", (("browse_provider", "BrowseActionProvider"),)),
    ("net-fetch", (("net_fetch_provider", "NetFetchActionProvider"),)),
    ("best-of-n", (("best_of_n_provider", "BestOfNActionProvider"),)),
    ("check-work", (("check_work_provider", "CheckWorkActionProvider"),)),
    ("second-opinion", (("second_opinion_provider", "SecondOpinionActionProvider"),)),
)


def register_action_provider(provider: "ActionProvider") -> None:
    with _registration_lock:
        _providers.update({provider.name: provider})


def get_action_provider(name: str) -> "ActionProvider | None":
    with _registration_lock:
        return _providers.get(name)


def list_action_providers() -> list[str]:
    with _registration_lock:
        return [*_providers]


def _ensure_default_providers_registered() -> None:
    from gideon.security.guardrails.rungs import ensure_core_action_types

    with _registration_lock:
        ensure_core_action_types()
        for sentinel, factories in _BUILTIN_GROUPS:
            if sentinel in _providers:
                continue
            for module, symbol in factories:
                implementation = getattr(
                    import_module(f"{__package__}.{module}"), symbol
                )
                register_action_provider(implementation())
