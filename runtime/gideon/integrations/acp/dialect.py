"""Protocol strategies for ACP configuration, discovery, and tool decisions."""

from __future__ import annotations

from dataclasses import dataclass, field

from gideon.integrations.acp.types import (
    METHOD_PROMPT,
    METHOD_SET_MODE,
    METHOD_SET_MODEL,
    OPTION_ALLOW_ALWAYS,
    OPTION_ALLOW_ONCE,
    OUTCOME_CANCELLED,
    OUTCOME_SELECTED,
)

METHOD_SET_CONFIG_OPTION = "session/set_config_option"


@dataclass(frozen=True)
class AcpRequest:
    method: str
    params: dict


@dataclass
class DiscoveryResult:
    agents: list[dict]
    models: list[str]
    permission_modes: list[str]
    supported_efforts: list[dict] = field(default_factory=list)


def _session_request(method: str, session_id: str, **values) -> AcpRequest:
    return AcpRequest(method, dict(sessionId=session_id, **values))


def _records(values):
    return (value for value in (values or ()) if isinstance(value, dict))


def _preference(option: dict, *, always: bool) -> int:
    label = (option.get("kind") or option.get("id") or "").lower()
    first, second = ("always", "once") if always else ("once", "always")
    return 0 if first in label else 1 if second in label else 2


def _choose_option(offered: list[dict], *, approve: bool, always: bool = False):
    choices = []
    for index, option in enumerate(offered):
        kind = (option.get("kind") or "").lower()
        identity = (option.get("id") or "").lower()
        if approve:
            eligible = kind.startswith("allow") or identity.startswith("allow")
        else:
            eligible = (
                kind.startswith("reject")
                or identity.startswith("reject")
                or "deny" in (kind + " " + identity)
            )
        if eligible:
            choices.append((_preference(option, always=always), index, option))
    if choices:
        selected = min(choices, key=lambda row: row[:2])[2].get("id", "")
        return selected if approve else str(selected or "")
    if approve:
        for option in offered:
            label = (option.get("kind") or option.get("id") or "").lower()
            if (
                not label.startswith("reject")
                and "cancel" not in label
                and "deny" not in label
            ):
                return option["id"]
    return ""


class ACPDialect:
    name: str = "default"
    supports_concurrent_sessions: bool = False
    supports_mid_turn_prompt: bool = False
    _protocol: object = "2025-08-22"
    _option_id_keys = ("id",)
    _option_label_keys = ("label",)

    def protocol_version(self) -> object:
        return self._protocol

    def client_info(self, *, client_name: str, client_version: str) -> dict:
        return dict(name=client_name, version=client_version)

    def mid_turn_prompt_request(
        self, *, session_id: str, text: str
    ) -> AcpRequest | None:
        body = (text or "").strip()
        if not body or not self.supports_mid_turn_prompt:
            return None
        from gideon.integrations.acp.translate import encode_prompt_content

        return _session_request(
            METHOD_PROMPT, session_id, prompt=encode_prompt_content(body)
        )

    def activate_agent_request(
        self, *, session_id: str, agent: str
    ) -> AcpRequest | None:
        return (
            _session_request(METHOD_SET_MODE, session_id, modeId=agent)
            if agent
            else None
        )

    def set_model_request(
        self, *, session_id: str, model: str, default_model: str
    ) -> AcpRequest | None:
        if not model or model == default_model:
            return None
        return _session_request(METHOD_SET_MODEL, session_id, modelId=model)

    def set_mode_request(self, *, session_id: str, mode: str) -> AcpRequest | None:
        return None

    def set_effort_request(self, *, session_id: str, effort: str) -> AcpRequest | None:
        return None

    def normalize_discovery(self, session_new: dict) -> DiscoveryResult:
        agents = []
        for mode in _records((session_new.get("modes") or {}).get("availableModes")):
            if not mode.get("id"):
                continue
            identity = str(mode["id"])
            agents.append(
                dict(
                    id=identity,
                    label=str(mode.get("name") or mode["id"]),
                    description=str(mode.get("description", "")),
                    provider_agent=identity,
                    reasoning_effort="",
                    use_runtime_prefix=False,
                )
            )
        models = []
        for model in _records((session_new.get("models") or {}).get("availableModels")):
            identity = model.get("modelId") or model.get("id")
            if identity:
                models.append(str(identity))
        return DiscoveryResult(agents, models, [])

    @staticmethod
    def _read_option(row: dict, keys: tuple[str, ...]):
        value = row.get(keys[0], "")
        for key in keys[1:]:
            if not value:
                value = row.get(key, "")
        return value

    def parse_permission_options(self, raw_options: list[dict]) -> list[dict[str, str]]:
        normalized = []
        for row in raw_options:
            identity = self._read_option(row, self._option_id_keys)
            if identity:
                normalized.append(
                    dict(
                        id=identity,
                        label=self._read_option(row, self._option_label_keys),
                        kind=row.get("kind", ""),
                    )
                )
        return normalized

    def default_permission_options(self) -> list[dict[str, str]]:
        return [
            dict(id=key, label=label, kind=key)
            for key, label in (
                (OPTION_ALLOW_ONCE, "Allow once"),
                (OPTION_ALLOW_ALWAYS, "Allow always"),
            )
        ]

    def select_allow_option_id(
        self, offered: list[dict[str, str]], *, prefer_always: bool = False
    ) -> str:
        return _choose_option(offered, approve=True, always=prefer_always)

    def select_reject_option_id(self, offered: list[dict[str, str]]) -> str:
        return _choose_option(offered, approve=False)

    def approve_outcome(self, option_id: str) -> dict:
        return dict(outcome=dict(outcome=OUTCOME_SELECTED, optionId=option_id))

    def reject_outcome(self, option_id: str = "") -> dict:
        if option_id:
            return self.approve_outcome(option_id)
        return dict(outcome=dict(outcome=OUTCOME_CANCELLED))

    def child_process_names(self) -> tuple[str, ...]:
        return ()


