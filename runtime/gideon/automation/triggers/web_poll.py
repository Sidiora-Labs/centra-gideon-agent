from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.automation.triggers.provider import armable

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECS = 60.0
MIN_POLL_INTERVAL_SECS = 300.0
MAX_REQUESTS_PER_DAY = 288
MAX_HEADLESS_REQUESTS_PER_DAY = 24
MAX_SEEN_KEYS = 500
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ITEM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"<(?:guid|id)[^>]*>\s*([^<\s][^<]*?)\s*</(?:guid|id)>", re.I),
    re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"'#\s][^\"']*)[\"']", re.I),
)


@dataclass
class WatchState:
    seeded: bool = False
    seen: list[str] = field(default_factory=list)
    day: str = ""
    requests_today: int = 0
    headless_today: int = 0
    last_polled_at: float = 0.0
    last_status: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self), seen=list(self.seen))

    @classmethod
    def from_dict(cls, raw: Any) -> WatchState:
        if not isinstance(raw, dict):
            return cls()
        seen = raw.get("seen")
        numbers = {
            key: int(raw.get(key, 0) or 0)
            for key in ("requests_today", "headless_today", "last_status")
        }
        return cls(
            seeded=bool(raw.get("seeded", False)),
            seen=(
                list(filter(lambda value: isinstance(value, str), seen))
                if isinstance(seen, list)
                else []
            ),
            day=str(raw.get("day", "") or ""),
            last_polled_at=float(raw.get("last_polled_at", 0.0) or 0.0),
            **numbers,
        )

    def charge(self, now: float, status: int) -> None:
        day = _day_of(now)
        if self.day != day:
            self.day, self.requests_today, self.headless_today = day, 0, 0
        self.requests_today += 1
        self.last_polled_at, self.last_status = now, status

    def remember(self, items: list[str]) -> list[str]:
        known = set(self.seen)
        keyed = [(_digest(item), item) for item in items]
        current = {key for key, _ in keyed}
        self.seen = (
            [key for key, _ in keyed] + [key for key in self.seen if key not in current]
        )[:MAX_SEEN_KEYS]
        return [item for key, item in keyed if key not in known]


def _state_dir(base_dir: Path | str | None) -> Path:
    from gideon.core.config.loader import config_dir

    return (Path(base_dir) if base_dir else config_dir()).joinpath("trigger-web-watch")


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    return _state_dir(base_dir).joinpath(
        (_SAFE_RE.sub("-", trigger_id) or "watch") + ".json"
    )


@dataclass(frozen=True)
class WebWatchJournal:
    trigger_id: str
    base_dir: Path | str | None

    def read(self) -> WatchState:
        try:
            document = _state_path(self.trigger_id, self.base_dir).read_text(
                encoding="utf-8"
            )
            return WatchState.from_dict(json.loads(document))
        except (OSError, json.JSONDecodeError):
            return WatchState()

    def write(self, state: WatchState) -> None:
        from gideon.core.atomic_write import atomic_write

        destination = _state_path(self.trigger_id, self.base_dir)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            document = json.dumps(state.to_dict(), indent=2, sort_keys=True)
            atomic_write(destination, document + "\n")
        except OSError:
            logger.debug(
                "web_poll: could not persist watch state for %s",
                self.trigger_id,
                exc_info=True,
            )


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> WatchState:
    return WebWatchJournal(trigger_id, base_dir).read()


def save_state(
    trigger_id: str, state: WatchState, *, base_dir: Path | str | None = None
) -> None:
    WebWatchJournal(trigger_id, base_dir).write(state)


def _spec(trigger: Any) -> dict[str, Any]:
    return trigger.spec if isinstance(trigger.spec, dict) else {}


def web_watch_triggers(store: Any) -> list[Any]:
    return [
        trigger
        for trigger in armable(store)
        if trigger.kind == "web_watch"
        and trigger.enabled
        and str(_spec(trigger).get("url", "") or "").strip()
    ]


def poll_interval_for(trigger: Any) -> float:
    try:
        requested = float(_spec(trigger).get("poll_interval", 0) or 0)
    except (TypeError, ValueError):
        requested = 0.0
    return max(requested or MIN_POLL_INTERVAL_SECS, MIN_POLL_INTERVAL_SECS)


@dataclass(frozen=True)
class ItemExtractor:
    novelty_key: str

    def patterns(self) -> tuple[re.Pattern[str], ...]:
        if self.novelty_key and self.novelty_key != "auto":
            try:
                return (re.compile(self.novelty_key, re.I),)
            except re.error:
                logger.debug(
                    "web_poll: invalid novelty_key %r; using auto", self.novelty_key
                )
        return _ITEM_RES

    def extract(self, body: str) -> list[str]:
        for pattern in self.patterns():
            keys = (
                (match.group(1) if match.groups() else match.group(0)).strip()
                for match in pattern.finditer(body)
            )
            ordered = dict.fromkeys(key for key in keys if key)
            if ordered:
                return list(ordered)
        return []


