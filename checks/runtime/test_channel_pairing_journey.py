"""Configured pairing channels are reachable from Apps and live channel health."""

import asyncio
import datetime
import json
import ssl
from contextlib import asynccontextmanager

import pytest
import websockets
import httpx
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from gideon.core.config import loader as config_loader
from gideon.extensions.apps.manager import InstalledApp, app_dir
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.apps.native_contract import NATIVE_DIR
from gideon.extensions.providers.registry import get_provider_registry, reset_provider_registry
from gideon.integrations.channel_transports import get_transport, unregister_transport
from gideon.interfaces.dashboard.handlers.apps import register_app_routes
from gideon.interfaces.dashboard.handlers.channels import api_channel_get


@asynccontextmanager
async def _tls_vendor(app, context):
    server = TestServer(app, host="localhost", scheme="https")
    await server.start_server(ssl=context)
    try:
        yield server
    finally:
        await server.close()


async def _health(client: TestClient, name: str, state: str) -> dict:
    last = {}
    for _ in range(100):
        response = await client.get(f"/api/channels/{name}")
        if response.status == 200:
            health = (await response.json())["health"]
            last = health
            if health["state"] == state:
                return health
        await asyncio.sleep(0.05)
    raise AssertionError(f"{name} never reached {state}: {last}")


@pytest.mark.asyncio
async def test_whatsapp_apps_save_starts_pairing_and_clears_qr_on_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    reset_provider_registry()
    name = "whatsapp-channel"
    manifest_path = NATIVE_DIR / name / "app.json"
    destination = app_dir(name)
    destination.mkdir(parents=True)
    (destination / "app.json").write_bytes(manifest_path.read_bytes())
    manifest = AppManifest.from_json_file(manifest_path)
    (destination / "installed.json").write_text(
        json.dumps(InstalledApp(name=name, version=manifest.version, enabled=True, origin="builtin").to_dict())
    )
    registry = get_provider_registry()
    registry.register(manifest, enabled=True)
    old = get_transport("whatsapp")
    assert old is not None
    await old.start_inbound(object())
    assert old.services is not None and old._task is None

    paired = asyncio.Event()
    auth_frames = []

    async def bridge(ws):
        auth_frames.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "qr", "qr": "whatsapp-pairing-code"}))
        await paired.wait()
        await ws.send(json.dumps({"type": "status", "status": "connected"}))
        await ws.wait_closed()

    app = web.Application()
    register_app_routes(app)
    app.router.add_get("/api/channels/{name}", api_channel_get)
    try:
        async with websockets.serve(bridge, "127.0.0.1", 0) as bridge_server:
            port = bridge_server.sockets[0].getsockname()[1]
            async with TestClient(TestServer(app)) as client:
                listing = await client.get("/api/apps")
                apps = (await listing.json())["apps"]
                assert any(entry["name"] == name and entry["hasConfig"] for entry in apps)
                configuration = await client.get(f"/api/apps/{name}/config")
                assert configuration.status == 200
                assert "bridge_url" in (await configuration.json())["schema"]["properties"]
                saved = await client.put(
                    f"/api/apps/{name}/config",
                    json={
                        "bridge_url": f"ws://127.0.0.1:{port}",
                        "bridge_token": "tenant-secret",
                        "auto_bridge": False,
                    },
                )
                assert saved.status == 200, await saved.text()
                assert (await saved.json())["config"]["bridge_token"] != "tenant-secret"
                current = get_transport("whatsapp")
                assert current is not old and old._task is None
                assert current.config["bridge_token"] == "tenant-secret"
                pairing = await _health(client, "whatsapp", "pairing")
                assert pairing["pairingQr"] == "whatsapp-pairing-code"
                assert auth_frames == [{"type": "auth", "token": "tenant-secret"}]
                paired.set()
                ready = await _health(client, "whatsapp", "ready")
                assert "pairingQr" not in ready
                await current.stop_inbound()
    finally:
        unregister_transport("whatsapp")
        reset_provider_registry()


