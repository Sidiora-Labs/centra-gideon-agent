"""Store discovery keeps update versions attached to the installed source pointer."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gideon.extensions.apps import catalog, manager, source


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_FIRST_PARTY_APPS_DIR", str(tmp_path / "absent-first-party"))
    monkeypatch.setenv("GIDEON_APP_CATALOG_URLS", "")
    monkeypatch.setenv("GIDEON_APP_REGISTRY_URL", "")
    for cache in (
        catalog._registry_cache,
        catalog._registry_offer_cache,
        catalog._git_scan_cache,
        catalog._git_root_versions,
        catalog._git_scan_failed,
    ):
        cache.clear()
    yield
    for cache in (
        catalog._registry_cache,
        catalog._registry_offer_cache,
        catalog._git_scan_cache,
        catalog._git_root_versions,
        catalog._git_scan_failed,
    ):
        cache.clear()


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c", "user.email=fixture@example.invalid",
            "-c", "user.name=Fixture",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class BareRepo:
    def __init__(self, path: Path) -> None:
        self.work = path / "work"
        self.work.mkdir(parents=True)
        git("init", "--initial-branch=main", ".", cwd=self.work)
        (self.work / "README.md").write_text("fixture\n", encoding="utf-8")
        git("add", "-A", cwd=self.work)
        git("commit", "-m", "initial", cwd=self.work)
        self.bare = path / "apps.git"
        git("clone", "--bare", str(self.work), str(self.bare), cwd=path)
        git("remote", "add", "origin", str(self.bare), cwd=self.work)
        self.url = f"file://{self.bare}"

    def publish(self, message: str) -> None:
        git("add", "-A", cwd=self.work)
        git("commit", "-m", message, cwd=self.work)
        git("push", "origin", "HEAD:main", cwd=self.work)


def manifest(name: str, version: str, provider: dict | None = None) -> str:
    value = {"name": name, "version": version, "displayName": name.title()}
    if provider is not None:
        value["provider"] = provider
    return json.dumps(value)


def install(name: str, version: str, pointer: str, origin: str = "external") -> None:
    path = manager.apps_dir() / name
    path.mkdir(parents=True)
    (path / "installed.json").write_text(
        json.dumps({"name": name, "version": version, "source": pointer, "origin": origin}),
        encoding="utf-8",
    )
    (path / "app.json").write_text(manifest(name, version), encoding="utf-8")


def updates() -> dict[str, dict]:
    return {item["name"]: item for item in catalog.updates_available()}


def test_git_pointer_parser_owns_repository_and_subdirectory_split() -> None:
    assert source.git_pointer("https://example.invalid/apps.git#nested/tool/") == (
        "https://example.invalid/apps.git", "nested/tool"
    )
    assert source.git_pointer("/var/lib/apps/tool") is None


def test_multi_app_and_single_app_sources_offer_only_the_installed_pointer(tmp_path: Path) -> None:
    multi = BareRepo(tmp_path / "multi")
    (multi.work / "alpha-app").mkdir()
    (multi.work / "alpha-app" / "app.json").write_text(manifest("alpha-app", "1.0.0"), encoding="utf-8")
    (multi.work / "beta-app").mkdir()
    (multi.work / "beta-app" / "app.json").write_text(manifest("beta-app", "1.0.0"), encoding="utf-8")
    multi.publish("publish two apps")
    catalog.add_git_source(multi.url)
    alpha_pointer = f"{multi.url}#alpha-app"
    install("alpha-app", "1.0.0", alpha_pointer)
    install("beta-app", "1.0.0", f"{multi.url}#beta-app")

    (multi.work / "alpha-app" / "app.json").write_text(manifest("alpha-app", "1.2.0"), encoding="utf-8")
    (multi.work / "beta-app" / "app.json").write_text(manifest("beta-app", "1.0.0"), encoding="utf-8")
    multi.publish("publish alpha update")
    catalog.available_catalog()
    offered = updates()
    assert offered["alpha-app"]["latestVersion"] == "1.2.0"
    assert offered["alpha-app"]["latestSource"] == alpha_pointer
    assert "beta-app" not in offered

    solo = BareRepo(tmp_path / "solo")
    (solo.work / "app.json").write_text(manifest("solo-app", "1.0.0"), encoding="utf-8")
    solo.publish("publish solo app")
    catalog.add_git_source(solo.url)
    install("solo-app", "1.0.0", solo.url)
    (solo.work / "app.json").write_text(manifest("solo-app", "1.3.0"), encoding="utf-8")
    solo.publish("publish solo update")
    catalog.available_catalog()
    assert updates()["solo-app"]["latestSource"] == solo.url


def test_registry_listing_offer_keeps_pointer_context_and_refused_sources_are_excluded(tmp_path: Path) -> None:
    repo = BareRepo(tmp_path / "registry")
    external_pointer = "https://downloads.example.invalid/gamma.git#gamma"
    refused_pointer = f"{repo.url}#delta"
    (repo.work / "app-registry.json").write_text(
        json.dumps({"apps": [
            {"name": "gamma-app", "repo": "https://downloads.example.invalid/gamma.git", "subdirectory": "gamma", "version": "2.0.0"},
            {"name": "delta-app", "repo": repo.url, "subdirectory": "delta", "version": "9.0.0"},
        ]}),
        encoding="utf-8",
    )
    repo.publish("publish registry")
    catalog.add_git_source(repo.url)
    install("gamma-app", "1.0.0", external_pointer)
    install("delta-app", "1.0.0", refused_pointer)

    catalog.available_catalog()
    offered = updates()
    assert offered["gamma-app"]["latestVersion"] == "2.0.0"
    assert offered["gamma-app"]["latestSource"] == external_pointer
    assert catalog.listing_source_for(external_pointer) == repo.url
    assert "delta-app" not in offered


def test_library_projection_uses_only_last_store_refresh_and_drops_removed_source(tmp_path: Path) -> None:
    repo = BareRepo(tmp_path / "catalog")
    (repo.work / "alpha-app").mkdir()
    (repo.work / "alpha-app" / "app.json").write_text(manifest("alpha-app", "2.0.0"), encoding="utf-8")
    repo.publish("publish current version")
    catalog.add_git_source(repo.url)
    install("alpha-app", "1.0.0", f"{repo.url}#alpha-app")
    catalog.available_catalog()

    (repo.work / "alpha-app" / "app.json").write_text(manifest("alpha-app", "3.0.0"), encoding="utf-8")
    repo.publish("publish unrefreshed version")
    assert updates()["alpha-app"]["latestVersion"] == "2.0.0"

    catalog.remove_git_source(repo.url)
    assert updates() == {}
