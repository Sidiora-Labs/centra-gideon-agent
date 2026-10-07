"""Public compatibility exports for canonical app-proxy authentication."""

from gideon.security.app_proxy_signatures import (  # noqa: F401 — compatibility exports
    PROXY_SIGNATURE_HEADER,
    PROXY_SIGNATURE_WINDOW_SECS,
    APP_SECRET_ENV,
    _DEFAULT_EXEMPT,
    build_signing_string,
    _hmac_hex,
    sign_proxy_request,
    _verify,
    _deny,
    require_proxy_signature,
    fence_untrusted,
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
