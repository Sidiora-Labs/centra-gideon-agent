"""Per-turn accounting journal, event projection and bounded query views."""

from __future__ import annotations

import json
import logging
import hashlib
import math
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import fcntl

from gideon.core.atomic_write import atomic_write
from gideon.core.constants import dashboard_session_key

logger = logging.getLogger(__name__)
_CAP = 50_000
_GROUP_KEYS = ("model", "source", "agent", "provider", "day")
_SESSION_KEY_SEP = "-"
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


@dataclass
class TurnUsage:
    ts: str
    session_key: str
    source: str  # chat | loop | cron | subagent | channel | cli | background
    agent: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    duration_ms: int = 0
    instance_id: str | None = None
    provider_instance: str | None = None
    credential_ref: str | None = None
    subscription_source: str | None = None
    attribution: str | None = None
    import_format: str | None = None
    import_source_name: str | None = None
    import_file_sha256: str | None = None
    import_record_id: str | None = None


def _path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath("usage", "turns.jsonl")


@dataclass(frozen=True)
class UsageJournal:
    path: Path

    def append(self, usage):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(usage), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        _maybe_trim(self.path)

    def rows(self):
        if not self.path.is_file():
            return []
        result = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    result.append(row)
        except OSError:
            logger.debug("usage ledger read failed", exc_info=True)
        return result

    def trim(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2 * _CAP:
                retained = lines[-_CAP:]
                atomic_write(self.path, "\n".join(retained) + "\n")
        except OSError:
            logger.debug("usage ledger trim failed", exc_info=True)


def _emission_attribution(u: TurnUsage) -> TurnUsage:
    from gideon.core.config.loader import config_dir
    from gideon.operations.durability.shards import machine_id
    instance = machine_id(config_dir())
    fields = dict(instance_id=instance, provider_instance=None, credential_ref=None, subscription_source=None, attribution="instance_only")
    try:
        from gideon.integrations.llm.registry import get_default_registry
        from gideon.integrations.llm.branded_specs import registered_spec
        entry = get_default_registry().get_entry(u.provider)
        spec = registered_spec(entry.type)
        source = spec.credential_source if spec and spec.type == entry.type and not entry.credential and not entry.options.get("api_key") else ""
        fields.update(provider_instance=entry.name, credential_ref=entry.credential, subscription_source=source or None, attribution="binding_at_emission")
    except Exception:
        logger.debug("Usage binding attribution unavailable; preserving turn accounting", exc_info=True)
    return replace(u, **fields)


def record_turn(u: TurnUsage) -> None:
    try:
        UsageJournal(_path()).append(_emission_attribution(u))
    except Exception:
        logger.debug("usage ledger append failed", exc_info=True)


_IMPORT_FORMATS = {"claude_code_jsonl", "codex_rollout_jsonl"}
_SOURCE_NAME = re.compile(r"[A-Za-z0-9_. -]{1,200}\Z")
_IMPORT_MAX_BYTES = 4 * 1024 * 1024
_IMPORT_MAX_LINES = 50_000


def _positive_int(value, field):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or int(value) != value:
        raise ValueError(f"Invalid {field}")
    return int(value)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Imported usage requires an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Imported usage timestamp requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _json_lines(content):
    lines = content.splitlines()
    if len(lines) > _IMPORT_MAX_LINES:
        raise ValueError("Usage import exceeds line limit")
    parsed, invalid = [], 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            invalid += 1
            continue
        if not isinstance(value, dict):
            invalid += 1
            continue
        parsed.append((line_number, value))
    return parsed, invalid, len(lines)


def _import_id(format_name, session, event):
    material = json.dumps([format_name, session, event], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _import_row(*, format_name, source_name, file_sha, record_id, ts, session, provider, model, counts):
    from gideon.core.config.loader import config_dir
    from gideon.operations.durability.shards import machine_id
    session_hash = hashlib.sha256(session.encode()).hexdigest()[:24]
    return TurnUsage(
        ts=ts,
        session_key=f"external:{provider}:{session_hash}",
        source="cli_import",
        agent="",
        provider=provider,
        model=model or "unknown",
        input_tokens=counts["input_tokens"],
        output_tokens=counts["output_tokens"],
        cache_read_tokens=counts["cache_read_tokens"],
        cache_creation_tokens=counts["cache_creation_tokens"],
        cost_usd=0.0,
        priced=False,
        instance_id=machine_id(config_dir()),
        provider_instance=f"{provider}-cli-import",
        credential_ref=None,
        subscription_source=None,
        attribution="historical_cli_import",
        import_format=format_name,
        import_source_name=source_name,
        import_file_sha256=file_sha,
        import_record_id=record_id,
    )


def _claude_rows(entries, source_name, file_sha):
    rows, seen = [], set()
    session = next((value.get("sessionId") for _, value in entries if isinstance(value.get("sessionId"), str)), None)
    if not session:
        raise ValueError("Claude Code transcript requires sessionId")
    for line_number, value in entries:
        usage = value.get("message", {}).get("usage") if value.get("type") == "assistant" and isinstance(value.get("message"), dict) else None
        if not isinstance(usage, dict):
            continue
        event = value["message"].get("id") or value.get("uuid")
        counts = {
            "input_tokens": _positive_int(usage.get("input_tokens", 0), "input tokens"),
            "output_tokens": _positive_int(usage.get("output_tokens", 0), "output tokens"),
            "cache_read_tokens": _positive_int(usage.get("cache_read_input_tokens", 0), "cache read tokens"),
            "cache_creation_tokens": _positive_int(usage.get("cache_creation_input_tokens", 0), "cache creation tokens"),
        }
        if not event:
            event = hashlib.sha256(json.dumps([value.get("timestamp"), value.get("requestId"), value["message"].get("model"), counts], sort_keys=True).encode()).hexdigest()
        record_id = _import_id("claude_code_jsonl", session, str(event))
        if record_id in seen:
            continue
        seen.add(record_id)
        rows.append(_import_row(format_name="claude_code_jsonl", source_name=source_name, file_sha=file_sha, record_id=record_id, ts=_timestamp(value.get("timestamp")), session=session, provider="claude", model=value["message"].get("model"), counts=counts))
    return rows


def _codex_counts(usage):
    total_input = _positive_int(usage.get("input_tokens", 0), "input tokens")
    cached = _positive_int(usage.get("cached_input_tokens", 0), "cached input tokens")
    if cached > total_input:
        raise ValueError("Cached input exceeds total input")
    return {
        "input_tokens": total_input - cached,
        "output_tokens": _positive_int(usage.get("output_tokens", 0), "output tokens"),
        "cache_read_tokens": cached,
        "cache_creation_tokens": 0,
    }


def _codex_rows(entries, source_name, file_sha):
    metadata = next((value.get("payload") for _, value in entries if value.get("type") == "session_meta" and isinstance(value.get("payload"), dict)), None)
    session = metadata.get("id") if metadata else None
    if not isinstance(session, str) or not session:
        raise ValueError("Codex rollout requires session metadata")
    model = metadata.get("model") if isinstance(metadata.get("model"), str) else None
    previous = dict.fromkeys(("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"), 0)
    rows, seen = [], set()
    for line_number, value in entries:
        payload = value.get("payload")
        if value.get("type") == "turn_context" and isinstance(payload, dict) and isinstance(payload.get("model"), str):
            model = payload["model"]
        if value.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        usage = info.get("total_token_usage") if isinstance(info, dict) else None
        if not isinstance(usage, dict):
            continue
        current = _codex_counts(usage)
        if any(current[key] < previous[key] for key in current):
            raise ValueError("Codex cumulative usage moved backwards")
        event = json.dumps(current, sort_keys=True, separators=(",", ":"))
        record_id = _import_id("codex_rollout_jsonl", session, event)
        if record_id in seen:
            previous = current
            continue
        seen.add(record_id)
        delta = {key: current[key] - previous[key] for key in current}
        previous = current
        if not any(delta.values()):
            continue
        rows.append(_import_row(format_name="codex_rollout_jsonl", source_name=source_name, file_sha=file_sha, record_id=record_id, ts=_timestamp(value.get("timestamp")), session=session, provider="codex", model=model, counts=delta))
    return rows


@contextmanager
def _import_lock():
    path = _path().with_name("import.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def import_cli_usage(format_name, content, source_name):
    if format_name not in _IMPORT_FORMATS:
        raise ValueError("Unsupported CLI usage format")
    if not isinstance(content, str) or not content or len(content.encode()) > _IMPORT_MAX_BYTES:
        raise ValueError("Usage import must be a non-empty file up to 4 MiB")
    if not isinstance(source_name, str) or not _SOURCE_NAME.fullmatch(source_name) or source_name in (".", ".."):
        raise ValueError("Usage import requires a safe source filename")
    entries, invalid_lines, total_lines = _json_lines(content)
    file_sha = hashlib.sha256(content.encode()).hexdigest()
    candidates = (_claude_rows if format_name == "claude_code_jsonl" else _codex_rows)(entries, source_name, file_sha)
    if not candidates:
        raise ValueError("Usage import contains no supported usage records")
    with _import_lock():
        journal = UsageJournal(_path())
        existing = {row.get("import_record_id") for row in journal.rows() if row.get("import_record_id")}
        imported = [row for row in candidates if row.import_record_id not in existing]
        for row in imported:
            journal.append(row)
    return {"format": format_name, "source_name": source_name, "source_file_sha256": file_sha, "total_lines": total_lines, "invalid_lines": invalid_lines, "candidate_records": len(candidates), "imported_records": len(imported), "duplicate_records": len(candidates) - len(imported), "priced": False, "recorded_cost_usd": 0.0}


def import_cli_usage_file(format_name, path):
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise ValueError("Usage import requires a regular file")
    if source.stat().st_size > _IMPORT_MAX_BYTES:
        raise ValueError("Usage import exceeds size limit")
    return import_cli_usage(format_name, source.read_text(encoding="utf-8"), source.name)


@dataclass(frozen=True)
class EventAccounting:
    event: object
    model: str
    estimate: bool

    def values(self):
        from gideon.operations.pricing import estimate_cost, has_pricing

        counts = {key: int(getattr(self.event, key, 0) or 0) for key in _TOKEN_FIELDS}
        cost = float(getattr(self.event, "cost_usd", 0.0) or 0.0)
        if not cost and self.model and self.estimate:
            cost = estimate_cost(self.model, **counts)
        return counts, cost, has_pricing

    def record(self, source, session_key, agent, provider):
        counts, cost, has_pricing = self.values()
        return TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent=agent,
            provider=provider,
            model=self.model,
            **counts,
            cost_usd=cost,
            priced=bool(cost) or has_pricing(self.model),
            duration_ms=int(getattr(self.event, "duration_ms", 0) or 0),
        )


def record_from_event(
    event: object,
    *,
    source: str,
    session_key: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    estimate_if_missing: bool = True,
) -> None:
    accounting = EventAccounting(event, model, estimate_if_missing)
    record_turn(accounting.record(source, session_key, agent, provider))


def _maybe_trim(p: Path) -> None:
    UsageJournal(p).trim()


def _iter_rows() -> list[dict]:
    return UsageJournal(_path()).rows()


def _day_of(ts: str) -> str:
    return ts[:10]


def _in_window(ts: str, since: str, until: str) -> bool:
    return not ((since and ts < since) or (until and ts >= until))


def canonical_session_query_key(session: str) -> str:
    key = str(session or "").strip()
    if not key or ":" in key:
        return key
    return dashboard_session_key(key)


def _blank_agg() -> dict:
    return {
        **dict.fromkeys(_TOKEN_FIELDS, 0),
        "cost_usd": 0.0,
        "turns": 0,
        "priced": True,
    }


def _fold(agg: dict, row: dict) -> None:
    for key in _TOKEN_FIELDS:
        agg[key] += int(row.get(key, 0) or 0)
    agg["cost_usd"] += float(row.get("cost_usd", 0.0) or 0.0)
    agg["turns"] += 1
    if not row.get("priced", True):
        agg["priced"] = False


def _session_matches(key: str, session_key: str, session_prefix: str) -> bool:
    exact = not session_key or key == session_key
    nested = (
        not session_prefix
        or key == session_prefix
        or key.startswith(session_prefix + _SESSION_KEY_SEP)
    )
    return exact and nested


@dataclass(frozen=True)
class TurnSelection:
    since: str = ""
    until: str = ""
    session_key: str = ""
    session_prefix: str = ""

    def accepts(self, row):
        return _in_window(
            str(row.get("ts", "")), self.since, self.until
        ) and _session_matches(
            str(row.get("session_key", "")), self.session_key, self.session_prefix
        )

    def rows(self):
        return filter(self.accepts, _iter_rows())


def _row_selected(
    row: dict, since: str, until: str, session_key: str, session_prefix: str = ""
) -> bool:
    return TurnSelection(since, until, session_key, session_prefix).accepts(row)


def rollup(
    *,
    since: str = "",
    until: str = "",
    group_by: str = "model",
    session_key: str = "",
    session_prefix: str = "",
) -> list[dict]:
    if group_by not in _GROUP_KEYS:
        raise ValueError(f"group_by must be one of {_GROUP_KEYS}, got {group_by!r}")
    groups: dict = {}
    selected = TurnSelection(since, until, session_key, session_prefix)
    for row in selected.rows():
        key = (
            _day_of(str(row.get("ts", "")))
            if group_by == "day"
            else str(row.get(group_by, ""))
        )
        _fold(groups.setdefault(key, _blank_agg()), row)
    keys = sorted(groups, key=lambda key: (-groups[key]["cost_usd"], str(key)))
    return [{group_by: key, **groups[key]} for key in keys]


def totals(
    *, since: str = "", until: str = "", session_key: str = "", session_prefix: str = ""
) -> dict:
    aggregate = _blank_agg()
    for row in TurnSelection(since, until, session_key, session_prefix).rows():
        _fold(aggregate, row)
    return aggregate
