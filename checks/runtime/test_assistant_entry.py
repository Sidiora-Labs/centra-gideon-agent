import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_ARTIFACT = Path(__file__).parents[2] / "apps" / "assistant" / "dist" / "web"
_REPOSITORY = Path(__file__).parents[2]


def _artifact_file(suffix: str) -> Path:
    return next(path for path in _ARTIFACT.rglob(f"*{suffix}") if path.is_file())


def _assistant_client(core) -> TestClient:
    from gideon.interfaces.dashboard.token_auth import token_auth_middleware

    app = web.Application()
    app.middlewares.append(token_auth_middleware(port=7777))
    app.router.add_route("*", "/{tail:.*}", core.index)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_assistant_entry_serves_nested_reload_and_exported_chunk(monkeypatch):
    from gideon.interfaces.dashboard.handlers import core

    assert (
        _ARTIFACT / "index.html"
    ).is_file(), "build the assistant web artifact first"
    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", _ARTIFACT)

    async with _assistant_client(core) as client:
        entry = await client.get("/assistant/chat", headers={"Accept": "text/html"})
        assert entry.status == 200
        assert entry.content_type == "text/html"
        assert await entry.read() == (_ARTIFACT / "index.html").read_bytes()

        for suffix, content_type in ((".js", "text/javascript"), (".css", "text/css")):
            asset = _artifact_file(suffix)
            relative_asset = asset.relative_to(_ARTIFACT).as_posix()
            response = await client.get(f"/assistant/{relative_asset}")
            assert response.status == 200
            assert response.content_type == content_type
            assert await response.read() == asset.read_bytes()


@pytest.mark.asyncio
async def test_assistant_artifact_absence_is_explicit_and_keeps_console_fallback(
    monkeypatch, tmp_path
):
    from gideon.interfaces.dashboard.handlers import core

    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", tmp_path / "not-installed")

    async with _assistant_client(core) as client:
        response = await client.get("/assistant/", headers={"Accept": "text/html"})
        assert response.status == 503
        body = await response.read()
        assert b"Gideon Assistant is unavailable" in body
        assert b' href="/">Open the existing Gideon Console (fallback)</a>' in body


@pytest.mark.asyncio
async def test_missing_assistant_assets_do_not_fall_back_to_console(monkeypatch):
    from gideon.interfaces.dashboard.handlers import core

    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", _ARTIFACT)
    assert (
        _ARTIFACT / "index.html"
    ).is_file(), "build the assistant web artifact first"

    async with _assistant_client(core) as client:
        response = await client.get(
            "/assistant/_expo/static/js/web/missing.js", headers={"Accept": "*/*"}
        )
        assert response.status == 404
        assert response.content_type == "text/plain"
        assert await response.read() == b"Assistant asset not found"


@pytest.mark.asyncio
async def test_assistant_static_bypass_does_not_cover_api_mutations_or_traversal():
    from gideon.interfaces.dashboard.handlers import core

    async with _assistant_client(core) as client:
        for path in ("/api/auth/session", "/api/chat/sessions"):
            response = await client.get(path)
            assert response.status == 403
            assert response.headers["X-Auth-Required"] == "true"
            await response.read()

        mutation = await client.post("/assistant/", data="ignored")
        assert mutation.status == 403
        assert mutation.headers["X-Auth-Required"] == "true"
        await mutation.read()

        traversal = await client.get("/assistant/%2e%2e/api/chat/sessions")
        assert traversal.status == 403
        assert traversal.headers["X-Auth-Required"] == "true"
        await traversal.read()

        assistant_api = await client.get(
            "/assistant/api/chat", headers={"Accept": "text/html"}
        )
        assert assistant_api.status == 403
        assert assistant_api.headers["X-Auth-Required"] == "true"
        await assistant_api.read()

        unknown_asset = await client.get(
            "/assistant/unknown-route", headers={"Accept": "*/*"}
        )
        assert unknown_asset.status == 403
        assert unknown_asset.headers["X-Auth-Required"] == "true"
        await unknown_asset.read()


