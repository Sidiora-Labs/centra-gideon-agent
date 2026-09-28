"""Auth modes and configuration for the Gideon gateway.

This module defines the two supported authentication modes
(``none`` and ``local_token``) and the
``AuthConfig`` dataclass that the gateway's middleware dispatches on.

The ``effective_bind`` helper enforces the loopback invariant: when the
mode is ``NONE``, the bind host is forced to ``127.0.0.1`` regardless of
what was configured. Any other mode honors the configured ``bind_host``.

This module MUST NOT import provider SDKs or auth libraries
(``cryptography``, ``httpx``): the JWT verification path loads lazily
inside ``auth/oidc.py`` only when ``AuthMode.OAUTH2`` is in use.
"""

from dataclasses import dataclass
from enum import Enum

LOOPBACK_HOST = "127.0.0.1"


class AuthMode(str, Enum):
    """Authentication mode selected by the operator at gateway start."""

    NONE = "none"
    LOCAL_TOKEN = "local_token"
    # Retained as rejected compatibility spellings for middleware callers that
    # still mention these values. They are not selectable runtime modes.
    API_KEY = "api_key"
    OAUTH2 = "oauth2"


AUTHORABLE_AUTH_MODES = frozenset({AuthMode.NONE, AuthMode.LOCAL_TOKEN})
_SUPPORTED = frozenset({AuthMode.NONE, AuthMode.LOCAL_TOKEN})


@dataclass(frozen=True)
class AuthConfig:
    """Runtime auth configuration consumed by ``auth_middleware``.

    Defaults to ``LOCAL_TOKEN`` mode bound to loopback with CSRF on.
    Operators flip ``mode`` (and supply the matching per-mode fields)
    to opt into stronger auth.
    """

    mode: AuthMode = AuthMode.LOCAL_TOKEN
    bind_host: str = LOOPBACK_HOST
    cookie_name: str = "gideon_token"
    oauth2_issuer: str | None = None
    oauth2_client_id: str | None = None
    oauth2_audience: str | None = None
    api_key_env: str | None = None
    csrf_required: bool = True
    requested_mode: str = ""

    def __post_init__(self) -> None:
        if self.mode not in _SUPPORTED:
            raise ValueError(
                f"unsupported GIDEON_AUTH_MODE {self.mode.value!r}; supported modes: none, local_token"
            )
        requested = (self.requested_mode or self.mode.value).strip().lower()
        if requested not in {mode.value for mode in _SUPPORTED}:
            raise ValueError(
                f"unsupported GIDEON_AUTH_MODE {requested!r}; supported modes: none, local_token"
            )
        object.__setattr__(self, "requested_mode", requested)

    @property
    def actual_mode(self) -> str:
        """The mode the admission middleware actually enforces."""
        return self.mode.value

    @property
    def authorable(self) -> bool:
        """Whether the requested mode can currently be selected from configuration."""
        return self.requested_mode in {mode.value for mode in AUTHORABLE_AUTH_MODES}

    @property
    def fell_back_from_unauthorable_mode(self) -> bool:
        """Compatibility diagnostic; unsupported modes now fail during parsing."""
        return False

    def mode_state(self) -> str:
        """Return a stable diagnostic summary of requested and effective auth."""
        return (
            f"requested={self.requested_mode} actual={self.actual_mode} "
            f"authorable={str(self.authorable).lower()}"
        )

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Build the runtime auth config, honoring ``GIDEON_AUTH_MODE``.

        Defaults to ``LOCAL_TOKEN``. Setting ``GIDEON_AUTH_MODE=none`` selects
        ``AuthMode.NONE`` — passes all requests through, with the bind host forced to
        loopback by ``effective_bind`` so an unauthenticated gateway can never reach a
        non-loopback interface (dev convenience on localhost only)."""
        import os

        raw = (os.environ.get("GIDEON_AUTH_MODE") or "").strip().lower()
        requested_mode = raw or AuthMode.LOCAL_TOKEN.value
        try:
            mode = AuthMode(requested_mode)
        except ValueError as exc:
            raise ValueError(
                f"unsupported GIDEON_AUTH_MODE {requested_mode!r}; supported modes: none, local_token"
            ) from exc
        if mode not in _SUPPORTED:
            raise ValueError(
                f"unsupported GIDEON_AUTH_MODE {requested_mode!r}; supported modes: none, local_token"
            )
        return cls(mode=mode, requested_mode=requested_mode)


def effective_bind(auth_cfg: AuthConfig) -> str:
    """Return the TCP bind host that must be used for ``auth_cfg``.

    When ``auth_cfg.mode == AuthMode.NONE`` the bind host is forced to
    ``127.0.0.1`` so an unauthenticated gateway can never reach a
    non-loopback interface. For every other mode the configured
    ``bind_host`` is returned unchanged.
    """
    if auth_cfg.mode == AuthMode.NONE:
        return LOOPBACK_HOST
    return auth_cfg.bind_host
