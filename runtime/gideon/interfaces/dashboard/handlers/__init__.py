"""Non-chat HTTP handlers — re-export façade over the per-domain submodules.

Aggregates the public handler API from the ``handlers/`` submodules (agents,
schedule, files, hooks, mcp, memory, messaging, prompts, sessions,
terminal, updates, usage, core, optimizer, durability) under the flat
``gideon.interfaces.dashboard.handlers.X`` import path used by ``server.py``.

System metrics (CPU, memory, network, disk) live in ``handlers_system.py``;
``api_status`` and ``api_system`` are re-exported here for convenience.
"""

import logging

from gideon.core.config import loader as config_loader
from gideon.core.config.loader import AppConfig  # noqa: F401
from gideon.engine.session import _sync_kill_provider  # noqa: F401
from gideon.interfaces.dashboard.handlers_system import (
    api_auth_status,
    api_healthz,
    api_onboarding,
    api_onboarding_state,
    api_status,
    api_system,
)
from gideon.interfaces.dashboard.origin import is_loopback  # noqa: F401
from gideon.security.security import (
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)


def sel():
    """Dynamic sel() that always resolves from gideon.security.sel for test patching."""
    from gideon.security.sel import sel as _s

    return _s()


logger = logging.getLogger(__name__)


from gideon.interfaces.dashboard.handlers._shared import (
    _blocks_reads_session,
    _get_memory,
    _get_skills,
    _is_restricted_session,
    _list_marketplace_skills,
    _resolve_skill_path,
)
from gideon.interfaces.dashboard.handlers.agents import (
    _CSS_VALUE_ALLOWED_RE,
    _THEME_CSS_VARS_SET,
    _auto_install_agent,
    _find_agent_config,
    _get_config_lock,
    _installed_agent_config,
    _sanitize_css_value,
    _slugify_theme_name,
    _strip_to_allowed_vars,
    _validate_theme_data,
    api_agent_config,
    api_agent_detail,
    api_agent_metadata_delete,
    api_agent_metadata_get,
    api_agent_metadata_put,
    api_agents_installed,
    api_config_schema,
    api_default_agent,
    api_gideon_agent_delete,
    api_gideon_agent_update,
    api_gideon_agents,
    api_gideon_agents_create,
    api_gideon_agents_sync,
    api_slash_commands,
    api_theme_detail,
    api_themes,
    api_themes_create,
)
from gideon.interfaces.dashboard.handlers.autonomy import (
    api_autonomy,
    api_autonomy_demote,
    api_autonomy_grant,
    api_autonomy_undo,
)
from gideon.interfaces.dashboard.handlers.companion import api_companion_discovery

# ── Core (handlers/core.py) ──
from gideon.interfaces.dashboard.handlers.core import (
    _DIST_DIR,
    api_gideon_config,
    api_gideon_config_patch,
    api_incident,
    api_incident_resume,
    api_logout,
    api_models_health,
    api_project_trust,
    api_security_denied_commands,
    api_security_egress,
    api_security_stats,
    api_sel_rotate,
    api_session_agent_result,
    api_session_agent_stream,
    api_session_agents_list,
    api_settings_config,
    api_stt_transcribe,
    api_token_local,
    favicon,
    index,
    manifest_webmanifest,
    service_worker,
)
from gideon.interfaces.dashboard.handlers.decisions import api_decision_journal

