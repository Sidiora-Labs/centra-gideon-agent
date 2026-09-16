import json

import pytest

from gideon.operations import seed as seeds
from gideon.operations import seed_local_model as local


def files(path):
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def test_actual_fixture_copy_is_identical_and_replace_preserves_admission(
    tmp_path, monkeypatch
):
    home = tmp_path / "seeded"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    source = seeds.FixtureSelection("demo-home").resolve()
    seeds.seed("demo-home")
    assert files(home) == files(source)
    (home / "extra").write_text("retain on refusal")
    before = files(home)
    with pytest.raises(seeds.SeedError) as rejected:
        seeds.seed("empty")
    assert rejected.value.rail == seeds.SeedError.RAIL_NON_EMPTY
    assert files(home) == before
    seeds.seed("empty", replace=True)
    assert files(home) == files(seeds.FixtureSelection("empty").resolve())


def test_real_symlink_replacement_refuses_without_touching_target(
    tmp_path, monkeypatch
):
    home = tmp_path / "real"
    home.mkdir()
    (home / "keep").write_text("preserved")
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("GIDEON_HOME", str(alias))
    with pytest.raises(seeds.SeedError) as rejected:
        seeds.seed("empty", replace=True)
    assert rejected.value.rail == seeds.SeedError.RAIL_SYMLINK_REPLACE
    assert alias.is_symlink() and (home / "keep").read_text() == "preserved"


def test_actual_provider_and_binding_writes_keep_settings_and_early_existing_exit(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"custom": {"retained": True}, "providers": []}))
    result = local.LocalBinding(
        "http://127.0.0.1:1", "llama3.2", "nomic-embed-text", None
    ).commit("llama3.2", "nomic-embed-text", None, True)
    assert result.wrote == ["config.json", "active_models.json"]
    assert "not pulled yet" in result.detail and result.ok
    data = json.loads(config.read_text())
    assert data["custom"] == {"retained": True}
    assert "credential" not in data["providers"][0]
    active = json.loads((tmp_path / "active_models.json").read_text())
    assert active == {
        "chat": ["Local Ollama:llama3.2"],
        "embedding": ["Local Ollama:nomic-embed-text"],
    }
    before = files(tmp_path)
    again = local.bind_local_model(endpoint="http://127.0.0.1:1", model="different")
    assert again.status == local.ALREADY_BOUND and again.wrote == []
    assert files(tmp_path) == before


def test_partial_binding_write_retains_existing_nontransactional_contract(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "active_models.json").mkdir()
    with pytest.raises(OSError):
        local.LocalBinding("http://127.0.0.1:1", "llama3.2", "", None).commit(
            "llama3.2", "", None, False
        )
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["providers"][0]["model"] == "llama3.2"
    assert (tmp_path / "active_models.json").is_dir()
