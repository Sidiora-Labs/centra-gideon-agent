"""Configuration field policies and section assembly."""

from gideon.core.config import loader as definitions
from gideon.core.config.codec import ConfigInput, Derived, Record, Value


def _decode(name, document, constructor=None):
    schema = RECORDS[name]
    factory = constructor or getattr(definitions, schema.model)
    return schema.read(document, factory)


def decode_configuration(data, constructor):
    return _decode("config", ConfigInput(data), constructor)


def _profiles(document):
    return {
        name: _decode("agent-profile", ConfigInput(values))
        for name, values in document.section("agents").items()
        if isinstance(values, dict)
    }


def _stores(document):
    values = {
        name: _decode("memory-store", ConfigInput(values))
        for name, values in document.section("memory_stores").items()
        if isinstance(values, dict)
    }
    return values or {"default": definitions.MemoryStoreConfig()}


def _default_agent(document):
    for raw in (
        document.values.get("default_agent"),
        document.section("agent").get("default_agent"),
    ):
        if isinstance(raw, str) and raw:
            return raw
    return ""


def _update_channel(document):
    updates = document.section("updates")
    if "channel" in updates:
        return definitions._safe_choice(
            updates["channel"], ("stable", "beta", "nightly"), "stable"
        )
    return (
        "nightly" if document.section("dashboard").get("update_dev_mode") else "stable"
    )


def _update_auto(document):
    updates = document.section("updates")
    if "auto" in updates:
        return definitions._safe_choice(updates["auto"], ("off", "staged"), "off")
    return "staged" if document.values.get("auto_update") else "off"


