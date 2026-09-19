"""Context-driven suggestions with bounded background turns and shared cache state."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web

from gideon.cognition.context import PromptAssembler
from gideon.engine.session import BACKGROUND_KEY
from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
)
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

if TYPE_CHECKING:
    from gideon.interfaces.dashboard.state import ConsoleState

logger = logging.getLogger(__name__)
_REFRESH_INTERVAL_SECS = 30 * 60
_FALLBACK_SUGGESTIONS = [
    "Show health-check status",
    "Break a goal into tasks",
    "Save a note to my knowledge base",
    "Run a command and explain the output",
    "What can you help me with?",
    "Set up a daily briefing",
]
_MEMORY_FIELDS = (
    (
        "read_preferences",
        "User Preferences",
        2000,
        "# User Preferences\n\n<!-- Learned from conversations -->",
    ),
    (
        "read_projects",
        "Active Projects",
        3000,
        "# Active Projects\n\n<!-- Current work context -->",
    ),
    ("read_recent_history", "Recent Activity", 4000, None),
)


@dataclass
class SuggestionsCache:
    suggestions: list[str] = field(default_factory=lambda: list(_FALLBACK_SUGGESTIONS))
    generated_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)


class _ContextSections:
    def __init__(self, state):
        self.state = state
        self.sections = []

    @staticmethod
    def heading(title, body):
        return f"## {title}\n{body}"

    def memory(self):
        source = PromptAssembler.get_memory_for(None)
        for method, label, limit, template in _MEMORY_FIELDS:
            read = getattr(source, method)
            body = read(days=2) if method == "read_recent_history" else read()
            if not body or (template is not None and body.strip() == template):
                continue
            yield self.heading(label, body[:limit])

    def session_line(self, session, log):
        title, key = session.get("title", ""), session.get("key", "")
        if not key:
            return None
        line = f"- **{title or key}**"
        try:
            recent = log.recent(key, max_messages=6)
            messages = [
                message["content"][:150]
                for message in recent
                if message.get("role") == "user" and message.get("content")
            ]
            if messages:
                line += "\n" + "\n".join(
                    f"  - User: {message}" for message in messages[-3:]
                )
        except Exception:
            pass
        return line

    def sessions(self):
        log = self.state.conversation_log
        if not log:
            return
        sessions = log.list_sessions()
        if not sessions:
            return
        lines = [self.session_line(session, log) for session in sessions[:5]]
        lines = [line for line in lines if line is not None]
        if lines:
            yield self.heading("Recent Sessions", "\n".join(lines))

    def automations(self):
        from gideon.automation.triggers.store import TriggerStore
        from gideon.core.config.loader import config_dir

        schedules = TriggerStore(base_dir=config_dir()).load()
        enabled = [row for row in schedules if row.trigger.enabled]
        if enabled:
            body = "\n".join(f"- {row.trigger.name}" for row in enabled[:5])
            yield self.heading("Active Automations", body)

    def render(self):
        for source in (self.memory, self.sessions, self.automations):
            try:
                self.sections.extend(source())
            except Exception:
                logger.debug(
                    "Failed to read %s for suggestions", source.__name__, exc_info=True
                )
        return "\n\n".join(self.sections)


def _build_context(state: "ConsoleState") -> str:
    return _ContextSections(state).render()


def _time_context() -> str:
    stamp = datetime.now().strftime("%A, %B %d %Y at %H:%M")
    return "## Current Time\n" + stamp


class _SuggestionResponse:
    def __init__(self, text):
        self.text = text.strip()

    def unfence(self):
        if self.text.startswith("```"):
            lines = self.text.split("\n")
            end = len(lines) - 1 if lines[-1].startswith("```") else len(lines)
            self.text = "\n".join(lines[1:end]).strip()

    def parse(self):
        self.unfence()
        try:
            values = json.loads(self.text)
        except (json.JSONDecodeError, TypeError):
            values = None
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            accepted = []
            for value in values:
                clean = value.strip()
                if clean and len(clean) <= 80:
                    accepted.append(clean)
            return accepted[:6]
        logger.warning("Failed to parse suggestions response: %s", self.text[:200])
        return []


def _parse_suggestions(text: str) -> list[str]:
    return _SuggestionResponse(text).parse()


def _redact_suggestions(suggestions: list[str]) -> list[str]:
    result = []
    for value in suggestions:
        for scrub in (redact_exfiltration_urls, redact_credentials):
            value, _ = scrub(value)
        result.append(value)
    return result


class _SuggestionTurn:
    def __init__(self, sessions, prompt):
        self.sessions, self.prompt = sessions, prompt
        self.client = None
        self.acquired = False
        self.text = ""

    async def acquire(self):
        from gideon.extensions.providers.provider_bridge import (
            ProviderResolutionError as BridgeError,
        )
        from gideon.integrations.llm.registry import (
            ProviderResolutionError as RegistryError,
        )

        try:
            client, _is_new, _resumed = await self.sessions.get_or_create(
                BACKGROUND_KEY
            )
        except (BridgeError, RegistryError):
            logger.debug("No model resolves for suggestions yet — using fallback")
            return False
        self.client, self.acquired = client, True
        return True

    async def consume(self):
        client = self.client
        if client is None:
            raise RuntimeError("suggestion provider was not acquired")
        async for event in client.stream(self.prompt):
            kind = event.kind
            if kind == EVENT_COMPLETE:
                return self.text
            if kind == EVENT_TEXT_CHUNK:
                self.text += event.text
            elif kind == EVENT_PERMISSION_REQUEST:
                record = dict(
                    session_key="_bg",
                    tool_name=getattr(event, "title", "unknown"),
                    outcome="denied",
                    source="suggestions",
                )
                sel().log_tool_invocation(**record)
                await client.reject_tool(event.request_id)
        return self.text

    async def execute(self):
        if not await self.acquire():
            return None
        try:
            return await asyncio.wait_for(self.consume(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Suggestions generation timed out")
            return None
        finally:
            self.sessions.release(BACKGROUND_KEY)
            self.acquired = False


async def generate_suggestions(state: "ConsoleState") -> list[str]:
    context = _build_context(state)
    if not context or len(context) < 50:
        logger.debug("Insufficient context for suggestions — using fallback")
        return list(_FALLBACK_SUGGESTIONS)
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    context = context + "\n\n" + _time_context()
    prompt = render_use_case_prompt("suggestions", {"context": context})
    if not prompt:
        logger.debug("Suggestions prompt unresolved — using fallback")
        return list(_FALLBACK_SUGGESTIONS)
    response = await _SuggestionTurn(state.sessions, prompt).execute()
    if response is not None:
        suggestions = _parse_suggestions(response)
        if suggestions:
            result = _redact_suggestions(suggestions)
            logger.info("Generated %d suggestions", len(result))
            return result
    return list(_FALLBACK_SUGGESTIONS)


class _SuggestionsLifecycle:
    def __init__(self, state, cache):
        self.state, self.cache = state, cache

    async def regenerate(self):
        lock = self.cache._lock
        await lock.acquire()
        try:
            values = await generate_suggestions(self.state)
            self.cache.suggestions = values
            self.cache.generated_at = time.time()
        except Exception:
            logger.warning("Suggestions generation failed", exc_info=True)
        finally:
            lock.release()

    def enqueue(self):
        expired = not (time.time() - self.cache.generated_at < _REFRESH_INTERVAL_SECS)
        if expired and not self.cache._lock.locked():
            task = asyncio.create_task(refresh_suggestions(self.state, self.cache))
            self.cache._task = task
            pending = self.state._background_tasks
            pending.add(task)
            task.add_done_callback(pending.discard)

    async def await_refresh(self):
        await self.wait(refresh_suggestions(self.state, self.cache))

    @staticmethod
    async def wait(awaitable):
        try:
            await asyncio.wait_for(awaitable, timeout=45)
        except Exception:
            pass

    async def join_running(self):
        if self.cache._lock.locked() and self.cache._task is not None:
            await self.wait(asyncio.shield(self.cache._task))

    async def seed_if_needed(self):
        if self.cache.generated_at == 0 and not self.cache._lock.locked():
            await self.await_refresh()

    async def schedule(self):
        await maybe_refresh(self.state, self.cache)

    async def prepare(self, force):
        actions = (
            (self.await_refresh,)
            if force
            else (
                (self.schedule,)
                if self.cache.generated_at != 0
                else (self.join_running, self.seed_if_needed)
            )
        )
        for action in actions:
            await action()

    def response(self):
        stamp = self.cache.generated_at
        stale = (time.time() - stamp) > _REFRESH_INTERVAL_SECS if stamp else True
        return dict(suggestions=self.cache.suggestions, generated_at=stamp, stale=stale)


async def refresh_suggestions(state: "ConsoleState", cache: SuggestionsCache) -> None:
    await _SuggestionsLifecycle(state, cache).regenerate()


async def maybe_refresh(state: "ConsoleState", cache: SuggestionsCache) -> None:
    _SuggestionsLifecycle(state, cache).enqueue()


def get_suggestions_cache(state: "ConsoleState") -> SuggestionsCache:
    cache = getattr(state, "_suggestions_cache", None)
    if isinstance(cache, SuggestionsCache):
        return cache
    cache = SuggestionsCache()
    setattr(state, "_suggestions_cache", cache)
    return cache


async def api_suggestions(request: web.Request) -> web.Response:
    state: "ConsoleState" = request.app["state"]
    lifecycle = _SuggestionsLifecycle(state, get_suggestions_cache(state))
    await lifecycle.prepare(request.query.get("force") == "1")
    return web.json_response(lifecycle.response())
