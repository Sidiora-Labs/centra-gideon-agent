"""Public compatibility exports for canonical app-proxy authentication."""

from gideon.security.app_proxy_signatures import (  # noqa: F401 — compatibility exports
    _DEFAULT_EXEMPT,
    APP_SECRET_ENV,
    PROXY_SIGNATURE_HEADER,
    PROXY_SIGNATURE_WINDOW_SECS,
    _deny,
    _hmac_hex,
    _verify,
    build_signing_string,
    fence_untrusted,
    require_proxy_signature,
    sign_proxy_request,
)

__all__ = [
    "fence_untrusted",
    "require_proxy_signature",
    "sign_proxy_request",
    "build_signing_string",
    "PROXY_SIGNATURE_HEADER",
    "PROXY_SIGNATURE_WINDOW_SECS",
    "APP_SECRET_ENV",
]
