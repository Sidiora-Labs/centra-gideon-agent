from gideon.core.config import loader
from gideon.security.guardrails.budgets import SpendMeter


def test_unspecified_home_keeps_read_resolver_and_meter_isolated(monkeypatch):
    monkeypatch.delenv("GIDEON_HOME", raising=False)
    selected = loader.config_dir()
    assert loader.resolve_config_dir() == selected
    assert SpendMeter()._spend_path().parent == selected


def test_explicit_home_preserves_read_only_resolution(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    monkeypatch.setenv("GIDEON_HOME", str(selected))
    assert loader.resolve_config_dir() == selected
    assert SpendMeter()._spend_path().parent == selected
    assert not selected.exists()
