"""Prices persist in real settings and retain unknown-versus-free semantics."""

import json

import pytest

import gideon.sdk.model
from gideon.engine.routing.rates import (
    UnitRate,
    adopt_prices_set_before,
    clear_rate,
    load_overlay,
    price_units,
    rate_entry,
    rates_view,
    set_rate,
)
from gideon.operations.pricing import price_row


@pytest.mark.parametrize(
    "unit,field,quantity,expected",
    [
        ("image", "per_image", 3, 0.6),
        ("second", "per_second", 10, 2),
        ("minute", "per_minute", 2.5, 0.5),
        ("character", "per_mchar", 1000000, 0.2),
    ],
)
def test_unit_conversion_and_config_transaction(
    tmp_path, unit, field, quantity, expected
):
    (tmp_path / "config.json").write_text(json.dumps({"agent": {"name": "retained"}}))
    set_rate("cloud:media", {"unit": unit, field: 0.2}, home=tmp_path)
    priced = price_units("cloud", "media", unit, quantity, home=tmp_path)
    assert priced.priced and priced.cost_usd == pytest.approx(expected)
    document = json.loads((tmp_path / "config.json").read_text())
    assert document["agent"]["name"] == "retained"
    assert document["model_prices"]["overrides"]["cloud:media"]["recorded"]
    assert (
        price_units(
            "cloud",
            "media",
            "image" if unit != "image" else "minute",
            quantity,
            home=tmp_path,
        ).cost_usd
        is None
    )
    clear_rate("cloud:media", home=tmp_path)
    assert not load_overlay(tmp_path)["rates"]


def test_image_cover_and_unknown():
    rate = UnitRate.from_obj(
        {
            "unit": "image",
            "tiers": [
                {"size": "1024x1024", "quality": "high", "per_image": 0.1},
                {"size": "2048x2048", "quality": "high", "per_image": 0.4},
            ],
            "default_size": "1024x1024",
            "default_quality": "high",
        },
        unit="image",
    )
    assert rate.cost(2) == 0.2
    assert rate.cost(2, size="1500x1500") == 0.8
    assert rate.cost(2, size="3000x3000") is None
    assert rate.cost(None) is None
    assert rate.cost(1, size="broken") is None
    assert UnitRate("minute", 0).cost(None) == 0


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_prices_rejected(value):
    with pytest.raises(ValueError):
        rate_entry({"key": "cloud:*", "unit": "minute", "per_minute": value})


def test_migration_preserves_settings_winner_and_removes_only_after_write(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "model_prices": {
                    "overrides": {"same": {"in_per_mtok": 9, "out_per_mtok": 9}}
                },
                "marker": True,
            }
        )
    )
    legacy = tmp_path / "model_rates.json"
    legacy.write_text(
        json.dumps(
            {
                "rates": {
                    "same": {"in_per_mtok": 1, "out_per_mtok": 2},
                    "new": {"unit": "minute", "per_minute": 0.1},
                }
            }
        )
    )
    assert adopt_prices_set_before(home=tmp_path)
    assert not legacy.exists()
    document = json.loads((tmp_path / "config.json").read_text())
    assert document["marker"] is True
    assert document["model_prices"]["overrides"]["same"]["in_per_mtok"] == 9
    assert document["model_prices"]["overrides"]["new"]["unit"] == "minute"
    assert not adopt_prices_set_before(home=tmp_path)


def test_unreadable_config_and_bad_migration_retained(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{bad")
    legacy = tmp_path / "model_rates.json"
    legacy.write_text(json.dumps({"rates": {"new": {"in_per_mtok": 1}}}))
    assert not adopt_prices_set_before(home=tmp_path)
    assert config.read_text() == "{bad" and legacy.exists()
    assert rates_view([], home=tmp_path)["unreadable"]


def test_reads_do_not_create_home(tmp_path, monkeypatch):
    from gideon.core.config import loader

    home = tmp_path / "missing"
    monkeypatch.setattr(loader, "resolve_config_dir", lambda: home)
    assert not load_overlay()["rates"]
    assert not home.exists()


def test_canonical_versions_do_not_match_unrelated_releases():
    assert price_row("claude-opus-4.80") is None
    assert price_row("claude-sonnet-4.5-new-release") is None
    assert price_row("claude-sonnet-4.5-20991231").key == "claude-sonnet-4.5"


def test_actual_configuration_load_save_preserves_override(tmp_path, monkeypatch):
    import gideon.core.config.loader as loader
    from gideon.core.config.loader import AppConfig

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(loader, "resolve_config_dir", lambda: tmp_path)
    AppConfig().save()
    set_rate("cloud:audio", {"unit": "minute", "per_minute": 0.3}, home=tmp_path)
    config = AppConfig.load()
    assert config.model_prices.overrides["cloud:audio"]["per_minute"] == 0.3
    config.save()
    assert load_overlay(tmp_path)["rates"]["cloud:audio"]["per_minute"] == 0.3
