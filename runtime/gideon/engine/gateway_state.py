from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LaunchSettings:
    no_dashboard: bool = False
    no_crons: bool = False
    no_open: bool = False
    port_override: str | None = None
    json_ready: bool = False
    approval_mode: str | None = None

    def initialize(self, runtime: Any, configuration: Any) -> None:
        from gideon.core.config.loader import (
            CRED_OWNER_ID,
            CRED_SLACK_APP_TOKEN,
            CRED_SLACK_BOT_TOKEN,
        )

        runtime._cfg = configuration
        for field, value in vars(self).items():
            setattr(runtime, f"_{field}", value)
        credentials = configuration.load_credentials()
        for attribute, key in (
            ("_app_token", CRED_SLACK_APP_TOKEN),
            ("_bot_token", CRED_SLACK_BOT_TOKEN),
            ("_owner_id", CRED_OWNER_ID),
        ):
            setattr(runtime, attribute, credentials.get(key, ""))
        runtime._slack_enabled = bool(runtime._app_token and runtime._bot_token)
        empty_services = (
            "sessions",
            "ctx_builder",
            "conv_log",
            "consolidator",
            "_file_watch_task",
            "_web_watch_task",
            "_clock_task",
            "_reaper_task",
            "heartbeat_svc",
            "loop_watchdog",
            "workflow_watchdog",
            "inbox_svc",
            "subagent_mgr",
            "channel_history",
            "dashboard_state",
            "_dashboard_runner",
        )
        for attribute in empty_services:
            setattr(runtime, attribute, None)
        for attribute, factory in (
            ("_running_script_ids", set),
            ("_cron_injecting", dict),
            ("_background_tasks", set),
            ("_handler_tasks", set),
            ("_session_tasks", dict),
            ("_pending_queue", dict),
        ):
            setattr(runtime, attribute, factory())
        runtime._last_autonomy_scan = 0.0
