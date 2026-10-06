import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_update_offers_only_a_newer_release import assert_update_offer_vectors

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.cli import server as cli
from gideon.interfaces.dashboard.handlers import updates
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations import self_update as su


@pytest.mark.asyncio
async def test_installed_and_release_prereleases_are_ordered_semantically(
    monkeypatch, tmp_path
) -> None:
    assert_update_offer_vectors()
    assert su.same_version("v0.3.0-rc.1", "0.3.0rc1")
    assert not su.is_newer("v0.3.0-rc.1", "0.3.0rc1")
    assert not su.is_newer("0.3.0rc1", "v0.3.0-rc.1")
    assert not su.same_version("nightly", "nightly")
    assert not su.is_newer("nightly", "0.1.0")
    assert not su.is_newer("0.1.0", "nightly")

    releases = [
        {"tag": "v0.3.0-rc.1", "prerelease": True},
        {"tag": "v0.3.0-rc.2", "prerelease": True},
        {"tag": "v0.2.1"},
    ]
    for catalog in (releases, list(reversed(releases))):
        assert su.select_target(catalog, "beta") == "v0.3.0-rc.2"
        assert su.select_target(catalog, "stable") == "v0.2.1"
        assert su.select_target(catalog, "stable", "0.3.0rc1") == "v0.3.0-rc.1"
        assert su.select_target(catalog, "beta", "0.2.1") == "v0.2.1"
    released = [{"tag": "v0.3.0"}, *releases]
    assert su.select_target(released, "beta") == "v0.3.0"
    assert (
        su.select_target([{"tag": "nightly-build"}, {"tag": "v0.1.0"}], "beta")
        == "v0.1.0"
    )
    assert (
        su.select_target([{"tag": "v0.3.0rc1"}, {"tag": "v0.2.1"}], "stable")
        == "v0.2.1"
    )
    assert not su.moves_to("v0.1.2", "0.2.0", "0.1.3")

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    monkeypatch.delenv("GIDEON_PROJECT_DIR", raising=False)
    monkeypatch.setattr(su, "_RELEASES_LATEST_URL", "")
    monkeypatch.setattr(cli, "__version__", "0.3.0rc1")
    monkeypatch.setattr(updates, "_local_version", "0.3.0rc1")
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())

    async def apply(request: web.Request) -> web.Response:
        return await updates._apply_pip_update(request, state)

    app = web.Application()
    app.router.add_post("/api/update", apply)
    async with TestClient(TestServer(app)) as client:
        for tag, available in (
            ("v0.2.1", False),
            ("v0.3.0-rc.1", False),
            ("v0.3.0-rc.2", True),
            ("v0.3.0", True),
        ):
            su.write_release_cache({"tag": tag})
            status = await su.build_update_status("0.3.0rc1")
            assert status["update_available"] is available
            assert cli._is_current(tag) is (not available)
            if not available:
                response = await client.post("/api/update")
                assert response.status == 200
                assert await response.json() == {
                    "ok": True,
                    "status": "up_to_date",
                    "kind": "pip",
                }
                assert not state._background_tasks
                assert su.read_update_state() == {}
                assert updates._apply_in_flight is False
