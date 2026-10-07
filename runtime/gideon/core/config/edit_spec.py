"""Shared policy dispatch for configuration edits from HTTP and command-line clients."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlparse

__all__ = ["ConfigValueError", "coerce_edit_value"]


@dataclass(frozen=True)
class SecurityControl:
    consent: str
    loosens: str


SECURITY_CONTROLS: dict[str, SecurityControl] = {
    "agent.yolo": SecurityControl(
        "Enable automatic approval of agent actions.", "true"
    ),
    "agent.approval_mode": SecurityControl(
        "Allow more agent actions without asking.", "approval"
    ),
    "agent.approval_timeout_minutes": SecurityControl(
        "Keep approval requests open for longer before denying them.", "higher"
    ),
    "agent.soft_stop_budget_secs": SecurityControl(
        "Give agent work longer before a soft stop takes effect.", "higher"
    ),
    "agent.max_subagents": SecurityControl(
        "Allow an agent to start more concurrent subagents.", "higher"
    ),
    "agent.subagent_timeout_secs": SecurityControl(
        "Allow each subagent to run longer.", "higher"
    ),
    "agent.spawn_min_memory_gb": SecurityControl(
        "Allow agent work to start with less available memory.", "lower"
    ),
    "agent.sandbox": SecurityControl("Disable the agent sandbox.", "sandbox"),
    "agent.subagent_cwd_allowed_roots": SecurityControl(
        "Allow agents to access more directories.", "added"
    ),
    "auth.require_totp": SecurityControl(
        "Stop requiring a second factor at sign in.", "false"
    ),
    "auth.login_enabled": SecurityControl("Enable password sign in.", "true"),
    "auth.session_ttl": SecurityControl(
        "Keep sign in sessions valid for longer.", "longer"
    ),
    "auth.lockout_threshold": SecurityControl(
        "Allow more failed sign in attempts.", "higher"
    ),
    "auth.lockout_window": SecurityControl(
        "Shorten the sign in lockout period.", "shorter"
    ),
    "security.egress": SecurityControl(
        "Allow connections to more network destinations.", "egress"
    ),
    "security.credential_keychain": SecurityControl(
        "Stop using the operating system credential store.", "false"
    ),
    "security.denied_commands": SecurityControl(
        "Allow commands that were previously denied.", "removed"
    ),
    "security.mcp_elicitation_servers": SecurityControl(
        "Allow more MCP servers to interrupt for input.", "added"
    ),
    "security.mcp_read_only_servers": SecurityControl(
        "Allow more MCP servers to make changes.", "removed"
    ),
    "security.outside_home": SecurityControl(
        "Allow more paths outside the Gideon home.", "added"
    ),
    "sandbox.nofile": SecurityControl("Raise the process file limit.", "higher"),
    "sandbox.max_pids": SecurityControl("Allow more processes to run.", "higher"),
    "sandbox.max_rss_mb": SecurityControl(
        "Allow more memory use by sandboxed processes.", "higher"
    ),
    "sandbox.cgroup_scopes": SecurityControl(
        "Disable resource isolation for child processes.", "false"
    ),
    "sandbox.env_passthrough": SecurityControl(
        "Expose more environment variables to child processes.", "added"
    ),
    "guardrails.budgets.max_tokens_per_run": SecurityControl(
        "Raise the per-run token limit.", "higher"
    ),
    "guardrails.budgets.max_tokens_per_day": SecurityControl(
        "Raise the daily token limit.", "higher"
    ),
    "guardrails.budgets.max_dollars_per_day": SecurityControl(
        "Raise the daily spending limit.", "higher"
    ),
    "guardrails.loop_breaker.circuit_threshold": SecurityControl(
        "Raise the circuit-breaker threshold.", "higher"
    ),
    "guardrails.breaker.failure_threshold": SecurityControl(
        "Allow more failures before the circuit breaker opens.", "higher"
    ),
    "guardrails.breaker.recovery_secs": SecurityControl(
        "Reduce the time a tripped circuit breaker remains open.", "lower"
    ),
    "guardrails.autonomy.clean_approvals": SecurityControl(
        "Require fewer successful approvals before granting autonomy.", "lower"
    ),
    "guardrails.autonomy.min_days": SecurityControl(
        "Require fewer days of history before granting autonomy.", "lower"
    ),
    "guardrails.autonomy.max_rejections": SecurityControl(
        "Allow more rejected actions before autonomy is suspended.", "higher"
    ),
    "guardrails.autonomy.cooldown_days": SecurityControl(
        "Shorten the cooldown before autonomy is restored.", "lower"
    ),
    "guardrails.autonomy.evidence_window_days": SecurityControl(
        "Use a shorter evidence window for autonomy decisions.", "lower"
    ),
    "guardrails.scan_mode": SecurityControl(
        "Use a less restrictive content scan mode.", "scan"
    ),
    "external_access.enabled": SecurityControl(
        "Enable external access to Gideon.", "true"
    ),
    "external_access.openai.enabled": SecurityControl(
        "Enable the OpenAI external API.", "true"
    ),
    "external_access.mcp.enabled": SecurityControl(
        "Enable the external MCP API.", "true"
    ),
    "external_access.a2a.enabled": SecurityControl(
        "Enable the external agent API.", "true"
    ),
    "external_access.capture.enabled": SecurityControl(
        "Enable external capture routes.", "true"
    ),
    "external_access.bridge.enabled": SecurityControl(
        "Enable external bridge routes.", "true"
    ),
    "external_access.rate_rps": SecurityControl(
        "Raise the external request rate limit.", "higher"
    ),
    "external_access.rate_burst": SecurityControl(
        "Raise the external request burst limit.", "higher"
    ),
    "external_access.rate_concurrent": SecurityControl(
        "Raise the external concurrent request limit.", "higher"
    ),
    "external_access.auto_disable_after_breaches": SecurityControl(
        "Allow more breaches before external access is disabled.", "higher"
    ),
    "external_access.capture_retention_days": SecurityControl(
        "Retain external request records for longer.", "higher"
    ),
    "external_access.capture.retention_days": SecurityControl(
        "Retain external request records for longer.", "higher"
    ),
    "external_access.capture.upstream_allowlist": SecurityControl(
        "Allow external capture to contact more destinations.", "added"
    ),
}


def security_control(field: str) -> SecurityControl | None:
    return SECURITY_CONTROLS.get(field)


def security_loosening(field: str, current: Any, new: Any) -> str:
    control = security_control(field)
    if control is None or current == new:
        return ""
    mode = control.loosens
    if mode == "true":
        loosened = new is True and current is not True
    elif mode == "false":
        loosened = new is False and current is not False
    elif mode == "higher":
        numeric = lambda value: isinstance(value, (int, float)) and not isinstance(
            value, bool
        )
        if not numeric(current) or not numeric(new):
            loosened = True
        elif field in {
            "guardrails.budgets.max_tokens_per_run",
            "guardrails.budgets.max_tokens_per_day",
            "guardrails.budgets.max_dollars_per_day",
        }:
            old_limit = math.inf if current == 0 else current
            new_limit = math.inf if new == 0 else new
            loosened = new_limit > old_limit
        elif field == "agent.subagent_timeout_secs":
            old_timeout = 1800 if current == 0 else current
            new_timeout = 1800 if new == 0 else new
            loosened = new_timeout > old_timeout
        elif field == "agent.max_subagents" and (current == 0 or new == 0):
            # Autosize depends on host capacity, so require owner confirmation when
            # changing between autosize and an explicit limit.
            loosened = True
        else:
            loosened = new > current
    elif mode == "lower":
        numeric = lambda value: isinstance(value, (int, float)) and not isinstance(
            value, bool
        )
        if not numeric(current) or not numeric(new):
            loosened = True
        elif field == "agent.spawn_min_memory_gb" and (current == 0 or new == 0):
            loosened = current != 0 and new == 0
        else:
            loosened = new < current
    elif mode == "longer":
        units = {"m": 1, "h": 60, "d": 1440}

        def minutes(value: Any) -> int | None:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d+[mhd]", value.strip()
            ):
                return None
            text = value.strip()
            return int(text[:-1]) * units[text[-1]]

        old, updated = minutes(current), minutes(new)
        loosened = old is None or (updated is not None and updated > old)
    elif mode == "shorter":
        units = {"m": 1, "h": 60, "d": 1440}

        def minutes(value: Any) -> int | None:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d+[mhd]", value.strip()
            ):
                return None
            text = value.strip()
            return int(text[:-1]) * units[text[-1]]

        old, updated = minutes(current), minutes(new)
        loosened = old is None or (updated is not None and updated < old)
    elif mode == "approval":
        rank = {"interactive": 0, "trust_reads": 1, "auto": 2}
        loosened = current not in rank or new not in rank or rank[new] > rank[current]
    elif mode == "sandbox":
        loosened = new == "off" and current != "off"
    elif mode in {"added", "removed"}:
        if not isinstance(current, list) or not isinstance(new, list):
            loosened = True
        else:
            loosened = (
                bool(set(new) - set(current))
                if mode == "added"
                else bool(set(current) - set(new))
            )
    elif mode == "scan":
        rank = {"block": 0, "redact": 1, "warn": 2}
        loosened = current not in rank or new not in rank or rank[new] > rank[current]
    elif mode == "egress":
        if not isinstance(current, dict) or not isinstance(new, dict):
            loosened = True
        else:
            loosened = bool(new.get("allow_private")) and not bool(
                current.get("allow_private")
            )
            loosened |= bool(
                set(new.get("allow_hosts", [])) - set(current.get("allow_hosts", []))
            )
            loosened |= bool(
                set(current.get("deny_hosts", [])) - set(new.get("deny_hosts", []))
            )
    else:
        loosened = False
    return control.consent if loosened else ""


class ConfigValueError(ValueError):
    def __init__(self, message: str, resources: str = "", status: int = 400):
        super().__init__(message)
        self.resources = resources
        self.status = status


@dataclass
class EditCandidate:
    path: str
    value: Any
    spec: dict

    def deny(
        self, message: str, resource: str | None = None, status: int = 400
    ) -> NoReturn:
        raise ConfigValueError(
            message,
            f"{self.path}={self.value}" if resource is None else resource,
            status,
        )

    def choices(self):
        if self.value not in self.spec["values"]:
            self.deny(f"invalid value, must be one of {self.spec['values']}")
        return self.value

    def numeric(self, integral: bool):
        constructor, description = (
            (int, "an integer") if integral else (float, "a number")
        )
        if self.value is None or isinstance(self.value, bool):
            self.deny(f"must be {description}")
        try:
            self.value = constructor(self.value)
        except (TypeError, ValueError):
            self.deny(f"must be {description}")
        if not integral and not math.isfinite(self.value):
            self.deny("must be a finite number")
        lower = self.spec.get("min", constructor(0))
        upper = self.spec.get("max", constructor(999999))
        if not lower <= self.value <= upper:
            self.deny(f"must be between {lower} and {upper}")
        return self.value

    def boolean(self):
        if not isinstance(self.value, bool):
            self.deny("must be a boolean")
        return self.value

    def duration(self):
        if not isinstance(self.value, str):
            self.deny("must be a duration string like 30d, 12h or 15m")
        if re.fullmatch(r"\d+[mhd]", self.value.strip()) is None:
            self.deny("must be a duration like 30d, 12h or 15m (integer + m/h/d)")
        self.value = self.value.strip()
        if int(self.value[:-1]) <= 0:
            self.deny("must be greater than zero")
        return self.value

    def strings(self):
        if not isinstance(self.value, list) or any(
            not isinstance(item, str) for item in self.value
        ):
            self.deny("must be a list of strings")
        maximum = self.spec.get("max_items", 20)
        if len(self.value) > maximum:
            self.deny(f"must have at most {maximum} items")
        if self.spec.get("each_regex"):
            for pattern in self.value:
                self.regex(pattern)
        return self.value

    def channel_ids(self):
        if not isinstance(self.value, list) or any(
            not isinstance(item, str) for item in self.value
        ):
            self.deny("must be a list of channel ids")
        if len(self.value) > 100:
            self.deny("must have at most 100 channel ids")
        result = []
        for item in self.value:
            value = item.strip()
            if (
                not value
                or len(value) > 256
                or any(
                    char.isspace() or ord(char) < 32 or ord(char) == 127
                    for char in value
                )
            ):
                self.deny(
                    "each channel id must be a non-empty, single-line value of at most 256 characters"
                )
            if value not in result:
                result.append(value)
        return result

    def regex(self, pattern: str, resource: str | None = None, operation: str = ""):
        try:
            re.compile(pattern)
        except re.error as failure:
            self.deny(
                f"invalid {operation + ' ' if operation else ''}regex {pattern!r}: {failure}",
                resource,
            )

    def text(self):
        if not isinstance(self.value, str):
            self.deny("must be a string")
        limit = self.spec.get("max_len", 256)
        if len(self.value) > limit:
            self.deny(f"must be at most {limit} characters")
        if "values" in self.spec:
            self.choices()
        choices = self.spec.get("values_fn")
        if choices and self.value not in choices():
            self.deny(f"invalid value for {self.path}")
        normalize = self.spec.get("sanitize")
        return normalize(self.value) if normalize else self.value

    def egress(self):
        if not isinstance(self.value, dict):
            self.deny("must be an object")
        result = {}
        for key in ("allow_hosts", "deny_hosts"):
            hosts = self.value.get(key, [])
            resource = f"{self.path}.{key}"
            if not isinstance(hosts, list) or any(
                not isinstance(host, str) for host in hosts
            ):
                self.deny(f"{key} must be a list of strings", resource)
            if len(hosts) > 100:
                self.deny(f"{key} must have at most 100 items", resource)
            invalid = next(
                (
                    host
                    for host in hosts
                    if any(character in host for character in ("/", ":", " "))
                    or len(host) > 253
                ),
                None,
            )
            if invalid is not None:
                self.deny(
                    f"invalid host {invalid!r} (bare domain/hostname only)", resource
                )
            result[key] = hosts
        private = self.value.get("allow_private", False)
        if not isinstance(private, bool):
            self.deny("allow_private must be a boolean", f"{self.path}.allow_private")
        return {**result, "allow_private": private}

    def records(self, noun: str):
        if not isinstance(self.value, list):
            self.deny("must be a list")
        if len(self.value) > 50:
            self.deny(f"must have at most 50 {noun}s", self.path)
        for index, row in enumerate(self.value):
            resource = f"{self.path}[{index}]"
            if not isinstance(row, dict):
                self.deny(f"each {noun} must be an object", resource)
            yield row, resource

    def projection(self):
        from gideon.integrations.tool_providers.projection import _PROJECTORS

        strategies = set(_PROJECTORS)
        result = []
        for row, resource in self.records("rule"):
            pattern = str(row.get("match_regex", "")).strip()
            strategy = str(row.get("strategy", "")).strip().lower()
            if not pattern:
                self.deny("each rule needs a match_regex", resource)
            if len(pattern) > 500:
                self.deny("match_regex too long (max 500)", resource)
            self.regex(pattern, resource)
            if strategy not in strategies:
                self.deny(f"strategy must be one of {sorted(strategies)}", resource)
            output: dict = {
                "name": str(row.get("name", "")).strip()[:80],
                "match_regex": pattern,
                "strategy": strategy,
            }
            for name in ("head", "tail"):
                try:
                    count = int(row.get(name, 0) or 0)
                except (TypeError, ValueError):
                    self.deny(f"{name} must be an integer", resource)
                if not 0 <= count <= 10000:
                    self.deny(f"{name} must be 0..10000", resource)
                if count:
                    output[name] = count
            for name in ("keep", "skip", "count"):
                operation = str(row.get(name, "") or "").strip()
                if not operation:
                    continue
                if len(operation) > 500:
                    self.deny(f"{name} regex too long (max 500)", resource)
                self.regex(operation, resource, name)
                output[name] = operation
            result.append(output)
        return result

    def https(self):
        if not isinstance(self.value, str):
            self.deny("must be a string")
        self.value = self.value.strip()
        maximum = self.spec.get("max_len", 512)
        if len(self.value) > maximum:
            self.deny(f"must be at most {maximum} characters", self.path)
        if self.value:
            address = urlparse(self.value)
            if address.scheme != "https" or not address.netloc:
                self.deny(
                    "must be a full https:// URL — a push ping must not travel in the clear",
                    self.path,
                )
        return self.value

    def catalogs(self):
        result = []
        for row, resource in self.records("catalog"):
            url = str(row.get("url", "")).strip()
            kind = str(row.get("kind", "index")).strip().lower() or "index"
            if not url:
                self.deny("each catalog needs a url", resource)
            if len(url) > 512:
                self.deny("url too long (max 512)", resource)
            if not url.startswith(("https://", "http://")):
                self.deny("url must be http(s)", resource)
            if kind not in ("index", "tap"):
                self.deny("kind must be 'index' or 'tap'", resource)
            result.append(
                {
                    "name": str(row.get("name", "")).strip()[:80],
                    "url": url,
                    "kind": kind,
                }
            )
        return result


_EDIT_POLICIES = {
    "enum": EditCandidate.choices,
    "int": lambda candidate: candidate.numeric(True),
    "float": lambda candidate: candidate.numeric(False),
    "bool": EditCandidate.boolean,
    "duration": EditCandidate.duration,
    "str_list": EditCandidate.strings,
    "channel_ids": EditCandidate.channel_ids,
    "str": EditCandidate.text,
    "egress": EditCandidate.egress,
    "projection_rules": EditCandidate.projection,
    "https_url": EditCandidate.https,
    "skill_catalogs": EditCandidate.catalogs,
}


def coerce_edit_value(path_key: str, value: Any, spec: dict) -> Any:
    candidate = EditCandidate(path_key, value, spec)
    policy = _EDIT_POLICIES.get(spec["type"])
    if policy is None:
        candidate.deny("unsupported config type", status=500)
    return policy(candidate)