# ── Desktop shell capability seam (handlers/desktop.py) ──
from gideon.interfaces.dashboard.handlers.desktop import (
    api_desktop_capability,
    api_desktop_register,
    api_desktop_state,
    api_desktop_state_push,
    api_desktop_unregister,
)
from gideon.interfaces.dashboard.handlers.doctor import (
    api_degraded,
    api_doctor,
    api_doctor_capability,
    api_doctor_crash,
    api_doctor_fix_apply,
    api_doctor_fixes,
    api_doctor_remediation,
    api_doctor_remediation_run,
    api_doctor_simulate_automation,
    api_doctor_simulate_surfacing,
    api_provider_selftest,
)
from gideon.interfaces.dashboard.handlers.durability import (
    api_durability_archive,
    api_durability_archive_restore,
    api_durability_conflict_resolve,
    api_durability_conflicts,
    api_durability_export,
    api_durability_history,
    api_durability_history_operate,
    api_durability_history_timeline,
    api_durability_import,
    api_durability_run,
    api_durability_status,
)
from gideon.interfaces.dashboard.handlers.external_access import (
    api_external_access,
    api_external_access_client,
    api_external_access_client_toggle,
)
from gideon.interfaces.dashboard.handlers.files import (
    _validate_dashboard_path,
    _write_file_restricted,
    api_attachment_extract,
    api_browse_dirs,
    api_channel_upload_file,
    api_config_fs_watch,
    api_create_dir,
    api_dashboard_config,
    api_file_complete,
    api_file_content_search,
    api_file_create,
    api_file_delete,
    api_file_git_commit,
    api_file_git_log,
    api_file_git_original,
    api_file_git_status,
    api_file_list,
    api_file_move,
    api_file_raw,
    api_file_read,
    api_file_search,
    api_file_upload,
    api_file_watch,
    api_file_write,
    api_outbox_download,
    api_outbox_list,
    api_outbox_notify,
    api_reveal_path,
    api_screenshot,
    api_upload,
    api_upload_file,
)
from gideon.interfaces.dashboard.handlers.hooks import (
    _get_hook_store,
    _load_hook_context,
    _run_hook_agent,
    _run_hook_inner,
    _verify_hook_token,
    api_action_providers,
    api_agent_hooks,
    api_hooks_agent,
)
from gideon.interfaces.dashboard.handlers.mcp import (
    _bg_mcp_probe,
    _sync_mcp_to_agent,
    api_mcp_active,
    api_mcp_apply,
    api_mcp_importable,
    api_mcp_pool_stats,
    api_mcp_probe,
    api_mcp_probe_cached,
    api_mcp_probe_one,
    api_mcp_remove,
    api_mcp_server_detail,
    api_mcp_servers,
    api_mcp_sync,
    api_mcp_toggle,
    api_mcp_toggle_all,
    api_mcp_toggle_tool,
)
from gideon.interfaces.dashboard.handlers.memory import (
    _get_provider,
    _redact_memory_field,
    _set_migrated,
    api_memory_approval_rule_add,
    api_memory_approval_rule_delete,
    api_memory_approval_rules,
    api_memory_consolidate,
    api_memory_context_preview,
    api_memory_daily_digests,
    api_memory_entities,
    api_memory_entity_backlinks,
    api_memory_entity_create,
    api_memory_entity_delete,
    api_memory_entity_graph,
    api_memory_entity_proposals,
    api_memory_entity_proposals_list,
    api_memory_episodic_delete,
    api_memory_episodic_list,
    api_memory_episodic_search,
    api_memory_event_undo,
    api_memory_events,
    api_memory_facet,
    api_memory_graph,
    api_memory_graph_export,
    api_memory_graph_rebuild,
    api_memory_history,
    api_memory_import,
    api_memory_lint,
    api_memory_migrate,
    api_memory_observability,
    api_memory_preferences,
    api_memory_projects,
    api_memory_promote,
    api_memory_recall,
    api_memory_record_links,
    api_memory_semantic,
    api_memory_semantic_delete,
    api_memory_semantic_write,
    api_memory_settings,
    api_memory_slot_append,
    api_memory_slot_line_retire,
    api_memory_slots,
    api_memory_stats,
    api_memory_vault_status,
    api_memory_vault_sync,
    api_memory_volunteer_stats,
)
from gideon.interfaces.dashboard.handlers.messaging import (
    _redact,
    _resolve_session_target,
    _sanitize_blocks,
    api_channel_profile,
    api_notification_ack,
    api_notification_delete,
    api_notification_unack,
    api_notifications,
    api_notifications_ack_all,
    api_notifications_clear,
    api_send_message,
    api_spawn,
    api_spawn_cancel_fanout,
    api_spawn_clear,
    api_spawn_delete,
    api_spawn_list,
    api_spawn_status,
)
from gideon.interfaces.dashboard.handlers.optimizer import handle_optimize
from gideon.interfaces.dashboard.handlers.proactive import (
    TRIAGE_TRIGGER_ID,
    api_proactive_digest,
    api_proactive_install,
    api_proactive_reply,
)
from gideon.interfaces.dashboard.handlers.prompts import (
    MAX_PROMPT_BYTES,
    _list_provider_prompts,
    api_campaign_template_launch,
    api_prompt_bindings,
    api_prompt_bindings_save,
    api_prompt_create,
    api_prompt_delete,
    api_prompt_detail,
    api_prompt_preview,
    api_prompt_render,
    api_prompt_save,
    api_prompt_syntax,
    api_prompts,
    api_skill_detail,
    api_skills_create,
    api_snippet_create,
    api_snippet_delete,
    api_snippet_detail,
    api_snippet_render,
    api_snippet_save,
    api_snippets,
)
from gideon.interfaces.dashboard.handlers.schedule import (
    api_lessons,
    api_lessons_create,
    api_lessons_delete,
)
from gideon.interfaces.dashboard.handlers.sessions import (
    _SHUTDOWN_TIMEOUT_SECS,
    _remove_session_for_history_key,
    _reset_all_sessions,
    api_approval_resolve,
    api_approvals,
    api_session_archive_list,
    api_session_archive_read,
    api_session_delete,
    api_session_detail,
    api_session_keepalive,
    api_session_tool_policy,
    api_sessions,
    api_sessions_clear,
    api_sessions_context,
    api_sessions_health,
    api_sessions_restart,
    api_sessions_search,
)
from gideon.interfaces.dashboard.handlers.terminal import (
    api_sandbox_providers,
    api_terminal_create,
    api_terminal_delete,
    api_terminal_list,
    api_terminal_ws,
    reap_orphaned_terminals,
)
from gideon.interfaces.dashboard.handlers.updates import (
    _UPDATE_CHECK_INTERVAL,
    _do_update_check,
    _log_ring,
    _QueueLogHandler,
    _RingLogHandler,
    _update_info,
    api_changelog,
    api_log_level,
    api_log_level_get,
    api_logs,
    api_restart,
    api_update_apply,
    api_update_auto,
    api_update_cancel,
    api_update_check,
    api_update_dev_mode,
    api_update_simulate,
    get_update_info,
    install_log_ring_handler,
)


def config_dir():
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


def config_path():
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()
