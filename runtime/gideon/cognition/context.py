"""Compose ordered, attributable prompt sections from conversation state."""

import concurrent.futures
import io
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from gideon.automation.schedule import get_local_tz
from gideon.cognition.context_headroom import Component
from gideon.cognition.memory import MemoryJournal
from gideon.core.config.loader import AppConfig, memory_dir_for_cwd
from gideon.engine.agent import _shipped_prompt
from gideon.engine.hooks import (
    HOOK_INJECT_CONTEXT,
    HOOK_MODIFY,
    HookManager,
    HookResult,
    safe_read_file,
)
from gideon.extensions.skills import ProcedureLibrary
from gideon.security.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from gideon.cognition.history import ConversationLog
    from gideon.cognition.memory_slots import _SlotStore
    from gideon.engine.session import ConversationDirectory
    from gideon.extensions.skills.allocation import SkillDecision
    from gideon.integrations.channel_history import ChannelHistory

logger = logging.getLogger(__name__)
_memory_stores: dict[str, MemoryJournal] = {}
_MAX_CONTEXT_CHARS = 165_000
_HISTORY_BUDGET_CHARS = 35_000
_CROSS_TAB_BUDGET_CHARS = 6_000
_MEMORY_PREFS_CAP = 4_000
_MEMORY_PROJECTS_CAP = 6_000
_MEMORY_HISTORY_CAP = 25_000
_SEMANTIC_MEMORY_CAP = 12_000
_EPISODIC_MEMORY_CAP = 12_000
_PER_MESSAGE_CAP = 8_000
_BASELINE_WINDOW = 200_000
_MAX_BUDGET_MULTIPLE = 5.0
_COMPRESSED_HISTORY_CAP = 45_000
_COMPRESSION_MAX_MESSAGES = 100
_HEAD_TAIL_MESSAGES = 2
_STOP_EVENT_CAP = 3
_STOP_EVENT_RESOLVED_STATES = frozenset({"stopped", "stop_failed_reset"})
_MULTIBYTE_TABLE = str.maketrans(
    {
        "—": "--",
        "–": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "\u00a0": " ",
        "•": "-",
        "→": "->",
        "←": "<-",
        "↔": "<->",
        "⇒": "=>",
        "✓": "[x]",
        "✗": "[ ]",
        "×": "x",
    }
)
_MODE_IDENTITY_RE = re.compile(r"## 🔒 Mode Identity.*?(?=\n## |\Z)", re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_JSON_BLOB_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_RUNTIME_DISPLAY = {
    "dashboard": "Gideon dashboard",
    "cron": "Gideon cron job",
    "subagent": "Gideon subagent",
    "background": "Gideon background",
    "cli": "CLI terminal",
    "channel": "messaging channel",
}


def _path_home_gideon():
    from gideon.core.config.loader import config_dir

    return config_dir()


def _attach_vector_store(store: MemoryJournal, ws_path) -> None:
    try:
        from gideon.cognition.vector_memory import SemanticArchive
        from gideon.integrations.embedding_providers.registry import (
            get_active_embed_fn,
            get_active_embedding_dim,
        )

        embedding = get_active_embed_fn()
        if embedding is not None:
            archive = SemanticArchive(
                db_path=ws_path / "memory_index.db",
                embedding_dim=get_active_embedding_dim() or 384,
            )
            archive.init()
            archive.embed_fn = embedding
            archive.contradiction_judge = _make_contradiction_judge()
            store.vector_store = archive
    except Exception:
        logger.debug("Could not attach vector store for %s", ws_path, exc_info=True)


def _make_contradiction_judge():
    def compare(new_rule: str, existing_rule: str) -> bool:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            from gideon.integrations.prompt_providers.runtime import (
                render_use_case_prompt,
            )

            prompt = render_use_case_prompt(
                "contradiction_judge",
                {
                    "new_rule": new_rule,
                    "existing_rule": existing_rule,
                },
            )
            if prompt:
                try:
                    from gideon.integrations.llm_helpers import one_shot_completion

                    reply = asyncio.run(
                        one_shot_completion(prompt, use_case="background")
                    )
                    return "CONTRADICT" in reply.upper()
                except Exception:
                    logger.debug("Contradiction check unavailable", exc_info=True)
        return False

    return compare


def _self_model_snapshot(svc) -> str:
    if svc is not None and getattr(svc, "has_vector", False):
        try:
            from gideon.cognition.learning.self_model import snapshot
            from gideon.cognition.learning.self_model_observer import load_live_entries

            entries = load_live_entries(svc)
            return snapshot(entries)
        except Exception:
            logger.debug("self-model snapshot failed", exc_info=True)
    return ""


def _record_ambient_measurements(alloc, *, sweep_args: dict) -> None:
    try:
        from gideon.cognition.learning.staging import get_store

        records = get_store()
        records.record_allocation(
            used_tokens=alloc.used_tokens, budget_tokens=alloc.budget_tokens
        )
        if records.ablation_due():
            from gideon.cognition.learning import ambient
            from gideon.cognition.learning.surfacing import ablation_deltas

            budget = int(sweep_args.pop("budget_tokens", 0) or 0)
            query = str(sweep_args.pop("query", "") or "")
            candidates = ambient.sources_for(**sweep_args)
            if candidates and budget > 0:
                records.record_ablation(
                    ablation_deltas(candidates, query=query, budget_tokens=budget)
                )
    except Exception:
        logger.debug("ambient measurement recording failed", exc_info=True)


def _render_ambient(
    *,
    lessons: str = "",
    skill_index: str = "",
    voice: str = "",
    persona: str = "",
    self_model: str = "",
    procedural: str = "",
    query: str = "",
) -> str:
    sources = dict(
        lessons=lessons,
        skill_index=skill_index,
        voice=voice,
        persona=persona,
        self_model=self_model,
        procedural=procedural,
    )
    if not any(sources.values()):
        return ""
    try:
        from gideon.cognition.learning import ambient
        from gideon.integrations.model_windows import active_chat_model_window

        budget = int(
            getattr(AppConfig.load().learning, "context_budget_tokens", 4000) or 4000
        )
        allocation = ambient.render(
            **sources,
            query=query,
            budget_tokens=budget,
            window=active_chat_model_window(),
        )
        framed = ambient.frame(allocation, lessons_block=lessons)
        if framed:
            logger.debug("ambient allocation: %s", ambient.report(allocation))
        _record_ambient_measurements(
            allocation,
            sweep_args={
                **sources,
                "query": query,
                "budget_tokens": allocation.budget_tokens,
            },
        )
        return framed
    except Exception:
        logger.debug(
            "ambient allocation failed; falling back to lessons", exc_info=True
        )
        return lessons or ""


class _MemoryCaps(TypedDict):
    prefs_cap: int
    projects_cap: int
    history_cap: int
    semantic_cap: int
    episodic_cap: int


def _memory_block_timeout_secs() -> float:
    try:
        configured = getattr(AppConfig.load().memory, "active_recall_timeout_ms", 1500)
        return float(configured) / 1000.0
    except Exception:
        return 1.5


def _guarded_recall(label: str, fn, *, timeout_secs: float | None = None):
    executor = None
    try:
        if timeout_secs is None:
            return fn()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pending = executor.submit(fn)
        return pending.result(timeout=timeout_secs)
    except Exception:
        logger.warning(
            "memory block %r unavailable — continuing without it", label, exc_info=True
        )
        return None
    finally:
        # A timed-out read may finish later; joining it would invalidate the deadline.
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def _memory_caps(context_window: int | None) -> _MemoryCaps:
    factor = min(
        _MAX_BUDGET_MULTIPLE,
        max(1.0, (context_window or _BASELINE_WINDOW) / _BASELINE_WINDOW),
    )
    return _MemoryCaps(
        prefs_cap=int(_MEMORY_PREFS_CAP * factor),
        projects_cap=int(_MEMORY_PROJECTS_CAP * factor),
        history_cap=int(_MEMORY_HISTORY_CAP * factor),
        semantic_cap=int(_SEMANTIC_MEMORY_CAP * factor),
        episodic_cap=int(_EPISODIC_MEMORY_CAP * factor),
    )


def _stop_record(message: dict) -> dict | None:
    if message.get("role") != "system":
        return None
    try:
        payload = json.loads(message.get("content", ""))
    except (TypeError, ValueError):
        return None
    return (
        payload
        if isinstance(payload, dict) and payload.get("kind") == "stop_event"
        else None
    )


def _build_stop_event_notes(
    conversation_log: "ConversationLog", session_key: str
) -> str:
    messages = conversation_log.recent(session_key, max_messages=20)
    count = 0
    for message in reversed(messages):
        record = _stop_record(message)
        if record is not None and record.get("state") in _STOP_EVENT_RESOLVED_STATES:
            count += 1
            if count == _STOP_EVENT_CAP:
                break
    note = "[User stopped the previous turn mid-execution.]"
    return "\n".join([note] * count) + "\n\n" if count else ""


def _runtime_display_name(session_key: str) -> str:
    exact = {"_bg": "background", "cli_chat": "cli"}
    source = exact.get(session_key)
    if source is None:
        source = next(
            (
                name
                for name, prefixes in (
                    ("dashboard", ("dashboard:", "dashboard_")),
                    ("cron", ("cron:", "cron_")),
                    ("subagent", ("subagent:",)),
                )
                if session_key.startswith(prefixes)
            ),
            "channel",
        )
    return _RUNTIME_DISPLAY[source]


def _prompt_use_case_for(session_key: str | None, explicit: str = "") -> str:
    if explicit and explicit != "chat":
        return explicit
    key = session_key or ""
    routes = (
        (("code:", "code_"), "code"),
        (("loop:", "loop_", "campaign"), "goal_loop"),
        (("cron:", "cron_", "subagent:", "workflow:"), "background"),
    )
    for prefixes, destination in routes:
        if key.startswith(prefixes):
            return destination
    return "background" if key == "_bg" else explicit or "chat"


def _snippet_resolver():
    provider = None
    try:
        from gideon.integrations.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
    except Exception:
        pass

    def resolve(name):
        return None if provider is None else provider.get_snippet(name)

    return resolve


def _redact_context(text: str) -> str:
    for redact in (redact_exfiltration_urls, redact_credentials):
        text, _ = redact(text)
    return text


def _compress_assistant_message(text: str) -> str:
    def excerpt(match: re.Match[str]) -> str:
        body = match.group(1)
        if len(body) <= 2000:
            return match.group(0)
        lines = body.strip().splitlines()
        if len(lines) <= 15:
            selected = body[:2000].splitlines()
            selected.append(f"  ... ({len(body) - 2000} chars truncated)")
        else:
            selected = [
                *lines[:10],
                f"  ... ({len(lines) - 15} lines omitted)",
                *lines[-5:],
            ]
        fence = match.group(0).partition("\n")[0]
        return "\n".join((fence, *selected, "```"))

    code = _CODE_BLOCK_RE.sub(excerpt, text)
    compacted = _JSON_BLOB_RE.sub(
        lambda match: "[tool output truncated]" if len(match[0]) > 1000 else match[0],
        code,
    )
    return re.sub(r"\n{3,}", "\n\n", compacted)


def build_cancelled_turn_preamble(
    conversation_log: "ConversationLog",
    session_key: str,
    *,
    user_cap: int = 2000,
    assist_cap: int = 2000,
) -> str:
    try:
        recent = conversation_log.recent(session_key, max_messages=20)
    except Exception:
        return ""
    boundary = next(
        (
            i
            for i in range(len(recent) - 1, -1, -1)
            if _stop_record(recent[i]) is not None
        ),
        len(recent),
    )
    user_index = next(
        (i for i in range(boundary - 1, -1, -1) if recent[i].get("role") == "user"),
        None,
    )
    if user_index is None:
        return ""
    fragments = [
        (message.get("content") or "").strip()
        for message in recent[user_index + 1 : boundary]
        if message.get("role") == "assistant"
    ]
    values = {
        "user_text": (recent[user_index].get("content") or "").strip(),
        "assistant_text": "\n".join(fragment for fragment in fragments if fragment),
    }
    for name, cap in (("user_text", user_cap), ("assistant_text", assist_cap)):
        if len(values[name]) > cap:
            values[name] = values[name][:cap] + "… [truncated]"
    from gideon.integrations.prompt_providers.runtime import render_snippet_block

    return render_snippet_block("cancelled-turn-preamble", values)


def has_restorable_history(
    conversation_log: "ConversationLog | None", session_key: str
) -> bool:
    try:
        return conversation_log is not None and bool(
            conversation_log.recent(
                session_key,
                max_messages=_COMPRESSION_MAX_MESSAGES,
                roles={"user", "assistant"},
            )
        )
    except Exception:
        logger.debug(
            "conversation_log.recent failed for %s", session_key, exc_info=True
        )
        return False


async def compress_thread_history(
    conversation_log: "ConversationLog",
    session_key: str,
    query: str,
    sessions: "ConversationDirectory",
) -> str | None:
    from gideon.engine.session import BACKGROUND_KEY
    from gideon.integrations.llm_helpers import stream_and_collect

    rows = conversation_log.recent(
        session_key, max_messages=_COMPRESSION_MAX_MESSAGES, roles={"user", "assistant"}
    )
    lines = [f"{row['role'].title()}: {row['content']}" for row in rows]
    if not lines:
        return None
    transcript = "\n".join(lines)
    if len(transcript) <= _COMPRESSED_HISTORY_CAP:
        return _redact_context(transcript).translate(_MULTIBYTE_TABLE)
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt(
        "history_compression",
        {
            "cap": _COMPRESSED_HISTORY_CAP,
            "query": query,
            "transcript": transcript,
        },
    )
    if not prompt:
        return None
    leased = False
    try:
        provider, _, _ = await sessions.get_or_create(
            BACKGROUND_KEY, agent="gideon-lite"
        )
        leased = True
        summary = await stream_and_collect(provider, prompt)
        if not summary:
            return None
        blocks = [
            "## Thread start (verbatim)\n" + "\n".join(lines[:_HEAD_TAIL_MESSAGES]),
            "## Compressed history\n" + summary[:_COMPRESSED_HISTORY_CAP],
        ]
        if len(lines) > _HEAD_TAIL_MESSAGES:
            blocks.append(
                "## Recent exchanges (verbatim)\n"
                + "\n".join(lines[-_HEAD_TAIL_MESSAGES:])
            )
        rendered = _redact_context("\n\n".join(blocks))
        from gideon.automation.triggers.lifecycle_fire import (
            context_compact_payload,
            fire,
        )

        await fire(
            context_compact_payload(
                session_key=session_key,
                before_chars=len(transcript),
                after_chars=len(rendered),
            )
        )
        return rendered.translate(_MULTIBYTE_TABLE)
    except Exception:
        logger.warning("Thread history compression failed", exc_info=True)
        return None
    finally:
        if leased:
            sessions.release(BACKGROUND_KEY)
            await sessions.recycle_background()


class _Parts:
    """Keep display attribution and sendable bytes on the same ordered stream."""

    def __init__(self) -> None:
        self._items: list[Component] = []
        self._joined = io.StringIO()

    def add(
        self, text: str, *, name: str, compressible: bool = True, content_type: str = ""
    ) -> None:
        if not text:
            return
        self._items.append(
            Component(
                name=name,
                text=text,
                compressible=compressible,
                content_type=content_type,
            )
        )
        self._joined.write(text)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return len(self) != 0

    def text(self) -> str:
        return self._joined.getvalue()

    def components(self) -> list[Component]:
        return self._items.copy()


@dataclass
class _MemorySections:
    direct: list[str] = field(default_factory=list)
    ambient: dict[str, str] = field(
        default_factory=lambda: dict(
            persona="",
            voice="",
            self_model="",
            procedural="",
            skill_index="",
            lessons="",
        )
    )


class PromptAssembler:
    """Apply startup and per-turn composition stages in a stable order."""

    @staticmethod
    def get_memory_for(cwd: str | None = None, memory_store: str | None = None):
        if memory_store:
            from gideon.integrations.memory_providers.registry import get_provider

            named = get_provider(memory_store)
            if named is not None:
                return named
            if memory_store not in AppConfig.load().memory_stores:
                logger.warning(
                    "memory_store %r not registered; using filesystem fallback",
                    memory_store,
                )
        directory = memory_dir_for_cwd(cwd)
        key = str(directory)
        cached = _memory_stores.get(key)
        if cached is None:
            cached = MemoryJournal(workspace=directory)
            cached.init()
            _attach_vector_store(cached, directory)
            _memory_stores[key] = cached
        return cached

    def __init__(
        self,
        memory: MemoryJournal | None = None,
        skills: ProcedureLibrary | None = None,
        hooks: HookManager | None = None,
        conversation_log: "ConversationLog | None" = None,
        channel_history: "ChannelHistory | None" = None,
        bot_name: str = "",
    ):
        self.memory = memory or MemoryJournal(workspace=memory_dir_for_cwd(None))
        self.skills = skills or ProcedureLibrary()
        self.hooks = hooks or HookManager()
        self.conversation_log, self.channel_history = conversation_log, channel_history
        self._bot_name_override = bot_name
        _memory_stores[str(memory_dir_for_cwd(None))] = self.memory
        try:
            from gideon.core.config.loader import default_workspace_dir

            _memory_stores.setdefault(
                str(memory_dir_for_cwd(default_workspace_dir())), self.memory
            )
        except Exception:
            logger.debug(
                "Could not register memory under the workspace key", exc_info=True
            )

    @property
    def _bot_name(self) -> str:
        if self._bot_name_override:
            return self._bot_name_override
        try:
            name = AppConfig.load().agent.bot_name
            return name or "Gideon"
        except Exception:
            return "Gideon"

    def _apply_runtime_vars(self, prompt: str, session_key: str) -> str:
        from gideon.integrations.prompt_providers.base import (
            PromptTemplate,
            PromptVariable,
        )
        from gideon.integrations.prompt_providers.engine import render_template

        values = dict(
            bot_name=self._bot_name, widget_block=self._widget_block(session_key)
        )
        template = PromptTemplate(
            name="_runtime",
            content=prompt,
            variables=list(map(lambda name: PromptVariable(name=name), values)),
        )
        return render_template(template, values, resolver=_snippet_resolver())

    @staticmethod
    def _widget_block(session_key: str) -> str:
        if not session_key or not session_key.startswith(("dashboard:", "dashboard_")):
            return ""
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        density = getattr(AppConfig.load().dashboard, "widget_density", "more")
        return render_snippet_block("widget-instructions", {"density": density})

    @staticmethod
    def _load_agent_prompt(agent: str) -> str:
        for path in (_path_home_gideon() / "agents").glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("name") != agent and path.stem != agent:
                    continue
                prompt = record.get("prompt") or ""
                if not prompt.startswith("file://"):
                    return prompt
                try:
                    return safe_read_file(prompt.removeprefix("file://"))
                except (OSError, PermissionError):
                    return ""
            except (json.JSONDecodeError, OSError):
                continue
        return ""

    def _slots_block(self, vector_store: "_SlotStore | None") -> str:
        if vector_store is not None:
            try:
                from gideon.cognition import memory_slots

                excess = memory_slots.over_cap(vector_store)
                if excess:
                    logger.warning(
                        "memory slots over cap (hand-edited rows?); block will truncate: %s",
                        excess,
                    )
                limit = memory_slots.resolve_block_limit(
                    getattr(AppConfig.load().memory, "slot_size_cap", None)
                )
                return memory_slots.render_slots_block(vector_store, limit=limit)
            except Exception:
                logger.debug("slots block render failed", exc_info=True)
        return ""

    @staticmethod
    def _conversation_lines(
        messages,
        *,
        budget: int,
        recent_first: bool = False,
        compress_assistant: bool = False,
    ) -> list[str]:
        selected = []
        remaining = budget
        sequence = reversed(messages) if recent_first else iter(messages)
        for message in sequence:
            content = _MODE_IDENTITY_RE.sub("", message["content"])
            if compress_assistant and message["role"] == "assistant":
                content = _compress_assistant_message(content)
            if len(content) > _PER_MESSAGE_CAP:
                content = content[:_PER_MESSAGE_CAP] + "…[truncated]"
            line = f"{message['role'].title()}: {content}"
            if len(line) > remaining:
                break
            selected.append(line)
            remaining -= len(line)
        return selected[::-1] if recent_first else selected

    def _thread_block(self, key: str, resumed: bool, compressed: str | None) -> str:
        if not key or self.conversation_log is None or resumed:
            return ""
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        header = render_snippet_block("thread-history-header") + "\n"
        if compressed:
            text = _MODE_IDENTITY_RE.sub("", _redact_context(compressed))
        else:
            recent = self.conversation_log.recent(key, roles={"user", "assistant"})
            lines = self._conversation_lines(
                recent,
                budget=_HISTORY_BUDGET_CHARS,
                recent_first=True,
                compress_assistant=True,
            )
            if not lines:
                return ""
            text = _redact_context("\n".join(lines))
        return header + text + "\n[End of thread history]\n\n"

    def _standing_memory(
        self, memory, session_key: str | None, agent: str | None
    ) -> _MemorySections:
        from gideon.cognition.memory_service import service_for
        from gideon.engine.agents.defaults import normalize_agent_name
        from gideon.integrations.model_windows import active_chat_model_window

        service = service_for(memory)
        sections = _MemorySections()
        caps = _memory_caps(active_chat_model_window())
        recall = _guarded_recall(
            "recall",
            lambda: service.get_context(**caps),
            timeout_secs=_memory_block_timeout_secs(),
        )
        if recall:
            sections.direct.append(recall)
        if session_key:
            working = _guarded_recall(
                "working_memory", lambda: service.working_memory(session_key)
            )
            if working:
                sections.direct.append(working)
        sections.ambient["persona"] = (
            _guarded_recall(
                "persona",
                lambda: service.persona_block(agent=normalize_agent_name(agent)),
            )
            or ""
        )
        slots = _guarded_recall(
            "slots", lambda: self._slots_block(getattr(service, "_vs", None))
        )
        if slots:
            sections.direct.append(slots + "\n")
        try:
            from gideon.cognition.preference_facets import render_profile_block

            vector = getattr(service, "_vs", None)
            if vector is not None:
                sections.ambient["voice"] = render_profile_block(vector) or ""
        except Exception:
            logger.debug("preference profile block render failed", exc_info=True)
        if getattr(AppConfig.load().learning, "self_model_enabled", True):
            sections.ambient["self_model"] = _self_model_snapshot(service)
        try:
            sections.ambient["procedural"] = service.procedural_block()
        except Exception:
            logger.debug("procedural prior block render failed", exc_info=True)
        return sections

    def _neighbor_blocks(self, key: str) -> list[str]:
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        sections: list = []
        if self.conversation_log is None:
            return sections
        if key.startswith("dashboard:"):
            others = self.conversation_log.recent_from_source(
                "dashboard:", exclude_key=key, max_messages=10
            )
            lines = self._conversation_lines(others, budget=_CROSS_TAB_BUDGET_CHARS)
            if lines:
                sections.append(
                    render_snippet_block(
                        "cross-tab-context", {"cross_lines": "\n".join(lines)}
                    )
                    + "\n\n"
                )
        origins = self.conversation_log.recent_with_provenance(key)
        if origins:
            lines = [
                f"- [thread {row['source_thread']}, {row['ts'][:16]}] {row['snippet']}"
                for row in origins
            ]
            sections.append("## Recent Session Context\n" + "\n".join(lines) + "\n\n")
        return sections

    def build_session_context(
        self,
        session_key: str | None = None,
        agent: str | None = None,
        resumed: bool = False,
        cwd: str | None = None,
        memory_store: str | None = None,
        compressed_history: str | None = None,
        mode: str = "",
        blocks_reads: bool = False,
        dropped_out: list[str] | None = None,
    ) -> str:
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        custom = bool(agent and agent != "gideon")
        sections = [render_snippet_block("critical-rules") + "\n\n"]
        captured = datetime.now(get_local_tz()[1])
        if session_key:
            sections.append(
                render_snippet_block(
                    "agent-runtime-identity",
                    {
                        "agent_label": agent or "gideon",
                        "runtime": _runtime_display_name(session_key),
                    },
                )
                + "\n\n"
            )
        if not custom:
            sections.append(
                render_snippet_block("workspace-identity", {"ws_path": cwd or "(none)"})
                + "\n\n"
            )
        sections.append(
            self._thread_block(session_key or "", resumed, compressed_history)
        )
        if session_key and self.conversation_log:
            sections.append(_build_stop_event_notes(self.conversation_log, session_key))
        memory = self.get_memory_for(cwd, memory_store)
        standing = (
            _MemorySections()
            if blocks_reads
            else self._standing_memory(memory, session_key, agent)
        )
        sections.extend(standing.direct)
        if not custom:
            standing.ambient["skill_index"] = self.skills.get_context(agent=agent) or ""
        if session_key:
            try:
                from gideon.extensions.skills.ephemeral import context_block

                ephemeral = context_block(session_key)
                if ephemeral:
                    sections.append(ephemeral)
            except Exception:
                logger.debug("ephemeral skills context failed", exc_info=True)
        if not blocks_reads:
            from gideon.cognition.memory_service import service_for

            standing.ambient["lessons"] = (
                _guarded_recall(
                    "lessons", lambda: service_for(memory).lessons_context(cwd)
                )
                or ""
            )
        ambient = _render_ambient(**standing.ambient)
        if ambient:
            sections.append(ambient)
        if session_key and not blocks_reads:
            sections.extend(self._neighbor_blocks(session_key))
        assembled = "".join(sections)
        size = len(assembled)
        if size > _MAX_CONTEXT_CHARS:
            logger.warning(
                "Session context too large (%d chars), truncating to %d",
                size,
                _MAX_CONTEXT_CHARS,
            )
            if dropped_out is not None:
                dropped_out.append(
                    f"Session context was over the assembly cap, so {size - _MAX_CONTEXT_CHARS:,} "
                    f"characters of the oldest history were dropped ({size:,} → {_MAX_CONTEXT_CHARS:,})."
                )
            prefix = assembled[:_MAX_CONTEXT_CHARS]
            newline = prefix.rfind("\n")
            assembled = prefix[: newline + 1] if newline > 0 else prefix
        return (
            assembled
            + f"[CURRENT DATE] {captured.strftime('%A, %Y-%m-%d %H:%M %Z')}\n\n"
        )

    def _identity_prompt(
        self,
        agent: str | None,
        key: str | None,
        use_case: str,
        override: str,
        suffix: str,
    ) -> str:
        rendered = False
        if override.strip():
            prompt = override
        elif agent and agent != "gideon":
            prompt = self._load_agent_prompt(agent)
        else:
            from gideon.integrations.prompt_providers.runtime import (
                render_use_case_prompt,
            )

            prompt = (
                render_use_case_prompt(
                    _prompt_use_case_for(key, use_case),
                    {
                        "bot_name": self._bot_name,
                        "widget_block": self._widget_block(key or ""),
                    },
                )
                or ""
            )
            rendered = bool(prompt)
            if not prompt:
                try:
                    prompt = _shipped_prompt().read_text(encoding="utf-8")
                except OSError:
                    prompt = ""
        if suffix.strip():
            prompt = "\n\n".join(filter(None, (prompt, suffix)))
        if not prompt:
            return ""
        if not rendered or suffix.strip():
            return self._apply_runtime_vars(prompt, key or "")
        return prompt

    def _startup_parts(
        self,
        parts: _Parts,
        *,
        key: str | None,
        agent: str | None,
        resumed: bool,
        cwd: str | None,
        memory_store: str | None,
        compressed_history: str | None,
        mode: str,
        blocks_reads: bool,
        use_case: str,
        override: str,
        suffix: str,
        notices: list[str] | None,
    ) -> None:
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        prompt = self._identity_prompt(agent, key, use_case, override, suffix)
        if prompt:
            parts.add(
                render_snippet_block(
                    "agent-system-prompt-wrapper", {"agent_prompt": prompt}
                )
                + "\n\n",
                name="system prompt",
                compressible=False,
            )
        context = self.build_session_context(
            key,
            agent=agent,
            resumed=resumed,
            cwd=cwd,
            memory_store=memory_store,
            compressed_history=compressed_history,
            mode=mode,
            blocks_reads=blocks_reads,
            dropped_out=notices,
        )
        if context:
            parts.add(
                render_snippet_block(
                    "session-context-wrapper", {"session_context": context}
                )
                + "\n\n",
                name="session context (memory · lessons · history)",
                compressible=False,
            )
        if resumed and key:
            from gideon.cognition.resume_account import (
                NOT_CONSULTED,
                resume_account_block,
            )

            account = resume_account_block(
                session_key=key, tool_messages=NOT_CONSULTED, tree_root=cwd
            )
            if account:
                parts.add(
                    account + "\n\n",
                    name="record of already-completed work",
                    compressible=False,
                )

    def _channel_parts(
        self,
        parts: _Parts,
        channel_id: str | None,
        thread_ts: str | None,
        parent: str | None,
    ) -> None:
        if channel_id and self.channel_history:
            context = self.channel_history.context_for(channel_id, thread_ts=thread_ts)
            if context:
                parts.add(context, name="channel history", content_type="log")
        if channel_id and thread_ts:
            from gideon.integrations.prompt_providers.runtime import (
                render_snippet_block,
            )

            context = render_snippet_block(
                "channel-thread-context",
                dict(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    thread_parent_text=parent or "",
                ),
            )
            parts.add(context + "\n\n", name="thread context")

    def _episodic_parts(
        self,
        parts: _Parts,
        text: str,
        cwd: str | None,
        memory_store: str | None,
        citations: list[dict] | None,
    ) -> None:
        from gideon.cognition.memory_service import service_for

        memory = self.get_memory_for(cwd, memory_store)
        recalled = service_for(memory).episodic_context(
            query_text=text, cap=3000, citations_out=citations
        )
        if not recalled:
            return
        parts.add(recalled + "\n", name="episodic memory")
        if citations:
            parts.add(
                "[Memory citation] When you use a fact from the episodic memory above, cite it inline as "
                "`[Memory N]` using its number, so the user can trace it to the source. Only cite a number "
                "that appears above; never invent one.\n",
                name="memory citation instructions",
                compressible=False,
            )
        parts.add(
            "[Answer only from memory] If the memory above does not contain what the user asked about, "
            "say you don't have it in memory rather than guessing — never present an un-recalled fact "
            "as remembered.\n",
            name="memory grounding instructions",
            compressible=False,
        )

    def _skill_parts(
        self,
        parts: _Parts,
        text: str,
        key: str | None,
        custom: bool,
        forced: list[str] | None,
        notices: list[str] | None,
        decisions: list["SkillDecision"] | None,
    ) -> None:
        from gideon.extensions.skills.allocation import (
            FORCED_SCORE,
            SkillRequest,
            allocate_skills,
        )

        requests = []
        loaded_forced = set()
        for name in forced or ():
            body = self.skills.load_skill(name)
            if body:
                requests.append(
                    SkillRequest(
                        name=name, content=body, score=FORCED_SCORE, forced=True
                    )
                )
                loaded_forced.add(name)
        if not custom:
            matches = [
                name
                for name in self.skills.get_surfaced_skills(text)
                if name not in loaded_forced
            ]
            try:
                threshold = AppConfig.load().skills.progressive_disclosure_threshold
            except Exception:
                threshold = 2
            if threshold and len(matches) > threshold:
                descriptions = {row["key"]: row for row in self.skills.list_skills()}
                lines = [
                    "[Relevant skills — INDEX only. Call skill_invoke{name} to load a skill's full "
                    "steps before using it. These are the matches for this turn; call skill_search(query) "
                    "to find others in the full library.]"
                ]
                lines.extend(
                    f"- {name}: {descriptions.get(name, {}).get('description') or name}"
                    for name in matches
                )
                lines.append("[End of skill index]")
                parts.add("\n".join(lines) + "\n\n", name="skill index")
            else:
                for name in matches:
                    body = self.skills.load_skill(name)
                    if body:
                        requests.append(SkillRequest(name=name, content=body))
        if not requests:
            return
        allocation = allocate_skills(
            self.skills, requests, query=text, session=key or ""
        )
        for name, block in allocation.blocks:
            parts.add(block, name=f"skill: {name}")
        if notices is not None:
            notices.extend(allocation.notices)
        if decisions is not None:
            decisions.extend(allocation.decisions)
        if allocation.loaded:
            try:
                from gideon.extensions.skills.usage import SkillUsageStore

                SkillUsageStore().record_uses(allocation.loaded)
            except Exception:
                logger.debug("skill usage record skipped (error)", exc_info=True)

    def build_message(
        self,
        text: str,
        is_new_session: bool,
        session_key: str | None = None,
        channel_id: str | None = None,
        agent: str | None = None,
        resumed: bool = False,
        thread_ts: str | None = None,
        cwd: str | None = None,
        memory_store: str | None = None,
        user_display_name: str | None = None,
        compressed_history: str | None = None,
        mode: str = "",
        prompt_use_case: str = "chat",
        blocks_reads: bool = False,
        action_context: str | None = None,
        thread_parent_text: str | None = None,
        system_prompt_override: str = "",
        system_prompt_suffix: str = "",
        resolved_agent_id: str = "",
        force_skill_ids: list[str] | None = None,
        force_workflow_ids: list[str] | None = None,
        citations_out: list[dict] | None = None,
        components_out: list[Component] | None = None,
        notices_out: list[str] | None = None,
        skill_decisions_out: list["SkillDecision"] | None = None,
    ) -> tuple[str, HookResult]:
        result = self.hooks.on_message(text)
        parts = _Parts()
        if is_new_session:
            self._startup_parts(
                parts,
                key=session_key,
                agent=agent,
                resumed=resumed,
                cwd=cwd,
                memory_store=memory_store,
                compressed_history=compressed_history,
                mode=mode,
                blocks_reads=blocks_reads,
                use_case=prompt_use_case,
                override=system_prompt_override,
                suffix=system_prompt_suffix,
                notices=notices_out,
            )
        self._channel_parts(parts, channel_id, thread_ts, thread_parent_text)
        if is_new_session and not blocks_reads:
            self._episodic_parts(parts, text, cwd, memory_store, citations_out)
        try:
            from gideon.automation.workflows.context_block import active_workflows_block

            workflows = active_workflows_block()
            if workflows:
                parts.add(workflows, name="active workflows")
        except Exception:
            logger.debug("active-workflows block skipped", exc_info=True)
        self._skill_parts(
            parts,
            text,
            session_key,
            bool(agent and agent != "gideon"),
            force_skill_ids,
            notices_out,
            skill_decisions_out,
        )
        if result.action == HOOK_INJECT_CONTEXT:
            parts.add(
                f"[Hook context:]\n{result.text}\n[End of hook context]\n\n",
                name="hook context",
            )
        if action_context:
            parts.add(
                action_context + "\n\n",
                name="action button context",
                compressible=False,
            )
        if parts:
            if user_display_name:
                parts.add(
                    f"[CURRENT USER] {user_display_name}\n",
                    name="current user",
                    compressible=False,
                )
            parts.add(
                "[CURRENT USER REQUEST — respond to this]\n",
                name="request header",
                compressible=False,
            )
        parts.add(
            result.text if result.action == HOOK_MODIFY else text,
            name="the user's request",
            compressible=False,
        )
        if session_key and session_key.startswith(("dashboard:", "dashboard_")):
            parts.add(
                "\n\n[WIDGETS] You can render rich HTML inline using "
                '<widget title="Title">HTML</widget> tags. Tailwind CSS is available. '
                "The widget iframe inherits the dashboard's active theme: use "
                "var(--bg), var(--text), var(--card), var(--border), var(--accent), "
                "var(--muted), var(--ok), var(--warn), var(--danger) (or Tailwind "
                "arbitrary values like bg-[var(--card)]) instead of hardcoded colors "
                "so widgets look right on every theme. "
                "Use when the response benefits from styled visual content that markdown "
                "cannot express well (e.g. charts with Chart.js, styled cards, color-coded "
                "tables, or visual previews). "
                "Keep widgets concise. For larger HTML, save to a file and return "
                "the path -- you can iterate on it in future turns.",
                name="widget instructions",
                compressible=False,
            )
        normalized = [
            replace(item, text=item.text.translate(_MULTIBYTE_TABLE))
            for item in parts.components()
        ]
        if components_out is not None:
            components_out.extend(normalized)
        return "".join(item.text for item in normalized), result
