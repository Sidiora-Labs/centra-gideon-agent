"""Interactive terminal client for the gateway's chat and approval protocol."""

from __future__ import annotations

import asyncio
import contextlib
import curses
import json
import locale
import os
import secrets
import sys
import textwrap
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlsplit

import aiohttp


class TerminalError(Exception):
    pass


@dataclass
class TerminalState:
    session: str = ""
    title: str = "New chat"
    agent: str = ""
    model: str = ""
    task_mode: str = "agent"
    approval_mode: str = "normal"
    running: bool = False
    pending: dict | None = None
    input: str = ""
    scroll: int = 0
    lines: list[str] = field(default_factory=list)
    error: str = ""
    revision: int = 0

    def add(self, text: str) -> None:
        self.lines.append(text)
        self.lines = self.lines[-1200:]
        self.scroll = 0
        self.revision += 1

    def event(self, envelope: dict) -> None:
        kind = envelope.get("type")
        data = envelope.get("data")
        if not isinstance(data, dict):
            return
        if kind == "approval_resolved":
            if self.pending and data.get("id") == self.pending.get("id"):
                self.add(f"Approval {data.get('decision', 'resolved')}")
                self.pending = None
            return
        if data.get("session") != self.session:
            return
        if kind == "chat_chunk":
            chunk = str(data.get("content", ""))
            if self.lines and self.lines[-1].startswith("Gideon: ") and self.running:
                self.lines[-1] += chunk
                self.revision += 1
            else:
                self.add("Gideon: " + chunk)
        elif kind == "chat_thinking":
            self.add("Thinking: " + str(data.get("content", "")))
        elif kind == "tool_call":
            self.add("Tool: " + str(data.get("tool", "")) + " " + str(data.get("purpose", "")))
            preview = str(data.get("input_preview", ""))
            if preview:
                self.add("  " + preview[:1000])
        elif kind == "tool_result":
            self.add("Result: " + str(data.get("output", ""))[:1200])
        elif kind == "approval":
            self.pending = data
            self.add("Approval requested: " + str(data.get("tool", "")))
            if data.get("tool_input"):
                self.add("  " + str(data["tool_input"])[:1200])
        elif kind == "chat_message" and data.get("role") == "error":
            self.add("Error: " + str(data.get("content", "")))
        elif kind == "chat_done":
            self.running = False
            self.add("Turn complete")


class GatewayClient:
    def __init__(self, url: str, token: str = "", cookie: str = "") -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise TerminalError("--url must be an http(s) gateway URL")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise TerminalError("--url must be the gateway origin without a path or query")
        if not token and not cookie:
            raise TerminalError("an authenticated token or cookie is required")
        self.url = url.rstrip("/")
        self.token = token
        self.cookie = cookie
        self.http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    def url_for(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        auth = f"{sep}{urlencode({'token': self.token})}" if self.token else ""
        return self.url + path + auth

    async def __aenter__(self) -> "GatewayClient":
        headers = {"Cookie": self.cookie} if self.cookie else {}
        self.http = aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=None))
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.ws:
            await self.ws.close()
        if self.http:
            await self.http.close()

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        assert self.http is not None
        async with self.http.request(method, self.url_for(path), json=body, timeout=35) as response:
            raw = await response.text()
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"error": raw[:200]}
            if response.status >= 400:
                error = data.get("error", data) if isinstance(data, dict) else data
                raise TerminalError(f"{method} {path}: HTTP {response.status}: {error}")
            if isinstance(data, dict) and data.get("ok") is False:
                raise TerminalError(f"{method} {path}: {data.get('error') or data}")
            return data if isinstance(data, dict) else {"items": data}

    async def connect(self) -> None:
        assert self.http is not None
        self.ws = await self.http.ws_connect(self.url_for("/api/ws"), heartbeat=25)


