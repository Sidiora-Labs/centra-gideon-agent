"""Provider failures speak guidance, not tracebacks — the shared-copy rail.

Doctrine (established by the durability sweep, ``test_durability_error_copy``):
raw exception text is operator diagnostics; wire copy is authored. The model-
provider surfaces relay REMOTE failures constantly (list/search/show/pull/
delete against an endpoint the user just typed in), which is exactly where a
``ClientConnectorError(ConnectionKey(...))`` used to land in a Settings toast.

Three pins:

1. The shared classifier maps each connectivity class to its guidance sentence
   and keeps authored ``ValueError`` words (unit truth, real exception values).
2. Neither handler file ships ``str(exc)`` inside a wire payload — the source
   scan that keeps the fix from regressing one site at a time.
3. ``instance_routes._probe_failure`` consumes the SAME classifier, so the
   probe endpoint and the catalog endpoints cannot drift apart.
"""

from __future__ import annotations

import re
import socket
import ssl
from pathlib import Path

import aiohttp

from gideon.extensions.providers.failure_copy import (
    UNEXPECTED_FAILURE_COPY,
    connectivity_guidance,
    relayed_failure_copy,
)

SRC = Path(__file__).resolve().parents[2] / "runtime" / "gideon"


def test_connection_refused_maps_to_guidance() -> None:
    text = connectivity_guidance(ConnectionRefusedError(61, "Connection refused"))
    assert text is not None and "connection was refused" in text
    key = object()
    wrapped = aiohttp.ClientConnectorError(  # type: ignore[arg-type]
        key, ConnectionRefusedError(61, "refused")
    )
    assert connectivity_guidance(wrapped) == text


def test_timeout_and_dns_and_tls_map_to_guidance() -> None:
    assert "timed out" in (connectivity_guidance(TimeoutError()) or "")
    assert "could not be resolved" in (
        connectivity_guidance(socket.gaierror(8, "nodename")) or ""
    )
    assert "TLS handshake" in (connectivity_guidance(ssl.SSLError("bad cert")) or "")


def test_generic_client_error_maps_to_guidance() -> None:
    assert "Could not reach that endpoint" in (
        connectivity_guidance(aiohttp.ClientError("boom")) or ""
    )


def test_internal_crash_is_not_a_connectivity_class() -> None:
    for exc in (
        TypeError("int is not iterable"),
        KeyError("digest"),
        RuntimeError("x"),
    ):
        assert connectivity_guidance(exc) is None


def test_relay_keeps_authored_valueerror_words_and_hides_internals() -> None:
    assert (
        relayed_failure_copy(ValueError("model card missing 'name'"))
        == "model card missing 'name'"
    )
    for exc in (TypeError("unhashable type: 'dict'"), KeyError("layers"), ValueError()):
        copy = relayed_failure_copy(exc)
        assert copy == UNEXPECTED_FAILURE_COPY
        assert "unhashable" not in copy and "layers" not in copy


_WIRE_LEAK = re.compile(
    r"(json_response\([^)]*str\(exc\)|message=str\(exc\)|dumps\(\{[^}]*str\(exc\))"
)


def _handler_source(name: str) -> str:
    return (SRC / "dashboard" / "handlers" / name).read_text(encoding="utf-8")


def test_providers_handlers_ship_no_exception_text_as_copy() -> None:
    for name in ("providers.py", "model_downloads.py"):
        src = _handler_source(name)
        hits = _WIRE_LEAK.findall(src)
        assert not hits, f"{name} places raw exception text in a wire payload: {hits}"


def test_self_check_the_leak_pattern_still_matches() -> None:
    """Vacuity guard: the regex recognises every shape this rail is meant to ban."""
    assert _WIRE_LEAK.search(
        'return web.json_response({"error": str(exc)}, status=500)'
    )
    assert _WIRE_LEAK.search('json_error("x", message=str(exc), status=404)')
    assert _WIRE_LEAK.search(
        'await resp.write((_json.dumps({"error": str(exc)[:200]}) + "\\n").encode())'
    )


def test_probe_failure_uses_the_shared_classifier() -> None:
    src = (SRC / "providers" / "instance_routes.py").read_text(encoding="utf-8")
    assert (
        "from gideon.extensions.providers.failure_copy import connectivity_guidance"
        in src
    )
    probe = src[
        src.index("def _probe_failure") : src.index("async def handle_test_instance")
    ]
    assert "connectivity_guidance(exc)" in probe
    assert "isinstance(root," not in probe
    assert 'getattr(exc, "os_error"' not in probe
