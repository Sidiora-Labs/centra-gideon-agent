"""Process counters and stateless prompt-cache measurements."""

import logging
import threading
import time

logger = logging.getLogger(__name__)
_COUNTER_NAMES = (
    "sessions_created",
    "sessions_cleaned",
    "subagents_spawned",
    "subagents_completed",
    "subagents_failed",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "total_turns",
    "total_duration_ms",
)


class RuntimeCounters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = dict.fromkeys(_COUNTER_NAMES, 0)
        self.dollars = 0.0
        self.started = time.monotonic()

    def add(self, key, amount):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + amount

    def add_cost(self, amount):
        with self.lock:
            self.dollars += amount

    def cost(self):
        with self.lock:
            return self.dollars

    def snapshot(self):
        with self.lock:
            return self.values.copy()

    def reset(self):
        with self.lock:
            self.values.update(dict.fromkeys(self.values, 0))
            self.started = time.monotonic()


def _uptime(seconds):
    hours, remainder = divmod(round(seconds), 3600)
    days, hours = divmod(hours, 24)
    minutes = remainder // 60
    return " ".join(([f"{days}d"] if days else []) + [f"{hours}h", f"{minutes}m"])


class Stats:
    _instance: "Stats | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "Stats":
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init_counters()
                cls._instance = instance
            return cls._instance

    def _init_counters(self) -> None:
        self._counts = RuntimeCounters()

    @property
    def _start_time(self):
        return self._counts.started

    @_start_time.setter
    def _start_time(self, value):
        self._counts.started = value

    def inc(self, key: str, n: int = 1) -> None:
        self._counts.add(key, n)

    def inc_input_tokens(self, n: int) -> None:
        self.inc("input_tokens", n)

    def inc_output_tokens(self, n: int) -> None:
        self.inc("output_tokens", n)

    def inc_cache_creation_tokens(self, n: int) -> None:
        self.inc("cache_creation_tokens", n)

    def inc_cache_read_tokens(self, n: int) -> None:
        self.inc("cache_read_tokens", n)

    def inc_cost_usd(self, amount: float) -> None:
        self._counts.add_cost(amount)

    def inc_turns(self, n: int) -> None:
        self.inc("total_turns", n)

    def inc_duration_ms(self, n: int) -> None:
        self.inc("total_duration_ms", n)

    def get_cost_usd(self) -> float:
        return self._counts.cost()

    def inc_session_created(self) -> None:
        self.inc("sessions_created")

    def inc_session_cleaned(self) -> None:
        self.inc("sessions_cleaned")

    def inc_subagent_spawned(self) -> None:
        self.inc("subagents_spawned")

    def inc_subagent_completed(self) -> None:
        self.inc("subagents_completed")

    def inc_subagent_failed(self) -> None:
        self.inc("subagents_failed")

    def uptime_str(self) -> str:
        return _uptime(time.monotonic() - self._counts.started)

    def snapshot(self) -> dict[str, int]:
        return self._counts.snapshot()

    def summary(self) -> str:
        counts = self.snapshot()
        fields = [
            f"uptime {self.uptime_str()}",
            f"sessions {counts['sessions_created']}/{counts['sessions_cleaned']}",
            "subagents {subagents_spawned} spawned, {subagents_completed} completed, "
            "{subagents_failed} failed".format_map(counts),
            f"turns {counts['total_turns']}",
            "tokens {input_tokens} in / {output_tokens} out".format_map(counts),
        ]
        return " · ".join(fields)

    def reset(self) -> None:
        self._counts.reset()


def cache_hit_pct(
    *,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    input_tokens: int,
) -> float | None:
    """Share of one turn's prompt tokens served from cache, or ``None``.

    This stateless helper uses counts already held by the caller. Process-lifetime
    cache counters remain in ``operations/stats.py:16-17``; computing a per-turn
    ratio does not create a second tally.

    The denominator is ``input_tokens + cache_read_tokens + cache_creation_tokens``.
    These are disjoint buckets: input tokens exclude cached reads and writes, so
    adding all three recovers the whole prompt. The supporting accounting paths are:

    * ``integrations/llm/anthropic.py:200-209`` copies the SDK's input count into
      the shared decoder usage without subtracting or adding cache counts. The
      separate SDK cache fields are read by ``_read_cache_usage`` at
      ``integrations/llm/anthropic.py:40-46``.
    * ``operations/pricing.py:74-84`` charges prompt, response, cache reads and
      cache writes separately at their respective rates, then adds the charges.
      Treating the input count as including cached tokens would double-bill them.
    * ``operations/usage_ledger.py:18-23`` declares distinct input, output,
      cache-read and cache-creation fields. ``operations/usage_ledger.py:172-174``
      folds each field into its own aggregate without combining the buckets.
      This preserves the provider's accounting distinction in persisted totals.
    * ``operations/pricing.py:119-122`` independently computes an uncached prompt
      from the same three input buckets for the cache-savings comparison. This
      is a counterpart calculation, rather than independent provider evidence.

    A nonpositive denominator returns ``None``: no prompt tokens means no
    measurement, not a measured zero percent. The turn-complete renderer applies
    the same rule to context usage at
    ``interfaces/dashboard/chat_runner.py:608-609``: it only prints context usage
    when ``context_pct is not None``. With prompt tokens present and no cache
    reads, this helper returns the measured value ``0.0``.
    """
    read = cache_read_tokens or 0
    whole_prompt = sum((read, cache_creation_tokens or 0, input_tokens or 0))
    return None if whole_prompt <= 0 else read / whole_prompt * 100.0
