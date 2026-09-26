"""Inbound ACP stdio adapter for editor-launched Gideon sessions."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from pathlib import Path
from typing import Any

from gideon.core.config import AppConfig
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
)

_MAX_FRAME = 8 * 1024 * 1024


class AcpStdioServer:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.turns: dict[str, asyncio.Task] = {}
        self.pending: dict[int, asyncio.Future] = {}
        self._next_request_id = -1
        self._write_lock = asyncio.Lock()

    async def send(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            await asyncio.to_thread(sys.stdout.buffer.write, data)
            await asyncio.to_thread(sys.stdout.buffer.flush)

    async def reply(self, request_id: Any, result: Any = None, error: tuple[int, str] | None = None) -> None:
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is None:
            frame["result"] = result
        else:
            frame["error"] = {"code": error[0], "message": error[1]}
        await self.send(frame)

    async def update(self, session_id: str, body: dict[str, Any]) -> None:
        await self.send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": body}})

    async def _permission(self, session_id: str, provider: Any, event: Any) -> None:
        request_id = self._next_request_id
        self._next_request_id -= 1
        offered = {"allow_once": "Allow once", "reject_once": "Reject"}
        fut = asyncio.get_running_loop().create_future()
        self.pending[request_id] = fut
        try:
            await self.send({
                "jsonrpc": "2.0", "id": request_id, "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": str(event.tool_call_id or event.request_id), "title": str(event.title or "Tool request")},
                    "options": [{"optionId": key, "name": label, "kind": "allow_once" if key == "allow_once" else "reject_once"} for key, label in offered.items()],
                },
            })
            response = await asyncio.wait_for(fut, timeout=120)
            outcome = response.get("result", {}).get("outcome", {}) if isinstance(response, dict) else {}
            if outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow_once":
                await provider.approve_tool(event.request_id)
            else:
                await provider.reject_tool(event.request_id)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await provider.reject_tool(event.request_id)
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
        finally:
            self.pending.pop(request_id, None)

    async def _prompt(self, session_id: str, request_id: Any, params: dict[str, Any]) -> None:
        provider = self.sessions[session_id]
        parts = params.get("prompt") or []
        if not isinstance(parts, list):
            await self.reply(request_id, error=(-32602, "prompt must be a list"))
            return
        text = "\n".join(str(part.get("text")) for part in parts if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)).strip()
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "image":
                continue
            data = part.get("data")
            mime = part.get("mimeType")
            if not isinstance(data, str) or not isinstance(mime, str) or not provider.stage_image_part(f"data:{mime};base64,{data}"):
                await self.reply(request_id, error=(-32602, "image content is unsupported by the selected provider"))
                return
        if not text:
            await self.reply(request_id, error=(-32602, "text prompt required"))
            return
        try:
            async for event in provider.stream(text):
                if event.kind in (EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK) and event.text:
                    kind = "agent_message_chunk" if event.kind == EVENT_TEXT_CHUNK else "agent_thought_chunk"
                    await self.update(session_id, {"sessionUpdate": kind, "content": {"type": "text", "text": event.text}})
                elif event.kind in (EVENT_TOOL_CALL, EVENT_TOOL_CALL_UPDATE):
                    await self.update(session_id, {
                        "sessionUpdate": "tool_call" if event.kind == EVENT_TOOL_CALL else "tool_call_update",
                        "toolCallId": str(event.tool_call_id or event.request_id),
                        "title": str(event.title or "Tool"),
                        "kind": str(event.tool_kind or "other"),
                        "status": "in_progress" if event.kind == EVENT_TOOL_CALL else "completed",
                        "rawInput": event.tool_input_obj or event.tool_input,
                    })
                elif event.kind == EVENT_TOOL_RESULT:
                    await self.update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": str(event.tool_call_id or event.request_id), "status": "completed", "content": [{"type": "text", "text": str(event.tool_output)}]})
                elif event.kind == EVENT_PERMISSION_REQUEST:
                    await self._permission(session_id, provider, event)
                elif event.kind == EVENT_COMPLETE:
                    await self.reply(request_id, {"stopReason": event.stop_reason or "end_turn"})
                    return
            await self.reply(request_id, {"stopReason": "end_turn"})
        except asyncio.CancelledError:
            await provider.cancel(wait_ack_timeout=2)
            await self.reply(request_id, {"stopReason": "cancelled"})
        except Exception as exc:
            await self.reply(request_id, error=(-32603, str(exc)[:300]))
        finally:
            self.turns.pop(session_id, None)

    async def dispatch(self, frame: dict[str, Any]) -> None:
        if "method" not in frame:
            future = self.pending.get(frame.get("id"))
            if future and not future.done():
                future.set_result(frame)
            return
        method = frame.get("method")
        request_id = frame.get("id")
        params = frame.get("params") or {}
        if not isinstance(params, dict):
            await self.reply(request_id, error=(-32602, "params must be an object"))
            return
        if method == "initialize":
            await self.reply(request_id, {"protocolVersion": 1, "agentCapabilities": {"promptCapabilities": {"image": True}, "loadSession": False}, "agentInfo": {"name": "Gideon", "title": "Gideon", "version": "1"}})
            return
        if method == "session/new":
            cwd = Path(str(params.get("cwd") or Path.cwd())).expanduser().resolve()
            if not cwd.is_dir():
                await self.reply(request_id, error=(-32602, "cwd must be an existing directory"))
                return
            session_id = secrets.token_urlsafe(18)
            config = AppConfig.load()
            provider = config.create_provider_factory()(f"acp:{session_id}", agent=config.default_agent or None, cwd=str(cwd))
            provider.set_workspace(cwd)
            provider.set_session_key(f"acp:{session_id}")
            try:
                await provider.start()
            except Exception as exc:
                await self.reply(request_id, error=(-32603, str(exc)[:300]))
                return
            self.sessions[session_id] = provider
            await self.reply(request_id, {"sessionId": session_id})
            return
        session_id = str(params.get("sessionId") or "")
        provider = self.sessions.get(session_id)
        if provider is None:
            await self.reply(request_id, error=(-32001, "session not found"))
            return
        if method == "session/prompt":
            if session_id in self.turns:
                await self.reply(request_id, error=(-32002, "session is busy"))
                return
            self.turns[session_id] = asyncio.create_task(self._prompt(session_id, request_id, params))
        elif method == "session/cancel":
            turn = self.turns.get(session_id)
            if turn:
                turn.cancel()
            await self.reply(request_id, {})
        else:
            await self.reply(request_id, error=(-32601, f"unsupported method {method!r}"))

    async def serve(self) -> None:
        try:
            while True:
                raw = await asyncio.to_thread(sys.stdin.buffer.readline, _MAX_FRAME + 1)
                if not raw:
                    break
                if len(raw) > _MAX_FRAME:
                    await self.reply(None, error=(-32600, "frame too large"))
                    break
                try:
                    frame = json.loads(raw)
                except (UnicodeDecodeError, ValueError):
                    await self.reply(None, error=(-32700, "invalid JSON"))
                    continue
                if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                    await self.reply(frame.get("id") if isinstance(frame, dict) else None, error=(-32600, "invalid JSON-RPC request"))
                    continue
                await self.dispatch(frame)
        finally:
            for task in self.turns.values():
                task.cancel()
            if self.turns:
                await asyncio.gather(*self.turns.values(), return_exceptions=True)
            await asyncio.gather(*(provider.shutdown() for provider in self.sessions.values()), return_exceptions=True)


def run_stdio() -> None:
    asyncio.run(AcpStdioServer().serve())
