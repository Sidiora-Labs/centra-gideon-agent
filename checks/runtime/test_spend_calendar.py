"""Configured calendar, DST boundaries, actual usage HTTP and retained-history truth."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientSession, web

from gideon.core import spend_day
from gideon.engine.routing import usage
from gideon.engine.routing.rates import load_overlay, set_rate
from gideon.integrations.action_providers.usage_recap_provider import previous_month
from gideon.interfaces.dashboard.handlers.usage import register_usage_routes
from gideon.security.guardrails.budgets import SpendMeter


class Clock(datetime):
    instant = datetime(2026, 10, 6, 4, 2, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return (
            cls.instant.astimezone(tz)
            if tz is not None
            else cls.instant.replace(tzinfo=None)
        )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("TZ", "UTC")
    (tmp_path / "config.json").write_text(json.dumps({"timezone": "America/Toronto"}))
    monkeypatch.setattr(spend_day, "datetime", Clock)
    Clock.instant = datetime(2026, 10, 6, 4, 2, tzinfo=timezone.utc)
    return tmp_path


@pytest.mark.parametrize("date,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_dst_day_has_successive_local_midnights(home, date, hours):
    Clock.instant = datetime.fromisoformat(spend_day.start_of(date))
    assert spend_day.today() == date
    assert spend_day.next_day_starts() - Clock.instant.timestamp() == hours * 3600


def test_meter_resets_at_configured_midnight_and_price_date_matches(home):
    meter = SpendMeter(config_dir=home)
    Clock.instant = datetime(2026, 10, 6, 3, 59, tzinfo=timezone.utc)
    meter.charge(10, 0.1)
    assert spend_day.today() == "2026-10-05"
    assert meter.day_totals().tokens == 10
    Clock.instant = datetime(2026, 10, 6, 4, 1, tzinfo=timezone.utc)
    assert meter.day_totals().tokens == 0
    meter.charge(20, 0.2)
    rows = json.loads((home / "spend.json").read_text())
    assert rows["2026-10-05"]["tokens"] == 10 and rows["2026-10-06"]["tokens"] == 20
    set_rate("p:m", {"in_per_mtok": 1, "out_per_mtok": 1}, home=home)
    assert load_overlay(home=home)["rates"]["p:m"]["recorded"] == "2026-10-06"


def _row(ts, audit):
    return dict(
        ts=ts,
        session_key="s",
        source="chat",
        agent="",
        provider="p",
        model="m",
        input_tokens=2,
        output_tokens=3,
        cost_usd=1,
        priced=True,
        estimated=False,
        audit_id=audit,
    )


def _write(home, rows):
    (home / "usage").mkdir(exist_ok=True)
    (home / "usage/turns.jsonl").write_text("\n".join(json.dumps(row) for row in rows))


def _zone(home, name):
    (home / "config.json").write_text(json.dumps({"timezone": name}))


def test_timezone_change_preserves_original_aggregates_without_double_counting_partial_raw(
    home,
):
    _zone(home, "UTC")
    rows = [
        _row("2026-10-06T00:30:00+00:00", "1"),
        _row("2026-10-01T12:00:00+00:00", "2"),
        _row("2026-10-02T12:00:00+00:00", "3"),
    ]
    _write(home, rows)
    original = deepcopy(usage.refresh(home)["days"])
    _write(home, [rows[0], rows[0]])
    _zone(home, "America/Toronto")
    changed = usage.refresh(home)
    assert set(changed["days"]) == {"2026-10-05"}
    assert changed["calendar_archives"][0]["days"] == original
    view = usage.query(changed, window="week", today="2026-10-06")
    assert view["total"]["calls"] == 1 and view["total"]["dollars_est"] == 1
    assert (
        view["legacy_total"]["calls"] == 2 and view["legacy_total"]["dollars_est"] == 2
    )
    assert view["calendar_timezone"] == "America/Toronto"
    assert usage.refresh(home)["unallocated_total"]["calls"] == 2
    _zone(home, "Asia/Tokyo")
    changed = usage.refresh(home)
    assert set(changed["days"]) == {"2026-10-06"}
    assert changed["unallocated_total"]["calls"] == 2
    assert changed["calendar_archives"][0]["days"] == original
    assert len(changed["calendar_archives"]) == 2


@pytest.mark.asyncio
async def test_actual_window_http_and_by_day_fold_share_configured_day(home):
    _write(
        home,
        [
            _row("2026-10-06T03:59:00+00:00", "1"),
            _row("2026-10-06T04:01:00+00:00", "2"),
        ],
    )
    app = web.Application()
    register_usage_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        async with ClientSession() as client:
            base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            async with client.get(base + "/api/usage/totals?window=day") as response:
                total = await response.json()
                assert response.status == 200
            assert (
                total["totals"]["turns"] == 1
                and total["since"] == "2026-10-06T04:00:00+00:00"
            )
            async with client.get(
                base + "/api/usage/rollup?window=day&group_by=day"
            ) as response:
                rows = await response.json()
                assert response.status == 200
            assert (
                rows["rows"][0]["day"] == "2026-10-06" and rows["rows"][0]["turns"] == 1
            )
            async with client.get(base + "/api/usage?window=day") as response:
                fold = await response.json()
                assert response.status == 200
            assert fold["total"]["calls"] == 1 and fold["dates"] == ["2026-10-06"]
            for query in ["window=day&since=2026-10-01", "window=year"]:
                async with client.get(base + "/api/usage/totals?" + query) as response:
                    assert response.status == 400
    finally:
        await runner.cleanup()


def test_recap_default_previous_month_uses_configured_zone(home, monkeypatch):
    from gideon.integrations.action_providers import usage_recap_provider

    monkeypatch.setattr(usage_recap_provider, "datetime", Clock)
    Clock.instant = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)
    assert spend_day.today() == "2026-10-31"
    assert previous_month() == "2026-09"