class DefaultDialect(ACPDialect):
    name = "default"
    supports_concurrent_sessions = True


class ZedAdapterDialect(ACPDialect):
    name = "zed"
    _protocol = 1
    _option_id_keys = ("optionId", "id")
    _option_label_keys = ("name", "label")

    def activate_agent_request(
        self, *, session_id: str, agent: str
    ) -> AcpRequest | None:
        return None

    @staticmethod
    def _configuration(session_id: str, axis: str, value: str) -> AcpRequest | None:
        return (
            _session_request(
                METHOD_SET_CONFIG_OPTION, session_id, configId=axis, value=value
            )
            if value
            else None
        )

    def set_model_request(
        self, *, session_id: str, model: str, default_model: str
    ) -> AcpRequest | None:
        value = "" if model == default_model else model
        return self._configuration(session_id, "model", value)

    def native_mode(self, mode: str) -> str:
        return mode

    def set_mode_request(self, *, session_id: str, mode: str) -> AcpRequest | None:
        value = self.native_mode(mode) if mode else ""
        return self._configuration(session_id, "mode", value)

    def set_effort_request(self, *, session_id: str, effort: str) -> AcpRequest | None:
        return self._configuration(session_id, "effort", effort)

    def normalize_discovery(self, session_new: dict) -> DiscoveryResult:
        axes = {}
        for axis in _records(session_new.get("configOptions")):
            if axis.get("id"):
                axes[str(axis["id"])] = list(_records(axis.get("options")))

        def selected(name):
            return [str(row["value"]) for row in axes.get(name, ()) if row.get("value")]

        efforts = []
        for row in axes.get("effort", ()):
            value = str(row.get("value", "")).strip()
            if value and value != "default":
                efforts.append(
                    dict(value=value, label=str(row.get("name") or value).strip())
                )
        base = dict(
            id="",
            label="",
            description="",
            provider_agent="",
            reasoning_effort="",
            use_runtime_prefix=True,
        )
        return DiscoveryResult([base], selected("model"), selected("mode"), efforts)


class ClaudeCodeDialect(ZedAdapterDialect):
    name = "claude"

    def child_process_names(self) -> tuple[str, ...]:
        return (self.name,)


class CodexDialect(ZedAdapterDialect):
    name = "codex"
    _NATIVE_MODES = {
        "default": "read-only",
        "plan": "read-only",
        "acceptedits": "agent",
        "acceptall": "agent",
        "acceptalledits": "agent",
        "dontask": "agent",
        "neverask": "agent",
        "bypasspermissions": "agent-full-access",
        "bypass": "agent-full-access",
        "yolo": "agent-full-access",
        "allowall": "agent-full-access",
        "dangerfullaccess": "agent-full-access",
        "fullauto": "agent-full-access",
        "autoapprove": "agent-full-access",
        "auto": "agent-full-access",
    }

    def native_mode(self, mode: str) -> str:
        from gideon.integrations.acp.permission_authority import canonical_mode

        key = canonical_mode(mode)
        return self._NATIVE_MODES[key] if key in self._NATIVE_MODES else "read-only"

    def child_process_names(self) -> tuple[str, ...]:
        return (self.name,)


_DIALECTS: dict[str, type[ACPDialect]] = {
    "default": DefaultDialect,
    "claude-code": ClaudeCodeDialect,
    "codex": CodexDialect,
}


def get_dialect(name: str | None) -> ACPDialect:
    selected = (name or "").strip()
    constructor = _DIALECTS[selected] if selected in _DIALECTS else DefaultDialect
    return constructor()
