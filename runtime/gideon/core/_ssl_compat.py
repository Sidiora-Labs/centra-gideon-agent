"""Select a system trust bundle before HTTP libraries construct SSL contexts."""

import os
from pathlib import Path

_CA_CANDIDATES = (
    "/etc/pki/tls/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt",
)


def _ensure_ssl_certs() -> None:
    if os.environ.get("SSL_CERT_FILE"):
        return
    import ssl

    configured = ssl.get_default_verify_paths().cafile
    if configured and Path(configured).exists():
        return
    selected = next(
        (candidate for candidate in _CA_CANDIDATES if Path(candidate).exists()), None
    )
    if selected is not None:
        os.environ.update(SSL_CERT_FILE=selected)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", selected)
