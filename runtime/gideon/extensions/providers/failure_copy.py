"""One vocabulary for what a remote-provider failure SAYS.

Raw exception text is operator diagnostics, not user copy: a truncated
``ClientConnectorError(ConnectionKey(...))`` in a Settings toast tells the user
nothing and reads as a crash. Every dashboard surface that relays a provider
failure routes the exception through :func:`connectivity_guidance` so the same
failure produces the same sentence everywhere — the classification (and its
wording) started life in ``instance_routes._probe_failure`` and was extracted
verbatim so the probe endpoint and the catalog endpoints cannot drift apart.

Two deliberate boundaries:

* **Authored refusals keep their own words.** A ``ValueError`` raised with a
  human-written message ("model card missing 'name'") is designed copy, not a
  leak; callers that expect them pass ``str(exc)`` through and only consult
  this module for the connectivity/unexpected classes.
* **This module never picks an envelope.** The probe endpoint speaks
  ``json_error`` (nested ``{"error": {code, message}}``); the catalog
  endpoints keep their historical flat shapes (``{"models": [], "error"}``,
  ``{"error"}``). Copy is shared; wire contracts stay each surface's own.
"""

from __future__ import annotations

import socket
import ssl

import aiohttp

from gideon.core.errors import AgentError

__all__ = ["connectivity_guidance", "relayed_failure_copy", "UNEXPECTED_FAILURE_COPY"]

UNEXPECTED_FAILURE_COPY = (
    "The request failed unexpectedly. Check the provider's logs and try again."
)


def relayed_failure_copy(exc: BaseException) -> str:
    """The user-facing text for relaying a provider-operation failure.

    Connectivity classes get their guidance sentence; a ``ValueError`` carrying
    a message keeps its own words (authored refusal copy — "model card missing
    'name'" is meant for the user); anything else is an internal crash whose
    text belongs in the log, so the wire gets :data:`UNEXPECTED_FAILURE_COPY`.
    """
    guidance = connectivity_guidance(exc)
    if guidance is not None:
        return guidance
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    return UNEXPECTED_FAILURE_COPY


def connectivity_guidance(exc: BaseException) -> str | None:
    """The guidance sentence for a connectivity failure, or ``None``.

    ``None`` means the exception is NOT one of the connectivity classes and the
    caller decides what its text is (an authored ``ValueError`` flows as-is; an
    internal crash gets :data:`UNEXPECTED_FAILURE_COPY`). The raw exception is
    the CALLER's to log — this function classifies, it does not report.
    """
    os_error = getattr(exc, "os_error", None)
    root: BaseException = os_error if isinstance(os_error, BaseException) else exc
    if isinstance(root, ConnectionRefusedError):
        return (
            "Could not reach that endpoint — the connection was refused. Check the "
            "URL and that the service is running."
        )
    if isinstance(root, TimeoutError):
        return "The connection timed out. Check the URL and that the service is reachable from here."
    if isinstance(root, socket.gaierror):
        return "That host could not be resolved. Check the endpoint's hostname."
    if isinstance(root, ssl.SSLError) or isinstance(exc, aiohttp.ClientSSLError):
        return (
            "The TLS handshake failed. Check the endpoint's certificate and that it "
            "expects HTTPS."
        )
    if isinstance(exc, aiohttp.ClientError):
        return "Could not reach that endpoint. Check the URL and that the service is running."
    return None


def provider_load_error(
    cause: str, *, use_case: str, provider: str = "", provider_type: str = ""
) -> AgentError:
    guidance = {
        "registry_unavailable": (
            "the provider registry could not be loaded",
            "check the gateway logs and repair the provider installation, then restart Gideon",
        ),
        "unsupported_capability": (
            f"{use_case!r} is not a supported model capability",
            "select a supported model use case in Settings → Models",
        ),
        "no_entries": (
            "the provider registry contains no configured entries",
            "add a model provider in Settings → Providers",
        ),
        "entry_missing": (
            f"provider entry {provider!r} is absent from the registry",
            "configure that provider in Settings → Providers, or select an available model",
        ),
        "capability_missing": (
            f"no eligible provider declares the {use_case!r} capability",
            "bind this use case to a provider that supports it in Settings → Models",
        ),
        "factory_missing": (
            f"no factory is registered for provider TYPE {provider_type!r}",
            f"enable or repair the app that registers TYPE {provider_type!r}, then restart Gideon",
        ),
        "credential_missing": (
            "the provider reported a missing credential",
            "configure its credential in Settings → Providers, then retry",
        ),
        "dependency_missing": (
            "a required module could not be imported while constructing the provider",
            "check the gateway logs for the missing module and repair the provider installation",
        ),
        "construction_failed": (
            "the provider constructor failed; Gideon is not sure of the underlying cause",
            "check the gateway logs for this provider's construction error, then retry",
        ),
    }
    why, fix = guidance.get(
        cause,
        (
            "Gideon is not sure why the provider could not be loaded",
            "check the gateway logs and the provider configuration, then retry",
        ),
    )
    return AgentError(
        code="ERR_MODEL_UNRESOLVED",
        what=f"the model provider {provider!r} could not be loaded for {use_case!r}",
        why=why,
        fix=fix,
    )