RECORDS = {
    "config": Record(
        "AppConfig",
        {
            "agent": Derived(
                lambda document: _decode("config.agent.AgentConfig", document)
            ),
            "session": Derived(
                lambda document: _decode("config.session.SessionConfig", document)
            ),
            "loops": Derived(
                lambda document: _decode("config.loops.LoopsConfig", document)
            ),
            "memory": Derived(
                lambda document: _decode("config.memory.MemoryConfig", document)
            ),
            "dashboard": Derived(
                lambda document: _decode("config.dashboard.DashboardConfig", document)
            ),
            "legibility": Derived(
                lambda document: _decode("config.legibility.LegibilityConfig", document)
            ),
            "ambient": Derived(
                lambda document: _decode("config.ambient.AmbientConfig", document)
            ),
            "companion": Derived(
                lambda document: _decode("config.companion.CompanionConfig", document)
            ),
            "browse": Derived(
                lambda document: _decode("config.browse.BrowseConfig", document)
            ),
            "mobile": Derived(
                lambda document: _decode("config.mobile.MobileConfig", document)
            ),
            "local_models": Derived(
                lambda document: _decode(
                    "config.local_models.LocalModelsConfig", document
                )
            ),
            "sources": Derived(
                lambda document: _decode("config.sources.SourcesConfig", document)
            ),
            "packs": Derived(
                lambda document: _decode("config.packs.PacksConfig", document)
            ),
            "apps": Derived(
                lambda document: _decode("config.apps.AppsConfig", document)
            ),
            "hooks": Value(("hooks",), {}),
            "agents": Derived(lambda document: _profiles(document)),
            "default_agent": Derived(lambda document: _default_agent(document)),
            "memory_stores": Derived(lambda document: _stores(document)),
            "auto_update": Value(("auto_update",), True),
            "updates": Derived(
                lambda document: _decode("config.updates.UpdatesConfig", document)
            ),
            "timezone": Value(("timezone",), ""),
            "snapshot_dir": Value(("snapshot_dir",), ""),
            "durability": Derived(
                lambda document: _decode("config.durability.DurabilityConfig", document)
            ),
            "proactive": Derived(
                lambda document: _decode("config.proactive.ProactiveConfig", document)
            ),
            "evals": Derived(
                lambda document: _decode("config.evals.EvalsConfig", document)
            ),
            "inbox": Derived(
                lambda document: _decode("config.inbox.InboxConfig", document)
            ),
            "tools": Derived(
                lambda document: _decode("config.tools.ToolsConfig", document)
            ),
            "feedback": Derived(
                lambda document: _decode("config.feedback.FeedbackConfig", document)
            ),
            "external_access": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig", document
                )
            ),
            "agents_routing": Derived(
                lambda document: _decode(
                    "config.agents_routing.AgentsRoutingConfig", document
                )
            ),
            "planning": Derived(
                lambda document: _decode("config.planning.PlanningConfig", document)
            ),
            "skills": Derived(
                lambda document: _decode("config.skills.SkillsConfig", document)
            ),
            "workflows": Derived(
                lambda document: _decode("config.workflows.WorkflowsConfig", document)
            ),
            "learning": Derived(
                lambda document: _decode("config.learning.LearningConfig", document)
            ),
            "knowledge": Derived(
                lambda document: _decode("config.knowledge.KnowledgeConfig", document)
            ),
            "security": Derived(
                lambda document: _decode("config.security.SecurityConfig", document)
            ),
            "auth": Derived(
                lambda document: _decode("config.auth.AuthConfigSection", document)
            ),
            "routing": Derived(
                lambda document: _decode("config.routing.RoutingConfig", document)
            ),
            "guardrails": Derived(
                lambda document: _decode("config.guardrails.GuardrailsConfig", document)
            ),
            "voice": Derived(
                lambda document: _decode("config.voice.VoiceConfig", document)
            ),
            "resilience": Derived(
                lambda document: _decode("config.resilience.ResilienceConfig", document)
            ),
            "sandbox": Derived(
                lambda document: _decode("config.sandbox.SandboxConfig", document)
            ),
            "checkpoints": Derived(
                lambda document: _decode(
                    "config.checkpoints.CheckpointsConfig", document
                )
            ),
            "observe_max_messages": Value(
                ("observe_max_messages",), 200, lambda value: max(1, int(value))
            ),
            "observe_ttl_hours": Value(
                ("observe_ttl_hours",), 168.0, lambda value: max(0.0, float(value))
            ),
        },
    ),
    "agent-profile": Record(
        "AgentProfile",
        {
            "provider": Value(("provider",), ""),
            "provider_agent": Value(("provider_agent",), ""),
            "acp_mode": Value(("acp_mode",), ""),
            "default_dir": Value(("default_dir",), ""),
            "memory_store": Value(("memory_store",), ""),
            "description": Value(("description",), ""),
            "system_prompt": Value(("system_prompt",), ""),
            "voice": Value(("voice",), ""),
            "natural_voice": Value(("natural_voice",), False, bool),
            "model": Value(("model",), ""),
            "approval_mode": Value(("approval_mode",), ""),
            "skills": Value(("skills",), []),
            "tools": Value(("tools",), []),
            "triggers": Derived(
                lambda document: document.section().get(
                    "triggers", document.section().get("hooks", [])
                )
                or []
            ),
            "source": Value(("source",), "gideon"),
            "specialty": Value(("specialty",), ""),
            "route_hints": Value(("route_hints",), ""),
        },
    ),
    "memory-store": Record(
        "MemoryStoreConfig",
        {
            "description": Value(("description",), ""),
        },
    ),
    "config.agent.AgentConfig": Record(
        "AgentConfig",
        {
            "approval_mode": Value(("agent", "approval_mode"), "auto"),
            "provider": Value(("agent", "provider"), "native"),
            "sandbox": Value(("agent", "sandbox"), "auto"),
            "yolo": Value(("agent", "yolo"), False),
            "acp_concurrent_sessions": Value(
                ("agent", "acp_concurrent_sessions"), False
            ),
            "prompt_cache_enabled": Value(
                ("agent", "prompt_cache_enabled"), True, bool
            ),
            "orchestrator_skill": Derived(
                lambda document: document.section("agent").get(
                    "orchestrator_skill",
                    document.section("agent").get("conductor_skill", False),
                )
            ),
            "max_subagents": Value(("agent", "max_subagents"), 3),
            "spawn_min_memory_gb": Value(("agent", "spawn_min_memory_gb"), 4.0, float),
            "subagent_max_turns": Value(("agent", "subagent_max_turns"), 100),
            "subagent_timeout_secs": Value(("agent", "subagent_timeout_secs"), 1800),
            "subagent_cwd_allowed_roots": Value(
                ("agent", "subagent_cwd_allowed_roots"),
                ["~/workspace", "~/workplace"],
                list,
            ),
            "log_level": Value(
                ("agent", "log_level"), "WARNING", lambda value: value.upper()
            ),
            "bot_name": Value(
                ("agent", "bot_name"), "", definitions._sanitize_bot_name
            ),
            "soft_stop_budget_secs": Value(
                ("agent", "soft_stop_budget_secs"),
                10.0,
                lambda value: max(0.5, min(60.0, float(value))),
            ),
            "unattended_requires_verified_adapter": Value(
                ("agent", "unattended_requires_verified_adapter"), False, bool
            ),
            "runner_health_check_secs": Value(
                ("agent", "runner_health_check_secs"),
                3600,
                lambda value: max(60, min(86400, int(value))),
            ),
            "runner_idle_release_secs": Value(
                ("agent", "runner_idle_release_secs"),
                1800,
                lambda value: max(60, min(86400, int(value))),
            ),
            "durable_sessions": Value(("agent", "durable_sessions"), False, bool),
            "self_qa": Derived(
                lambda document: _decode(
                    "config.agent.AgentConfig.self_qa.SelfQaConfig", document
                )
            ),
        },
    ),
    "config.session.SessionConfig": Record(
        "SessionConfig",
        {
            "timeout_secs": Value(
                ("session", "timeout_secs"), definitions.DEFAULT_SESSION_TIMEOUT
            ),
            "autocompact_pct": Value(("session", "autocompact_pct"), 90.0, float),
            "pool_size": Value(("session", "pool_size"), 0, int),
            "pool_agent": Value(("session", "pool_agent"), "", str),
            "pool_ttl_secs": Value(("session", "pool_ttl_secs"), 1800, int),
            "auto_archive_days": Value(
                ("session", "auto_archive_days"),
                None,
                lambda value: definitions._safe_int(value, 30),
            ),
        },
    ),
    "config.loops.LoopsConfig": Record(
        "LoopsConfig",
        {
            "max_cycles_hard_cap": Value(("loops", "max_cycles_hard_cap"), 100),
            "default_idle_secs": Value(("loops", "default_idle_secs"), 120),
            "trust_ttl_secs": Value(("loops", "trust_ttl_secs"), 24 * 3600),
            "judge_use_case": Value(
                ("loops", "judge_use_case"), "reasoning", definitions._judge_axis
            ),
            "stagnation_window": Value(
                ("loops", "stagnation_window"), 5, definitions._stagnation_window
            ),
            "check_work_stages": Value(("loops", "check_work_stages"), False, bool),
            "worktree_sparse": Value(("loops", "worktree_sparse"), True, bool),
        },
    ),
    "config.memory.MemoryConfig": Record(
        "MemoryConfig",
        {
            "semantic_confidence_threshold": Value(
                ("memory", "semantic_confidence_threshold"), 0.8
            ),
            "episodic_dedup_threshold": Value(
                ("memory", "episodic_dedup_threshold"), 0.88
            ),
            "episodic_max_results": Value(("memory", "episodic_max_results"), 8),
            "episodic_max_count": Value(("memory", "episodic_max_count"), 10000),
            "semantic_keys": Value(("memory", "semantic_keys"), []),
            "history_idle_hours": Value(("memory", "history_idle_hours"), 3.0),
            "history_max_days": Value(("memory", "history_max_days"), 365),
            "migrated": Value(("memory", "migrated"), False),
            "l1_manifest": Value(("memory", "l1_manifest"), True),
            "active_recall": Value(("memory", "active_recall"), True),
            "active_recall_timeout_ms": Value(
                ("memory", "active_recall_timeout_ms"), 1500
            ),
            "proactive_commitments": Value(("memory", "proactive_commitments"), False),
            "proactive_commitments_max_per_day": Value(
                ("memory", "proactive_commitments_max_per_day"), 3
            ),
            "auto_promote_enabled": Value(("memory", "auto_promote_enabled"), True),
            "auto_promote_every_n": Value(("memory", "auto_promote_every_n"), 10),
            "auto_promote_max_per_run": Value(
                ("memory", "auto_promote_max_per_run"), 5
            ),
            "vault_mode": Derived(
                lambda document: definitions._vault_mode(document.section("memory"))
            ),
            "vault_path": Value(("memory", "vault_path"), "memory-vault"),
            "graph_enabled": Value(
                ("memory", "graph_enabled"), None, definitions._guard_flag
            ),
            "push_context": Value(("memory", "push_context"), False, bool),
            "push_min_confidence": Value(
                ("memory", "push_min_confidence"),
                0.7,
                lambda value: max(0.0, min(1.0, float(value or 0.7))),
            ),
            "graph_topology_in_context": Value(
                ("memory", "graph_topology_in_context"), False, bool
            ),
            "holder_attribution": Value(("memory", "holder_attribution"), False, bool),
            "slot_size_cap": Value(
                ("memory", "slot_size_cap"), 1400, lambda value: int(value or 1400)
            ),
        },
    ),
    "config.dashboard.DashboardConfig": Record(
        "DashboardConfig",
        {
            "url": Value(("dashboard", "url"), ""),
            "public_url": Value(
                ("dashboard", "public_url"), "", lambda value: str(value or "")
            ),
            "trusted_proxies": Value(
                ("dashboard", "trusted_proxies"),
                [],
                lambda value: [
                    str(p) for p in value or [] if isinstance(p, str) and str(p).strip()
                ],
            ),
            "restore_sessions": Value(("dashboard", "restore_sessions"), False),
            "restore_window_minutes": Value(
                ("dashboard", "restore_window_minutes"), 30
            ),
            "user_name": Value(("dashboard", "user_name"), ""),
            "username": Value(
                ("dashboard", "username"), "", definitions._slug_username
            ),
            "merge_queued_messages": Value(
                ("dashboard", "merge_queued_messages"), False
            ),
            "auto_tag_sessions": Value(("dashboard", "auto_tag_sessions"), True),
            "mcp_probe_timeout_secs": Value(
                ("dashboard", "mcp_probe_timeout_secs"),
                15,
                lambda value: definitions._safe_int(value, 15),
            ),
            "widget_density": Value(("dashboard", "widget_density"), "more"),
            "send_on_enter": Value(("dashboard", "send_on_enter"), True),
            "show_timestamps": Value(("dashboard", "show_timestamps"), False),
            "show_thinking_inline": Value(("dashboard", "show_thinking_inline"), False),
            "simplified_tool_names": Value(
                ("dashboard", "simplified_tool_names"), False
            ),
            "followup_chips": Value(("dashboard", "followup_chips"), True),
            "offer_check_work": Value(("dashboard", "offer_check_work"), True, bool),
            "stream_reveal": Value(("dashboard", "stream_reveal"), "smooth"),
            "auto_open_browser": Value(("dashboard", "auto_open_browser"), True),
            "update_dev_mode": Value(("dashboard", "update_dev_mode"), False),
            "screen_share_enabled": Value(
                ("dashboard", "screen_share_enabled"), False, bool
            ),
            "document_editing": Value(("dashboard", "document_editing"), False, bool),
            "terminal": Value(("dashboard", "terminal"), {"enabled": True}),
            "dashboard_layout": Value(
                ("dashboard", "dashboard_layout"), {}, lambda value: value or {}
            ),
        },
    ),
    "config.legibility.LegibilityConfig": Record(
        "LegibilityConfig",
        {
            "discover_tips": Value(("legibility", "discover_tips"), True, bool),
            "context_adapters": Value(("legibility", "context_adapters"), False, bool),
        },
    ),
    "config.ambient.AmbientConfig": Record(
        "AmbientConfig",
        {
            "tiles_enabled": Value(("ambient", "tiles_enabled"), True, bool),
            "max_tiles": Value(
                ("ambient", "max_tiles"),
                None,
                lambda value: definitions._safe_int(value, 12),
            ),
            "default_refresh_ttl_secs": Value(
                ("ambient", "default_refresh_ttl_secs"),
                None,
                lambda value: definitions._safe_int(value, 900),
            ),
            "genui_enabled": Value(("ambient", "genui_enabled"), True, bool),
            "surfaces_max_layer": Value(
                ("ambient", "surfaces_max_layer"),
                None,
                lambda value: definitions._safe_int(value, 2),
            ),
            "tray_enabled": Value(("ambient", "tray_enabled"), False, bool),
        },
    ),
    "config.companion.CompanionConfig": Record(
        "CompanionConfig",
        {
            "discovery_enabled": Value(("companion", "discovery_enabled"), False, bool),
            "instance_name": Value(
                ("companion", "instance_name"), "", lambda value: str(value or "")
            ),
        },
    ),
    "config.browse.BrowseConfig": Record(
        "BrowseConfig",
        {
            "user_browser_enabled": Value(
                ("browse", "user_browser_enabled"), False, bool
            ),
        },
    ),
    "config.mobile.MobileConfig": Record(
        "MobileConfig",
        {
            "push_backend": Value(
                ("mobile", "push_backend"),
                None,
                lambda value: definitions._safe_choice(
                    value or "webpush", definitions.PUSH_BACKENDS, "webpush"
                ),
            ),
            "ntfy_topic_url": Value(
                ("mobile", "ntfy_topic_url"), "", lambda value: str(value or "").strip()
            ),
            "relay_url": Value(
                ("mobile", "relay_url"), "", lambda value: str(value or "").strip()
            ),
        },
    ),
    "config.local_models.LocalModelsConfig": Record(
        "LocalModelsConfig",
        {
            "pressure_warn_pct": Value(
                ("local_models", "pressure_warn_pct"),
                None,
                lambda value: min(100, max(1, definitions._safe_int(value, 85))),
            ),
            "sidecar_restart_max": Value(
                ("local_models", "sidecar_restart_max"),
                None,
                lambda value: max(0, definitions._safe_int(value, 3)),
            ),
            "memory_reserve_gb": Value(
                ("local_models", "memory_reserve_gb"),
                None,
                lambda value: min(64.0, max(0.0, definitions._safe_float(value, 3.0))),
            ),
            "hide_unrunnable_models": Value(
                ("local_models", "hide_unrunnable_models"), True, bool
            ),
            "hf_whoami_ttl_secs": Value(
                ("local_models", "hf_whoami_ttl_secs"),
                None,
                lambda value: min(86400, max(0, definitions._safe_int(value, 300))),
            ),
            "selftest_timeout_secs": Value(
                ("local_models", "selftest_timeout_secs"),
                None,
                lambda value: min(600, max(1, definitions._safe_int(value, 60))),
            ),
        },
    ),
    "config.sources.SourcesConfig": Record(
        "SourcesConfig",
        {
            "enabled": Value(("sources", "enabled"), True, bool),
            "poll_interval_default_secs": Value(
                ("sources", "poll_interval_default_secs"),
                None,
                lambda value: definitions._safe_int(value, 3600),
            ),
            "network_floor_secs": Value(
                ("sources", "network_floor_secs"),
                None,
                lambda value: definitions._safe_int(value, 900),
            ),
            "max_sources": Value(
                ("sources", "max_sources"),
                None,
                lambda value: definitions._safe_int(value, 100),
            ),
            "max_items_per_poll": Value(
                ("sources", "max_items_per_poll"),
                None,
                lambda value: definitions._safe_int(value, 50),
            ),
            "daily_request_budget": Value(
                ("sources", "daily_request_budget"),
                None,
                lambda value: definitions._safe_int(value, 288),
            ),
        },
    ),
    "config.packs.PacksConfig": Record(
        "PacksConfig",
        {
            "skill_catalogs": Derived(
                lambda document: [
                    _decode(
                        "config.packs.PacksConfig.skill_catalogs.SkillCatalogConfig",
                        ConfigInput(c),
                    )
                    for c in document.section("packs").get("skill_catalogs", [])
                    if isinstance(c, dict) and str(c.get("url", "")).strip()
                ]
            ),
            "fingerprint_enabled": Value(
                ("packs", "fingerprint_enabled"), None, definitions._guard_flag
            ),
            "connector_catalog_url": Value(
                ("packs", "connector_catalog_url"), "", lambda value: str(value or "")
            ),
        },
    ),
    "config.apps.AppsConfig": Record(
        "AppsConfig",
        {
            "registry_source_enabled": Value(
                ("apps", "registry_source_enabled"), True, bool
            ),
            "bundled_source_enabled": Value(
                ("apps", "bundled_source_enabled"), True, bool
            ),
        },
    ),
    "config.updates.UpdatesConfig": Record(
        "UpdatesConfig",
        {
            "channel": Derived(lambda document: _update_channel(document)),
            "pin": Value(("updates", "pin"), "", lambda value: str(value or "")),
            "auto": Derived(lambda document: _update_auto(document)),
            "check_enabled": Value(("updates", "check_enabled"), True, bool),
            "check_interval_hours": Value(
                ("updates", "check_interval_hours"),
                None,
                lambda value: min(168, max(1, definitions._safe_int(value, 12))),
            ),
            "last_version": Value(
                ("updates", "last_version"), "", lambda value: str(value or "")
            ),
        },
    ),
    "config.durability.DurabilityConfig": Record(
        "DurabilityConfig",
        {
            "auto_backup": Value(
                ("durability", "auto_backup"), None, definitions._guard_flag
            ),
            "keep_daily": Value(
                ("durability", "keep_daily"),
                None,
                lambda value: definitions._safe_int(value, 14),
            ),
            "keep_weekly": Value(
                ("durability", "keep_weekly"),
                None,
                lambda value: definitions._safe_int(value, 8),
            ),
            "keep_monthly": Value(
                ("durability", "keep_monthly"),
                None,
                lambda value: definitions._safe_int(value, 12),
            ),
            "restore_drills": Value(
                ("durability", "restore_drills"), None, definitions._guard_flag
            ),
            "time_travel": Value(
                ("durability", "time_travel"), None, definitions._guard_flag
            ),
            "sync_enabled": Value(("durability", "sync_enabled"), False, bool),
            "sync_transport": Value(
                ("durability", "sync_transport"), "", lambda value: str(value or "")
            ),
            "sync_stale_after_secs": Value(
                ("durability", "sync_stale_after_secs"),
                None,
                lambda value: definitions._safe_int(value, 900),
            ),
            "sync_encrypt": Value(
                ("durability", "sync_encrypt"),
                "auto",
                lambda value: definitions._safe_choice(
                    value, ("auto", "on", "off"), "auto"
                ),
            ),
        },
    ),
    "config.proactive.ProactiveConfig": Record(
        "ProactiveConfig",
        {
            "triage_enabled": Value(("proactive", "triage_enabled"), False, bool),
            "digest_schedule": Value(
                ("proactive", "digest_schedule"),
                "",
                lambda value: str(value or "0 8 * * *"),
            ),
            "auto_execute_enabled": Value(
                ("proactive", "auto_execute_enabled"), False, bool
            ),
            "max_auto_actions_per_run": Value(
                ("proactive", "max_auto_actions_per_run"),
                None,
                lambda value: definitions._safe_int(value, 5),
            ),
            "classifier_gate_enabled": Value(
                ("proactive", "classifier_gate_enabled"), True, bool
            ),
            "decision_default_horizon_days": Value(
                ("proactive", "decision_default_horizon_days"),
                None,
                lambda value: definitions._safe_int(value, 90),
            ),
        },
    ),
    "config.evals.EvalsConfig": Record(
        "EvalsConfig",
        {
            "enabled": Value(("evals", "enabled"), False, bool),
            "study_default_k": Value(
                ("evals", "study_default_k"),
                None,
                lambda value: definitions._safe_int(value, 5),
            ),
            "judge_agreement_floor": Value(
                ("evals", "judge_agreement_floor"),
                0.6,
                lambda value: float(value or 0.6),
            ),
            "ablation_cadence_days": Value(
                ("evals", "ablation_cadence_days"),
                None,
                lambda value: definitions._safe_int(value, 30),
            ),
            "bakeoff_capture_enabled": Value(
                ("evals", "bakeoff_capture_enabled"), False, bool
            ),
            "default_budget_usd": Value(
                ("evals", "default_budget_usd"), 0.0, lambda value: float(value or 0.0)
            ),
        },
    ),
    "config.inbox.InboxConfig": Record(
        "InboxConfig",
        {
            "enabled": Value(("inbox", "enabled"), False, bool),
            "user_id": Value(("inbox", "user_id"), "", str),
            "watched_channels": Value(
                ("inbox", "watched_channels"),
                [],
                lambda value: [str(c) for c in value if isinstance(c, str)],
            ),
            "poll_interval_seconds": Value(
                ("inbox", "poll_interval_seconds"),
                60,
                lambda value: max(30, int(value)),
            ),
            "style_rules": Value(
                ("inbox", "style_rules"),
                [],
                lambda value: [str(r) for r in value if isinstance(r, str)],
            ),
            "test_mode": Value(("inbox", "test_mode"), False, bool),
            "engagement_ranking_enabled": Value(
                ("inbox", "engagement_ranking_enabled"), False, bool
            ),
            "engagement_half_life_days": Value(
                ("inbox", "engagement_half_life_days"),
                0.0,
                lambda value: float(value or 0.0),
            ),
        },
    ),
    "config.tools.ToolsConfig": Record(
        "ToolsConfig",
        {
            "projection_rules": Derived(
                lambda document: [
                    _decode(
                        "config.tools.ToolsConfig.projection_rules.ProjectionRuleConfig",
                        ConfigInput(r),
                    )
                    for r in document.section("tools").get("projection_rules", [])
                    if isinstance(r, dict) and str(r.get("match_regex", "")).strip()
                ]
            ),
            "bg_compress_enabled": Value(("tools", "bg_compress_enabled"), True, bool),
            "bg_compress_idle_days": Value(
                ("tools", "bg_compress_idle_days"), 7.0, float
            ),
            "groups_enabled": Value(("tools", "groups_enabled"), False, bool),
            "group_defaults": Value(
                ("tools", "group_defaults"),
                None,
                lambda value: {
                    str(k): [str(g) for g in v if isinstance(g, str)]
                    for k, v in (value or {}).items()
                    if isinstance(k, str) and isinstance(v, list)
                },
            ),
        },
    ),
    "config.feedback.FeedbackConfig": Record(
        "FeedbackConfig",
        {
            "enabled": Value(("feedback", "enabled"), True, bool),
            "retire_threshold": Value(("feedback", "retire_threshold"), 0.4, float),
            "min_n": Value(("feedback", "min_n"), 5, int),
            "window_days": Value(("feedback", "window_days"), 90, int),
        },
    ),
    "config.external_access.ExternalAccessConfig": Record(
        "ExternalAccessConfig",
        {
            "enabled": Value(
                ("external_access", "enabled"), None, definitions._expose_flag
            ),
            "openai": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig.openai.ExternalAccessSurfaceConfig",
                    document,
                )
            ),
            "mcp": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig.mcp.ExternalAccessSurfaceConfig",
                    document,
                )
            ),
            "a2a": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig.a2a.ExternalAccessSurfaceConfig",
                    document,
                )
            ),
            "capture": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig.capture.CaptureSurfaceConfig",
                    document,
                )
            ),
            "bridge": Derived(
                lambda document: _decode(
                    "config.external_access.ExternalAccessConfig.bridge.ExternalAccessSurfaceConfig",
                    document,
                )
            ),
            "public_url": Value(
                ("external_access", "public_url"), "", lambda value: str(value or "")
            ),
            "rate_rps": Value(
                ("external_access", "rate_rps"),
                None,
                lambda value: definitions._num(value, 1.0),
            ),
            "rate_burst": Value(
                ("external_access", "rate_burst"),
                None,
                lambda value: int(definitions._num(value, 20)),
            ),
            "rate_concurrent": Value(
                ("external_access", "rate_concurrent"),
                None,
                lambda value: int(definitions._num(value, 4)),
            ),
            "auto_disable_after_breaches": Value(
                ("external_access", "auto_disable_after_breaches"),
                None,
                lambda value: int(definitions._num(value, 10)),
            ),
            "capture_retention_days": Derived(
                lambda document: int(
                    definitions._capture_retention(document.section("external_access"))
                )
            ),
        },
    ),
    "config.agents_routing.AgentsRoutingConfig": Record(
        "AgentsRoutingConfig",
        {
            "enabled": Value(("agents_routing", "enabled"), True, bool),
            "min_confidence": Value(("agents_routing", "min_confidence"), 0.62, float),
            "cooldown_hours": Value(("agents_routing", "cooldown_hours"), 24.0, float),
        },
    ),
    "config.planning.PlanningConfig": Record(
        "PlanningConfig",
        {
            "scratchpad_path": Value(
                ("planning", "scratchpad_path"), "", lambda value: str(value or "")
            ),
        },
    ),
    "config.skills.SkillsConfig": Record(
        "SkillsConfig",
        {
            "max_triggered": Value(("skills", "max_triggered"), 3, int),
            "auto_create_from_sessions": Value(
                ("skills", "auto_create_from_sessions"), False, bool
            ),
            "auto_refine_on_deviation": Value(
                ("skills", "auto_refine_on_deviation"), False, bool
            ),
            "auto_min_tool_calls": Value(("skills", "auto_min_tool_calls"), 5, int),
            "auto_similarity_threshold": Value(
                ("skills", "auto_similarity_threshold"), 0.85, float
            ),
            "progressive_disclosure_threshold": Value(
                ("skills", "progressive_disclosure_threshold"), 8, int
            ),
        },
    ),
    "config.workflows.WorkflowsConfig": Record(
        "WorkflowsConfig",
        {
            "enabled": Value(("workflows", "enabled"), True, bool),
            "max_active_runs": Value(
                ("workflows", "max_active_runs"),
                10,
                lambda value: definitions._safe_int(value, 10),
            ),
            "self_schedule_max_outstanding": Value(
                ("workflows", "self_schedule_max_outstanding"),
                20,
                lambda value: definitions._safe_int(value, 20),
            ),
            "max_concurrent_nodes": Value(
                ("workflows", "max_concurrent_nodes"),
                6,
                lambda value: definitions._safe_int(value, 6),
            ),
            "default_node_timeout_total_secs": Value(
                ("workflows", "default_node_timeout_total_secs"),
                900,
                lambda value: definitions._safe_int(value, 900),
            ),
            "default_node_timeout_stall_secs": Value(
                ("workflows", "default_node_timeout_stall_secs"),
                300,
                lambda value: definitions._safe_int(value, 300),
            ),
            "retention_per_def": Value(
                ("workflows", "retention_per_def"),
                100,
                lambda value: definitions._safe_int(value, 100),
            ),
            "max_concurrent_llm_nodes": Value(
                ("workflows", "max_concurrent_llm_nodes"),
                4,
                lambda value: definitions._safe_int(value, 4),
            ),
            "max_concurrent_io_nodes": Value(
                ("workflows", "max_concurrent_io_nodes"),
                2,
                lambda value: definitions._safe_int(value, 2),
            ),
            "model_tier_reasoning": Value(
                ("workflows", "model_tier_reasoning"),
                "reasoning",
                lambda value: str(value or "reasoning"),
            ),
            "model_tier_standard": Value(
                ("workflows", "model_tier_standard"),
                "orchestration",
                lambda value: str(value or "orchestration"),
            ),
            "model_tier_fast": Value(
                ("workflows", "model_tier_fast"),
                "background",
                lambda value: str(value or "background"),
            ),
            "match_threshold": Value(
                ("workflows", "match_threshold"),
                0.62,
                lambda value: max(0.0, min(1.0, float(value or 0.62))),
            ),
            "surface_mode_default": Value(
                ("workflows", "surface_mode_default"),
                None,
                definitions._surface_mode_default,
            ),
            "max_materialized_per_foreach": Value(
                ("workflows", "max_materialized_per_foreach"),
                20,
                lambda value: definitions._safe_int(value, 20),
            ),
            "confirmation_ttl_secs": Value(
                ("workflows", "confirmation_ttl_secs"),
                7 * 24 * 3600,
                lambda value: definitions._safe_int(value, 7 * 24 * 3600),
            ),
            "lease_ttl_secs": Value(
                ("workflows", "lease_ttl_secs"),
                900,
                lambda value: definitions._safe_int(value, 900),
            ),
            "default_quiet_windows": Value(
                ("workflows", "default_quiet_windows"),
                "",
                lambda value: str(value or "").strip(),
            ),
            "duty_gate_default": Value(
                ("workflows", "duty_gate_default"),
                "",
                lambda value: str(value or "").strip(),
            ),
            "workspace_default_mode": Value(
                ("workflows", "workspace_default_mode"),
                None,
                definitions._workspace_default_mode,
            ),
            "workspace_teardown_on_expiry": Value(
                ("workflows", "workspace_teardown_on_expiry"), True, bool
            ),
        },
    ),
    "config.learning.LearningConfig": Record(
        "LearningConfig",
        {
            "enabled": Value(("learning", "enabled"), True, bool),
            "min_tool_calls": Value(("learning", "min_tool_calls"), 4, int),
            "correction_heuristic": Value(
                ("learning", "correction_heuristic"), True, bool
            ),
            "surface_chip": Value(("learning", "surface_chip"), True, bool),
            "skill_ladder": Value(("learning", "skill_ladder"), True, bool),
            "min_evidence": Value(
                ("learning", "min_evidence"), 3, lambda value: int(value or 3)
            ),
            "min_lesson_confidence": Value(
                ("learning", "min_lesson_confidence"),
                None,
                lambda value: definitions._safe_float(value, 0.5),
            ),
            "staging_enabled": Value(("learning", "staging_enabled"), True, bool),
            "self_model_enabled": Value(("learning", "self_model_enabled"), True, bool),
            "min_session_score": Value(
                ("learning", "min_session_score"),
                0.0,
                lambda value: float(value or 0.0),
            ),
            "context_budget_tokens": Value(
                ("learning", "context_budget_tokens"),
                4000,
                lambda value: int(value or 4000),
            ),
            "curator_enabled": Value(("learning", "curator_enabled"), True, bool),
            "propose_quota_per_run": Value(
                ("learning", "propose_quota_per_run"), 5, lambda value: int(value or 5)
            ),
            "replay_enabled": Value(("learning", "replay_enabled"), False, bool),
            "replay_max_dollars": Value(
                ("learning", "replay_max_dollars"),
                None,
                lambda value: max(0.0, definitions._safe_float(value, 0.0)),
            ),
            "run_end_enabled": Value(("learning", "run_end_enabled"), True, bool),
            "attribution_enabled": Value(
                ("learning", "attribution_enabled"), True, bool
            ),
            "identity_report_cadence": Value(
                ("learning", "identity_report_cadence"),
                "monthly",
                definitions._identity_report_cadence,
            ),
        },
    ),
    "config.knowledge.KnowledgeConfig": Record(
        "KnowledgeConfig",
        {
            "idempotent_persist": Value(
                ("knowledge", "idempotent_persist"), True, bool
            ),
            "require_citations": Value(("knowledge", "require_citations"), True, bool),
            "report_budget_chars": Value(
                ("knowledge", "report_budget_chars"),
                40000,
                lambda value: int(value or 40000),
            ),
            "default_ttl": Value(
                ("knowledge", "default_ttl"), "", lambda value: str(value or "")
            ),
            "max_mentions_per_claim": Value(
                ("knowledge", "max_mentions_per_claim"),
                20,
                lambda value: int(value or 20),
            ),
            "synthesis_window": Value(
                ("knowledge", "synthesis_window"), 20, lambda value: int(value or 20)
            ),
            "lint_every_n_persists": Value(
                ("knowledge", "lint_every_n_persists"),
                12,
                lambda value: int(value or 12),
            ),
            "embed_batch_size": Value(
                ("knowledge", "embed_batch_size"), 32, lambda value: int(value or 32)
            ),
            "embed_retry_budget": Value(
                ("knowledge", "embed_retry_budget"), 3, lambda value: int(value or 3)
            ),
            "maintenance_max_staleness_secs": Value(
                ("knowledge", "maintenance_max_staleness_secs"),
                900,
                lambda value: int(value or 900),
            ),
            "similarity_min_score": Value(
                ("knowledge", "similarity_min_score"),
                None,
                lambda value: min(
                    1.0, max(0.0, definitions._safe_float(value, 0.55)) or 0.55
                ),
            ),
            "similarity_top_k": Value(
                ("knowledge", "similarity_top_k"),
                None,
                lambda value: max(1, definitions._safe_int(value, 8) or 8),
            ),
            "similarity_degree_cap": Value(
                ("knowledge", "similarity_degree_cap"),
                None,
                lambda value: max(1, definitions._safe_int(value, 32) or 32),
            ),
            "reranker_enabled": Value(("knowledge", "reranker_enabled"), False, bool),
            "reranker_model": Value(
                ("knowledge", "reranker_model"),
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                lambda value: str(
                    value or "cross-encoder/ms-marco-MiniLM-L-6-v2"
                ).strip(),
            ),
            "reranker_max_candidates": Value(
                ("knowledge", "reranker_max_candidates"),
                None,
                lambda value: min(128, max(1, definitions._safe_int(value, 32) or 32)),
            ),
            "consolidate_min_cluster": Value(
                ("knowledge", "consolidate_min_cluster"),
                5,
                lambda value: int(value or 5),
            ),
            "consolidate_min_hours": Value(
                ("knowledge", "consolidate_min_hours"), 6, lambda value: int(value or 6)
            ),
            "session_brief_max_tokens": Value(
                ("knowledge", "session_brief_max_tokens"),
                800,
                lambda value: int(value or 800),
            ),
            "conflict_model_pass": Value(
                ("knowledge", "conflict_model_pass"), True, bool
            ),
            "auto_ingest_artifacts": Value(
                ("knowledge", "auto_ingest_artifacts"), True, bool
            ),
            "vault_mode": Value(
                ("knowledge", "vault_mode"),
                "",
                lambda value: (
                    str(value or "").strip().lower()
                    if str(value or "").strip().lower()
                    in definitions.MEMORY_VAULT_MODES
                    else "off"
                ),
            ),
            "vault_path": Value(
                ("knowledge", "vault_path"),
                "",
                lambda value: str(value or "knowledge-vault"),
            ),
        },
    ),
    "config.security.SecurityConfig": Record(
        "SecurityConfig",
        {
            "denied_commands": Value(
                ("security", "denied_commands"),
                [],
                lambda value: [str(p) for p in value if isinstance(p, str)],
            ),
            "credential_keychain": Value(
                ("security", "credential_keychain"), None, lambda value: value is True
            ),
            "egress": Derived(
                lambda document: _decode(
                    "config.security.SecurityConfig.egress.EgressConfig", document
                )
            ),
            "autonomy_denylist": Value(
                ("security", "autonomy_denylist"),
                [],
                lambda value: [d for d in value or [] if isinstance(d, dict)],
            ),
        },
    ),
    "config.auth.AuthConfigSection": Record(
        "AuthConfigSection",
        {
            "login_enabled": Value(("auth", "login_enabled"), False, bool),
            "session_ttl": Value(
                ("auth", "session_ttl"), "30d", lambda value: str(value or "30d")
            ),
            "require_totp": Value(("auth", "require_totp"), False, bool),
            "lockout_threshold": Value(
                ("auth", "lockout_threshold"),
                5,
                lambda value: max(1, definitions._safe_int(value, 5)),
            ),
            "lockout_window": Value(
                ("auth", "lockout_window"), "15m", lambda value: str(value or "15m")
            ),
        },
    ),
    "config.routing.RoutingConfig": Record(
        "RoutingConfig",
        {
            "enabled": Value(("routing", "enabled"), False, bool),
            "local_timeout_secs": Value(
                ("routing", "local_timeout_secs"),
                20.0,
                lambda value: max(0.0, definitions._safe_float(value, 20.0)),
            ),
            "min_samples": Value(
                ("routing", "min_samples"),
                5,
                lambda value: max(1, definitions._safe_int(value, 5)),
            ),
            "weights": Derived(
                lambda document: _decode(
                    "config.routing.RoutingConfig.weights.RoutingWeightsConfig",
                    document,
                )
            ),
            "hysteresis": Value(
                ("routing", "hysteresis"),
                0.05,
                lambda value: max(0.0, definitions._safe_float(value, 0.05)),
            ),
            "cloud_quality_margin": Value(
                ("routing", "cloud_quality_margin"),
                0.1,
                lambda value: max(0.0, definitions._safe_float(value, 0.1)),
            ),
            "energy_sampling": Value(("routing", "energy_sampling"), False, bool),
            "reproposal_cooldown_days": Value(
                ("routing", "reproposal_cooldown_days"),
                14,
                lambda value: max(0, definitions._safe_int(value, 14)),
            ),
        },
    ),
    "config.guardrails.GuardrailsConfig": Record(
        "GuardrailsConfig",
        {
            "budgets": Derived(
                lambda document: _decode(
                    "config.guardrails.GuardrailsConfig.budgets.BudgetConfig", document
                )
            ),
            "breaker": Derived(
                lambda document: _decode(
                    "config.guardrails.GuardrailsConfig.breaker.BreakerConfig", document
                )
            ),
            "autonomy": Derived(
                lambda document: _decode(
                    "config.guardrails.GuardrailsConfig.autonomy.AutonomyConfig",
                    document,
                )
            ),
            "scan_mode": Value(
                ("guardrails", "scan_mode"),
                "redact",
                lambda value: (
                    str(value) if value in ("warn", "redact", "block") else "redact"
                ),
            ),
        },
    ),
    "config.voice.VoiceConfig": Record(
        "VoiceConfig",
        {
            "confirmation_phrases": Value(
                ("voice", "confirmation_phrases"),
                None,
                lambda value: definitions._voice_phrases(value)
                or list(definitions.DEFAULT_CONFIRMATION_PHRASES),
            ),
            "exit_phrases": Value(
                ("voice", "exit_phrases"),
                None,
                lambda value: definitions._voice_phrases(value)
                or list(definitions.DEFAULT_EXIT_PHRASES),
            ),
            "push_to_talk_chord": Value(
                ("voice", "push_to_talk_chord"),
                None,
                lambda value: str(value or "").strip()
                or definitions.DEFAULT_PUSH_TO_TALK_CHORD,
            ),
            "echo_filter_enabled": Value(("voice", "echo_filter_enabled"), True, bool),
            "duplex_mute_enabled": Value(("voice", "duplex_mute_enabled"), True, bool),
            "clean_for_speech_enabled": Value(
                ("voice", "clean_for_speech_enabled"), True, bool
            ),
            "voice_disclaimer_enabled": Value(
                ("voice", "voice_disclaimer_enabled"), True, bool
            ),
        },
    ),
    "config.resilience.ResilienceConfig": Record(
        "ResilienceConfig",
        {
            "doctor_enabled": Value(
                ("resilience", "doctor_enabled"), None, definitions._guard_flag
            ),
            "degraded_indicator": Value(
                ("resilience", "degraded_indicator"), None, definitions._guard_flag
            ),
            "mid_turn_policy": Value(
                ("resilience", "mid_turn_policy"),
                "queue",
                lambda value: (
                    str(value)
                    if value in ("queue", "steer", "cancel_and_replace")
                    else "queue"
                ),
            ),
            "cancel_replace_min_interval_secs": Value(
                ("resilience", "cancel_replace_min_interval_secs"),
                2.0,
                lambda value: max(0.0, float(value)),
            ),
            "remediation": Derived(
                lambda document: _decode(
                    "config.resilience.ResilienceConfig.remediation.RemediationConfig",
                    document,
                )
            ),
        },
    ),
    "config.sandbox.SandboxConfig": Record(
        "SandboxConfig",
        {
            "nofile": Value(
                ("sandbox", "nofile"),
                4096,
                lambda value: max(0, definitions._safe_int(value, 4096)),
            ),
            "max_pids": Value(
                ("sandbox", "max_pids"),
                0,
                lambda value: max(0, definitions._safe_int(value, 0)),
            ),
            "max_rss_mb": Value(
                ("sandbox", "max_rss_mb"),
                0,
                lambda value: max(0, definitions._safe_int(value, 0)),
            ),
            "cgroup_scopes": Value(
                ("sandbox", "cgroup_scopes"), None, definitions._expose_flag
            ),
            "env_passthrough": Value(
                ("sandbox", "env_passthrough"),
                None,
                lambda value: [str(n).strip() for n in value or [] if str(n).strip()],
            ),
        },
    ),
    "config.checkpoints.CheckpointsConfig": Record(
        "CheckpointsConfig",
        {
            "enabled": Value(("checkpoints", "enabled"), True, bool),
            "max_mb": Value(
                ("checkpoints", "max_mb"),
                200,
                lambda value: max(0, definitions._safe_int(value, 200)),
            ),
            "max_turns": Value(
                ("checkpoints", "max_turns"),
                50,
                lambda value: max(1, definitions._safe_int(value, 50)),
            ),
            "max_file_mb": Value(
                ("checkpoints", "max_file_mb"),
                8,
                lambda value: max(0, definitions._safe_int(value, 8)),
            ),
        },
    ),
    "config.agent.AgentConfig.self_qa.SelfQaConfig": Record(
        "SelfQaConfig",
        {
            "enabled": Value(("agent", "self_qa", "enabled"), False, bool),
            "watched_repo": Value(
                ("agent", "self_qa", "watched_repo"), "", lambda value: str(value or "")
            ),
            "fix_branch_enabled": Value(
                ("agent", "self_qa", "fix_branch_enabled"), False, bool
            ),
            "max_scenarios_per_fire": Value(
                ("agent", "self_qa", "max_scenarios_per_fire"),
                3,
                lambda value: max(1, min(20, int(value))),
            ),
        },
    ),
    "config.packs.PacksConfig.skill_catalogs.SkillCatalogConfig": Record(
        "SkillCatalogConfig",
        {
            "name": Value(("name",), "", str),
            "url": Value(("url",), "", str),
            "kind": Value(("kind",), "index", lambda value: str(value or "index")),
        },
    ),
    "config.tools.ToolsConfig.projection_rules.ProjectionRuleConfig": Record(
        "ProjectionRuleConfig",
        {
            "name": Value(("name",), "", str),
            "match_regex": Value(("match_regex",), "", str),
            "strategy": Value(("strategy",), "log", str),
            "head": Value(("head",), 0, lambda value: int(value or 0)),
            "tail": Value(("tail",), 0, lambda value: int(value or 0)),
            "keep": Value(("keep",), "", str),
            "skip": Value(("skip",), "", str),
            "count": Value(("count",), "", str),
        },
    ),
    "config.external_access.ExternalAccessConfig.openai.ExternalAccessSurfaceConfig": Record(
        "ExternalAccessSurfaceConfig",
        {
            "enabled": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "openai"
                    ).get("enabled")
                )
            ),
            "allow_remote": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "openai"
                    ).get("allow_remote")
                )
            ),
        },
    ),
    "config.external_access.ExternalAccessConfig.mcp.ExternalAccessSurfaceConfig": Record(
        "ExternalAccessSurfaceConfig",
        {
            "enabled": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "mcp"
                    ).get("enabled")
                )
            ),
            "allow_remote": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "mcp"
                    ).get("allow_remote")
                )
            ),
        },
    ),
    "config.external_access.ExternalAccessConfig.a2a.ExternalAccessSurfaceConfig": Record(
        "ExternalAccessSurfaceConfig",
        {
            "enabled": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "a2a"
                    ).get("enabled")
                )
            ),
            "allow_remote": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "a2a"
                    ).get("allow_remote")
                )
            ),
        },
    ),
    "config.external_access.ExternalAccessConfig.capture.CaptureSurfaceConfig": Record(
        "CaptureSurfaceConfig",
        {
            "enabled": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "capture"
                    ).get("enabled")
                )
            ),
            "allow_remote": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "capture"
                    ).get("allow_remote")
                )
            ),
            "retention_days": Derived(
                lambda document: int(
                    definitions._capture_retention(document.section("external_access"))
                )
            ),
            "upstream_allowlist": Derived(
                lambda document: definitions._str_list(
                    definitions._ea_surface_data(
                        document.section("external_access"), "capture"
                    ).get("upstream_allowlist")
                )
            ),
        },
    ),
    "config.external_access.ExternalAccessConfig.bridge.ExternalAccessSurfaceConfig": Record(
        "ExternalAccessSurfaceConfig",
        {
            "enabled": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "bridge"
                    ).get("enabled")
                )
            ),
            "allow_remote": Derived(
                lambda document: definitions._expose_flag(
                    definitions._ea_surface_data(
                        document.section("external_access"), "bridge"
                    ).get("allow_remote")
                )
            ),
        },
    ),
    "config.security.SecurityConfig.egress.EgressConfig": Record(
        "EgressConfig",
        {
            "allow_hosts": Value(
                ("security", "egress"),
                {},
                lambda value: [
                    str(h)
                    for h in (value or {}).get("allow_hosts", [])
                    if isinstance(h, str)
                ],
            ),
            "deny_hosts": Value(
                ("security", "egress"),
                {},
                lambda value: [
                    str(h)
                    for h in (value or {}).get("deny_hosts", [])
                    if isinstance(h, str)
                ],
            ),
            "allow_private": Value(
                ("security", "egress"),
                {},
                lambda value: bool((value or {}).get("allow_private", False)),
            ),
        },
    ),
    "config.routing.RoutingConfig.weights.RoutingWeightsConfig": Record(
        "RoutingWeightsConfig",
        {
            "success": Value(
                ("routing", "weights", "success"),
                0.6,
                lambda value: max(0.0, definitions._safe_float(value, 0.6)),
            ),
            "feedback": Value(
                ("routing", "weights", "feedback"),
                0.4,
                lambda value: max(0.0, definitions._safe_float(value, 0.4)),
            ),
        },
    ),
    "config.guardrails.GuardrailsConfig.budgets.BudgetConfig": Record(
        "BudgetConfig",
        {
            "max_tokens_per_run": Value(
                ("guardrails", "budgets", "max_tokens_per_run"),
                0,
                lambda value: max(0, int(value)),
            ),
            "max_tokens_per_day": Value(
                ("guardrails", "budgets", "max_tokens_per_day"),
                0,
                lambda value: max(0, int(value)),
            ),
            "max_dollars_per_day": Value(
                ("guardrails", "budgets", "max_dollars_per_day"),
                0.0,
                lambda value: max(0.0, float(value)),
            ),
        },
    ),
    "config.guardrails.GuardrailsConfig.breaker.BreakerConfig": Record(
        "BreakerConfig",
        {
            "failure_threshold": Value(
                ("guardrails", "breaker", "failure_threshold"),
                5,
                lambda value: max(1, int(value)),
            ),
            "recovery_secs": Value(
                ("guardrails", "breaker", "recovery_secs"),
                30.0,
                lambda value: max(0.0, float(value)),
            ),
        },
    ),
    "config.guardrails.GuardrailsConfig.autonomy.AutonomyConfig": Record(
        "AutonomyConfig",
        {
            "clean_approvals": Value(
                ("guardrails", "autonomy", "clean_approvals"),
                10,
                lambda value: max(1, definitions._safe_int(value, 10)),
            ),
            "min_days": Value(
                ("guardrails", "autonomy", "min_days"),
                7,
                lambda value: max(0, definitions._safe_int(value, 7)),
            ),
            "max_rejections": Value(
                ("guardrails", "autonomy", "max_rejections"),
                0,
                lambda value: max(0, definitions._safe_int(value, 0)),
            ),
            "cooldown_days": Value(
                ("guardrails", "autonomy", "cooldown_days"),
                14,
                lambda value: max(0, definitions._safe_int(value, 14)),
            ),
            "evidence_window_days": Value(
                ("guardrails", "autonomy", "evidence_window_days"),
                30,
                lambda value: max(1, definitions._safe_int(value, 30)),
            ),
        },
    ),
    "config.resilience.ResilienceConfig.remediation.RemediationConfig": Record(
        "RemediationConfig",
        {
            "enabled": Value(
                ("resilience", "remediation", "enabled"), None, definitions._guard_flag
            ),
            "target_score": Value(
                ("resilience", "remediation", "target_score"),
                90,
                lambda value: max(0, min(100, int(value))),
            ),
            "max_cost_usd": Value(
                ("resilience", "remediation", "max_cost_usd"),
                1.0,
                lambda value: max(0.0, float(value)),
            ),
            "idle_minutes_healthy": Value(
                ("resilience", "remediation", "idle_minutes_healthy"),
                60,
                lambda value: max(1, int(value)),
            ),
            "tick_minutes_degraded": Value(
                ("resilience", "remediation", "tick_minutes_degraded"),
                5,
                lambda value: max(1, int(value)),
            ),
        },
    ),
}
