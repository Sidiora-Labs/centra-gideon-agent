"""Construct the shared conversation and knowledge services as one resource set."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.cognition.context import PromptAssembler
    from gideon.cognition.history import ConversationLog, HistoryConsolidator
    from gideon.cognition.vector_memory import SemanticArchive
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.channel_history import ChannelHistory


@dataclass(slots=True)
class ConversationResources:
    context: "PromptAssembler"
    history: "ConversationLog"
    consolidation: "HistoryConsolidator"
    conversations: "ConversationDirectory"
    semantic: "SemanticArchive"
    channels: "ChannelHistory"
    indexed_files: int


def assemble_conversations(configuration: "AppConfig") -> ConversationResources:
    from gideon.cognition.context import PromptAssembler
    from gideon.cognition.history import ConversationLog, HistoryConsolidator
    from gideon.cognition.memory import MemoryJournal
    from gideon.cognition.vector_memory import SemanticArchive
    from gideon.core.config.loader import config_dir
    from gideon.engine.hooks import HookManager, HooksConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.extensions.skills import ProcedureLibrary
    from gideon.integrations.channel_history import ChannelHistory

    memory_policy = configuration.memory
    skill_policy = configuration.skills
    factory = configuration.create_provider_factory()
    journal = MemoryJournal()
    journal.init()
    semantics = SemanticArchive(
        confidence_threshold=memory_policy.semantic_confidence_threshold,
        extra_prefixes=memory_policy.semantic_keys or None,
        dedup_threshold=memory_policy.episodic_dedup_threshold,
        episodic_max=memory_policy.episodic_max_count,
        episodic_limit=memory_policy.episodic_max_results,
    )
    semantics.init()
    journal.vector_store = semantics
    procedures = ProcedureLibrary()
    context = PromptAssembler(
        memory=journal,
        skills=procedures,
        hooks=HookManager(HooksConfig.from_dict(configuration.hooks)),
    )
    history = ConversationLog()
    history.init()
    context.conversation_log = history
    conversations = ConversationDirectory(configuration, provider_factory=factory)
    consolidation = HistoryConsolidator(
        log=history,
        memory=journal,
        sessions=conversations,
        history_idle_secs=memory_policy.history_idle_hours * 3600,
        vector_store=semantics,
        migrated=memory_policy.migrated,
        skills_loader=procedures,
        auto_skills_enabled=skill_policy.auto_create_from_sessions,
        auto_refine_enabled=skill_policy.auto_refine_on_deviation,
        auto_min_tool_calls=skill_policy.auto_min_tool_calls,
        auto_similarity_threshold=skill_policy.auto_similarity_threshold,
    )
    conversations.set_session_expire_callback(consolidation.consolidate_session)
    channels = ChannelHistory(
        observe_max_entries=configuration.observe_max_messages,
        observe_ttl_secs=int(configuration.observe_ttl_hours * 3600),
        history_dir=config_dir() / "history",
    )
    context.channel_history = channels
    return ConversationResources(
        context,
        history,
        consolidation,
        conversations,
        semantics,
        channels,
        journal.rebuild_index(),
    )
