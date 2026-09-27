"""Exclusive versioned control of owned customer browser processes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from gideon.integrations.browse.customer_engine import BrowserUnavailable, OwnedBrowser, launch_owned_browser
from gideon.integrations.browse.customer_sessions import (
    CustomerBrowserSession, CustomerBrowserSessionStore, InvalidSessionTransition,
    StaleSessionVersion,
)
from gideon.integrations.browse.grant import BrowserGrant
from gideon.integrations.browse.killswitch import browse_killed


class ControlDenied(RuntimeError):
    pass


class CustomerBrowserControl:
    def __init__(self, store: CustomerBrowserSessionStore, profile_root: Path):
        self.store = store
        self.profile_root = Path(profile_root)
        self._engines: dict[str, OwnedBrowser] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._actions: dict[str, asyncio.Task] = {}
        self._interrupting: set[str] = set()

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _engine(self, current: CustomerBrowserSession) -> OwnedBrowser:
        engine = self._engines.get(current.id)
        if current.status != "active" or engine is None or not engine.healthy():
            raise BrowserUnavailable("Browser is not connected")
        return engine

    async def start(self, session_id: str, account_id: str, owner_id: str,
                    expected_version: int) -> CustomerBrowserSession:
        async with self._lock(session_id):
            current = self.store.get(session_id, account_id, owner_id)
            if current.version != expected_version:
                raise StaleSessionVersion
            if current.status != "reserved":
                raise InvalidSessionTransition
            try:
                engine = await launch_owned_browser(self.profile_root / current.id)
            except Exception as exc:
                self.store.transition(session_id, account_id, owner_id,
                                      expected_version=current.version, action="error")
                raise BrowserUnavailable("Browser could not become ready") from exc
            try:
                changed = self.store.transition(session_id, account_id, owner_id,
                                                expected_version=current.version, action="activate")
            except BaseException:
                await engine.close()
                raise
            self._engines[session_id] = engine
            return changed

    async def state(self, session_id: str, account_id: str, owner_id: str) -> CustomerBrowserSession:
        async with self._lock(session_id):
            current = self.store.get(session_id, account_id, owner_id)
            if current.status == "active" and (session_id not in self._engines or
                                                  not self._engines[session_id].healthy()):
                current = self.store.transition(session_id, account_id, owner_id,
                                                expected_version=current.version, action="error")
            return current

    async def preview(self, session_id: str, account_id: str, owner_id: str):
        async with self._lock(session_id):
            current = await self._state_unlocked(session_id, account_id, owner_id)
            engine = await self._engine(current)
        image = await engine.preview()
        async with self._lock(session_id):
            latest = await self._state_unlocked(session_id, account_id, owner_id)
            if (latest.status != "active" or self._engines.get(session_id) is not engine
                    or latest.version != current.version
                    or latest.control_holder != current.control_holder):
                raise BrowserUnavailable("Browser changed during preview")
            return latest, image

    async def _state_unlocked(self, session_id: str, account_id: str, owner_id: str):
        current = self.store.get(session_id, account_id, owner_id)
        if current.status == "active" and (session_id not in self._engines or
                                              not self._engines[session_id].healthy()):
            current = self.store.transition(session_id, account_id, owner_id,
                                            expected_version=current.version, action="error")
        return current

    async def change_holder(self, session_id: str, account_id: str, owner_id: str,
                            expected_version: int, action: str) -> CustomerBrowserSession:
        async with self._lock(session_id):
            current = await self._state_unlocked(session_id, account_id, owner_id)
            engine = await self._engine(current)
            if session_id in self._interrupting:
                raise ControlDenied("Browser control is changing")
            changed = self.store.transition(session_id, account_id, owner_id,
                                            expected_version=expected_version, action=action)
            pending = self._actions.pop(session_id, None)
            if pending is None:
                return changed
            if not pending.done():
                pending.cancel()
            self._interrupting.add(session_id)

        async def stop_old_navigation() -> None:
            try:
                if pending is not None:
                    await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 0.25)
                await asyncio.wait_for(engine.transport.send("Page.stopLoading", {}), 0.75)
            except Exception as exc:
                await engine.close()
                async with self._lock(session_id):
                    latest = self.store.get(session_id, account_id, owner_id)
                    if latest.status == "active" and latest.version == changed.version:
                        self.store.transition(session_id, account_id, owner_id,
                                              expected_version=latest.version, action="error")
                raise BrowserUnavailable("Browser could not stop the previous navigation") from exc
            finally:
                async with self._lock(session_id):
                    self._interrupting.discard(session_id)

        stopping = asyncio.create_task(stop_old_navigation())
        stopping.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        await asyncio.shield(stopping)
        return changed

    async def navigate(self, session_id: str, account_id: str, owner_id: str,
                       expected_version: int, url: str, *, actor: str = "customer",
                       grant: BrowserGrant | None = None) -> CustomerBrowserSession:
        async with self._lock(session_id):
            current = await self._state_unlocked(session_id, account_id, owner_id)
            engine = await self._authorized(current, expected_version, actor, grant, url)
            if session_id in self._interrupting:
                raise ControlDenied("Browser control is changing")
            if session_id in self._actions:
                raise ControlDenied("Browser action already in progress")
            pending = asyncio.create_task(engine.gate.navigate(url))
            self._actions[session_id] = pending
        try:
            result = await pending
        except asyncio.CancelledError:
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            raise ControlDenied("Browser control changed during navigation") from None
        except BaseException:
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            raise
        async with self._lock(session_id):
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            latest = await self._state_unlocked(session_id, account_id, owner_id)
            if latest.version != expected_version or latest.control_holder != actor or browse_killed():
                raise ControlDenied("Browser control changed during navigation")
            if not result.ok:
                raise ControlDenied(result.reason or "Navigation denied")
            return self.store.transition(session_id, account_id, owner_id,
                                         expected_version=expected_version, action="touch")

    async def input(self, session_id: str, account_id: str, owner_id: str,
                    expected_version: int, command: str, value: str,
                    *, actor: str = "customer", grant: BrowserGrant | None = None):
        async with self._lock(session_id):
            current = await self._state_unlocked(session_id, account_id, owner_id)
            engine = await self._authorized(current, expected_version, actor, grant)
            if session_id in self._interrupting:
                raise ControlDenied("Browser control is changing")
            if session_id in self._actions:
                raise ControlDenied("Browser action already in progress")
            pending = asyncio.create_task(self._send_input(engine, command, value))
            self._actions[session_id] = pending
        try:
            await pending
        except asyncio.CancelledError:
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            raise ControlDenied("Browser control changed during input") from None
        except BaseException:
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            raise
        async with self._lock(session_id):
            if self._actions.get(session_id) is pending:
                self._actions.pop(session_id, None)
            latest = await self._state_unlocked(session_id, account_id, owner_id)
            if latest.version != expected_version or latest.control_holder != actor or browse_killed():
                raise ControlDenied("Browser control changed during input")
            return self.store.transition(session_id, account_id, owner_id,
                                         expected_version=expected_version, action="touch")

    async def _send_input(self, engine: OwnedBrowser, command: str, value: str) -> None:
        if command == "scroll" and value in {"up", "down"}:
            await engine.page.scroll(value)
        elif command == "key" and value in {"Enter", "Tab", "Escape", "Backspace"}:
            await engine.transport.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": value})
            await engine.transport.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": value})
        elif command == "text" and 0 < len(value) <= 2000:
            await engine.transport.send("Input.insertText", {"text": value})
        else:
            raise ValueError("Unsupported browser input")

    async def _authorized(self, current: CustomerBrowserSession, version: int,
                          actor: str, grant: BrowserGrant | None, url: str = "") -> OwnedBrowser:
        if current.version != version:
            raise StaleSessionVersion
        if current.control_holder != actor or browse_killed():
            raise ControlDenied("Browser control is unavailable")
        if actor == "assistant":
            engine = await self._engine(current)
            host = urlparse(url or await engine.page.current_url()).hostname
            if grant is None or not grant.granted or grant.granted_at is None or (
                grant.bound_device_id != current.id or not host or host not in grant.scope
            ):
                raise ControlDenied("A current scoped browser grant is required")
        elif actor != "customer":
            raise ControlDenied("Unknown browser actor")
        return await self._engine(current)

    async def close(self, session_id: str, account_id: str, owner_id: str,
                    expected_version: int) -> CustomerBrowserSession:
        async with self._lock(session_id):
            current = self.store.get(session_id, account_id, owner_id)
            if current.version != expected_version:
                raise StaleSessionVersion
            engine = self._engines.pop(session_id, None)
            pending = self._actions.pop(session_id, None)
            if pending is not None:
                pending.cancel()
            if engine is not None:
                await engine.close()
            return self.store.transition(session_id, account_id, owner_id,
                                         expected_version=expected_version, action="close")

    async def shutdown(self) -> None:
        for pending in self._actions.values():
            pending.cancel()
        self._actions.clear()
        for engine in list(self._engines.values()):
            await engine.close()
        self._engines.clear()
