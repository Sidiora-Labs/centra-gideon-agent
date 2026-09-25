"""Explicitly enrolled customer-native helper; no host command or endpoint parameters."""

import argparse
import hmac
import re
import ssl
from pathlib import Path

from aiohttp import web

from .iterm import ExternalTerminalMirror


def create_app(
    device_id, token, *, provider_cli=False, duplex_audio=False, duplex_executor=None
):
    if not isinstance(device_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", device_id
    ):
        raise ValueError("Invalid native device identity")
    if (
        not isinstance(token, str)
        or len(token) < 32
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise ValueError("Use a strong native pairing credential")
    mirror = ExternalTerminalMirror()
    provider = None
    if provider_cli:
        from .native_provider import NativeProvider

        provider = NativeProvider()
    if duplex_executor is not None and not duplex_audio:
        raise ValueError("Duplex executor requires explicit opt-in")

    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + token
        ):
            return web.json_response(
                {"error": "Native authentication required"}, status=401
            )
        return await handler(request)

    async def identity(request):
        return web.json_response(
            {
                "device_id": device_id,
                "protocol": 1,
                "capabilities": ["terminal.read"]
                + (["provider.launch"] if provider_cli else [])
                + (["voice.duplex"] if duplex_audio else []),
            }
        )

    async def terminals(request):
        availability = mirror.availability()
        if not availability["available"]:
            return web.json_response(
                {"error": "Native iTerm unavailable", "availability": availability},
                status=503,
            )
        try:
            pane = request.match_info.get("id")
            result = await mirror.screen(pane) if pane else await mirror.inventory()
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, RuntimeError):
            return web.json_response(
                {"error": "Native terminal unavailable"}, status=503
            )

    app = web.Application(
        middlewares=[authenticate],
        client_max_size=3 * 1024 * 1024 if duplex_audio else 1048576,
    )
    app.router.add_get("/v1/identity", identity)
    app.router.add_get("/v1/terminals", terminals)
    app.router.add_get("/v1/terminals/{id}", terminals)
    if provider is not None:

        async def provider_request(request):
            from .store import ConflictError

            try:
                action = request.path.rsplit("/", 1)[-1]
                if request.method == "GET":
                    result = (
                        provider.inventory()
                        if action == "profiles"
                        else {"launches": provider.history()}
                    )
                else:
                    payload = await request.json()
                    result = (
                        provider.image(payload)
                        if action == "images"
                        else await provider.launch(payload)
                    )
                return web.json_response(result, headers={"Cache-Control": "no-store"})
            except ConflictError:
                return web.json_response(
                    {"error": "Native launch conflicts"}, status=409
                )
            except (ValueError, TypeError):
                return web.json_response(
                    {"error": "Invalid native provider operation"}, status=400
                )
            except (OSError, RuntimeError):
                return web.json_response(
                    {"error": "Customer-native provider unavailable"}, status=503
                )

        app.router.add_get("/v1/provider/profiles", provider_request)
        app.router.add_get("/v1/provider/launches", provider_request)
        app.router.add_post("/v1/provider/images", provider_request)
        app.router.add_post("/v1/provider/launches", provider_request)
    if duplex_audio:
        from gideon.workspace.capabilities.experience.native_duplex_helper import (
            register_native_duplex_executor,
        )

        register_native_duplex_executor(app, duplex_executor)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--provider-cli", action="store_true")
    parser.add_argument("--duplex-audio", action="store_true")
    parser.add_argument("--port", type=int, default=9463)
    args = parser.parse_args()
    token = Path(args.token_file)
    if token.is_symlink() or token.stat().st_mode & 0o077:
        raise ValueError("Native token file must be private")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    web.run_app(
        create_app(
            args.device_id,
            token.read_text().strip(),
            provider_cli=args.provider_cli,
            duplex_audio=args.duplex_audio,
        ),
        host="127.0.0.1",
        port=args.port,
        ssl_context=context,
    )


if __name__ == "__main__":
    main()
