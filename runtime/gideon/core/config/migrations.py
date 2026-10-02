"""Apply ordered in-memory upgrades and persist them only at runtime startup."""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from gideon.core.config import loader as config_loader

if TYPE_CHECKING:
    from gideon.core.config.loader import AppConfig

logger = logging.getLogger(__name__)


def config_path():
    return config_loader.config_path()


def _native_default(config) -> bool:
    if getattr(config.agent, "provider", "") != "acp":
        return False
    config.agent.provider = "native"
    inherited = (
        profile
        for profile in (config.agents or {}).values()
        if not getattr(profile, "provider", "")
    )
    for profile in inherited:
        if getattr(profile, "provider_agent", "") == "gideon":
            profile.provider, profile.provider_agent = "native", ""
    return True


def _seed_profiles(config) -> bool:
    from gideon.engine.agents import defaults

    before = len(config.agents)
    if not config.agents:
        config.agents[defaults.DEFAULT_NATIVE_AGENT_NAME] = (
            defaults.make_default_native_profile(config_loader.AgentProfile)
        )
    builtins = (
        (defaults.LOOP_WORKER_AGENT_NAME, defaults.make_loop_worker_profile),
        (defaults.CODER_AGENT_NAME, defaults.make_coder_profile),
        (defaults.CODE_PLANNER_AGENT_NAME, defaults.make_code_planner_profile),
        (defaults.LOOP_PLANNER_AGENT_NAME, defaults.make_loop_planner_profile),
        (defaults.LITE_AGENT_NAME, defaults.make_lite_agent_profile),
        (defaults.TEMPLATE_REFINER_AGENT_NAME, defaults.make_template_refiner_profile),
    )
    for name, build in builtins:
        if name not in config.agents:
            config.agents[name] = build(config_loader.AgentProfile)
    return len(config.agents) != before


def _retire_profiles(config) -> bool:
    from gideon.engine.agents.defaults import RETIRED_AGENT_NAMES

    retired = RETIRED_AGENT_NAMES.intersection(config.agents)
    for name in retired:
        config.agents.pop(name)
        logger.info("Config migration: pruned retired system agent %r", name)
    return bool(retired)


def _retire_seeded_native_prompt(config) -> bool:
    """Remove only the retired built-in prompt from the selected native profile."""
    from gideon.engine.agents.defaults import DEFAULT_NATIVE_SYSTEM_PROMPT

    profile = (config.agents or {}).get(config.default_agent)
    if profile is None or profile.system_prompt != DEFAULT_NATIVE_SYSTEM_PROMPT:
        return False
    profile.system_prompt = ""
    logger.info("Config migration: retired seeded native system prompt")
    return True


def _repair_selection(config) -> bool:
    if config.default_agent and config.default_agent in config.agents:
        return False
    config.default_agent = (
        "default"
        if "default" in config.agents
        else next(iter(config.agents), "default")
    )
    return True


def _normalize_hypermid(config) -> bool:
    from gideon.hypermid.config import ContextConfig, ContextMode

    current = getattr(config, "hypermid", None)
    if isinstance(current, ContextConfig):
        return False
    mode = ContextMode.OFF
    if current is True:
        mode = ContextMode.PASS_THROUGH
    elif isinstance(current, str):
        try:
            mode = ContextMode(current)
        except ValueError:
            mode = ContextMode.OFF
    config.hypermid = ContextConfig(mode=mode)
    logger.info("Config migration: normalized legacy Hypermid configuration")
    return True


_UPGRADES = (
    _native_default,
    _seed_profiles,
    _retire_profiles,
    _repair_selection,
    _retire_seeded_native_prompt,
    _normalize_hypermid,
)


def apply_config_migrations(cfg: AppConfig) -> bool:
    changed = tuple(upgrade(cfg) for upgrade in _UPGRADES)
    return any(changed)


def load_and_persist_migrations() -> AppConfig:
    configuration, changed = config_loader.AppConfig.load_with_migration_state()
    if changed:
        try:
            source = config_path()
            if source.exists():
                backup = source.with_suffix(".json.bak")
                shutil.copy2(source, backup)
                logger.info("Config migrated — backup saved to %s", backup)
            configuration.save()
        except Exception as failure:
            logger.warning("Config write-back failed: %s", failure)
    return configuration
