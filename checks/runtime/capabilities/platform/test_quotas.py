import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_platform_quotas import register
from gideon.operations.usage_ledger import TurnUsage, UsageJournal
from gideon.workspace.capabilities.platform.quotas import (
    ProviderQuotaSnapshot,
    QuotaError,
    SubscriptionQuotaStore,
)


def times():
    now = datetime.now(timezone.utc)
    return (
        now,
        (now - timedelta(days=1)).isoformat(),
        (now + timedelta(days=29)).isoformat(),
    )


def create(store, request="plan", provider="Work API", tokens=1000, dollars=10.0):
    _, start, end = times()
    return store.create_plan(
        {
            "request_id": request,
            "provider": provider,
            "name": "Work subscription",
            "source": "owner copied plan terms",
            "cycle_start": start,
            "cycle_end": end,
            "token_limit": tokens,
            "dollar_limit": dollars,
            "monthly_cost_usd": 20.0,
        }
    )


def reserve(
    store, plan, request="reserve", run="run-1", tokens=100, dollars=1.0, expiry=None
):
    expiry = expiry or (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return store.reserve(
        plan["id"],
        {
            "request_id": request,
            "run_id": run,
            "purpose": "bounded analysis",
            "expected_tokens": tokens,
            "expected_dollars": dollars,
            "expires_at": expiry,
        },
    )


def usage(home, provider="Work API", tokens=250, dollars=2.5, priced=True, at=None):
    at = at or datetime.now(timezone.utc)
    UsageJournal(home / "usage" / "turns.jsonl").append(
        TurnUsage(
            ts=at.isoformat(),
            session_key="dashboard:q",
            source="chat",
            agent="",
            provider=provider,
            model="model-a",
            input_tokens=tokens - 50,
            output_tokens=50,
            cost_usd=dollars,
            priced=priced,
        )
    )


def test_plan_summary_reuses_canonical_usage_and_labels_configured_authority(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store)
    usage(tmp_path, tokens=250, dollars=2.5)
    usage(tmp_path, tokens=100, dollars=1.0, priced=False)
    usage(tmp_path, provider="Other API", tokens=900, dollars=9.0)
    view = store.summary(plan["id"])
    assert view["plan"]["quota_basis"] == "user_configured"
    assert view["usage"] == {
        "tokens": 350,
        "dollars": 3.5,
        "turns": 2,
        "unpriced_turns": 1,
        "basis": "canonical_local_usage_ledger",
    }
    assert view["available"] == {"tokens": 650, "dollars": 6.5}
    assert view["reservations"] == {
        "active": 0,
        "expired_unreleased": 0,
        "reserved_tokens": 0,
        "reserved_dollars": 0,
    }
    assert view["provider_evidence"] is None
    assert (
        "does not grant provider entitlement or runtime admission" in view["coverage"]
    )
    assert "no vendor quota is inferred" in view["coverage"]
    assert SubscriptionQuotaStore(tmp_path).summary(plan["id"]) == view


def test_atomic_reservations_hold_configured_capacity_and_reject_overbooking(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store, tokens=500, dollars=5.0)
    usage(tmp_path, tokens=200, dollars=2.0)
    first = reserve(store, plan, tokens=200, dollars=2.0)
    assert first["status"] == "held"
    assert first["evidence_basis"] == "local_planning_only"
    view = store.summary(plan["id"])
    assert view["reservations"]["reserved_tokens"] == 200
    assert view["reservations"]["reserved_dollars"] == 2.0
    assert view["available"] == {"tokens": 100, "dollars": 1.0}
    with pytest.raises(QuotaError, match="token quota is exhausted") as exc:
        reserve(store, plan, "too-many-tokens", "run-2", 101, 0)
    assert exc.value.code == "quota_exhausted"
    with pytest.raises(QuotaError, match="dollar quota is exhausted"):
        reserve(store, plan, "too-many-dollars", "run-3", 0, 1.01)
    assert len(store.list_reservations(plan["id"])) == 1
    assert store.summary(plan["id"])["available"] == {"tokens": 100, "dollars": 1.0}


def test_concurrent_final_capacity_has_one_winner(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store, tokens=100, dollars=None)

    def attempt(index):
        try:
            return SubscriptionQuotaStore(tmp_path).reserve(
                plan["id"],
                {
                    "request_id": f"reserve-{index}",
                    "run_id": f"run-{index}",
                    "purpose": "competing work",
                    "expected_tokens": 100,
                    "expected_dollars": 0,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                },
            )
        except QuotaError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(isinstance(row, dict) for row in results) == 1
    assert results.count("quota_exhausted") == 1
    assert store.summary(plan["id"])["available"]["tokens"] == 0
    assert len(store.list_reservations(plan["id"])) == 1


def test_release_is_revisioned_idempotent_and_returns_capacity(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store)
    held = reserve(store, plan)
    payload = {
        "request_id": "release",
        "revision": held["revision"],
        "reason": "work ended before provider call",
    }
    released = store.release(held["id"], payload)
    assert released["status"] == "released"
    assert released["release_reason"] == "work ended before provider call"
    assert store.release(held["id"], payload) == released
    assert store.summary(plan["id"])["reservations"]["active"] == 0
    assert store.summary(plan["id"])["available"] == {"tokens": 1000, "dollars": 10.0}
    with pytest.raises(QuotaError, match="changed; reload"):
        store.release(
            held["id"],
            {"request_id": "stale", "revision": 1, "reason": "duplicate tab"},
        )
    with pytest.raises(QuotaError, match="Request ID already used"):
        store.release(
            held["id"],
            {"request_id": "release", "revision": 2, "reason": "different payload"},
        )


def test_expired_unreleased_reservation_is_visible_but_not_active(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store)
    reserve(store, plan)
    after_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    view = store.summary(plan["id"], after_expiry)
    assert view["reservations"]["active"] == 0
    assert view["reservations"]["expired_unreleased"] == 1
    assert view["reservations"]["reserved_tokens"] == 0
    assert view["available"]["tokens"] == 1000
    assert store.list_reservations(plan["id"])[0]["status"] == "held"
    with pytest.raises(QuotaError, match="future and within plan cycle"):
        reserve(
            store,
            plan,
            "expired-at-create",
            "run-expired",
            10,
            0,
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )


def test_plan_validation_revision_history_and_archive_block_new_reservations(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    _, start, end = times()
    with pytest.raises(QuotaError, match="At least one"):
        store.create_plan(
            {
                "request_id": "empty",
                "provider": "P",
                "name": "N",
                "source": "owner",
                "cycle_start": start,
                "cycle_end": end,
                "token_limit": None,
                "dollar_limit": None,
            }
        )
    with pytest.raises(QuotaError, match="after"):
        store.create_plan(
            {
                "request_id": "backward",
                "provider": "P",
                "name": "N",
                "source": "owner",
                "cycle_start": end,
                "cycle_end": start,
                "token_limit": 1,
                "dollar_limit": None,
            }
        )
    plan = create(store)
    updated = store.update_plan(
        plan["id"],
        {
            "request_id": "archive",
            "revision": 1,
            "name": "Archived work plan",
            "archived": True,
        },
    )
    assert updated["revision"] == 2
    assert updated["archived"] is True
    assert [row["name"] for row in store.history(plan["id"])] == [
        "Work subscription",
        "Archived work plan",
    ]
    with pytest.raises(QuotaError, match="Archived"):
        reserve(store, updated)
    with pytest.raises(QuotaError, match="changed; reload"):
        store.update_plan(
            plan["id"], {"request_id": "stale", "revision": 1, "name": "Old tab"}
        )


def test_provider_snapshot_is_typed_evidence_and_never_changes_configured_availability(
    tmp_path,
):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store)
    before = store.summary(plan["id"])["available"]
    with pytest.raises(QuotaError, match="provider adapter boundary"):
        store.record_provider_snapshot(plan["id"], {"provider": "Work API"})
    captured = datetime.now(timezone.utc).isoformat()
    snapshot = ProviderQuotaSnapshot(
        provider="Work API",
        captured_at=captured,
        source="provider account endpoint",
        limits=(
            {
                "key": "week",
                "label": "Current week",
                "percent_used": 37,
                "resets_at": (
                    datetime.now(timezone.utc) + timedelta(days=2)
                ).isoformat(),
            },
        ),
    )
    evidence = store.record_provider_snapshot(plan["id"], snapshot)
    assert evidence["evidence_basis"] == "provider_reported"
    assert evidence["limits"][0]["percent_used"] == 37.0
    assert store.summary(plan["id"])["available"] == before
    assert store.summary(plan["id"])["provider_evidence"] == evidence
    assert store.provider_evidence(plan["id"]) == [evidence]
    with pytest.raises(QuotaError, match="does not match"):
        store.record_provider_snapshot(
            plan["id"],
            ProviderQuotaSnapshot(
                "Other API", captured, "provider endpoint", snapshot.limits
            ),
        )
    with pytest.raises(QuotaError, match="between 0 and 100"):
        store.record_provider_snapshot(
            plan["id"],
            ProviderQuotaSnapshot(
                "Work API",
                (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
                "provider endpoint",
                (
                    {
                        "key": "week",
                        "label": "Week",
                        "percent_used": 101,
                        "resets_at": (
                            datetime.now(timezone.utc) + timedelta(days=1)
                        ).isoformat(),
                    },
                ),
            ),
        )


@pytest.mark.asyncio
async def test_real_http_flow_is_home_scoped_no_store_and_has_no_entitlement_or_evidence_write(
    tmp_path,
):
    app, other_app = web.Application(), web.Application()
    register(app, tmp_path / "one")
    register(other_app, tmp_path / "two")
    base = "/api/capabilities/platform/quotas"
    _, start, end = times()
    async with (
        TestClient(TestServer(app)) as client,
        TestClient(TestServer(other_app)) as other,
    ):
        response = await client.post(
            base + "/plans",
            json={
                "request_id": "plan",
                "provider": "HTTP API",
                "name": "HTTP plan",
                "source": "owner terms",
                "cycle_start": start,
                "cycle_end": end,
                "token_limit": 500,
                "dollar_limit": 5,
                "monthly_cost_usd": None,
            },
        )
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        plan = await response.json()
        response = await client.post(
            f"{base}/plans/{plan['id']}/reservations",
            json={
                "request_id": "reserve",
                "run_id": "http-run",
                "purpose": "HTTP journey",
                "expected_tokens": 100,
                "expected_dollars": 1,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        held = await response.json()
        assert held["evidence_basis"] == "local_planning_only"
        summary = await (await client.get(f"{base}/plans/{plan['id']}/summary")).json()
        assert summary["available"] == {"tokens": 400, "dollars": 4.0}
        assert summary["provider_evidence"] is None
        assert (await other.get(f"{base}/plans/{plan['id']}")).status == 404
        assert (
            await client.post(f"{base}/plans/{plan['id']}/provider-evidence", json={})
        ).status == 405
        assert (
            await client.post(f"{base}/plans/{plan['id']}/admit", json={})
        ).status == 404
        assert (
            await client.post(f"{base}/plans/{plan['id']}/entitlement", json={})
        ).status == 404
        release = await client.post(
            f"{base}/reservations/{held['id']}/release",
            json={"request_id": "release", "revision": 1, "reason": "done"},
        )
        assert release.status == 200
        assert (await release.json())["status"] == "released"
        assert (
            len(
                (await (await client.get(f"{base}/plans/{plan['id']}/history")).json())[
                    "history"
                ]
            )
            == 1
        )
        assert (
            await (
                await client.get(f"{base}/plans/{plan['id']}/provider-evidence")
            ).json()
        )["evidence"] == []
        raw = SubscriptionQuotaStore(tmp_path / "one").path.read_bytes()
        assert b"provider_reported" not in raw


def test_usage_cycle_boundary_provider_binding_and_optional_ceiling_are_exact(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store, provider="Bound API", tokens=None, dollars=8.0)
    cycle_start = datetime.fromisoformat(plan["cycle_start"])
    cycle_end = datetime.fromisoformat(plan["cycle_end"])
    usage(tmp_path, provider="Bound API", tokens=100, dollars=1.0, at=cycle_start)
    usage(
        tmp_path,
        provider="Bound API",
        tokens=200,
        dollars=2.0,
        at=cycle_end - timedelta(microseconds=1),
    )
    usage(
        tmp_path,
        provider="Bound API",
        tokens=400,
        dollars=4.0,
        at=cycle_start - timedelta(microseconds=1),
    )
    usage(tmp_path, provider="Bound API", tokens=800, dollars=8.0, at=cycle_end)
    usage(
        tmp_path,
        provider="bound api",
        tokens=1600,
        dollars=16.0,
        at=cycle_start + timedelta(days=1),
    )
    view = store.summary(plan["id"])
    assert view["usage"]["tokens"] == 300
    assert view["usage"]["dollars"] == 3.0
    assert view["usage"]["turns"] == 2
    assert view["available"]["tokens"] is None
    assert view["available"]["dollars"] == 5.0
    held = reserve(store, plan, tokens=10_000, dollars=4.5)
    assert held["expected_tokens"] == 10_000
    assert store.summary(plan["id"])["available"] == {"tokens": None, "dollars": 0.5}
    with pytest.raises(QuotaError, match="dollar quota is exhausted"):
        reserve(store, plan, "over-dollar", "other-run", 0, 0.51)


def test_plan_and_reservation_request_replay_cannot_change_meaning(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    _, start, end = times()
    payload = {
        "request_id": "same-plan",
        "provider": "Replay API",
        "name": "Replay plan",
        "source": "owner terms",
        "cycle_start": start,
        "cycle_end": end,
        "token_limit": 200,
        "dollar_limit": None,
        "monthly_cost_usd": None,
    }
    first = store.create_plan(payload)
    assert store.create_plan(payload) == first
    assert len(store.list_plans()) == 1
    changed = dict(payload, name="Changed meaning")
    with pytest.raises(QuotaError, match="Request ID already used"):
        store.create_plan(changed)
    reservation_payload = {
        "request_id": "same-reservation",
        "run_id": "replay-run",
        "purpose": "replay-safe work",
        "expected_tokens": 50,
        "expected_dollars": 0,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    reserved = store.reserve(first["id"], reservation_payload)
    assert store.reserve(first["id"], reservation_payload) == reserved
    assert len(store.list_reservations(first["id"])) == 1
    with pytest.raises(QuotaError, match="Request ID already used"):
        store.reserve(first["id"], dict(reservation_payload, expected_tokens=51))
    assert store.summary(first["id"])["reservations"]["reserved_tokens"] == 50


def test_multiple_plans_sort_and_isolate_reservations_usage_and_history(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    zebra = create(store, "zebra", "Z API", 900, 9.0)
    alpha = store.create_plan(
        {
            **{
                key: value
                for key, value in {
                    "request_id": "alpha",
                    "provider": "A API",
                    "name": "Alpha plan",
                    "source": "owner terms",
                    "cycle_start": times()[1],
                    "cycle_end": times()[2],
                    "token_limit": 300,
                    "dollar_limit": 3.0,
                    "monthly_cost_usd": None,
                }.items()
            }
        }
    )
    assert [row["name"] for row in store.list_plans()] == [
        "Alpha plan",
        "Work subscription",
    ]
    usage(tmp_path, provider="Z API", tokens=100, dollars=1.0)
    usage(tmp_path, provider="A API", tokens=50, dollars=0.5)
    zebra_reservation = reserve(store, zebra, "z-reserve", "z-run", 200, 2.0)
    alpha_reservation = reserve(store, alpha, "a-reserve", "a-run", 25, 0.25)
    assert [row["id"] for row in store.list_reservations(zebra["id"])] == [
        zebra_reservation["id"]
    ]
    assert [row["id"] for row in store.list_reservations(alpha["id"])] == [
        alpha_reservation["id"]
    ]
    assert store.summary(zebra["id"])["available"] == {"tokens": 600, "dollars": 6.0}
    assert store.summary(alpha["id"])["available"] == {"tokens": 225, "dollars": 2.25}
    changed = store.update_plan(
        alpha["id"],
        {"request_id": "rename-alpha", "revision": 1, "name": "Alpha revised"},
    )
    assert changed["revision"] == 2
    assert [row["revision"] for row in store.history(alpha["id"])] == [1, 2]
    assert store.get_plan(zebra["id"])["revision"] == 1


def test_provider_snapshots_retain_order_and_do_not_replace_local_usage(tmp_path):
    store = SubscriptionQuotaStore(tmp_path)
    plan = create(store)
    usage(tmp_path, tokens=125, dollars=1.25)
    first_at = datetime.now(timezone.utc)
    second_at = first_at + timedelta(seconds=1)
    first = store.record_provider_snapshot(
        plan["id"],
        ProviderQuotaSnapshot(
            "Work API",
            first_at.isoformat(),
            "provider endpoint request 1",
            (
                {
                    "key": "session",
                    "label": "Current session",
                    "percent_used": 20,
                    "resets_at": (first_at + timedelta(hours=5)).isoformat(),
                },
            ),
        ),
    )
    second = store.record_provider_snapshot(
        plan["id"],
        ProviderQuotaSnapshot(
            "Work API",
            second_at.isoformat(),
            "provider endpoint request 2",
            (
                {
                    "key": "week",
                    "label": "Current week",
                    "percent_used": 40,
                    "resets_at": (first_at + timedelta(days=7)).isoformat(),
                },
            ),
        ),
    )
    evidence = store.provider_evidence(plan["id"])
    assert evidence == [second, first]
    view = store.summary(plan["id"])
    assert view["provider_evidence"] == second
    assert view["usage"]["tokens"] == 125
    assert view["usage"]["dollars"] == 1.25
    assert view["available"] == {"tokens": 875, "dollars": 8.75}
    assert second["limits"][0]["percent_used"] == 40.0
    assert view["available"]["tokens"] != 600


@pytest.mark.asyncio
async def test_http_validation_and_conflicts_use_structured_errors(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    base = "/api/capabilities/platform/quotas"
    _, start, end = times()
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            base + "/plans",
            json={
                "request_id": "no-limit",
                "provider": "P",
                "name": "No limit",
                "source": "owner",
                "cycle_start": start,
                "cycle_end": end,
                "token_limit": None,
                "dollar_limit": None,
            },
        )
        assert response.status == 400
        assert (await response.json())["error"]["code"] == "invalid_request"
        plan = await (
            await client.post(
                base + "/plans",
                json={
                    "request_id": "plan",
                    "provider": "P",
                    "name": "Plan",
                    "source": "owner",
                    "cycle_start": start,
                    "cycle_end": end,
                    "token_limit": 10,
                    "dollar_limit": None,
                },
            )
        ).json()
        response = await client.put(
            f"{base}/plans/{plan['id']}",
            json={"request_id": "stale", "revision": 2, "name": "Wrong revision"},
        )
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "conflict"
        response = await client.post(
            f"{base}/plans/{plan['id']}/reservations",
            json={
                "request_id": "zero",
                "run_id": "run",
                "purpose": "nothing",
                "expected_tokens": 0,
                "expected_dollars": 0,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        assert response.status == 400
        assert (
            "must request tokens or dollars"
            in (await response.json())["error"]["message"]
        )
        response = await client.post(
            f"{base}/plans/{plan['id']}/reservations",
            json={
                "request_id": "over",
                "run_id": "run",
                "purpose": "too large",
                "expected_tokens": 11,
                "expected_dollars": 0,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "quota_exhausted"
        assert (await client.get(base + "/plans/missing/summary")).status == 404
        assert (
            await client.post(
                base + "/reservations/missing/release",
                json={"request_id": "release", "revision": 1, "reason": "none"},
            )
        ).status == 404
