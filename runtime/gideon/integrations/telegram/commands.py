"""Telegram commands backed by Gideon's own sessions, skills and delegation."""

from __future__ import annotations
import asyncio
import json
from .api import TelegramError

COMMANDS = {
    "help": "Show available commands",
    "people": "List people and relationship rings",
    "care": "Show overdue or missing relationship touchpoints",
    "commands": "List commands",
    "whoami": "Your identity and access",
    "status": "Current conversation",
    "new": "Start a new conversation",
    "reset": "Reset this topic",
    "stop": "Stop the current response",
    "topic": "Manage parallel conversations",
    "resume": "Restore a conversation",
    "sessions": "List your conversations",
    "history": "Recent conversation messages",
    "title": "Rename this conversation",
    "skills": "List available skills",
    "usage": "Current conversation usage",
    "context": "Current conversation context",
    "background": "Run a task in the background",
    "retry": "Retry your last prompt",
    "sethome": "Use this chat for notifications",
    "voice": "Turn voice replies on or off",
    "agents": "List your agents",
    "compress": "Compact this conversation",
    "reasoning": "Reasoning effort when supported",
    "approvals": "View or change conversation approvals",
    "yolo": "Control global auto-approval when permitted",
}
HOSTED_EXCLUSIONS = {
    "model",
    "provider",
    "fast",
    "codex",
    "update",
    "restart",
}


def menu(config):
    priority = config.get("command_menu", {})
    selected = [v for v in priority.get("priority", []) if v in COMMANDS]
    keys = list(COMMANDS)
    if priority.get("priority_mode") == "append":
        keys += selected
    else:
        keys = selected + keys
    return [{"command": k, "description": COMMANDS[k]} for k in dict.fromkeys(keys)][
        : max(1, min(100, int(priority.get("max_commands", 60))))
    ]