@pytest.mark.asyncio
async def test_mochat_apps_save_adopts_settings_and_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    reset_provider_registry()
    name = "mochat-channel"
    manifest_path = NATIVE_DIR / name / "app.json"
    destination = app_dir(name)
    destination.mkdir(parents=True)
    (destination / "app.json").write_bytes(manifest_path.read_bytes())
    manifest = AppManifest.from_json_file(manifest_path)
    registry = get_provider_registry()
    registry.register(manifest, enabled=True)
    old = get_transport("mochat")
    assert old is not None
    await old.start_inbound(object())

    credentials = []

    async def sessions(request):
        credentials.append(request.headers.get("X-Claw-Token"))
        return web.json_response({"data": {"sessions": []}})

    async def watch(request):
        await asyncio.sleep(0.1)
        return web.json_response({"data": {"events": [], "cursor": 0}})

    vendor = web.Application()
    vendor.router.add_post("/api/claw/sessions/list", sessions)
    vendor.router.add_post("/api/claw/sessions/watch", watch)
    app = web.Application()
    register_app_routes(app)
    app.router.add_get("/api/channels/{name}", api_channel_get)
    try:
        async with TestClient(TestServer(vendor)) as vendor_client:
            async with TestClient(TestServer(app)) as client:
                saved = await client.put(
                    f"/api/apps/{name}/config",
                    json={
                        "base_url": str(vendor_client.make_url("/")).rstrip("/"),
                        "claw_token": "mochat-secret",
                        "sessions": "room-1",
                    },
                )
                assert saved.status == 200, await saved.text()
                current = get_transport("mochat")
                assert current is not old and old._task is None
                assert current.config["sessions"] == "room-1"
                assert (await _health(client, "mochat", "ready"))["detail"] == "Connected"
                assert "mochat-secret" in credentials
                await current.stop_inbound()
    finally:
        unregister_transport("mochat")
        reset_provider_registry()


@pytest.mark.asyncio
async def test_weixin_apps_save_reaches_qr_and_confirmed_health(tmp_path, monkeypatch, capsys):
    pytest.importorskip("qrcode")
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    reset_provider_registry()
    name = "weixin-channel"
    manifest_path = NATIVE_DIR / name / "app.json"
    destination = app_dir(name)
    destination.mkdir(parents=True)
    (destination / "app.json").write_bytes(manifest_path.read_bytes())
    manifest = AppManifest.from_json_file(manifest_path)
    registry = get_provider_registry()
    registry.register(manifest, enabled=True)
    old = get_transport("weixin")
    assert old is not None
    await old.start_inbound(object())
    assert old._task is None

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "localhost.crt"
    key_path = tmp_path / "localhost.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(str(cert_path), str(key_path))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    paired = asyncio.Event()

    async def ticket(request):
        return web.json_response({"qrcode": "ticket-123", "qrcode_img_content": "weixin-pairing-code"})

    async def status(request):
        assert request.query["qrcode"] == "ticket-123"
        await paired.wait()
        return web.json_response({"status": "confirmed", "bot_token": "weixin-secret"})

    async def updates(request):
        assert request.headers["Authorization"] == "Bearer weixin-secret"
        await asyncio.sleep(0.1)
        return web.json_response({"msgs": [], "get_updates_buf": ""})

    vendor = web.Application()
    vendor.router.add_get("/ilink/bot/get_bot_qrcode", ticket)
    vendor.router.add_get("/ilink/bot/get_qrcode_status", status)
    vendor.router.add_post("/ilink/bot/getupdates", updates)
    app = web.Application()
    register_app_routes(app)
    app.router.add_get("/api/channels/{name}", api_channel_get)
    try:
        async with _tls_vendor(vendor, server_ssl) as vendor_server:
            async with httpx.AsyncClient() as probe:
                check = await probe.get(f"https://localhost:{vendor_server.port}/ilink/bot/get_bot_qrcode")
                assert check.status_code == 200
            async with TestClient(TestServer(app)) as client:
                saved = await client.put(
                    f"/api/apps/{name}/config",
                    json={"base_url": f"https://localhost:{vendor_server.port}"},
                )
                assert saved.status == 200, await saved.text()
                current = get_transport("weixin")
                assert current is not old and old._task is None
                pairing = await _health(client, "weixin", "pairing")
                assert pairing["pairingQr"] == "weixin-pairing-code"
                assert capsys.readouterr().out.strip()
                paired.set()
                ready = await _health(client, "weixin", "ready")
                assert "pairingQr" not in ready
                await current.stop_inbound()
    finally:
        unregister_transport("weixin")
        reset_provider_registry()