class TerminalApp:
    def __init__(self, client: GatewayClient, session: str = "", allow_setup: bool = True) -> None:
        self.client = client
        self.state = TerminalState()
        self.requested_session = session
        self.allow_setup = allow_setup
        self.screen = None
        self.alive = True
        self._reader: asyncio.Task | None = None

    async def start(self, screen) -> None:
        self.screen = screen
        screen.keypad(True)
        screen.nodelay(True)
        with contextlib.suppress(curses.error):
            curses.curs_set(1)
        await self.client.connect()
        self._reader = asyncio.create_task(self._read_events())
        try:
            if self.requested_session:
                await self._open_session(self.requested_session)
            else:
                await self._new_session()
            self.state.add("/help lists commands. Enter sends. Page Up and Page Down scroll. Esc clears input.")
            while self.alive:
                self._render()
                key = self._read_key()
                if key != -1:
                    await self._key(key)
                await asyncio.sleep(0.04)
        finally:
            if self._reader:
                self._reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader

    def _read_key(self) -> int:
        try:
            key = self.screen.get_wch()
        except curses.error:
            return -1
        return ord(key) if isinstance(key, str) else key

    async def _read_events(self) -> None:
        assert self.client.ws is not None
        try:
            async for msg in self.client.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    with contextlib.suppress(ValueError):
                        data = json.loads(msg.data)
                        if isinstance(data, dict):
                            self.state.event(data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            if self.alive:
                self.state.running = False
                self.state.add("Connection to gateway closed. Reconnect by restarting the terminal.")
                self.alive = False

    async def _new_session(self, name: str = "") -> None:
        cleaned = name.strip()
        if cleaned and any(not (c.isalnum() or c in "-_.") for c in cleaned):
            raise TerminalError("session names may contain letters, numbers, dash, dot, or underscore")
        key = "terminal-" + (cleaned or secrets.token_hex(6))
        data = await self.client.request("POST", "/api/chat/sessions", {"name": key})
        self.state.session = str(data.get("key") or key)
        await self._load_session()

    async def _open_session(self, key: str) -> None:
        sessions = (await self.client.request("GET", "/api/chat/sessions?all=1")).get("items", [])
        if not any(row.get("key") == key for row in sessions):
            raise TerminalError(f"session {key!r} was not found")
        await self.client.request("POST", f"/api/chat/sessions/{quote(key, safe='')}/resume", {"key": key})
        self.state.session = key
        await self._load_session()

    async def _load_session(self) -> None:
        s = self.state
        data = await self.client.request("GET", f"/api/chat/sessions/{quote(s.session, safe='')}?limit=500")
        s.title = str(data.get("title") or s.session)
        s.agent = str(data.get("agent") or "")
        s.model = str(data.get("model") or "")
        s.task_mode = str(data.get("task_mode") or "agent")
        s.approval_mode = str(data.get("approval") or "normal")
        s.running = bool(data.get("running"))
        s.pending = None
        s.lines = []
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", ""))
            content = str(msg.get("content", ""))
            if content and role not in ("chunk",):
                s.lines.append(f"{role.title()}: {content}")
        s.lines = s.lines[-1200:]
        s.scroll = 0

    async def _key(self, key: int) -> None:
        s = self.state
        if s.pending:
            if key in (ord("y"), ord("Y"), ord("n"), ord("N"), 27):
                action = "approved" if key in (ord("y"), ord("Y")) else "rejected"
                pending = s.pending
                s.pending = None
                try:
                    await self.client.request("POST", f"/api/chat/sessions/{quote(s.session, safe='')}/approve", {"request_id": pending["id"], "action": action})
                    s.add(f"Approval {action}")
                except TerminalError as exc:
                    s.pending = pending
                    s.add(str(exc))
            return
        if key == 27:
            s.input = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            s.input = s.input[:-1]
        elif key == curses.KEY_PPAGE:
            s.scroll += 8
        elif key == curses.KEY_NPAGE:
            s.scroll = max(0, s.scroll - 8)
        elif key in (10, 13, curses.KEY_ENTER):
            line, s.input = s.input.strip(), ""
            if line:
                try:
                    await self._submit(line)
                except TerminalError as exc:
                    s.add("Error: " + str(exc))
        elif key == 3:
            if s.running:
                await self._stop()
            else:
                self.alive = False
        elif 32 <= key <= 0x10FFFF:
            try:
                s.input += chr(key)
            except ValueError:
                pass

    async def _submit(self, line: str) -> None:
        s = self.state
        if line.startswith("/"):
            command, _, arg = line[1:].partition(" ")
            arg = arg.strip()
            await self._command(command.lower(), arg)
            return
        if s.running:
            s.add("A turn is running. Use /stop before sending another message.")
            return
        s.add("You: " + line)
        s.running = True
        try:
            await self.client.request("POST", "/api/chat?ws=1", {"message": line, "session": s.session})
        except Exception:
            s.running = False
            raise

    async def _stop(self) -> None:
        await self.client.request("POST", f"/api/chat/sessions/{quote(self.state.session, safe='')}/stop", {})
        self.state.add("Stop requested")

    async def _command(self, cmd: str, arg: str) -> None:
        s = self.state
        key = quote(s.session, safe="")
        if cmd in ("q", "quit", "exit"):
            self.alive = False
        elif cmd == "help":
            s.add("/new [name]  /sessions  /switch ID  /history  /agents  /agent NAME  /model NAME")
            s.add("/mode agent|ask|plan|build  /approval normal|trust_reads|trust  /stop  /setup  /quit")
        elif cmd == "new":
            await self._new_session(arg)
        elif cmd == "sessions":
            rows = (await self.client.request("GET", "/api/chat/sessions?all=1")).get("items", [])
            for row in rows[:30]:
                s.add(f"{row.get('key', '')}  {row.get('title', '')}")
        elif cmd == "switch":
            if not arg:
                raise TerminalError("usage: /switch SESSION_ID")
            await self._open_session(arg)
        elif cmd == "history":
            await self._load_session()
        elif cmd == "agents":
            data = await self.client.request("GET", "/api/agents")
            for row in data.get("agents", []):
                s.add(str(row.get("name", row)) if isinstance(row, dict) else str(row))
        elif cmd in ("agent", "model"):
            if not arg:
                raise TerminalError(f"usage: /{cmd} NAME")
            await self.client.request("POST", f"/api/chat/sessions/{key}/{cmd}", {cmd: arg})
            setattr(s, cmd, arg)
            s.add(f"Session {cmd}: {arg}")
        elif cmd == "mode":
            if arg not in ("agent", "ask", "plan", "build"):
                raise TerminalError("usage: /mode agent|ask|plan|build")
            await self.client.request("POST", "/api/chat/task-mode", {"mode": arg, "session": s.session})
            s.task_mode = arg
            s.add(f"Session task mode: {arg}")
        elif cmd == "approval":
            if arg not in ("normal", "trust_reads", "trust"):
                raise TerminalError("usage: /approval normal|trust_reads|trust")
            await self.client.request("POST", "/api/chat/mode", {"mode": arg, "session": s.session})
            s.approval_mode = arg
            s.add(f"Session approval mode: {arg}")
        elif cmd == "stop":
            await self._stop()
        elif cmd == "setup" and self.allow_setup:
            await self._setup_provider()
        else:
            raise TerminalError("unknown command; use /help")

    async def _ask(self, label: str, *, secret: bool = False, default: str = "") -> str:
        s = self.state
        s.input = ""
        while True:
            self._render(prompt=f"{label} [{default}]: " if default else f"{label}: ", mask=secret)
            key = self._read_key()
            if key in (10, 13, curses.KEY_ENTER):
                value, s.input = s.input.strip(), ""
                return value or default
            if key == 27:
                s.input = ""
                raise TerminalError("setup cancelled")
            if key in (curses.KEY_BACKSPACE, 127, 8):
                s.input = s.input[:-1]
            elif 32 <= key <= 0x10FFFF:
                with contextlib.suppress(ValueError):
                    s.input += chr(key)
            await asyncio.sleep(0.04)

    async def _setup_provider(self) -> None:
        s = self.state
        providers = (await self.client.request("GET", "/api/model-providers")).get("providers", [])
        if providers:
            s.add("Configured instances: " + ", ".join(str(p.get("name")) for p in providers))
        name = await self._ask("Existing instance to use (blank adds new)")
        if name:
            if not any(p.get("name") == name for p in providers):
                raise TerminalError("choose a listed provider instance")
        else:
            types = (await self.client.request("GET", "/api/model-provider-types")).get("types", [])
            if not types:
                catalog = await self.client.request("GET", "/api/apps/catalog")
                apps = [
                    entry
                    for group in ("bundled", "localApps", "remoteApps", "gitApps")
                    for entry in catalog.get(group, [])
                    if entry.get("providerType") == "model"
                    and "chat" in entry.get("providerCapabilities", [])
                ]
                if not apps:
                    raise TerminalError("no chat provider apps are available in the gateway catalog")
                s.add("Available chat provider apps: " + ", ".join(str(a.get("name")) for a in apps))
                app_name = await self._ask("App to install (blank skips)")
                selected_app = next((app for app in apps if app.get("name") == app_name), None)
                if not selected_app:
                    s.add("No provider app installed")
                    return
                source = str(selected_app.get("pointer") or selected_app.get("source") or "")
                if not source:
                    raise TerminalError("the chosen app has no installable source")
                assert self.client.http is not None
                async with self.client.http.post(self.client.url_for("/api/apps"), json={"source": source}) as response:
                    result = await response.json()
                if result.get("needs_consent"):
                    s.add("Install warning: " + str(result.get("error") or result.get("scan") or "review required"))
                    if (await self._ask("Install despite warning? type yes")).lower() != "yes":
                        s.add("Installation cancelled")
                        return
                    async with self.client.http.post(self.client.url_for("/api/apps"), json={"source": source, "confirm": True}) as response:
                        result = await response.json()
                if not result.get("ok"):
                    raise TerminalError("app install failed: " + str(result.get("error") or result))
                s.add("Installed " + app_name)
                types = (await self.client.request("GET", "/api/model-provider-types")).get("types", [])
            s.add("Installed provider types: " + ", ".join(str(t.get("type")) for t in types))
            ptype = await self._ask("Provider type")
            selected = next((t for t in types if t.get("type") == ptype), None)
            if not selected:
                raise TerminalError("choose one of the listed installed provider types")
            name = await self._ask("Instance name", default=ptype)
            schema = selected.get("settingsSchema") or {}
            options = {}
            for field_name, spec in (schema.get("properties") or {}).items():
                if not isinstance(spec, dict):
                    continue
                label = str((spec.get("x-meta") or {}).get("label") or field_name)
                secret = bool(spec.get("writeOnly") or spec.get("format") == "password" or (spec.get("x-meta") or {}).get("sensitive"))
                value = await self._ask(label, secret=secret, default="" if secret else str(spec.get("default") or ""))
                if field_name in (schema.get("required") or []) and not value:
                    raise TerminalError(f"{label} is required; nothing was saved")
                if value:
                    options[field_name] = value
            await self.client.request("POST", "/api/model-providers", {"name": name, "type": ptype, "model": "", "options": options})
        test = await self.client.request("POST", f"/api/model-providers/{quote(name, safe='')}/test", {})
        s.add(f"Provider {name}: {test.get('status', '')} {test.get('message', '')}")
        if test.get("status") == "error":
            return
        data = await self.client.request("GET", f"/api/model-providers/{quote(name, safe='')}/models")
        if data.get("error"):
            raise TerminalError("model discovery failed: " + str(data["error"]))
        models = data.get("models", [])
        if models:
            s.add("Available models: " + ", ".join(str(m.get("id") or m.get("name") or m) if isinstance(m, dict) else str(m) for m in models[:20]))
        model = await self._ask("Model to use in this chat (blank skips)")
        if model:
            known_ids = {str(m.get("id") or m.get("name")) if isinstance(m, dict) else str(m) for m in models}
            if known_ids and model not in known_ids:
                raise TerminalError("choose a discovered model id")
            await self.client.request("PUT", f"/api/model-providers/{quote(name, safe='')}", {"model": model})
            await self.client.request("PUT", "/api/models/active/chat", {"models": [f"{name}:{model}"]})
            await self.client.request("POST", f"/api/chat/sessions/{quote(s.session, safe='')}/model", {"model": model})
            s.model = model
            s.add(f"Default chat model and session model: {name}:{model}")

    def _render(self, prompt: str = "> ", mask: bool = False) -> None:
        screen = self.screen
        height, width = screen.getmaxyx()
        if height < 5 or width < 25:
            return
        screen.erase()
        s = self.state
        header = f" Gideon  {s.title}  agent:{s.agent or 'default'}  model:{s.model or 'default'} "
        header += f" mode:{s.task_mode} approval:{s.approval_mode} "
        with contextlib.suppress(curses.error):
            screen.addnstr(0, 0, header, width - 1, curses.A_REVERSE)
        wrapped = []
        for line in s.lines:
            wrapped.extend(textwrap.wrap(line, width=max(10, width - 2), replace_whitespace=False, drop_whitespace=False) or [""])
        visible = wrapped[max(0, len(wrapped) - (height - 3) - s.scroll):max(0, len(wrapped) - s.scroll)]
        for idx, line in enumerate(visible, 1):
            with contextlib.suppress(curses.error):
                screen.addnstr(idx, 0, line, width - 1)
        status = "Approve? y=yes, n=no (default deny)" if s.pending else "Running — Ctrl+C stops" if s.running else "Ready"
        with contextlib.suppress(curses.error):
            screen.addnstr(height - 2, 0, status, width - 1, curses.A_REVERSE)
            value = "*" * len(s.input) if mask else s.input
            screen.addnstr(height - 1, 0, prompt + value[-max(0, width - len(prompt) - 2):], width - 1)
            screen.move(height - 1, min(width - 2, len(prompt) + len(value)))
        screen.refresh()


def run_terminal(args, *, allow_local_auth: bool = True, allow_setup: bool = True) -> None:
    locale.setlocale(locale.LC_ALL, "")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("gideon tui requires an interactive terminal")
    url = (getattr(args, "url", "") or "").strip()
    token = (getattr(args, "token", "") or os.environ.get("GIDEON_TOKEN", "")).strip()
    cookie = (getattr(args, "cookie", "") or os.environ.get("GIDEON_COOKIE", "")).strip()
    if not url:
        if not allow_local_auth:
            raise SystemExit("gideon tui requires --url and GIDEON_TOKEN or GIDEON_COOKIE")
        from gideon.interfaces.cli.run import mint_local_token, probe_gateway
        from gideon.interfaces.cli.server import resolve_client_port

        port = resolve_client_port(getattr(args, "port", None))
        if not probe_gateway(port):
            raise SystemExit(f"No gateway on port {port}; start `gideon gateway` first")
        url = f"http://127.0.0.1:{port}"
        if not token and not cookie:
            token = mint_local_token(port)
    try:
        client = GatewayClient(url, token, cookie)
        app = TerminalApp(client, getattr(args, "session", "") or "", allow_setup=allow_setup)

        async def _main(screen) -> None:
            async with client:
                await app.start(screen)

        curses.wrapper(lambda screen: asyncio.run(_main(screen)))
    except (TerminalError, aiohttp.ClientError) as exc:
        raise SystemExit(f"gideon tui: {exc}") from exc
