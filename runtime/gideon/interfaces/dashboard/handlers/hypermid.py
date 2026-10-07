"""Native dashboard routes backed by the authenticated Hypermid adapter."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from aiohttp import web

from gideon.http_errors import json_error
from gideon.hypermid.authority_operations import (
    AuthorityOperationError,
    AuthorityPlanStale,
)
from gideon.hypermid.authority_operations import service_for as authority_service_for
from gideon.hypermid.client import (
    HypermidClientError,
    HypermidOutcomeUnknown,
    HypermidRemoteError,
)
from gideon.hypermid.config_store import ChangeAlreadyPending, StaleConfiguration
from gideon.hypermid.configuration import ConfigurationContractError, service_for
from gideon.hypermid.effects import (
    EffectContractError,
    EffectReviewPlan,
    EffectService,
    EffectSnapshot,
)
from gideon.hypermid.foundation import Id
from gideon.hypermid.handlers import (
    HypermidHandlerError,
)
from gideon.hypermid.handlers import service_for as operator_service_for
from gideon.hypermid.integrations import (
    IntegrationScopeError,
    IntegrationStatusError,
    IntegrationStatusFacade,
    SmartNoteConditionSource,
)
from gideon.hypermid.memory_client import MemoryClient
from gideon.hypermid.remote import RemoteAccessService, RemoteViolation
from gideon.hypermid.security_status import (
    SecurityStatusError,
    SecurityStatusFacade,
)

_CREDENTIAL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_REMOTE_CAPABILITIES = frozenset(
    {
        "sessions.inspect",
        "memory.list",
        "memory.inspect",
        "cache.list",
        "config.read",
        "maintenance.status",
    }
)
_LIFECYCLE_ACTIONS = frozenset(
    {"install", "update", "uninstall", "migrate", "export", "restore", "rollback"}
)
_INTEGRATION_ACTIONS = frozenset({"enable", "disable", "reconnect", "stop"})
_SECURITY_PLAN_FIELDS = {
    "backup": frozenset({"destination", "export_id", "credential_ref"}),
    "restore": frozenset({"artifact_path", "source_digest", "credential_ref"}),
    "tombstone": frozenset(
        {"record_id", "revision_digest", "now_ms", "retention_until_ms"}
    ),
    "purge": frozenset({"record_id", "now_ms"}),
}


def _adapter(request: web.Request) -> Any | None:
    adapter = request.app.get("hypermid_adapter")
    if adapter is not None:
        return adapter
    state = request.app.get("state")
    adapter = getattr(state, "hypermid_adapter", None)
    if adapter is not None:
        return adapter
    lifecycle = getattr(state, "hypermid", None)
    adapter = getattr(lifecycle, "adapter", None)
    if adapter is not None:
        return adapter
    from gideon.cognition.context_engine import get_engine

    active = get_engine()
    return active if getattr(active, "name", "") == "hypermid" else None


def _client(request: web.Request) -> Any | None:
    adapter = _adapter(request)
    return getattr(adapter, "client", None) if adapter is not None else None


def _params(request: web.Request, allowed: set[str]) -> dict[str, str]:
    return {
        key: value[:512]
        for key, value in request.query.items()
        if key in allowed and value
    }


def _mapping(value: object, operation: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{operation} returned a non-object result")
    return dict(value)


def _remote_error(error: BaseException) -> web.Response:
    if isinstance(error, HypermidOutcomeUnknown):
        return json_error(
            "service_unavailable",
            message="The connection ended after Hypermid may have accepted the change. Check its authoritative status before retrying.",
            status=503,
        )
    if isinstance(error, HypermidRemoteError):
        detail = error.error
        status = (
            409
            if detail.code
            in {
                "STALE_CONFIGURATION",
                "STALE_REVISION",
                "DIGEST_MISMATCH",
                "PLAN_STALE",
            }
            else (
                403
                if detail.code in {"FORBIDDEN", "UNAUTHORIZED", "SCOPE_DENIED"}
                else 400
            )
        )
        code = (
            "stale_write"
            if status == 409
            else "forbidden" if status == 403 else "bad_request"
        )
        return json_error(
            code,
            message=detail.message,
            status=status,
            error_extra={
                "upstream_code": detail.code,
                "retryable": detail.retryable,
                "effect_state": detail.effect_state,
            },
        )
    return json_error(
        "service_unavailable",
        message="Hypermid did not return an authoritative result.",
        status=503,
    )


async def _invoke(
    request: web.Request,
    operation: str,
    params: dict[str, Any],
    *,
    effect: Literal["query", "idempotent", "durable"] = "query",
) -> web.Response:
    client = _client(request)
    if client is None:
        return json_error(
            "service_unavailable",
            message="Hypermid is not connected for this Gideon instance.",
            status=503,
        )
    try:
        result = await client.request(operation, params, effect_kind=effect)
        return web.json_response(_mapping(result, operation))
    except (HypermidClientError, TimeoutError, OSError) as error:
        return _remote_error(error)
    except ValueError as error:
        return json_error("bad_request", message=str(error), status=502)


async def api_hypermid_overview(request: web.Request) -> web.Response:
    adapter = _adapter(request)
    if adapter is None:
        return web.json_response(
            {
                "availability": "unavailable",
                "daemon": {
                    "state": "absent",
                    "health": "unavailable",
                    "detail": "Hypermid is not configured.",
                },
                "adapter": {
                    "availability": "unavailable",
                    "full_host_integration": False,
                    "detail": "No authenticated host adapter is active.",
                },
                "scope": {"owner_id": "unavailable", "project_id": "unavailable"},
                "mode": "off",
                "store_health": "unknown",
                "maintenance": {"active": False},
                "versions": {"protocol": None, "storage": None, "build": None},
                "checked_at_ms": 0,
                "failure_code": "NOT_CONFIGURED",
            }
        )
    status = adapter.status()
    client = getattr(adapter, "client", None)
    scope = getattr(client, "scope", None)
    scope_wire = (
        scope.to_wire()
        if scope is not None
        else {"owner_id": "unavailable", "project_id": "unavailable"}
    )
    availability = (
        "available"
        if status.available and status.healthy
        else "degraded" if status.available else "unavailable"
    )
    daemon_state = (
        "ready"
        if status.availability == "ready"
        else (
            "starting"
            if status.availability == "starting"
            else (
                "draining"
                if status.availability == "draining"
                else (
                    "stopped"
                    if status.availability in {"disabled", "stopped"}
                    else "failed"
                )
            )
        )
    )
    return web.json_response(
        {
            "availability": availability,
            "daemon": {
                "state": daemon_state,
                "health": (
                    "healthy"
                    if status.healthy
                    else "degraded" if status.available else "unavailable"
                ),
                "detail": status.failure_message,
            },
            "adapter": {
                "availability": availability,
                "full_host_integration": bool(
                    status.scope_bound and status.capabilities
                ),
                "detail": (
                    None
                    if status.scope_bound
                    else "The authenticated project scope is not bound."
                ),
            },
            "scope": scope_wire,
            "mode": status.mode,
            "store_health": (
                "healthy"
                if status.digest_health == "healthy"
                else "failing" if status.digest_health == "broken" else "unknown"
            ),
            "maintenance": {"active": False},
            "cursor": status.cursor.to_wire() if status.cursor is not None else None,
            "versions": {
                "protocol": status.protocol_version,
                "storage": status.storage_version,
                "build": status.build_version,
            },
            "checked_at_ms": status.checked_at_ms,
            "failure_code": status.failure_code,
        }
    )


async def api_hypermid_sessions(request: web.Request) -> web.Response:
    return await _invoke(
        request,
        "sessions.list",
        _params(request, {"q", "state", "model", "since", "until"}),
    )


async def api_hypermid_session(request: web.Request) -> web.Response:
    params: dict[str, Any] = {"session_id": request.match_info["session_id"]}
    params.update(_params(request, {"after_epoch", "after_sequence"}))
    return await _invoke(request, "sessions.inspect", params)


async def api_hypermid_primary_context_inspection(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    if (
        not session_id
        or len(session_id) > 160
        or any(not character.isprintable() for character in session_id)
    ):
        return json_error(
            "invalid_request",
            message="Hypermid session identity is invalid.",
            status=400,
        )
    provider = getattr(request.app.get("state"), "hypermid_context_inspection", None)
    if not callable(provider):
        return json_error(
            "service_unavailable",
            message="Primary context inspection is not available for this runtime.",
            status=503,
        )
    try:
        result = provider(session_id)
        if inspect.isawaitable(result):
            result = await result
        return web.json_response(_mapping(result, "primary context inspection"))
    except KeyError:
        return json_error(
            "not_found",
            message="No primary context observation is available for this session.",
            status=404,
        )
    except (TypeError, ValueError):
        return json_error(
            "service_unavailable",
            message="The primary context inspection result was unreadable.",
            status=503,
        )


async def api_hypermid_memory(request: web.Request) -> web.Response:
    return await _invoke(
        request,
        "memory.list",
        _params(request, {"q", "kind", "state", "verified", "contradicted"}),
    )


async def api_hypermid_memory_record(request: web.Request) -> web.Response:
    return await _invoke(
        request, "memory.inspect", {"record_id": request.match_info["record_id"]}
    )


def _expected_digest(request: web.Request) -> str:
    return request.headers.get("If-Match", "").strip().strip('"')


async def api_hypermid_memory_update(request: web.Request) -> web.Response:
    body = await request.json()
    content = body.get("content") if isinstance(body, Mapping) else None
    digest = _expected_digest(request)
    if not isinstance(content, str) or not digest:
        return json_error(
            "invalid_request",
            message="Memory content and its displayed revision are required.",
            status=400,
        )
    return await _invoke(
        request,
        "memory.update",
        {
            "record_id": request.match_info["record_id"],
            "content": content,
            "expected_digest": digest,
        },
        effect="durable",
    )


async def api_hypermid_caches(request: web.Request) -> web.Response:
    return await _invoke(request, "cache.list", _params(request, {"freshness", "kind"}))


async def api_hypermid_cache_plan(request: web.Request) -> web.Response:
    body = await request.json()
    action = body.get("action") if isinstance(body, Mapping) else None
    if action not in {"clear", "rebuild"}:
        return json_error(
            "invalid_request",
            message="Cache action must be clear or rebuild.",
            status=400,
        )
    return await _invoke(
        request,
        "cache.plan",
        {"cache_id": request.match_info["cache_id"], "action": action},
    )


def _configuration(request: web.Request) -> Any:
    adapter = _adapter(request)
    if adapter is None:
        raise ConfigurationContractError("Hypermid is not connected")
    from gideon.core.config.loader import config_dir

    return service_for(request.app["state"], adapter, config_dir())


def _configuration_error(error: BaseException) -> web.Response:
    if isinstance(error, (StaleConfiguration, ChangeAlreadyPending)):
        current = error.current
        return json_error(
            "stale_write",
            message=(
                "Hypermid configuration changed after it was read. The submitted draft was not applied."
                if isinstance(error, StaleConfiguration)
                else "Another Hypermid configuration is already staged for the next turn boundary."
            ),
            status=409,
            error_extra={
                "upstream_code": error.code,
                "current": {
                    "config": current.config.to_wire(),
                    "policy_revision": current.policy_revision,
                    "config_digest": current.config_digest,
                },
            },
        )
    if isinstance(error, (HypermidClientError, TimeoutError, OSError)):
        return _remote_error(error)
    return json_error(
        (
            "invalid_request"
            if isinstance(error, (ValueError, TypeError))
            else "service_unavailable"
        ),
        message=(
            str(error)
            if isinstance(error, (ValueError, TypeError))
            else "Hypermid configuration is unavailable."
        ),
        status=400 if isinstance(error, (ValueError, TypeError)) else 503,
    )


async def api_hypermid_runtime_config(request: web.Request) -> web.Response:
    try:
        service = _configuration(request)
        if request.method == "GET":
            return web.json_response(service.runtime_snapshot())
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("configuration body must be an object")
        expected_digest = str(body.get("expected_digest") or _expected_digest(request))
        if expected_digest != _expected_digest(request):
            raise ValueError("If-Match and expected_digest must name the same revision")
        result = service.stage_runtime(
            body.get("config"),
            expected_revision=body.get("expected_revision"),
            expected_digest=expected_digest,
        )
        return web.json_response(result)
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_models(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _configuration(request).models())
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_model_plan(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("model binding plan must be an object")
        return web.json_response(
            await _configuration(request).plan_model_binding(
                str(body.get("duty") or ""),
                str(body.get("model_id") or ""),
                str(body.get("expected_digest") or ""),
            )
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_model_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("model binding must be an object")
        expected_digest = str(body.get("expected_digest") or _expected_digest(request))
        if expected_digest != _expected_digest(request):
            raise ValueError("If-Match and expected_digest must name the same revision")
        return web.json_response(
            await _configuration(request).save_model_binding(
                str(body.get("duty") or ""),
                str(body.get("model_id") or ""),
                expected_digest,
            )
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_model_probe(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _configuration(request).probe_model(request.match_info["model_id"])
        )
    except Exception as error:
        return _configuration_error(error)


def _credential_name(request: web.Request) -> str:
    name = request.match_info["name"]
    if _CREDENTIAL_NAME.fullmatch(name) is None:
        raise ValueError("credential name is invalid")
    return name


async def api_hypermid_credentials(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _configuration(request).credentials())
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_credential_put(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        value = body.get("value") if isinstance(body, Mapping) else None
        if not isinstance(value, str) or not value or len(value) > 65_536:
            raise ValueError(
                "credential value is required and must be at most 65536 characters"
            )
        return web.json_response(
            await _configuration(request).put_credential(
                _credential_name(request), value
            )
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_credential_validate(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _configuration(request).validate_credential(_credential_name(request))
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_credential_delete_plan(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _configuration(request).plan_credential_delete(
                _credential_name(request)
            )
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_credential_delete(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping) or body.get("confirm") is not True:
            return json_error(
                "confirmation_required",
                message="Deleting a Hypermid credential requires explicit confirmation.",
                status=400,
            )
        return web.json_response(
            await _configuration(request).delete_credential(_credential_name(request))
        )
    except Exception as error:
        return _configuration_error(error)


async def api_hypermid_remote_status(request: web.Request) -> web.Response:
    try:
        return web.json_response(_remote_access(request).invoke("remote.status", {}))
    except (RemoteViolation, ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)


def _remote_access(request: web.Request) -> RemoteAccessService:
    cached = request.app.get("hypermid_remote_access")
    if isinstance(cached, RemoteAccessService):
        return cached
    client = _client(request)
    scope = getattr(client, "scope", None)
    if scope is None:
        raise RemoteViolation(
            "Hypermid is not connected to an authenticated project scope"
        )
    from gideon.core.config.loader import config_dir

    service = RemoteAccessService(
        Path(config_dir()) / "hypermid" / "remote-access.json", scope
    )
    request.app["hypermid_remote_access"] = service
    return service


def _operator(request: web.Request) -> Any:
    state = request.app.get("state")
    lifecycle = getattr(state, "hypermid", None)
    if lifecycle is None:
        raise HypermidHandlerError("NOT_CONFIGURED", "Hypermid is not configured")
    return operator_service_for(lifecycle)


def _integrations(request: web.Request) -> IntegrationStatusFacade:
    condition_source = request.app.get("hypermid_condition_source")
    if condition_source is None:
        state = request.app.get("state")
        lifecycle = getattr(state, "hypermid", None)
        adapter = getattr(lifecycle, "adapter", None)
        client = getattr(adapter, "client", None)
        capability_id = getattr(lifecycle, "capability_id", None)
        status = adapter.status() if adapter is not None else None
        if (
            client is not None
            and capability_id is not None
            and bool(getattr(status, "available", False))
        ):
            memory_client = MemoryClient(client, capability_id=capability_id)
            condition_source = SmartNoteConditionSource(
                memory_client, scope=memory_client.scope, limit=32
            )
            request.app["hypermid_condition_source"] = condition_source
    cached = request.app.get("hypermid_integrations")
    if (
        isinstance(cached, IntegrationStatusFacade)
        and cached.condition_source is condition_source
    ):
        return cached
    service = IntegrationStatusFacade.from_runtime(
        request.app["state"],
        mcp_bridge=request.app.get("hypermid_mcp_bridge"),
        condition_source=condition_source,
    )
    request.app["hypermid_integrations"] = service
    return service


def _integration_error(error: IntegrationStatusError) -> web.Response:
    status = 403 if isinstance(error, IntegrationScopeError) else 503
    return json_error(
        "forbidden" if status == 403 else "service_unavailable",
        message=str(error)[:512],
        status=status,
        error_extra={"upstream_code": error.code},
    )


def _security_surface(request: web.Request) -> SecurityStatusFacade:
    service = request.app.get("security_status")
    if isinstance(service, SecurityStatusFacade):
        return service
    raise SecurityStatusError(
        "SECURITY_STATUS_UNAVAILABLE",
        "Hypermid security status is not configured",
        503,
    )


def _effects(request: web.Request) -> EffectService:
    cached = request.app.get("hypermid_effects")
    if isinstance(cached, EffectService):
        return cached
    client = _client(request)
    if client is None:
        raise EffectContractError(
            "NOT_CONFIGURED", "Hypermid effect status is unavailable"
        )
    service = EffectService(client)
    request.app["hypermid_effects"] = service
    return service


def _effect_scope(request: web.Request) -> Any:
    scope = getattr(_client(request), "scope", None)
    if scope is None:
        raise EffectContractError(
            "NOT_CONFIGURED", "Hypermid effect status has no authenticated scope"
        )
    return scope


def _effect_plan_wire(plan: EffectReviewPlan) -> dict[str, Any]:
    return {
        "review_id": str(plan.review_id),
        "effect_id": str(plan.effect_id),
        "idempotency_key": str(plan.idempotency_key),
        "module_id": str(plan.module_id),
        "operation": plan.operation,
        "scope": plan.scope.to_wire(),
        "input_digest": str(plan.input_digest),
        "provider_id": str(plan.provider_id),
        "provider_proof_id": str(plan.provider_proof_id),
        "provider_proof_digest": str(plan.provider_proof_digest),
        "proposed_state": plan.proposed_state.value,
        "result_digest": (
            str(plan.result_digest) if plan.result_digest is not None else None
        ),
        "reason": plan.reason,
        "created_ms": plan.created_ms,
        "plan_digest": str(plan.plan_digest),
    }


def _effect_snapshot_wire(effect: EffectSnapshot) -> dict[str, Any]:
    return {
        "effect_id": str(effect.effect_id),
        "module_id": str(effect.module_id),
        "operation": effect.operation,
        "principal_id": str(effect.principal_id),
        "scope": effect.scope.to_wire(),
        "input_digest": str(effect.input_digest),
        "created_ms": effect.created_ms,
        "state": effect.state.value,
        "reviewable": effect.reviewable,
        "result_digest": (
            str(effect.result_digest) if effect.result_digest is not None else None
        ),
        "reason": effect.reason,
        "settled_ms": effect.settled_ms,
        "next_action": effect.next_action.value,
        "review_plan": (
            _effect_plan_wire(effect.review_plan)
            if effect.review_plan is not None
            else None
        ),
    }


def _effect_error(error: BaseException) -> web.Response:
    if (
        isinstance(error, HypermidRemoteError)
        and error.error.code == "EFFECT_NOT_FOUND"
    ):
        return json_error(
            "not_found",
            message="The effect is not available in this scope.",
            status=404,
        )
    if isinstance(error, HypermidRemoteError) and error.error.code in {
        "PROVIDER_PROOF_NOT_FOUND",
        "PROVIDER_PROOF_AMBIGUOUS",
        "REVIEW_MISMATCH",
        "REVIEW_DIGEST_MISMATCH",
        "EFFECT_STATE_CHANGED",
    }:
        return json_error(
            "stale_write",
            message=error.error.message,
            status=409,
            error_extra={"upstream_code": error.error.code},
        )
    if isinstance(error, (HypermidClientError, TimeoutError, OSError)):
        return _remote_error(error)
    if isinstance(error, EffectContractError):
        if error.code == "SCOPE_MISMATCH":
            status = 403
        elif error.code == "NOT_CONFIGURED":
            status = 503
        elif error.code.startswith("INVALID_"):
            status = 400
        else:
            status = 409
        return json_error(
            (
                "forbidden"
                if status == 403
                else (
                    "service_unavailable"
                    if status == 503
                    else "invalid_request" if status == 400 else "stale_write"
                )
            ),
            message=str(error)[:512],
            status=status,
            error_extra={"upstream_code": error.code},
        )
    if isinstance(error, (ValueError, TypeError)):
        return json_error("invalid_request", message=str(error), status=400)
    return json_error(
        "service_unavailable",
        message="Hypermid effect status is unavailable.",
        status=503,
    )


async def api_hypermid_effects(request: web.Request) -> web.Response:
    try:
        service = _effects(request)
        result = await service.list_unresolved(_effect_scope(request))
        return web.json_response(
            {
                "scope": result.scope.to_wire(),
                "effects": [_effect_snapshot_wire(effect) for effect in result.effects],
                "checked_at_ms": result.checked_at_ms,
            }
        )
    except (EffectContractError, HypermidClientError, TimeoutError, OSError) as error:
        return _effect_error(error)


async def api_hypermid_effect_status(request: web.Request) -> web.Response:
    try:
        service = _effects(request)
        return web.json_response(
            _effect_snapshot_wire(
                await service.status(
                    _effect_scope(request), Id(request.match_info["effect_id"])
                )
            )
        )
    except (
        EffectContractError,
        HypermidClientError,
        TimeoutError,
        OSError,
        ValueError,
    ) as error:
        return _effect_error(error)


async def api_hypermid_effect_review(request: web.Request) -> web.Response:
    try:
        service = _effects(request)
        plan = await service.mark_reviewed(
            _effect_scope(request), Id(request.match_info["effect_id"])
        )
        return web.json_response({"plan": _effect_plan_wire(plan)})
    except (
        EffectContractError,
        HypermidClientError,
        TimeoutError,
        OSError,
        ValueError,
    ) as error:
        return _effect_error(error)


async def api_hypermid_effect_reconcile(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        review_id = body.get("review_id") if isinstance(body, Mapping) else None
        reviewed_digest = (
            body.get("reviewed_plan_digest") if isinstance(body, Mapping) else None
        )
        if not isinstance(review_id, str) or not isinstance(reviewed_digest, str):
            raise ValueError(
                "the reviewed effect plan identity and digest are required"
            )
        service = _effects(request)
        scope = _effect_scope(request)
        effect = await service.status(scope, Id(request.match_info["effect_id"]))
        plan = effect.review_plan
        if (
            plan is None
            or str(plan.review_id) != review_id
            or str(plan.plan_digest) != reviewed_digest
        ):
            raise EffectContractError(
                "REVIEW_MISMATCH", "effect review changed after it was displayed"
            )
        return web.json_response(
            _effect_snapshot_wire(await service.reconcile(scope, plan))
        )
    except (
        EffectContractError,
        HypermidClientError,
        TimeoutError,
        OSError,
        ValueError,
        TypeError,
    ) as error:
        return _effect_error(error)


def _security_surface_error(error: SecurityStatusError) -> web.Response:
    status = (
        error.http_status if error.http_status in {400, 403, 404, 409, 503} else 503
    )
    code = (
        "forbidden"
        if status == 403
        else (
            "not_found"
            if status == 404
            else (
                "stale_write"
                if status == 409
                else "invalid_request" if status == 400 else "service_unavailable"
            )
        )
    )
    return json_error(
        code,
        message=error.message,
        status=status,
        error_extra={"upstream_code": error.code},
    )


async def api_hypermid_security_policy(request: web.Request) -> web.Response:
    try:
        return web.json_response(await _security_surface(request).status())
    except SecurityStatusError as error:
        return _security_surface_error(error)


async def api_hypermid_security_grant_revoke(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _security_surface(request).revoke(request.match_info["grant_id"])
        )
    except (TypeError, ValueError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except SecurityStatusError as error:
        return _security_surface_error(error)


async def api_hypermid_memory_provenance(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _security_surface(request).lookup(request.match_info["memory_id"])
        )
    except (TypeError, ValueError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except SecurityStatusError as error:
        return _security_surface_error(error)


async def api_hypermid_memory_promote(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        revision = body.get("expected_revision") if isinstance(body, Mapping) else None
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("a positive expected memory revision is required")
        return web.json_response(
            await _security_surface(request).promote(
                request.match_info["memory_id"], revision
            )
        )
    except (TypeError, ValueError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except SecurityStatusError as error:
        return _security_surface_error(error)


async def api_hypermid_connections(request: web.Request) -> web.Response:
    try:
        service = _integrations(request)
        snapshot = await service.snapshot(service.scope)
        return web.json_response(snapshot.to_wire())
    except IntegrationStatusError as error:
        return _integration_error(error)


async def api_hypermid_connection_action(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        operation = body.get("operation") if isinstance(body, Mapping) else None
        connection_id = request.match_info["connection_id"]
        if operation not in _INTEGRATION_ACTIONS:
            raise ValueError(
                "integration operation must be enable, disable, reconnect, or stop"
            )
        if (
            not connection_id
            or len(connection_id) > 160
            or any(not character.isprintable() for character in connection_id)
        ):
            raise ValueError("integration connection id is invalid")
        service = _integrations(request)
        result = await service.act(
            service.scope,
            None,
            connection_id,
            operation,
        )
        return web.json_response(result.to_wire())
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except IntegrationStatusError as error:
        return _integration_error(error)


def _operator_error(error: HypermidHandlerError) -> web.Response:
    if error.code in {
        "OUTCOME_UNKNOWN",
        "DAEMON_UNAVAILABLE",
        "NOT_CONFIGURED",
        "SECURITY_CREDENTIAL_UNAVAILABLE",
    }:
        status = 503
    elif error.code in {
        "FORBIDDEN",
        "UNAUTHORIZED",
        "SCOPE_DENIED",
        "AUTHORIZATION_DENIED",
    }:
        status = 403
    elif error.code in {
        "PLAN_EXPIRED",
        "PLAN_NOT_FOUND",
        "PLAN_STALE",
        "DIGEST_MISMATCH",
        "PLAN_DIGEST_MISMATCH",
        "AUTHORITY_CHANGED",
        "STALE_RECORD",
    }:
        status = 409
    else:
        status = 400
    return json_error(
        (
            "service_unavailable"
            if status == 503
            else (
                "forbidden"
                if status == 403
                else "stale_write" if status == 409 else "invalid_request"
            )
        ),
        message=str(error)[:512],
        status=status,
        error_extra={"upstream_code": error.code},
    )


def _authority(request: web.Request) -> Any:
    state = request.app.get("state")
    lifecycle = getattr(state, "hypermid", None)
    if lifecycle is None:
        raise AuthorityOperationError(
            "AUTHORITY_UNAVAILABLE", "Hypermid is not configured"
        )
    return authority_service_for(lifecycle)


def _authority_error(error: AuthorityOperationError) -> web.Response:
    status = (
        409
        if isinstance(error, AuthorityPlanStale)
        or error.code
        in {
            "PLAN_EXPIRED",
            "PLAN_BLOCKED",
            "PLAN_STALE",
            "PLAN_TAMPERED",
            "REVIEW_MISMATCH",
        }
        else (
            503
            if error.code in {"AUTHORITY_UNAVAILABLE", "AUTHORITY_DIVERGED"}
            else 400
        )
    )
    return json_error(
        (
            "stale_write"
            if status == 409
            else "service_unavailable" if status == 503 else "invalid_request"
        ),
        message=str(error)[:512],
        status=status,
        error_extra={"upstream_code": error.code},
    )


async def api_hypermid_authority_status(request: web.Request) -> web.Response:
    try:
        value = await _authority(request).status(
            reconcile_unknown=request.query.get("reconcile_unknown") == "true"
        )
        return web.json_response(value.to_wire())
    except AuthorityOperationError as error:
        return _authority_error(error)


async def api_hypermid_authority_plan(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("authority plan body must be an object")
        value = await _authority(request).plan(
            str(body.get("action") or ""),
            ttl_seconds=body.get("ttl_seconds", 300),
        )
        return web.json_response(value.to_wire())
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except AuthorityOperationError as error:
        return _authority_error(error)


async def api_hypermid_authority_apply(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping) or not isinstance(body.get("plan"), Mapping):
            raise ValueError("reviewed authority plan is required")
        value = await _authority(request).apply(
            body["plan"],
            reviewed_digest=str(body.get("reviewed_digest") or ""),
        )
        return web.json_response(value.to_wire())
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except AuthorityOperationError as error:
        return _authority_error(error)


async def api_hypermid_diagnostics(request: web.Request) -> web.Response:
    try:
        operation = (
            "diagnostics.rerun"
            if request.query.get("refresh") == "true"
            else "diagnostics.get"
        )
        return web.json_response(await _operator(request).dispatch(operation, {}))
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_logs(request: web.Request) -> web.Response:
    payload: dict[str, Any] = {}
    after_epoch = request.query.get("after_epoch")
    after_sequence = request.query.get("after_sequence")
    if after_epoch is not None or after_sequence is not None:
        try:
            payload["after"] = {
                "epoch": int(after_epoch or ""),
                "sequence": int(after_sequence or ""),
            }
        except ValueError:
            return json_error(
                "invalid_request",
                message="Both log cursor values must be integers.",
                status=400,
            )
    filters = _params(
        request,
        {"severity", "component", "scope", "session", "trace", "since", "until"},
    )
    if filters:
        payload["filters"] = filters
    try:
        payload["limit"] = min(1_000, max(1, int(request.query.get("limit", "200"))))
        return web.json_response(
            await _operator(request).dispatch("logs.read", payload)
        )
    except ValueError:
        return json_error(
            "invalid_request", message="Log limit must be an integer.", status=400
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_maintenance_plan(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("maintenance plan body must be an object")
        action = body.get("action")
        params = body.get("params", {})
        if not isinstance(action, str) or not action or not isinstance(params, Mapping):
            raise ValueError("maintenance action and object params are required")
        return web.json_response(
            await _operator(request).dispatch(
                "maintenance.plan", {"action": action, "params": dict(params)}
            )
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_maintenance_apply(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("maintenance apply body must be an object")
        return web.json_response(
            await _operator(request).dispatch("maintenance.apply", dict(body))
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_maintenance_status(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "maintenance.status", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_maintenance_cancel(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "maintenance.cancel", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


def _lifecycle_action(request: web.Request) -> str:
    action = request.match_info["action"]
    if action not in _LIFECYCLE_ACTIONS:
        raise ValueError("unsupported Hypermid lifecycle action")
    return action


async def api_hypermid_lifecycle_plan(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("lifecycle plan body must be an object")
        action = _lifecycle_action(request)
        return web.json_response(
            await _operator(request).dispatch(f"lifecycle.{action}.plan", dict(body))
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_lifecycle_apply(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("lifecycle apply body must be an object")
        action = _lifecycle_action(request)
        return web.json_response(
            await _operator(request).dispatch(f"lifecycle.{action}.apply", dict(body))
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_lifecycle_status(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "lifecycle.status", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_lifecycle_recover(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "lifecycle.recover", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_lifecycle_resume(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("lifecycle resume body must be an object")
        return web.json_response(
            await _operator(request).dispatch(
                "lifecycle.resume",
                {"job_id": request.match_info["job_id"], "after": body.get("after")},
            )
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


def _security_action(request: web.Request) -> str:
    action = request.match_info["action"]
    if action not in _SECURITY_PLAN_FIELDS:
        raise ValueError("unsupported Hypermid security lifecycle action")
    return action


async def api_hypermid_security_credentials(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch("security.credentials", {})
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_security_plan(request: web.Request) -> web.Response:
    try:
        action = _security_action(request)
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("security lifecycle plan body must be an object")
        if set(body) - _SECURITY_PLAN_FIELDS[action]:
            raise ValueError("security lifecycle plan contains unsupported fields")
        if action == "restore" and not isinstance(body.get("source_digest"), str):
            raise ValueError("the expected restore source digest is required")
        return web.json_response(
            await _operator(request).dispatch(f"security.{action}.plan", dict(body))
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_security_apply(request: web.Request) -> web.Response:
    try:
        action = _security_action(request)
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("security lifecycle apply body must be an object")
        allowed = {"plan_id", "plan_digest", "confirm_destructive", "confirm_purge"}
        if (
            set(body) - allowed
            or not isinstance(body.get("plan_id"), str)
            or not isinstance(body.get("plan_digest"), str)
        ):
            raise ValueError(
                "security lifecycle apply requires only the reviewed plan identity and confirmations"
            )
        return web.json_response(
            await _operator(request).dispatch(f"security.{action}.apply", dict(body))
        )
    except (ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_security_status(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "security.status", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_security_recover(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            await _operator(request).dispatch(
                "security.recover", {"job_id": request.match_info["job_id"]}
            )
        )
    except HypermidHandlerError as error:
        return _operator_error(error)


async def api_hypermid_remote_plan(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, Mapping):
            raise ValueError("remote enrollment plan must be an object")
        endpoint = str(body.get("endpoint") or "")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "tcp"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "remote endpoint must be an explicit TCP host and port protected by TLS"
            )
        server_name = str(body.get("server_name") or "").strip()
        device_name = str(body.get("device_name") or "").strip()
        expires_at = body.get("expires_at")
        capabilities = body.get("capabilities")
        if (
            not server_name
            or len(server_name) > 253
            or not device_name
            or len(device_name) > 120
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
        ):
            raise ValueError("server name, device name, and expiry are required")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(item not in _REMOTE_CAPABILITIES for item in capabilities)
        ):
            raise ValueError("remote capabilities must be a non-empty supported list")
        return web.json_response(
            _remote_access(request).invoke(
                "remote.enable.plan",
                {
                    "endpoint": endpoint,
                    "server_name": server_name,
                    "device_name": device_name,
                    "expires_at": expires_at,
                    "capabilities": capabilities,
                },
            ),
        )
    except (RemoteViolation, ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)


async def api_hypermid_remote_enable(request: web.Request) -> web.Response:
    body = await request.json()
    digest = body.get("plan_digest") if isinstance(body, Mapping) else None
    if not isinstance(digest, str) or not digest:
        return json_error(
            "invalid_request",
            message="A reviewed remote plan digest is required.",
            status=400,
        )
    try:
        return web.json_response(
            _remote_access(request).invoke("remote.enable", {"plan_digest": digest})
        )
    except (RemoteViolation, ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)


async def api_hypermid_remote_revoke(request: web.Request) -> web.Response:
    try:
        return web.json_response(
            _remote_access(request).invoke(
                "remote.devices.revoke", {"device_id": request.match_info["device_id"]}
            )
        )
    except (RemoteViolation, ValueError, TypeError) as error:
        return json_error("invalid_request", message=str(error), status=400)


def register_hypermid_routes(app: web.Application) -> None:
    app.router.add_get("/api/hypermid/overview", api_hypermid_overview)
    app.router.add_get("/api/hypermid/sessions", api_hypermid_sessions)
    app.router.add_get("/api/hypermid/sessions/{session_id}", api_hypermid_session)
    app.router.add_get(
        "/api/hypermid/sessions/{session_id}/primary-context",
        api_hypermid_primary_context_inspection,
    )
    app.router.add_get("/api/hypermid/memory", api_hypermid_memory)
    app.router.add_get("/api/hypermid/memory/{record_id}", api_hypermid_memory_record)
    app.router.add_patch("/api/hypermid/memory/{record_id}", api_hypermid_memory_update)
    app.router.add_get("/api/hypermid/caches", api_hypermid_caches)
    app.router.add_post("/api/hypermid/caches/{cache_id}/plan", api_hypermid_cache_plan)
    app.router.add_get("/api/hypermid/config/runtime", api_hypermid_runtime_config)
    app.router.add_put("/api/hypermid/config/runtime", api_hypermid_runtime_config)
    app.router.add_get("/api/hypermid/config/models", api_hypermid_models)
    app.router.add_post("/api/hypermid/config/models/plan", api_hypermid_model_plan)
    app.router.add_put("/api/hypermid/config/models", api_hypermid_model_save)
    app.router.add_post(
        "/api/hypermid/config/models/{model_id}/probe", api_hypermid_model_probe
    )
    app.router.add_get("/api/hypermid/config/credentials", api_hypermid_credentials)
    app.router.add_put(
        "/api/hypermid/config/credentials/{name}", api_hypermid_credential_put
    )
    app.router.add_post(
        "/api/hypermid/config/credentials/{name}/validate",
        api_hypermid_credential_validate,
    )
    app.router.add_get(
        "/api/hypermid/config/credentials/{name}/delete-plan",
        api_hypermid_credential_delete_plan,
    )
    app.router.add_post(
        "/api/hypermid/config/credentials/{name}/delete", api_hypermid_credential_delete
    )
    app.router.add_get("/api/hypermid/connections", api_hypermid_connections)
    app.router.add_post(
        "/api/hypermid/connections/{connection_id}/actions",
        api_hypermid_connection_action,
    )
    app.router.add_get("/api/hypermid/security", api_hypermid_security_policy)
    app.router.add_post(
        "/api/hypermid/security/grants/{grant_id}/revoke",
        api_hypermid_security_grant_revoke,
    )
    app.router.add_get(
        "/api/hypermid/security/memory/{memory_id}/provenance",
        api_hypermid_memory_provenance,
    )
    app.router.add_post(
        "/api/hypermid/security/memory/{memory_id}/promote", api_hypermid_memory_promote
    )
    app.router.add_get("/api/hypermid/effects", api_hypermid_effects)
    app.router.add_get("/api/hypermid/effects/{effect_id}", api_hypermid_effect_status)
    app.router.add_post(
        "/api/hypermid/effects/{effect_id}/review", api_hypermid_effect_review
    )
    app.router.add_post(
        "/api/hypermid/effects/{effect_id}/reconcile", api_hypermid_effect_reconcile
    )
    app.router.add_get("/api/hypermid/remote", api_hypermid_remote_status)
    app.router.add_post("/api/hypermid/remote/plan", api_hypermid_remote_plan)
    app.router.add_post("/api/hypermid/remote/enable", api_hypermid_remote_enable)
    app.router.add_post(
        "/api/hypermid/remote/devices/{device_id}/revoke", api_hypermid_remote_revoke
    )
    app.router.add_get("/api/hypermid/diagnostics", api_hypermid_diagnostics)
    app.router.add_get("/api/hypermid/logs", api_hypermid_logs)
    app.router.add_post(
        "/api/hypermid/operations/maintenance/plan", api_hypermid_maintenance_plan
    )
    app.router.add_post(
        "/api/hypermid/operations/maintenance/apply", api_hypermid_maintenance_apply
    )
    app.router.add_get(
        "/api/hypermid/operations/maintenance/jobs/{job_id}",
        api_hypermid_maintenance_status,
    )
    app.router.add_post(
        "/api/hypermid/operations/maintenance/jobs/{job_id}/cancel",
        api_hypermid_maintenance_cancel,
    )
    app.router.add_post(
        "/api/hypermid/operations/lifecycle/{action}/plan", api_hypermid_lifecycle_plan
    )
    app.router.add_post(
        "/api/hypermid/operations/lifecycle/{action}/apply",
        api_hypermid_lifecycle_apply,
    )
    app.router.add_get(
        "/api/hypermid/operations/lifecycle/jobs/{job_id}",
        api_hypermid_lifecycle_status,
    )
    app.router.add_post(
        "/api/hypermid/operations/lifecycle/jobs/{job_id}/recover",
        api_hypermid_lifecycle_recover,
    )
    app.router.add_post(
        "/api/hypermid/operations/lifecycle/jobs/{job_id}/resume",
        api_hypermid_lifecycle_resume,
    )
    app.router.add_get("/api/hypermid/authority/status", api_hypermid_authority_status)
    app.router.add_post("/api/hypermid/authority/plan", api_hypermid_authority_plan)
    app.router.add_post("/api/hypermid/authority/apply", api_hypermid_authority_apply)
    app.router.add_get(
        "/api/hypermid/operations/security/credentials",
        api_hypermid_security_credentials,
    )
    app.router.add_post(
        "/api/hypermid/operations/security/{action}/plan", api_hypermid_security_plan
    )
    app.router.add_post(
        "/api/hypermid/operations/security/{action}/apply", api_hypermid_security_apply
    )
    app.router.add_get(
        "/api/hypermid/operations/security/jobs/{job_id}", api_hypermid_security_status
    )
    app.router.add_post(
        "/api/hypermid/operations/security/jobs/{job_id}/recover",
        api_hypermid_security_recover,
    )


__all__ = ["register_hypermid_routes"]