def extract_items(body: str, *, novelty_key: str = "") -> list[str]:
    return ItemExtractor(novelty_key).extract(body) if body else []


def _digest(key: str) -> str:
    encoded = key.encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _day_of(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _budget(state: WatchState, now: float, limit: int, spent: int) -> int:
    return limit if state.day != _day_of(now) else max(0, limit - spent)


def budget_remaining(state: WatchState, *, now: float) -> int:
    return _budget(state, now, MAX_REQUESTS_PER_DAY, state.requests_today)


def headless_budget_for(trigger: Any) -> int:
    try:
        limit = int(_spec(trigger).get("max_headless_requests", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    return min(
        limit if limit > 0 else MAX_HEADLESS_REQUESTS_PER_DAY, MAX_REQUESTS_PER_DAY
    )


def headless_budget_remaining(state: WatchState, *, now: float, limit: int) -> int:
    return _budget(state, now, limit, state.headless_today)


def _escalate_enabled(trigger: Any) -> bool:
    return bool(_spec(trigger).get("escalate_headless", False))


def _await_maybe(result: Any) -> Any:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if asyncio.iscoroutine(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        with ThreadPoolExecutor() as worker:
            receipt = worker.submit(asyncio.run, result)
            return receipt.result(timeout=120)
    return result


def _poll_egress_policy(trigger_id: str) -> Any:
    from gideon.security.guardrails.policy import (
        profile_for_session,
        unattended_dispatch_key,
    )
    from gideon.security.net.policy import (
        SOURCE,
        egress_policy_for,
        egress_policy_for_profile,
    )

    identity = unattended_dispatch_key(f"trigger:{trigger_id}")
    profile = profile_for_session(identity)
    return egress_policy_for_profile(egress_policy_for(SOURCE), profile.egress_tier)


def _render_headless(url: str, renderer: Any, policy: Any) -> Any:
    from gideon.integrations.web.render import RenderResult

    try:
        if renderer is None:
            from gideon.integrations.web.render import render_url

            renderer = render_url
        response = renderer(url, policy=policy)
        return _await_maybe(response)
    except Exception as exc:
        return RenderResult(
            ok=False,
            url=url,
            error=f"headless render raised ({type(exc).__name__}: {exc})",
        )


def _with_escalation(base: str, escalation: str) -> str:
    return " ".join((base, f"[{escalation}]")) if escalation else base


@dataclass(frozen=True)
class KnowledgeDigest:
    trigger: Any
    url: str

    def item(self, raw: str) -> dict[str, Any]:
        return {
            "item_type": "bookmark",
            "title": raw if len(raw) <= 200 else raw[:197] + "...",
            "url": raw if raw[:4].lower() == "http" else "",
            "provider": "web_watch",
            "summary": f"New item from web watch {self.trigger.name!r} ({self.url})",
            "tags": ["web-watch", self.trigger.name],
        }

    def write(self, fresh: list[str], store: Any) -> int:
        written = 0
        try:
            if store is None:
                from gideon.cognition.knowledge import get_knowledge_store

                store = get_knowledge_store()
            for item in map(self.item, fresh):
                store.create_typed_item(**item)
                written += 1
        except Exception:
            logger.debug(
                "web_poll: could not route digest to knowledge for %s",
                self.trigger.id,
                exc_info=True,
            )
        return written


def _route_to_knowledge(
    trigger: Any, url: str, fresh_raw: list[str], store: Any
) -> int:
    return KnowledgeDigest(trigger, url).write(fresh_raw, store)


@dataclass
class PollOutcome:
    payload: dict[str, Any] | None = None
    reason: str = ""
    fetched: bool = False
    escalation: str = ""


@dataclass
class WebPoll:
    trigger: Any
    now: float
    base_dir: Path | str | None
    fetcher: Any
    renderer: Any
    knowledge_store: Any
    escalation: str = ""

    def escalate(
        self, items: list[str], url: str, state: WatchState, policy: Any, novelty: str
    ) -> list[str]:
        if items or not _escalate_enabled(self.trigger):
            return items
        limit = headless_budget_for(self.trigger)
        if headless_budget_remaining(state, now=self.now, limit=limit) <= 0:
            self.escalation = (
                f"headless escalation budget spent ({limit} renders); resumes tomorrow"
            )
            return items
        result = _render_headless(url, self.renderer, policy)
        if getattr(result, "unavailable", False):
            self.escalation = "headless tier unavailable; install gideon[js-render]"
            return items
        state.headless_today += 1
        if not getattr(result, "ok", False):
            self.escalation = (
                f"headless render failed: {getattr(result, 'error', '') or 'unknown'}"
            )
            return items
        rendered = extract_items(
            str(getattr(result, "html", "") or ""), novelty_key=novelty
        )
        self.escalation = (
            f"escalated to headless; extracted {len(rendered)} item(s)"
            if rendered
            else "escalated to headless; still no items after JS render"
        )
        return rendered or items

    def settle(self, url: str, state: WatchState, items: list[str]) -> PollOutcome:
        from gideon.security.security import fence_untrusted

        fresh = state.remember(items)
        was_seeded = state.seeded
        state.seeded = True
        save_state(self.trigger.id, state, base_dir=self.base_dir)
        if not was_seeded or not fresh:
            reason = (
                "no new items"
                if was_seeded
                else f"seeded {len(items)} item(s) without firing"
            )
            return PollOutcome(
                reason=_with_escalation(reason, self.escalation),
                fetched=True,
                escalation=self.escalation,
            )
        _route_to_knowledge(self.trigger, url, fresh, self.knowledge_store)
        payload = {
            "trigger_id": self.trigger.id,
            "trigger_name": self.trigger.name,
            "kind": "web_watch",
            "url": url,
            "new_count": len(fresh),
            "escalation": self.escalation,
            "new_items": [
                fence_untrusted(
                    item,
                    source=f"web_watch:{self.trigger.id}",
                    source_type="web_watch",
                    source_id=url,
                    transformation_path="poll:extract-items",
                )
                for item in fresh[:20]
            ],
        }
        return PollOutcome(payload=payload, fetched=True, escalation=self.escalation)

    def run(self) -> PollOutcome:
        spec = _spec(self.trigger)
        url = str(spec.get("url", "") or "").strip()
        if not url:
            return PollOutcome(reason="no url")
        state = load_state(self.trigger.id, base_dir=self.base_dir)
        if (
            state.last_polled_at
            and self.now - state.last_polled_at < poll_interval_for(self.trigger)
        ):
            return PollOutcome(reason="not due")
        if budget_remaining(state, now=self.now) <= 0:
            return PollOutcome(
                reason=f"daily request budget spent ({MAX_REQUESTS_PER_DAY} requests); resumes tomorrow"
            )
        policy = _poll_egress_policy(str(getattr(self.trigger, "id", "") or ""))
        if policy is None:
            return PollOutcome(
                reason="this run's safety profile denies all network egress (egress tier 'off')"
            )
        body, status, error = _fetch(url, self.fetcher, policy)
        state.charge(self.now, status)
        if error:
            save_state(self.trigger.id, state, base_dir=self.base_dir)
            return PollOutcome(reason=error, fetched=True)
        novelty = str(spec.get("novelty_key", ""))
        items = extract_items(body, novelty_key=novelty)
        items = self.escalate(items, url, state, policy, novelty)
        return self.settle(url, state, items)


def poll_one(
    trigger: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    fetcher: Any = None,
    renderer: Any = None,
    knowledge_store: Any = None,
) -> PollOutcome:
    return WebPoll(trigger, now, base_dir, fetcher, renderer, knowledge_store).run()


def _fetch(url: str, fetcher: Any, policy: Any) -> tuple[str, int, str]:
    try:
        if fetcher is None:
            from gideon.security.net import fetch

            request = fetch(url, policy=policy)
        else:
            request = fetcher(url)
        response = _await_maybe(request)
    except Exception as exc:
        return "", 0, f"fetch failed ({type(exc).__name__}: {exc})"
    status = int(getattr(response, "status", 0) or 0)
    raw = getattr(response, "body", b"") or b""
    body = (
        raw.decode("utf-8", errors="replace")
        if isinstance(raw, (bytes, bytearray))
        else str(raw)
    )
    return (
        ("", status, f"the page answered HTTP {status}")
        if status and not 200 <= status < 300
        else (body, status, "")
    )


def poll_all(
    store: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    fetcher: Any = None,
    renderer: Any = None,
    knowledge_store: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    payloads, skipped = [], []
    arguments = dict(
        now=now,
        base_dir=base_dir,
        fetcher=fetcher,
        renderer=renderer,
        knowledge_store=knowledge_store,
    )
    for trigger in web_watch_triggers(store):
        try:
            outcome = poll_one(trigger, **arguments)
        except Exception:
            logger.warning("web_watch poll failed for %s", trigger.id, exc_info=True)
            skipped.append({"trigger_id": trigger.id, "reason": "the poll raised"})
        else:
            if outcome.payload is not None:
                payloads.append(outcome.payload)
            elif outcome.reason and outcome.reason != "not due":
                skipped.append({"trigger_id": trigger.id, "reason": outcome.reason})
    return payloads, skipped