async def extra_command(transport, cm, command, argument):
    if command in ("people", "care"):
        from .policy import command_allowed
        from gideon.workspace.capabilities.communications import PeopleStore, PeopleError
        from gideon.workspace.capabilities.communications.telegram import command as people_command

        if not command_allowed(transport.config, cm, command):
            return "Your Telegram role does not allow that command."
        if argument:
            return f"/{command} does not accept arguments."
        try:
            return (await asyncio.to_thread(people_command, PeopleStore(), "/" + command))["text"]
        except PeopleError as exc:
            return str(exc)
    state = transport.services.dashboard_state
    session = state.get_linked_session(cm.thread_id)
    if command in HOSTED_EXCLUSIONS:
        return "Model selection, runtime maintenance and security policy are managed by hosted Gideon."
    if command == "commands":
        return "\n".join(f"/{key} — {value}" for key, value in COMMANDS.items())
    if command == "resume":
        if not argument:
            return "Use /sessions to find an unlinked conversation, then /resume <session-id>."
        restored = transport.topics.restore(state, cm, argument)
        return f"Restored {restored.title} ({restored.key})."
    if command == "agents":
        from gideon.core.config.loader import AppConfig

        config = await asyncio.to_thread(AppConfig.load)
        return "\n".join(config.agents) or "Gideon"
    if command == "compress":
        if session and session.running:
            return "Stop the current response before compacting."
        cm.text = "/compact" + (" " + argument if argument else "")
        return None
    if command in ("approvals", "yolo"):
        if cm.sender != str(transport.config.get("owner_id", "")):
            return "Only the paired owner may change approval settings."
        from gideon.integrations.channel_trust import is_allowed_sender

        if not is_allowed_sender("telegram", cm.sender):
            return "Pair your Telegram account before changing approvals."
        if not argument:
            mode = (
                "yolo"
                if state.is_yolo_active()
                else (
                    "trust"
                    if session and session._trust
                    else "trust_reads" if session and session._trust_reads else "normal"
                )
            )
            return f"Current approval mode: {mode}. /approvals normal|trust_reads|trust changes this conversation; /yolo on|off controls all conversations, subject to the operator ceiling."
        mode = (
            {"on": "yolo", "off": "normal"}.get(argument, "")
            if command == "yolo"
            else argument
        )
        if mode not in ("normal", "trust_reads", "trust", "yolo"):
            return "Use normal, trust_reads or trust; /yolo on|off controls global auto-approval."
        from gideon.security.guardrails.ladder import approval_screening_verdict

        screening = approval_screening_verdict(mode)
        if not screening.allowed:
            return screening.reason
        if mode == "yolo":
            state.enable_yolo()
        elif command == "yolo":
            state.disable_yolo()
        elif session:
            session._trust = mode == "trust"
            session._trust_reads = mode == "trust_reads"
        else:
            return "Start a conversation first."
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        if state.sessions:
            targets = (
                list(state._sessions.values())
                if command == "yolo" or mode == "yolo"
                else [session]
            )
            for target in targets:
                state.sessions.set_approval_policy(
                    _history_key_for(target.key),
                    "auto" if target._trust or state.is_yolo_active() else "",
                )
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=f"telegram:{cm.sender}",
            operation=f"mode_change:{mode}",
            outcome="enabled",
            resources=session.key if session else "all-sessions",
        )
        state.push_sessions_update()
        return f"Approval mode changed to {mode}. Existing pending prompts still need a decision."
    if command == "skills":
        from gideon.extensions.skills.loader import ProcedureLibrary

        rows = await asyncio.to_thread(
            ProcedureLibrary(install_builtins=False).list_skills
        )
        return (
            "\n".join(f"{v['key']}: {v.get('description', '')}" for v in rows)
            or "No skills installed."
        )
    if command in ("sethome", "voice"):
        if cm.sender != str(transport.config.get("owner_id", "")):
            return "Only the paired owner can change bot settings."
        if command == "voice" and argument not in ("on", "off"):
            return "/voice on or /voice off"
        from gideon.extensions.apps.app_config import write_config
        from gideon.extensions.apps.app_manager import _manifest_of

        manifest = _manifest_of("telegram-channel")
        update = (
            {"voice_replies": argument == "on"}
            if command == "voice"
            else {"home_channel": cm.channel_id, "home_topic": cm.thread_id}
        )
        persisted = update
        if transport.slot != "primary":
            from .policy import configured
            from gideon.extensions.apps.app_config import read_config

            preferences = configured(
                read_config("telegram-channel").get("bot_preferences"), {}
            )
            preferences[transport.slot] = {
                **preferences.get(transport.slot, {}),
                **update,
            }
            persisted = {"bot_preferences": json.dumps(preferences)}
        await asyncio.to_thread(
            write_config,
            "telegram-channel",
            persisted,
            manifest.provider.settingsSchema,
        )
        transport.config.update(update)
        return (
            "Voice replies " + argument + "."
            if command == "voice"
            else "Notifications will arrive in this chat and topic."
        )
    if not session:
        return "Send a message to start this conversation first."
    if command == "title":
        if not argument:
            return session.title
        session.title = argument[:128]
        session._titled = True
        session._dirty = True
        state.push_session_title(session.key, session.title)
        from gideon.interfaces.dashboard.chat_persistence import save_session_to_history

        save_session_to_history(state, session)
        await transport.topics.rename(transport, cm, session)
        return "Conversation renamed."
    if command == "reasoning":
        from gideon.interfaces.dashboard.chat_persistence import (
            _validate_reasoning_effort,
        )
        from gideon.interfaces.dashboard.chat_handlers import _effort_not_honorable

        if not argument:
            return "Reasoning effort: " + (
                session.reasoning_effort or "provider default"
            )
        effort = _validate_reasoning_effort("" if argument == "default" else argument)
        if argument != "default" and not effort:
            return "Use a supported reasoning effort or default."
        refusal = _effort_not_honorable(session.acp_provider or "", effort)
        if refusal:
            return refusal
        if session.running:
            return "Stop the current response before changing reasoning effort."
        session.reasoning_effort = effort
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        if state.sessions:
            await state.sessions.reset(_history_key_for(session.key))
        state.push_sessions_update()
        return "Reasoning effort updated."
    if command == "context":
        return f"Conversation: {session.key}\nMessages: {session.total_messages}\nAgent: {session.agent or 'Gideon'}\nRunning: {session.running}"
    if command == "usage":
        from gideon.operations import usage_ledger
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        totals = await asyncio.to_thread(
            usage_ledger.totals, session_key=_history_key_for(session.key)
        )
        return json.dumps(totals, indent=2, default=str)
    if command == "retry":
        if session.running:
            return "Stop the current response before retrying."
        prompt = next(
            (
                m.get("content", "")
                for m in reversed(session.messages)
                if m.get("role") == "user"
            ),
            "",
        )
        if not prompt:
            return "No previous prompt to retry."
        cm.text = prompt
        return None
    if command == "background":
        if not argument:
            return "/background <task>"
        if state.subagents is None:
            return "Background tasks are unavailable in this runtime."
        from gideon.interfaces.dashboard.chat_utils import _history_key_for

        info = state.subagents.spawn(
            argument,
            parent_session_key=_history_key_for(session.key),
            agent=session.agent or "",
            cwd=session.workspace_dir or "",
            silent=False,
        )
        if not info:
            return "Background task capacity reached."
        if info.done and info.error:
            return f"Task could not start: {info.error}"
        return f"Background task started: {info.id}. Its result will return to this conversation."
    raise TelegramError(400, "Unknown command")
