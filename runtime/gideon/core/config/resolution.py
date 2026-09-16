"""Derive a session's effective agent, workspace and memory bindings."""

from dataclasses import asdict
from pathlib import Path


class AgentBindingPlan:
    def __init__(
        self, config, workspace, compose_voice, merge_memory, binding_type, logger
    ):
        self.config = config
        self.workspace = workspace
        self.compose_voice = compose_voice
        self.merge_memory = merge_memory
        self.binding_type = binding_type
        self.logger = logger

    def profile(self, requested):
        profiles = self.config.agents
        for name in (requested, self.config.default_agent):
            if name and name in profiles:
                return profiles[name]
        if not profiles:
            self.logger.warning("No agents configured, using bare defaults")
            return None
        selected = next(iter(profiles))
        self.logger.warning(
            "default_agent '%s' not found in agents, using '%s'",
            self.config.default_agent,
            selected,
        )
        return profiles[selected]

    def resolve(self, requested):
        profile = self.profile(requested)
        memory = asdict(self.config.memory)
        if profile is None:
            return self.binding_type(
                workspace_dir=self.workspace(),
                memory_store_name="",
                effective_memory_config=memory,
                provider_agent=self.config.default_agent,
            )
        store = profile.memory_store
        if store and store not in self.config.memory_stores:
            self.logger.warning(
                "Agent memory_store '%s' not found; using filesystem fallback", store
            )
            store = ""
        record = self.config.memory_stores.get(store)
        overrides = asdict(record) if record else {}
        binding = {
            "workspace_dir": (
                Path(profile.default_dir) if profile.default_dir else self.workspace()
            ),
            "memory_store_name": store,
            "effective_memory_config": self.merge_memory(memory, overrides),
            "provider_agent": profile.provider_agent,
            "acp_mode": getattr(profile, "acp_mode", ""),
            "system_prompt": self.compose_voice(
                getattr(profile, "voice", ""), profile.system_prompt
            ),
            "approval_mode": profile.approval_mode,
            "provider": getattr(profile, "provider", "") or self.config.agent.provider,
        }
        binding.update(
            {
                key: list(getattr(profile, key, []) or [])
                for key in ("tools", "skills", "triggers")
            }
        )
        return self.binding_type(**binding)
