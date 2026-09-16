"""Public Gideon API organized around conversations, work and application data."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from .context import ContextBuffer, ContextEntry
from .errors import ErrorCode, GideonError
from .transport import GatewayTransport, app_home


class GideonClient(GatewayTransport):
    def __init__(
        self, *, base_url: str = "", token: str = "", app_name: str = "",
        timeout: int = 30, max_retries: int = 3, retry_base_delay: float = 1.0,
        message_length_limit: int = 40_000,
        on_auth_expired: Callable[[], Awaitable[str]] | None = None,
    ):
        connection = dict(base_url=base_url, token=token, app_name=app_name, timeout=timeout,
                          max_retries=max_retries, retry_base_delay=retry_base_delay,
                          on_auth_expired=on_auth_expired)
        super().__init__(**connection)
        self.message_length_limit = message_length_limit
        self._default_session: str | None = None
        self._context = ContextBuffer()

    @staticmethod
    def _resource(collection: str, identity: str = "", action: str = "") -> str:
        parts = ["/api", collection]
        if identity:
            parts.append(identity)
        if action:
            parts.append(action)
        return "/".join(parts)

    async def _collection(self, path: str) -> list[dict[str, Any]]:
        payload = await self._get(path)
        return payload if isinstance(payload, list) else []

    def _validate_text(self, text: str) -> None:
        size = len(text)
        if size > self.message_length_limit:
            raise GideonError(ErrorCode.VALIDATION_ERROR,
                              f"Message length {size} exceeds limit {self.message_length_limit}")

    async def ping(self) -> bool:
        try:
            await self.get_status()
        except Exception:
            return False
        return True

    async def get_status(self) -> dict[str, Any]:
        return await self._get(self._resource("status"))

    async def get_system_info(self) -> dict[str, Any]:
        return await self._get(self._resource("system"))

    async def create_session(self, name: str, agent: str = "") -> dict[str, Any]:
        payload = {"name": name, **({"agent": agent} if agent else {})}
        return await self._post(self._resource("chat/sessions"), payload)

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self._collection(self._resource("chat/sessions"))

    async def delete_session(self, session_id: str) -> None:
        await self._delete(self._resource("chat/sessions", session_id))

    async def send_message(self, session_id: str, message: str) -> None:
        self._validate_text(message)
        if session_id == self._default_session and self.pending_context_count:
            await self.flush_pending_context(session_id)
        await self._post(self._resource("chat"), dict(message=message, session=session_id))

    async def inject_context(self, session_id: str | None, content: str, *,
                             source: str | None = None, ephemeral: bool = True,
                             max_age: float | None = None) -> None:
        entry = ContextEntry(content, source, ephemeral, max_age)
        if session_id is None:
            self._context.append(entry)
        else:
            await self._post(self._resource("chat/sessions", session_id, "context"), entry.payload())

    async def flush_pending_context(self, session_id: str) -> None:
        await self._context.flush(session_id, self._post)

    def set_default_session(self, session_id: str) -> None:
        self._default_session = session_id

    @property
    def pending_context_count(self) -> int:
        return len(self._context.entries)

    async def spawn(self, task: str, agent: str = "") -> str:
        payload = {"task": task, **({"agent": agent} if agent else {})}
        result = await self._post(self._resource("spawn"), payload)
        return str(result.get("id", ""))

    async def spawn_many(self, tasks: list[str], agents: list[str] | None = None) -> list[str]:
        assignments = agents or []
        operations = (self.spawn(task, assignments[index] if index < len(assignments) else "")
                      for index, task in enumerate(tasks))
        return list(await asyncio.gather(*operations))

    async def list_subagents(self) -> list[dict[str, Any]]:
        return await self._collection(self._resource("spawn"))

    async def get_subagent_status(self, agent_id: str) -> dict[str, Any]:
        return await self._get(self._resource("spawn", agent_id))

    async def dispatch_agent(self, agent: str, prompt: str) -> dict[str, Any]:
        return await self._post(self._resource("chat"), dict(message=prompt, agent=agent, app=self.app_name))

    async def dispatch_agent_async(self, agent: str, prompt: str) -> str:
        result = await self._post(self._resource("spawn"), dict(task=prompt, agent=agent))
        return str(result.get("id", ""))

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        return await self.get_subagent_status(task_id)

    async def add_cron(self, name: str, **options: Any) -> dict[str, Any]:
        return await self._post(self._resource("crons"), {"name": name, **options})

    async def list_crons(self) -> list[dict[str, Any]]:
        return await self._collection(self._resource("crons"))

    async def update_cron(self, job_id: str, **options: Any) -> dict[str, Any]:
        return await self._put(self._resource("crons", job_id), options)

    async def remove_cron(self, job_id: str) -> None:
        await self._delete(self._resource("crons", job_id))

    async def _set_cron_enabled(self, job_id: str, enabled: bool) -> None:
        await self._post(self._resource("crons", job_id, "enable"), dict(enabled=enabled))

    async def pause_cron(self, job_id: str) -> None:
        await self._set_cron_enabled(job_id, False)

    async def resume_cron(self, job_id: str) -> None:
        await self._set_cron_enabled(job_id, True)

    async def add_lesson(self, rule: str, category: str, scope: str = "") -> None:
        await self._post(self._resource("lessons"), dict(rule=rule, category=category, scope=scope))

    async def list_lessons(self) -> list[dict[str, Any]]:
        return await self._collection(self._resource("lessons"))

    async def remove_lesson(self, query: str) -> None:
        await self._delete_with_body(self._resource("lessons"), dict(rule=query))

    async def _delete_with_body(self, path: str, body: Any) -> Any:
        return await self._request("DELETE", path, body)

    async def send_notification(self, text: str, **options: Any) -> None:
        self._validate_text(text)
        await self._post(self._resource("send-message"), {"text": text, **options})

    async def list_mcp_servers(self) -> list[dict[str, Any]]:
        return await self._collection(self._resource("mcp/servers"))

    async def register_mcp_server(self, name: str, command: str, args: list[str] | None = None,
                                  env: dict[str, str] | None = None) -> None:
        if not all((name, command)):
            raise GideonError(ErrorCode.VALIDATION_ERROR, "MCP server requires name and command")
        payload: dict[str, Any] = dict(command=command)
        payload.update({key: value for key, value in (("args", args), ("env", env)) if value})
        await self._put(self._resource("mcp/servers", name), payload)

    async def remove_mcp_server(self, name: str) -> None:
        await self._delete(self._resource("mcp/servers", name))

    def get_app_data_dir(self) -> Path:
        return app_home().joinpath("apps", self.app_name or "unknown", "data")

    async def get_app_config(self) -> dict[str, Any]:
        return await self._get(self._resource("apps", self.app_name, "config"))

    async def set_app_config(self, config: dict[str, Any]) -> None:
        await self._put(self._resource("apps", self.app_name, "config"), config)

    async def memory_search(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        path = self._resource("memory/episodic/search") + f"?q={quote(query)}&top_k={top_k}"
        return await self._collection(path)
