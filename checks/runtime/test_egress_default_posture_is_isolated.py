"""The DEFAULT egress posture, proved against a configuration of this test's own making.

**Measured gap (2026-09-18).** The profile-posture rail in
``checks/runtime/test_net_egress.py`` (``test_profiles_have_expected_postures``) reads the
module constants — ``STRICT.allow_private is False`` and friends. Those constants are not
what any caller gets. Every surface goes through
:func:`gideon.security.net.policy.egress_policy_for`, which layers the operator's
``security.egress`` block onto the profile via ``AppConfig.load()``: an operator
``allow_private: true`` ORs straight in, and their ``allow_hosts`` are UNIONed on. So the
constants can be perfect while the policy the guard actually consults is wide open, and
nothing in the suite read the composed object against a known-empty configuration.

The sibling file ``test_egress_deny_survives_a_config_error.py`` covers the composed
object, but only through a monkeypatched ``AppConfig.load``, and only for the failure
path. A patched loader cannot answer "what does a plain install get" — it answers "what
does this stub return".

**What this file does instead.** It gives the layering a real, empty home
(``GIDEON_HOME`` at a tmp dir with no ``config.json``), asserts the read SUCCEEDS and finds
no overrides — which is what separates "the default is active" from "the config read
threw and we fell back to the base", two states ``egress_policy_for`` returns the same
object for — and then drives that composed policy through the real
:func:`gideon.security.net.guard.evaluate`, the real :func:`classify_host`, and a real
aiohttp server on loopback.

**No network is touched.** The allowed leg dials a socket this test opened on 127.0.0.1;
the denied legs use IP literals, which ``socket.getaddrinfo`` answers from the string
itself. Nothing here needs DNS or a route off the box, so the classifier and the guard are
the genuine ones rather than a fake resolver standing in for them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from gideon.core.config.loader import AppConfig
from gideon.security.net import client
from gideon.security.net import policy as pol
from gideon.security.net.guard import classify_host, evaluate

PRIVATE_LITERAL = "http://10.20.30.40/thing"
PUBLIC_LITERAL = "http://93.184.216.34/thing"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of this test's own: real, empty, and explicitly chosen.

    ``GIDEON_HOME`` is set rather than left unset on purpose — the suite-wide
    ``_isolate_real_home_writers`` fixture only redirects when the caller expressed NO
    preference, so an unset variable would hand this file a tmp home it did not build and
    cannot assert is override-free.

    ``_LAST_DENY_HOSTS`` is module state that outlives a test: a neighbour that seeded a
    deny list and then broke the loader leaves it populated, and it would be re-applied
    here on any read failure. Cleared so the posture measured below is this home's.
    """
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setattr(pol, "_LAST_DENY_HOSTS", (), raising=False)
    assert not (home / "config.json").exists()
    return home


def test_the_isolated_home_really_has_no_operator_overrides(
    isolated_home: Path,
) -> None:
    """The floor under every leg below, and the one that distinguishes the two ways
    ``egress_policy_for`` can return its base: a clean read of an empty config, or a read
    that threw. Only the first is evidence about the default."""
    egress = AppConfig.load().security.egress
    assert list(egress.allow_hosts) == []
    assert list(egress.deny_hosts) == []
    assert egress.allow_private is False


def test_the_default_policy_is_the_one_active(isolated_home: Path) -> None:
    """With nothing layered on, the composed policy IS the shipped profile."""
    assert pol.egress_policy_for(pol.STRICT) == pol.STRICT
    assert pol.egress_policy_for(pol.LOOPBACK_INTERNAL) == pol.LOOPBACK_INTERNAL

    composed = pol.egress_policy_for(pol.STRICT)
    assert composed.allow_private is False
    assert composed.allow_hosts == ()
    assert composed.deny_hosts == ()
    assert composed.pin_resolved_ip is True


def test_an_operator_override_in_THIS_home_changes_the_posture(
    isolated_home: Path,
) -> None:
    """🪤 Vacuity floor. If the layering never reached this home, the leg above would pass
    against a hard-coded constant and prove nothing about the composed policy.

    So: write the override into the same home, re-compose, and require the answer to MOVE.
    """
    (isolated_home / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_private": True}}}),
        encoding="utf-8",
    )
    assert AppConfig.load().security.egress.allow_private is True
    assert pol.egress_policy_for(pol.STRICT).allow_private is True, (
        "the operator block never reached the policy — the isolation fixture is not "
        "pointing the loader at this home, so the default-posture leg is vacuous"
    )


def test_the_default_posture_denies_loopback(isolated_home: Path) -> None:
    """The deny half at the decision plane: the real classifier says "loopback" and the
    composed default refuses. (The leg below repeats it against a socket that is genuinely
    listening, which is where "refused" stops being a statement about an unreachable port.)
    """
    assert classify_host("127.0.0.1").category == "loopback"
    decision = evaluate("http://127.0.0.1:1/x", pol.egress_policy_for(pol.STRICT))
    assert decision.allow is False
    assert "127.0.0.1" in decision.reason and "loopback" in decision.reason


def test_the_default_posture_denies_a_private_address(isolated_home: Path) -> None:
    """RFC-1918, through the real classifier and the real resolver (an IP literal needs
    no DNS), under the policy composed from this home."""
    assert classify_host("10.20.30.40").category == "private"
    decision = evaluate(PRIVATE_LITERAL, pol.egress_policy_for(pol.STRICT))
    assert decision.allow is False
    assert "10.20.30.40" in decision.reason
    assert decision.risk_level == "destructive"


def test_the_default_posture_allows_a_public_address(isolated_home: Path) -> None:
    """🪤 The other vacuity floor: a posture that denied everything would pass both legs
    above. The default is public-only, not nothing-at-all — so a public literal is ALLOWED
    (evaluated, not fetched; no packet leaves the box)."""
    decision = evaluate(PUBLIC_LITERAL, pol.egress_policy_for(pol.STRICT))
    assert decision.allow is True, decision.reason
    assert decision.pinned_ips == ["93.184.216.34"]


async def test_the_internal_default_reaches_a_real_loopback_server(
    isolated_home: Path,
) -> None:
    """The allowed half, end to end: guard → pinned IP → a real HTTP round trip.

    ``LOOPBACK_INTERNAL`` is the surface whose default posture EXPECTS 127.0.0.1 (the
    gateway↔mcp self-call). Composed from this home it must still allow it, and
    ``client.fetch`` — which evaluates and then dials only the pinned IPs — must come back
    with the body the server wrote.

    The same live URL is then offered to the composed DEFAULT (public-only) posture and
    must be refused. That ordering is the point: the port is provably listening, so the
    refusal is the guard's decision and not a connection that would have failed anyway.
    """
    hits: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        hits.append(request.path)
        return web.Response(text="internal-ok")

    app = web.Application()
    app.router.add_get("/internal", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/internal"
    try:
        internal = pol.egress_policy_for(pol.LOOPBACK_INTERNAL)
        decision = evaluate(url, internal)
        assert decision.allow is True, decision.reason
        assert decision.pinned_ips == ["127.0.0.1"]

        response = await client.fetch(url, policy=internal)
        assert response.status == 200
        assert response.text == "internal-ok"
        assert hits == ["/internal"]

        with pytest.raises(client.EgressBlocked) as blocked:
            await client.fetch(url, policy=pol.egress_policy_for(pol.STRICT))
        assert "loopback" in blocked.value.decision.reason
        assert hits == ["/internal"], (
            "the public-only default let a request through to the loopback server: "
            f"{hits}"
        )
    finally:
        await runner.cleanup()
