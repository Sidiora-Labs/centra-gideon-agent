from __future__ import annotations

import json

import pytest

from gideon.hypermid.shared_models import (
    CacheConflict,
    CacheState,
    Durability,
    FileCacheStore,
    ModelCatalog,
    ProviderUsage,
)


def test_cache_catalog_and_usage_contracts_preserve_identity_and_missing_values(tmp_path):
    store = FileCacheStore(tmp_path / "cache.json")
    state = CacheState.empty().rebuild_local(0, "message:42", b"\x00exact\xff")
    store.compare_and_swap(None, state)
    loaded = store.load()
    assert loaded is not None
    deferred, replay = loaded.defer(1, boundary_present=True)
    assert replay == b"\x00exact\xff"
    store.compare_and_swap(1, deferred)
    with pytest.raises(CacheConflict):
        store.compare_and_swap(1, deferred)

    pending, replay = deferred.defer(2, boundary_present=False)
    assert replay is None
    assert pending.reconciliation_pending is True
    assert pending.frozen == deferred.frozen
    rebuilt = pending.rebuild_full(3, "message:99", b"complete")
    assert rebuilt.deferred_passes == 0
    reset = rebuilt.with_durability(4, Durability.PROCESS)
    assert reset.frozen is None

    catalog = ModelCatalog.from_wire(
        {
            "providers": [
                {
                    "id": "centra",
                    "region": "local",
                    "models": [
                        {
                            "id": "reasoner",
                            "input_price": "0.0000000025",
                            "output_price": 0,
                            "future": {"supported": True},
                        },
                        {"id": "unpriced"},
                    ],
                }
            ]
        }
    )
    priced, unpriced = catalog.providers[0].models
    assert priced.input_price.nanodollars_per_million_tokens == 2
    assert priced.output_price.nanodollars_per_million_tokens == 0
    assert unpriced.input_price is None
    assert priced.raw["future"] == {"supported": True}
    assert catalog.providers[0].raw["region"] == "local"

    usage_wire = {
        "provider_id": "centra",
        "windows": [
            {
                "kind": "primary",
                "model_id": None,
                "raw_percent_basis_points": 0,
                "effective_percent_basis_points": None,
                "reset_at_ms": None,
                "regeneration": {
                    "amount_basis_points": 125,
                    "interval_seconds": 60,
                },
            }
        ],
        "breakdowns": [{"category": "summary", "used": 8, "limit": None}],
        "balances": [
            {
                "basis": "granted",
                "amount": {"minor_units": 0, "exponent": 2, "currency": "USD"},
            }
        ],
        "saved_credits": None,
        "account": {
            "label": "team",
            "subject": "acct-1",
            "provenance": "provider_verified",
        },
        "observed_at_ms": 42,
        "stale_after_ms": 60,
        "error": {
            "code": "RATE_LIMIT",
            "message": "temporarily unavailable",
            "retryable": True,
        },
        "future": "kept",
    }
    usage = ProviderUsage.from_wire(json.loads(json.dumps(usage_wire)))
    assert usage.windows[0].raw_percent_basis_points == 0
    assert usage.windows[0].effective_percent_basis_points is None
    assert usage.error["code"] == "RATE_LIMIT"
    assert usage.account.provenance == "provider_verified"
    assert usage.raw == {"future": "kept"}
