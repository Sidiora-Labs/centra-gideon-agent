"""Unavailable skill catalogues remain visible without discarding useful results."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import make_mocked_request


def _search(path: str) -> tuple[web.Response, dict]:
    from gideon.interfaces.dashboard.handlers.skills import api_skills_search

    response = asyncio.run(
        api_skills_search(make_mocked_request("GET", path, app=web.Application()))
    )
    return response, json.loads(response.body)


class _QuietStaticFiles(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def _closed_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_unavailable_catalogue_is_named_and_reachable_results_remain(
    tmp_path: Path, monkeypatch
):
    from gideon.extensions.packs.catalog_marketplace import register_skill_catalogs
    from gideon.extensions.skills import get_default_skills_registry

    static_root = tmp_path / "catalogue-files"
    static_root.mkdir()
    (static_root / "reachable.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "mcp14-reachable-postgres",
                        "name": "Postgres helper",
                        "description": "Manage PostgreSQL deployments",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_QuietStaticFiles, directory=str(static_root))
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    config_home = tmp_path / "gideon-home"
    config_home.mkdir()
    reachable_url = f"http://127.0.0.1:{server.server_port}/reachable.json"
    unavailable_url = f"http://127.0.0.1:{_closed_loopback_port()}/unavailable.json"
    (config_home / "config.json").write_text(
        json.dumps(
            {
                "packs": {
                    "skill_catalogs": [
                        {"name": "reachable", "url": reachable_url},
                        {"name": "unavailable", "url": unavailable_url},
                    ]
                },
                "security": {"egress": {"allow_hosts": ["127.0.0.1"]}},
            }
        ),
        encoding="utf-8",
    )

    registry = get_default_skills_registry()
    registered = register_skill_catalogs()
    try:
        assert registered == ["catalog:reachable", "catalog:unavailable"]

        missing_query, body = _search("/api/skills/search?q=")
        assert missing_query.status == 400
        assert body["error"]["code"] == "bad_request"

        for invalid_limit in ("many", "0", "-3"):
            invalid_response, invalid_body = _search(
                f"/api/skills/search?q=postgres&limit={invalid_limit}"
            )
            assert invalid_response.status == 400
            assert invalid_body["error"]["code"] == "invalid_limit"

        missing_catalogue, missing_body = _search(
            "/api/skills/search?q=postgres&marketplace=catalog%3Amissing"
        )
        assert missing_catalogue.status == 404
        assert missing_body["error"]["code"] == "not_found"

        response, body = _search("/api/skills/search?q=postgres")
        assert response.status == 200
        assert "mcp14-reachable-postgres" in {
            result["id"] for result in body["results"]
        }
        assert body["counts"]["catalog:reachable"] == 1
        assert body["installable_sources"] == 2
        (unreachable,) = [
            item for item in body["unreachable"] if item["source"] == "catalog:unavailable"
        ]
        assert "connection was refused" in unreachable["reason"]
        assert str(tmp_path) not in json.dumps(body["unreachable"])
        assert "unavailable.json" not in json.dumps(body["unreachable"])

        scoped_response, scoped_body = _search(
            "/api/skills/search?q=postgres&marketplace=catalog%3Areachable"
        )
        assert scoped_response.status == 200
        assert scoped_body["unreachable"] == []
        assert [item["id"] for item in scoped_body["results"]] == [
            "mcp14-reachable-postgres"
        ]

        selected_failure, selected_body = _search(
            "/api/skills/search?q=postgres&marketplace=catalog%3Aunavailable"
        )
        assert selected_failure.status == 500
        assert selected_body["error"]["code"] == "upstream_failed"
        assert str(tmp_path) not in selected_body["error"]["message"]
        assert "unavailable.json" not in selected_body["error"]["message"]
    finally:
        for name in registered:
            registry.unregister(name)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
