import json
import socket
import sys
from pathlib import Path

import pytest

from gideon.extensions.apps import catalog, source
from gideon.extensions.apps.catalog import CatalogEntry
from gideon.extensions.apps.source import SourceRefused
from gideon.security.net.git import GitEgressRefused, GuardedTunnel, run_git_guarded
from gideon.security.net.guard import evaluate
from gideon.security.net.policy import LISTING


def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_APP_CATALOG_URLS", "")
    monkeypatch.delenv("GIDEON_APP_REGISTRY_URL", raising=False)
    return home


def test_local_registry_entries_refuse_nonpublic_and_malformed_repos_without_dns(
    tmp_path, monkeypatch
):
    _isolate_home(tmp_path, monkeypatch)
    local_registry = tmp_path / "registry"
    local_registry.mkdir()
    rows = [
        {"name": "loopback", "repo": "https://127.0.0.1/apps.git"},
        {"name": "private", "repo": "https://10.23.4.5/apps.git"},
        {"name": "metadata", "repo": "https://169.254.169.254/latest/meta-data"},
        {"name": "local-file", "repo": str(tmp_path / "apps.git")},
        {"name": "file-url", "repo": "file:///tmp/apps.git"},
        {"name": "credential", "repo": "https://user:secret@example.invalid/apps.git"},
        {"name": "port", "repo": "https://example.invalid:8443/apps.git"},
        {"name": "hostname", "repo": "https://public-name.invalid/apps.git"},
        {"name": "public-ip", "repo": "https://8.8.8.8/apps.git"},
    ]
    (local_registry / "app-registry.json").write_text(
        json.dumps({"apps": rows}), encoding="utf-8"
    )
    pointers = catalog._fetch_registry_index(str(local_registry), is_git=False, now=10)
    assert pointers is not None and len(pointers) == len(rows)
    policy = catalog.listing_policy(str(local_registry))

    observed_dns = []
    active = [True]

    def observe_dns(event, args):
        if active[0] and event == "socket.getaddrinfo":
            observed_dns.append(args)

    sys.addaudithook(observe_dns)
    entries = [
        catalog._pointer_to_entry(
            str(local_registry), pointer, is_git=False, policy=policy
        )
        for pointer in pointers
    ]
    active[0] = False

    by_name = {entry.name: entry for entry in entries}
    for name in (
        "loopback",
        "private",
        "metadata",
        "local-file",
        "file-url",
        "credential",
        "port",
    ):
        assert not by_name[name].installable
        assert by_name[name].refused
    assert "secret" not in by_name["credential"].refused
    assert by_name["hostname"].installable
    assert by_name["public-ip"].installable
    assert by_name["hostname"].listedBy == str(local_registry)
    assert catalog.listing_source_for("https://127.0.0.1/apps.git") == str(
        local_registry
    )
    assert observed_dns == []

    direct_public_ip = evaluate("https://8.8.8.8/apps.git", policy)
    assert direct_public_ip.allow
    assert direct_public_ip.pinned_ips == ["8.8.8.8"]

    git_spawns = []
    active[0] = True

    def observe_git(event, args):
        if (
            active[0]
            and event == "subprocess.Popen"
            and Path(str(args[0])).name == "git"
        ):
            git_spawns.append(args)

    sys.addaudithook(observe_git)
    with pytest.raises(SourceRefused) as refused:
        source.resolve("https://127.0.0.1/apps.git")
    active[0] = False
    assert refused.value.code == "app_listing_refused"
    assert git_spawns == []


def test_installable_duplicate_beats_refused_registry_card(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    refused = CatalogEntry(
        name="same-app",
        displayName="Same app",
        sourceKind="git",
        installable=False,
        refused="This listed destination is not allowed.",
    )
    installable = CatalogEntry(
        name="same-app",
        displayName="Same app",
        sourceKind="git",
        installable=True,
    )
    assert catalog.resolve_catalog_entries([refused, installable]) == [installable]
    assert catalog.resolve_catalog_entries([installable, refused]) == [installable]


def test_owner_added_loopback_registry_exception_is_applied_by_real_policy(
    tmp_path, monkeypatch
):
    _isolate_home(tmp_path, monkeypatch)
    registry = "https://127.0.0.1/registry.git"
    catalog.add_git_source(registry)
    decision = evaluate("https://127.0.0.1/apps.git", catalog.listing_policy(registry))
    assert decision.allow
    assert decision.pinned_ips == ["127.0.0.1"]


def _proxy_reply(port: int, request: str) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.sendall(request.encode("ascii") + b"\r\n\r\n")
        return client.recv(2048)


def test_guarded_tunnel_refuses_private_metadata_port_and_plain_http_hops():
    with GuardedTunnel(LISTING) as tunnel:
        responses = [
            _proxy_reply(
                tunnel.port,
                "CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443",
            ),
            _proxy_reply(
                tunnel.port,
                "CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: 169.254.169.254:443",
            ),
            _proxy_reply(
                tunnel.port,
                "CONNECT example.invalid:8443 HTTP/1.1\r\nHost: example.invalid:8443",
            ),
            _proxy_reply(
                tunnel.port,
                "GET https://example.invalid/ HTTP/1.1\r\nHost: example.invalid",
            ),
        ]
        assert b"403 Forbidden" in responses[0]
        assert b"403 Forbidden" in responses[1]
        assert b"403 Forbidden" in responses[2]
        assert b"405 Method Not Allowed" in responses[3]
        assert [refusal.category for refusal in tunnel.refused] == [
            "loopback",
            "deny_list",
            "port",
            "method",
        ]
        assert tunnel.unreachable == []


def test_git_uses_guard_for_loopback_and_ignores_inherited_config_and_proxy(
    tmp_path, monkeypatch
):
    home = _isolate_home(tmp_path, monkeypatch)
    (home / ".gitconfig").write_text(
        "[safe]\n\tmarker = home-config-marker\n", encoding="utf-8"
    )
    injected = tmp_path / "injected.gitconfig"
    injected.write_text("[safe]\n\tmarker = file-config-marker\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG", str(injected))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'safe.marker=parameter-config-marker'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.injected")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "count-config-marker")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy-sentinel.invalid:8123")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")

    proc = run_git_guarded(
        ["config", "--list", "--show-origin"], policy=LISTING, timeout=8
    )
    assert proc.returncode == 0
    for marker in (
        "home-config-marker",
        "file-config-marker",
        "parameter-config-marker",
        "count-config-marker",
        "proxy-sentinel",
    ):
        assert marker not in proc.stdout

    with pytest.raises(GitEgressRefused) as refused:
        run_git_guarded(
            ["ls-remote", "https://127.0.0.1/repo.git"],
            policy=LISTING,
            timeout=8,
        )
    assert refused.value.refusal.category == "loopback"
