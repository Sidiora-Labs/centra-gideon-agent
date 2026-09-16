"""Configured registry seeding and durable source removal through the real gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REGISTRY_URL = "https://registry.example.test/apps.git"


@pytest.fixture
def boot_home(tmp_path, monkeypatch):
    """An isolated ``GIDEON_HOME`` with NO sources file — a genuinely fresh home.

    The absence of ``apps/app-sources.json`` is the premise: "first run" is defined by
    the missing seed marker, not by the missing row, and these rails have to start from
    the state a new install is actually in.
    """
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    monkeypatch.setenv("GIDEON_APP_REGISTRY_URL", REGISTRY_URL)
    monkeypatch.delenv("GIDEON_APP_CATALOG_URLS", raising=False)

    import gideon.core.config as config_pkg
    import gideon.core.config.loader as config_loader

    assert config_loader.config_dir().resolve() == tmp_path.resolve()
    assert config_pkg.config_dir().resolve() == tmp_path.resolve()
    assert not (tmp_path / "apps" / "app-sources.json").exists()
    return tmp_path


def _sources_file(home: Path) -> dict[str, list[str]]:
    return json.loads((home / "apps" / "app-sources.json").read_text(encoding="utf-8"))


async def _boot():
    """Boot the real gateway on an ephemeral port; return ``(runner, port)``."""
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.server import start_dashboard

    sessions = ConversationDirectory(AppConfig.load())
    runner, _state = await start_dashboard(sessions=sessions, port=0)
    return runner, runner.addresses[0][1]


async def _get_sources(port: int) -> list[str]:
    """The Store's own view of the configured git sources, over HTTP."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/apps/sources") as resp:
            assert resp.status == 200
            return list((await resp.json())["sources"])


async def _delete_source(port: int, url: str) -> list[str]:
    """Remove a source the way the Store's remove control does — the real DELETE."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"http://127.0.0.1:{port}/api/apps/sources", params={"url": url}
        ) as resp:
            assert resp.status == 200
            return list((await resp.json())["sources"])


@pytest.mark.asyncio
async def test_first_run_boot_seeds_the_registry_as_a_removable_default(boot_home):
    """Booting a fresh home writes the registry row — and the Store can remove it."""
    runner, port = await _boot()
    try:
        raw = _sources_file(boot_home)
        assert raw["git"] == [REGISTRY_URL]
        assert raw["seeded"] == ["registry"]

        assert REGISTRY_URL in await _get_sources(port)

        from gideon.extensions.apps import catalog

        assert REGISTRY_URL in catalog.default_git_sources()
        assert REGISTRY_URL not in catalog.builtin_git_sources()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_a_removal_made_in_the_store_survives_the_next_boot(boot_home):
    """The failure this atom exists to prevent: a default that silently comes back."""
    runner, port = await _boot()
    try:
        assert REGISTRY_URL in await _get_sources(port)
        assert REGISTRY_URL not in await _delete_source(port, REGISTRY_URL)
    finally:
        await runner.cleanup()

    raw = _sources_file(boot_home)
    assert raw["git"] == []
    assert raw["seeded"] == ["registry"]

    runner, port = await _boot()
    try:
        assert REGISTRY_URL not in await _get_sources(port)
        assert _sources_file(boot_home)["git"] == []
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_the_flag_off_boot_acquires_no_network_source(boot_home, monkeypatch):
    """Positive control for the two rails above: the boot leg can produce the OTHER
    outcome, so their green is an observation of the boot and not of a constant.

    Also the operator-facing promise — ``apps.registry_source_enabled`` off means a
    fresh home never acquires a shipped NETWORK source at all.
    """
    (boot_home / "config.json").write_text(
        json.dumps({"apps": {"registry_source_enabled": False}}), encoding="utf-8"
    )

    runner, port = await _boot()
    try:
        assert REGISTRY_URL not in await _get_sources(port)
        assert not (boot_home / "apps" / "app-sources.json").exists()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_unconfigured_boot_adds_no_remote_source(boot_home, monkeypatch):
    monkeypatch.delenv("GIDEON_APP_REGISTRY_URL")
    runner, port = await _boot()
    try:
        assert await _get_sources(port) == []
        assert not (boot_home / "apps" / "app-sources.json").exists()
    finally:
        await runner.cleanup()