def test_runtime_build_includes_the_complete_assistant_web_artifact(tmp_path):
    assert (
        _ARTIFACT / "index.html"
    ).is_file(), "build the assistant web artifact first"
    build_lib = tmp_path / "runtime-build-lib"
    isolated_home = tmp_path / "gideon-home"
    env = os.environ.copy()
    env["GIDEON_HOME"] = str(isolated_home)

    subprocess.run(
        [
            sys.executable,
            "setup.py",
            "build_py",
            "--build-lib",
            str(build_lib),
        ],
        cwd=_REPOSITORY,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    packaged = build_lib / "gideon" / "static" / "assistant"
    relative_paths = [
        Path("index.html"),
        Path("assistant-source-notices.txt"),
        Path(_artifact_file(".js").relative_to(_ARTIFACT)),
        Path(_artifact_file(".css").relative_to(_ARTIFACT)),
    ]
    for relative_path in relative_paths:
        assert (packaged / relative_path).read_bytes() == (
            _ARTIFACT / relative_path
        ).read_bytes()


def test_dockerfile_nginx_routes_the_exported_assistant_artifact():
    nginx = shutil.which("nginx")
    openssl = shutil.which("openssl")
    if not nginx or not openssl:
        pytest.skip("nginx and openssl are required for the Docker routing proof")

    assert (
        _ARTIFACT / "index.html"
    ).is_file(), "build the assistant web artifact first"
    dockerfile = (_REPOSITORY / "deploy/docker/Dockerfile.web").read_text()
    lines = dockerfile.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("RUN printf '%s\\n'")
    )
    end = next(
        i
        for i in range(start, len(lines))
        if "&& rm /tmp/assistant-routing.conf" in lines[i]
    )
    insertion = "\n".join(lines[start : end + 1]).removeprefix("RUN ")

    temp_root = Path(tempfile.mkdtemp(prefix="gideon-assistant-nginx-"))
    process_started = False
    try:
        site = temp_root / "html"
        assistant_site = site / "assistant"
        shutil.copytree(_ARTIFACT, assistant_site)
        for directory in (temp_root, site, assistant_site):
            directory.chmod(0o755)
        for path in assistant_site.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)

        template = temp_root / "default.conf.template"
        shutil.copyfile(
            _REPOSITORY / "deploy/docker/nginx.conf.template", template
        )
        routing_fragment = temp_root / "assistant-routing.conf"
        insertion = insertion.replace(
            "/tmp/assistant-routing.conf", str(routing_fragment)
        ).replace("/etc/nginx/templates/default.conf.template", str(template))
        subprocess.run(["/bin/sh", "-eu", "-c", insertion], check=True, cwd=_REPOSITORY)

        cert = temp_root / "server.crt"
        key = temp_root / "server.key"
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        cert.chmod(0o644)
        key.chmod(0o644)

        import socket

        def available_port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        http_port, https_port = available_port(), available_port()
        while https_port == http_port:
            https_port = available_port()
        config_template = template.read_text()
        config_template = (
            config_template.replace("listen 80;", f"listen {http_port};")
            .replace("listen 443 ssl;", f"listen {https_port} ssl;")
            .replace("/etc/nginx/certs/gideon.crt", str(cert))
            .replace("/etc/nginx/certs/gideon.key", str(key))
            .replace("/usr/share/nginx/html", str(site))
            .replace("${NGINX_LOCAL_RESOLVERS}", "127.0.0.1")
            .replace("    http2 on;\n", "")
        )
        template.write_text(config_template)
        config = temp_root / "nginx.conf"
        config.write_text(
            "\n".join(
                (
                    "worker_processes 1;",
                    f"pid {temp_root / 'nginx.pid'};",
                    f"error_log {temp_root / 'error.log'};",
                    "events { worker_connections 64; }",
                    "http {",
                    "    include /etc/nginx/mime.types;",
                    f"    include {template};",
                    "}",
                    "",
                )
            )
        )
        prefix = [nginx, "-p", f"{temp_root}/", "-c", str(config)]
        subprocess.run([*prefix, "-t"], check=True, capture_output=True, text=True)
        subprocess.run(prefix, check=True, capture_output=True, text=True)
        process_started = True

        context = ssl._create_unverified_context()

        def fetch(path):
            request = urllib.request.Request(
                f"https://127.0.0.1:{https_port}{path}",
                headers={"Host": "localhost", "Accept": "text/html"},
            )
            try:
                response = urllib.request.urlopen(request, context=context, timeout=3)
            except urllib.error.HTTPError as error:
                return error.code, error.headers.get_content_type(), error.read()
            with response:
                return (
                    response.status,
                    response.headers.get_content_type(),
                    response.read(),
                )

        entry = fetch("/assistant/chat")
        assert entry == (200, "text/html", (_ARTIFACT / "index.html").read_bytes())

        for suffix, content_type in (
            (".js", "application/javascript"),
            (".css", "text/css"),
        ):
            asset = _artifact_file(suffix)
            route = "/assistant/" + asset.relative_to(_ARTIFACT).as_posix()
            status, actual_type, body = fetch(route)
            assert (status, actual_type, body) == (
                200,
                content_type,
                asset.read_bytes(),
            )

        for missing in ("missing.js", "missing.webp"):
            status, actual_type, body = fetch(f"/assistant/_expo/static/{missing}")
            assert status == 404
            assert body == b"Assistant asset not found"
            assert actual_type != "text/html"
            assert body != (_ARTIFACT / "index.html").read_bytes()
    finally:
        if process_started:
            subprocess.run(
                [
                    nginx,
                    "-p",
                    f"{temp_root}/",
                    "-c",
                    str(temp_root / "nginx.conf"),
                    "-s",
                    "quit",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            pid_file = temp_root / "nginx.pid"
            for _ in range(30):
                if not pid_file.exists():
                    break
                time.sleep(0.1)
        shutil.rmtree(temp_root, ignore_errors=True)
