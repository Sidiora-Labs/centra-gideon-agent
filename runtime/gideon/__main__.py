"""Entry point for ``python -m gideon``.

``_ensure_ssl_certs()`` MUST run before ``from gideon.interfaces.cli.main import main``
because that import triggers ``aiohttp`` (via ``dashboard.origin`` →
``dashboard.__init__`` → ``dashboard.server`` → ``from aiohttp import web``).

aiohttp caches its default SSL context at import time
(``aiohttp.connector._SSL_CONTEXT_VERIFIED``).  On some systems the
cafile may be missing, so the cached context ends up with zero CA
certs and every HTTPS connection fails with CERTIFICATE_VERIFY_FAILED.
"""

from gideon.core._ssl_compat import _ensure_ssl_certs

_ensure_ssl_certs()

if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--desktop-smoke"]:
        from gideon.operations.desktop_smoke import main
    else:
        from gideon.interfaces.cli.main import main

    main()
