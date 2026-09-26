"""Owner-configured Telegram triggers and ephemeral conversation context."""

from __future__ import annotations
import json
import hashlib
from gideon.extensions.apps.app_config import read_config


def configured(value, default):
    if isinstance(value, str):
        if not value.strip():
            return default
        return json.loads(value)
    return value if value is not None else default


def settings(raw):
    result = dict(raw)
    for key in (
        "dm_topics",
        "mention_patterns",
        "allow_admin_from",
        "group_allow_admin_from",
        "user_allowed_commands",
        "group_user_allowed_commands",
        "ignored_threads",
        "allow_from",
        "group_allow_from",
        "group_allowed_chats",
    ):
        result[key] = configured(raw.get(key), [])
        if not isinstance(result[key], list):
            raise ValueError(f"{key} must be a JSON list")
    for key in ("channel_prompts", "group_topics", "command_menu"):
        result[key] = configured(raw.get(key), {})
        if not isinstance(result[key], dict):
            raise ValueError(f"{key} must be a JSON object")
    return result


def bot_configs(raw):
    primary = settings(raw)
    extra = configured(primary.pop("additional_bots", None), [])
    if not isinstance(extra, list) or len(extra) > 10:
        raise ValueError("additional_bots must contain at most ten bots")
    preferences = configured(primary.pop("bot_preferences", None), {})
    if not isinstance(preferences, dict):
        raise ValueError("bot_preferences must be an object")
    configs = {"primary": primary}
    ids = {str(primary.get("bot_token", "")).split(":")[0]}
    secrets = {primary.get("webhook_secret")} - {None, ""}
    for record in extra:
        if not isinstance(record, dict):
            raise ValueError("Each additional bot must be an object")
        token = str(record.get("bot_token", ""))
        slot = token.split(":")[0]
        if not slot.isdigit() or ":" not in token or slot in ids:
            raise ValueError("Each bot needs its own valid token")
        ids.add(slot)
        child = settings(
            {
                **primary,
                "transport": "polling",
                "webhook_secret": "",
                "webhook_url": "",
                **record,
            }
        )
        overrides = preferences.get(slot, {})
        if isinstance(overrides, dict):
            child.update(
                {
                    k: v
                    for k, v in overrides.items()
                    if k in ("voice_replies", "home_channel", "home_topic")
                }
            )
        child["enabled"] = bool(primary.get("enabled") and record.get("enabled", True))
        secret = child.get("webhook_secret")
        if secret and secret in secrets:
            raise ValueError("Each webhook bot needs its own secret")
        if secret:
            secrets.add(secret)
        configs[slot] = child
    return configs


def config_for(raw, slot="primary"):
    return bot_configs(raw).get(str(slot), {"enabled": False})


def command_allowed(config, cm, command):
    if command in ("help", "start", "whoami"):
        return True
    if cm.sender == str(config.get("owner_id", "")):
        return True
    prefix = "" if cm.metadata.get("chat_type") == "private" else "group_"
    admins = {str(v) for v in config.get(prefix + "allow_admin_from", [])}
    allowed = {
        str(v).lstrip("/") for v in config.get(prefix + "user_allowed_commands", [])
    }
    return cm.sender in admins or command in allowed


def turn_context(thread):
    if not str(thread).startswith("telegram:"):
        return "", []
    parts = thread.split(":")
    if len(parts) not in (3, 4):
        return "", []
    config = config_for(
        read_config("telegram-channel"), parts[1] if len(parts) == 4 else "primary"
    )
    if not config.get("enabled"):
        return "", []
    chat, topic = parts[-2:]
    prompts = config["channel_prompts"]
    prompt = str(
        prompts.get(f"{chat}:{topic}", prompts.get(topic, prompts.get(chat, "")))
    )
    topic_config = config["group_topics"].get(f"{chat}:{topic}", {})
    skill = topic_config.get("skill", "") if isinstance(topic_config, dict) else ""
    if not skill:
        from gideon.extensions.providers.settings import ProviderSettings
        from .topics import TopicStore

        key = hashlib.sha256(str(config.get("bot_token", "")).encode()).hexdigest()[:16]
        store = TopicStore(
            ProviderSettings.config_path("telegram-channel").parent
            / f"topics-{key}.json"
        )
        skill = store.skill(chat, topic)
    return prompt, [str(skill)] if skill else []


def configured_admission(config, cm, is_dm):
    from gideon.integrations.channel_trust import (
        TrustVerdict,
        fence_channel_content,
        trust_policies,
    )

    if not config.get("enabled"):
        return None
    if not is_dm and trust_policies("telegram").get("group") == "off":
        return None
    allowed = {str(v) for v in config.get("allow_from", [])}
    granted = cm.sender in allowed or "*" in allowed
    if not is_dm:
        users = {str(v) for v in config.get("group_allow_from", [])}
        chats = {str(v) for v in config.get("group_allowed_chats", [])}
        granted = (
            granted
            or cm.sender in users
            or "*" in users
            or cm.channel_id in chats
            or "*" in chats
        )
        granted = granted or (
            config.get("guest_mode", False)
            and cm.metadata.get("telegram_direct_mention") is True
        )
    if granted:
        return TrustVerdict(
            True,
            "telegram_configured_access",
            fenced_text=fence_channel_content(cm.text, "telegram", cm.sender),
        )
    return None


def pattern_trigger(patterns, text):
    import regex

    for pattern in patterns[:32]:
        try:
            if (
                isinstance(pattern, str)
                and len(pattern) <= 512
                and regex.search(pattern, text[:16000], regex.IGNORECASE, timeout=0.01)
            ):
                return True
        except (regex.error, TimeoutError):
            continue
    return False
