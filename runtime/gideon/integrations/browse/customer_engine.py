"""Owned Chromium process and guarded page for a customer browser reservation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from gideon.integrations.browse.cdp import GatedCdpSession
from gideon.integrations.browse.customer_proxy import CustomerBrowserProxy
from gideon.integrations.browse.killswitch import browse_killed
from gideon.integrations.browse.launch import ready_page_target
from gideon.integrations.browse.page import CdpPageDriver
from gideon.integrations.browse.transport import WebSocketCdpTransport


class BrowserUnavailable(RuntimeError):
    pass


@dataclass
class OwnedBrowser:
    process: subprocess.Popen[bytes]
    transport: WebSocketCdpTransport
    gate: GatedCdpSession
    page: CdpPageDriver
    proxy: CustomerBrowserProxy

    async def close(self) -> None:
        try:
            await self.transport.close()
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(self.process.wait), 5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait)
            await self.proxy.close()

    def healthy(self) -> bool:
        return self.process.poll() is None and not self.gate.quarantine_reason

    async def preview(self) -> tuple[bytes, str, str, float]:
        if not self.healthy():
            raise BrowserUnavailable("Browser disconnected")
        reply = await self.transport.send("Page.captureScreenshot", {"format": "png"})
        image = base64.b64decode(reply["data"], validate=True)
        url = await self.page.current_url()
        title = await self.page._eval("document.title || ''")
        return image, url, str(title or ""), time.time()


async def launch_owned_browser(profile: Path, *, timeout: float = 15) -> OwnedBrowser:
    if browse_killed():
        raise BrowserUnavailable("Browser activity is stopped")
    executable = next(
        (
            candidate
            for name in ("chromium", "chromium-browser", "google-chrome")
            if (candidate := shutil.which(name))
        ),
        None,
    )
    if executable == "/snap/bin/chromium":
        snap_binary = Path("/snap/chromium/current/usr/lib/chromium-browser/chrome")
        if snap_binary.is_file():
            executable = str(snap_binary)
    if executable is None:
        raise BrowserUnavailable("Chromium is unavailable")
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    port_file = profile / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    proxy = CustomerBrowserProxy()
    await proxy.start()
    args = [
        executable,
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--remote-debugging-address=127.0.0.1",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        f"--proxy-server=http=127.0.0.1:{proxy.port};https=127.0.0.1:{proxy.port}",
        "--proxy-bypass-list=<-loopback>",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "about:blank",
    ]
    if os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        await proxy.close()
        raise BrowserUnavailable("Chromium could not start") from exc
    transport = None
    try:
        deadline = time.monotonic() + timeout
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserUnavailable("Chromium exited before readiness")
            await asyncio.sleep(0.1)
        if not port_file.exists():
            raise BrowserUnavailable("Chromium readiness timed out")
        port = int(port_file.read_text().splitlines()[0])
        target = await asyncio.to_thread(ready_page_target, port, deadline)
        if target is None:
            raise BrowserUnavailable("Chromium page did not accept commands")
        transport = await WebSocketCdpTransport.connect(target)
        gate = GatedCdpSession(
            transport, caller_identity="customer_browser", source="dashboard"
        )
        await gate.start()

        async def browser_event(method: str, params: dict) -> None:
            if method == "Fetch.requestPaused":
                await transport.send(
                    "Fetch.continueRequest", {"requestId": params["requestId"]}
                )
            elif method == "Fetch.authRequired":
                challenge = params.get("authChallenge") or {}
                origin = str(challenge.get("origin") or "")
                parsed = urlsplit(origin if "://" in origin else f"http://{origin}")
                is_own_proxy = (
                    challenge.get("source") == "Proxy"
                    and parsed.hostname == "127.0.0.1"
                    and parsed.port == proxy.port
                )
                response = {
                    "response": "ProvideCredentials" if is_own_proxy else "CancelAuth"
                }
                if is_own_proxy:
                    response["username"], response["password"] = proxy.credentials
                await transport.send(
                    "Fetch.continueWithAuth",
                    {
                        "requestId": params["requestId"],
                        "authChallengeResponse": response,
                    },
                )
            else:
                await gate.handle_event(method, params)

        transport.set_event_listener(browser_event)
        await transport.send("Fetch.enable", {"handleAuthRequests": True})
        page = CdpPageDriver(transport)
        await page.current_url()
        return OwnedBrowser(process, transport, gate, page, proxy)
    except BaseException:
        if transport is not None:
            with contextlib.suppress(Exception):
                await transport.close()
        if process.poll() is None:
            process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.to_thread(process.wait), 5)
            if process.poll() is None:
                process.kill()
        await proxy.close()
        raise
