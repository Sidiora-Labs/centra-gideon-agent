"""Prepare unattended GET requests and project guarded responses into bounded text."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from gideon.core.errors import AgentError
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock

PROVIDER_NAME = "net-fetch"
MAX_TEXT_CHARS = 20_000
FENCE_SOURCE_TYPE = "net_fetch"
FENCE_TRANSFORMATION = "action:http-get"


def _screen_url(raw: str) -> str:
    from gideon.security.security import redact_url_userinfo

    return redact_url_userinfo(raw)[0]


def _has_userinfo(url: str) -> bool:
    try:
        address = urlparse(url)
    except Exception:
        return False
    return bool(address.username or address.password)


@dataclass(frozen=True)
class _FetchReceipt:
    url: str
    status: int
    content_type: str
    response: Any

    @classmethod
    def read(cls, response: Any, fallback: str) -> _FetchReceipt:
        headers = getattr(response, "headers", None) or {}
        return cls(
            _screen_url(str(getattr(response, "url", "") or fallback)),
            int(getattr(response, "status", 0) or 0),
            str(headers.get("Content-Type", "") or ""),
            response,
        )

    def metadata(self) -> dict[str, Any]:
        return dict(url=self.url, status=self.status, content_type=self.content_type)

    def body(self, limit: int) -> dict[str, Any]:
        from gideon.security.security import fence_untrusted

        full = str(getattr(self.response, "text", "") or "")
        bounded = full[:limit]
        result = self.metadata()
        result.update(
            chars=len(bounded),
            truncated=len(full) > limit,
            bytes_truncated=bool(getattr(self.response, "truncated", False)),
            text=fence_untrusted(
                bounded,
                source=f"{PROVIDER_NAME}:{self.url}",
                source_type=FENCE_SOURCE_TYPE,
                source_id=self.url,
                transformation_path=FENCE_TRANSFORMATION,
            ),
        )
        return result


class NetFetchActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Fetch a URL"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        clock = ActionClock()
        raw = str(action_config.get("url") or "").strip()
        if not raw:
            return self._error(
                "net-fetch needs a `url`",
                why="the action config named no URL to fetch",
                fix='set config to {"url": "https://…"}',
                started=clock.started,
            )
        if _has_userinfo(raw):
            return self._error(
                f"net-fetch refused {_screen_url(raw)} because the URL carries credentials",
                why="the URL's userinfo field holds a username or token, and this provider never sends credentials — a fetched body ends up in a model context",
                fix="remove the credentials from the URL; an authenticated fetch is not something this action can do",
                started=clock.started,
            )
        target = _screen_url(raw)
        from gideon.security.guardrails.incident import incident_active

        if incident_active():
            return clock.result(
                False,
                error="incident mode is active — unattended network fetches are suspended",
                agent_error=AgentError(
                    code="ERR_NET_FETCH_INCIDENT_ACTIVE",
                    what="net-fetch refused to run because incident mode is active",
                    why="incident mode suspends all unattended work",
                    fix="clear incident mode in Settings → Guardrails, then re-run",
                ),
            )
        ceiling = self._max_chars(action_config)
        from gideon.security.net import fetch
        from gideon.security.net.client import EgressBlocked
        from gideon.security.net.policy import fetch_action_egress_policy

        policy = fetch_action_egress_policy()
        if timeout and timeout > 0:
            policy = policy.with_overrides(
                timeout_s=min(float(timeout), policy.timeout_s)
            )
        try:
            response = await fetch(target, policy=policy)
        except EgressBlocked as denial:
            return self._refused(denial, policy=policy, started=clock.started)
        except Exception as error:
            return self._error(
                f"net-fetch could not reach {target}: {type(error).__name__}: {error}",
                why="the request failed before a response arrived (DNS, TLS, connection or timeout)",
                fix="check the URL and the host's availability, then re-run",
                started=clock.started,
                code="ERR_NET_FETCH_FAILED",
            )
        return self._to_result(
            response, requested_url=target, max_chars=ceiling, started=clock.started
        )

    @staticmethod
    def _max_chars(action_config: dict[str, Any]) -> int:
        requested = action_config.get("max_chars")
        try:
            limit = MAX_TEXT_CHARS if requested is None else int(requested)
        except (TypeError, ValueError):
            limit = MAX_TEXT_CHARS
        return min(MAX_TEXT_CHARS, max(1, limit))

    def _refused(self, blocked: Any, *, policy: Any, started: float) -> ActionResult:
        decision = getattr(blocked, "decision", None)
        origin = str(getattr(decision, "host", "") or "")
        reason = str(getattr(decision, "reason", "") or "egress blocked")
        setting = (
            "Settings → Security → Allowed Egress Hosts (security.egress.allow_hosts)"
        )
        destination = origin or "the host"
        action = f"add {destination} to {setting}"
        configured = len(getattr(policy, "allow_hosts", ()) or ())
        recovery = (
            f"{action}, or point the action at a listed host"
            if configured
            else f"no hosts are permitted for automated fetches yet — {action}"
        )
        envelope = AgentError(
            code="ERR_NET_FETCH_EGRESS_BLOCKED",
            what=f"net-fetch did not reach {origin or 'the requested host'}: {reason}",
            why="automated fetches are limited to an operator allow-list, which is exclusive: a host that is not on it is refused before the request is made",
            fix=recovery,
            suggestions=tuple(getattr(decision, "recovery_hints", ()) or ()),
        )
        return ActionClock(started).result(
            False,
            error=f"net-fetch was refused by the egress guard: {reason}",
            agent_error=envelope,
        )

    def _to_result(
        self, response: Any, *, requested_url: str, max_chars: int, started: float
    ) -> ActionResult:
        receipt = _FetchReceipt.read(response, requested_url)
        if 200 <= receipt.status < 300:
            return ActionClock(started).result(
                True, stdout=json.dumps(receipt.body(max_chars))
            )
        return self._error(
            f"net-fetch got HTTP {receipt.status} from {receipt.url}",
            why="the host answered with a non-success status, so there is no body to use",
            fix="check the URL; a 4xx is usually the wrong path and a 5xx is the host's own fault",
            started=started,
            code="ERR_NET_FETCH_FAILED",
            stdout=json.dumps(receipt.metadata()),
        )

    @staticmethod
    def _ms(started: float) -> int:
        return int(1000 * (time.monotonic() - started))

    def _error(
        self,
        message: str,
        *,
        why: str,
        fix: str,
        started: float,
        code: str = "ERR_NET_FETCH_CONFIG",
        stdout: str = "",
    ) -> ActionResult:
        failure = AgentError(code=code, what=message, why=why, fix=fix)
        return ActionClock(started).result(
            False, stdout=stdout, error=message, agent_error=failure
        )


def create_provider(config: dict[str, Any] | None = None) -> NetFetchActionProvider:
    return NetFetchActionProvider()
