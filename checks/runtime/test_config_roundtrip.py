"""Tests that AppConfig.save() preserves all dataclass fields.

Regression test for the bug where to_dict() omitted inbox and skills —
causing save() to silently drop them from config.json, and for the dual
bug-class where a field present in to_dict() but missing from load()'s
field-by-field mapping silently reverts to its default on every reload
(and the next save() then wipes the user's value from the file).
"""

import json
from dataclasses import fields, is_dataclass
from unittest.mock import patch

import pytest

from gideon.core.config.loader import (
    AppConfig,
    ProjectionRuleConfig,
    SkillCatalogConfig,
)


@pytest.fixture()
def cfg_file(tmp_path):
    """Redirect config_path() to a temp file for isolation."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("gideon.core.config.loader.config_path", return_value=p):
        yield p


def test_to_dict_includes_all_dataclass_fields():
    """Every field on AppConfig must appear in to_dict() output."""
    cfg = AppConfig()
    d = cfg.to_dict()
    for f in fields(AppConfig):
        assert f.name in d, f"to_dict() missing field: {f.name}"


def test_save_load_roundtrip_inbox(cfg_file):
    """Inbox config must survive a save/load cycle."""
    cfg = AppConfig()
    cfg.inbox.enabled = True
    cfg.inbox.poll_interval_seconds = 30
    cfg.inbox.style_rules = ["never commit to dates"]
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["inbox"]["enabled"] is True
    assert raw["inbox"]["poll_interval_seconds"] == 30
    assert raw["inbox"]["style_rules"] == ["never commit to dates"]


def test_save_load_roundtrip_skills(cfg_file):
    """Skills config must survive a save/load cycle."""
    cfg = AppConfig()
    cfg.skills.max_triggered = 5
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["skills"]["max_triggered"] == 5


def test_cli_config_set_preserves_unmodeled_document_sections(cfg_file):
    """A keyed CLI edit merges modeled values into the raw config document."""
    from argparse import Namespace

    from gideon.interfaces.cli.config import _config_cmd

    opaque = {
        "providers": {"anthropic": {"api_key": "sk-keep"}},
        "use_cases": {"chat": "anthropic:claude-sonnet"},
        "slack": {"bot_token": "xoxb-keep"},
        "meta": {"format": 7, "createdBy": "setup"},
    }
    cfg_file.write_text(json.dumps(opaque), encoding="utf-8")

    _config_cmd(
        Namespace(
            config_action="set",
            file=None,
            key="agent.bot_name",
            value="Round Trip",
        )
    )

    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    for section, value in opaque.items():
        assert written[section] == value
    assert written["agent"]["bot_name"] == "Round Trip"


def test_save_load_roundtrip_companion(cfg_file):
    """Companion config (CA-4) must survive a save/load cycle to disk AND back through load()."""
    cfg = AppConfig()
    cfg.companion.discovery_enabled = True
    cfg.companion.instance_name = "Living room Mac"
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["companion"]["discovery_enabled"] is True
    assert raw["companion"]["instance_name"] == "Living room Mac"

    loaded = AppConfig.load()
    assert loaded.companion.discovery_enabled is True
    assert loaded.companion.instance_name == "Living room Mac"


def test_companion_fields_in_editable_allowlist():
    """CA-4: both companion fields are PATCH-editable (the write path of the round-trip)."""
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG.get("companion.discovery_enabled") == {"type": "bool"}
    assert _EDITABLE_CONFIG.get("companion.instance_name", {}).get("type") == "str"


def test_companion_discovery_defaults_off():
    """Announcing a service on the LAN is an opt-in — discovery must default OFF."""
    assert AppConfig().companion.discovery_enabled is False


def test_save_load_roundtrip_local_models(cfg_file):
    """Local-model knobs (LMMV-5) survive a save/load cycle to disk AND back."""
    cfg = AppConfig()
    cfg.local_models.pressure_warn_pct = 70
    cfg.local_models.sidecar_restart_max = 5
    cfg.local_models.memory_reserve_gb = 6.5
    cfg.local_models.hide_unrunnable_models = False
    cfg.local_models.hf_whoami_ttl_secs = 120
    cfg.local_models.selftest_timeout_secs = 90
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["local_models"] == {
        "pressure_warn_pct": 70,
        "sidecar_restart_max": 5,
        "memory_reserve_gb": 6.5,
        "hide_unrunnable_models": False,
        "hf_whoami_ttl_secs": 120,
        "selftest_timeout_secs": 90,
    }

    loaded = AppConfig.load()
    assert loaded.local_models.pressure_warn_pct == 70
    assert loaded.local_models.sidecar_restart_max == 5
    assert loaded.local_models.memory_reserve_gb == 6.5
    assert loaded.local_models.hide_unrunnable_models is False
    assert loaded.local_models.hf_whoami_ttl_secs == 120
    assert loaded.local_models.selftest_timeout_secs == 90


def test_local_models_fields_in_editable_allowlist():
    """LMMV-5/LMMV-8: every knob is PATCH-editable (the write path of the round-trip)."""
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["local_models.pressure_warn_pct"] == {
        "type": "int",
        "min": 1,
        "max": 100,
    }
    assert _EDITABLE_CONFIG["local_models.sidecar_restart_max"]["type"] == "int"
    assert _EDITABLE_CONFIG["local_models.memory_reserve_gb"] == {
        "type": "float",
        "min": 0.0,
        "max": 64.0,
    }
    assert _EDITABLE_CONFIG["local_models.hide_unrunnable_models"] == {"type": "bool"}
    assert _EDITABLE_CONFIG["local_models.hf_whoami_ttl_secs"] == {
        "type": "int",
        "min": 0,
        "max": 86_400,
    }
    assert _EDITABLE_CONFIG["local_models.selftest_timeout_secs"] == {
        "type": "int",
        "min": 1,
        "max": 600,
    }


def test_the_fit_reserve_defaults_to_three_gb_and_the_filter_defaults_on():
    """LMMV-8: the shipped defaults the fit readers fall back to must be the REAL ones."""
    cfg = AppConfig()
    assert cfg.local_models.memory_reserve_gb == 3.0
    assert cfg.local_models.hide_unrunnable_models is True


def test_relevance_reranker_defaults_off_and_roundtrips(cfg_file):
    """The heavyweight ranking stage is opt-in and all of its bounds survive disk."""
    cfg = AppConfig()
    assert cfg.knowledge.reranker_enabled is False
    assert cfg.knowledge.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert cfg.knowledge.reranker_max_candidates == 32

    cfg.knowledge.reranker_enabled = True
    cfg.knowledge.reranker_model = "local/reranker-v2"
    cfg.knowledge.reranker_max_candidates = 17
    cfg.save()

    loaded = AppConfig.load().knowledge
    assert loaded.reranker_enabled is True
    assert loaded.reranker_model == "local/reranker-v2"
    assert loaded.reranker_max_candidates == 17


def test_relevance_reranker_fields_are_patch_editable():
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["knowledge.reranker_enabled"] == {"type": "bool"}
    assert _EDITABLE_CONFIG["knowledge.reranker_model"] == {
        "type": "str",
        "max_len": 256,
    }
    assert _EDITABLE_CONFIG["knowledge.reranker_max_candidates"] == {
        "type": "int",
        "min": 1,
        "max": 128,
    }


def test_the_fit_readers_read_the_configured_values_not_their_fallbacks(cfg_file):
    """LMMV-8's fifth round-trip point: ``fit``'s two readers see a WRITTEN value.

    Both helpers fall back on any exception, so a missing field would make them look
    healthy while reporting the shipped default forever. Writing a value no fallback
    could produce is what separates "wired" from "silently defaulting".
    """
    from gideon.integrations.local_models import fit

    assert fit.configured_reserve_gb() == 3.0
    assert fit.hide_unrunnable_default() is True

    cfg_file.write_text(
        json.dumps(
            {
                "local_models": {
                    "memory_reserve_gb": 11.25,
                    "hide_unrunnable_models": False,
                }
            }
        ),
        encoding="utf-8",
    )
    assert fit.configured_reserve_gb() == 11.25
    assert fit.hide_unrunnable_default() is False


@pytest.mark.asyncio
async def test_an_out_of_range_reserve_is_rejected_by_patch_not_clamped(cfg_file):
    """A reserve edit outside 0-64 GB gets the normal typed error, never a quiet clamp.

    A clamp here would be the worst outcome: the PATCH reports success while the stored
    number differs from the one the user typed, and every later fit verdict is computed
    from a budget they never chose.
    """
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.interfaces.dashboard.handlers import api_gideon_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)

    async with TestClient(TestServer(app)) as client:
        for bad in (-1.0, 65.0):
            resp = await client.patch(
                "/api/config/gideon",
                json={"path": "local_models.memory_reserve_gb", "value": bad},
            )
            assert resp.status == 400
            assert "between 0.0 and 64.0" in json.dumps(await resp.json())
        assert AppConfig.load().local_models.memory_reserve_gb == 3.0

        resp = await client.patch(
            "/api/config/gideon",
            json={"path": "local_models.memory_reserve_gb", "value": 5.5},
        )
        assert resp.status == 200
        assert AppConfig.load().local_models.memory_reserve_gb == 5.5


def test_a_nonsense_reserve_in_config_json_is_clamped_to_the_same_window(cfg_file):
    """A hand-edited config.json still loads: the read path clamps what PATCH refuses."""
    cfg_file.write_text(
        json.dumps(
            {"local_models": {"memory_reserve_gb": -9, "hide_unrunnable_models": True}}
        ),
        encoding="utf-8",
    )
    assert AppConfig.load().local_models.memory_reserve_gb == 0.0

    cfg_file.write_text(
        json.dumps({"local_models": {"memory_reserve_gb": 4096}}), encoding="utf-8"
    )
    assert AppConfig.load().local_models.memory_reserve_gb == 64.0


def test_a_nonsense_pressure_threshold_is_clamped_to_a_real_percentage(cfg_file):
    """A threshold of 0 would warn forever and 900 could never warn — both read as broken."""
    cfg_file.write_text(
        json.dumps(
            {"local_models": {"pressure_warn_pct": 900, "sidecar_restart_max": -4}}
        ),
        encoding="utf-8",
    )
    loaded = AppConfig.load()
    assert loaded.local_models.pressure_warn_pct == 100
    assert loaded.local_models.sidecar_restart_max == 0


_SECTIONS = [
    "agent",
    "sandbox",
    "session",
    "loops",
    "memory",
    "dashboard",
    "inbox",
    "tools",
    "skills",
    "workflows",
    "learning",
    "security",
    "guardrails",
    "resilience",
    "evals",
    "packs",
    "companion",
    "local_models",
    "proactive",
    "apps",
    "updates",
]

_SPECIAL = {
    ("agent", "approval_mode"): "trust_reads",
    ("agent", "sandbox"): "off",
    ("agent", "log_level"): "DEBUG",
    ("agent", "provider"): "acp:claude-code",
    ("agent", "bot_name"): "TestBot",
    ("agent", "soft_stop_budget_secs"): 12.5,
    ("dashboard", "widget_density"): "less",
    ("dashboard", "stream_reveal"): "immediate",
    ("dashboard", "terminal"): {"enabled": False, "persist": True},
    ("dashboard", "dashboard_layout"): {"widgets": [], "v": 1},
    ("inbox", "poll_interval_seconds"): 90,
    # loops.judge_use_case is constrained to the use-case vocabulary (WF2LOO-17) — a
    # generated "reasoning-x" would (correctly) be refused by load() and collapse back to
    # `reasoning`. `code_tools` is a real non-default axis that proves the field
    # round-trips; `loops` deliberately is NOT used here, since a fixture should not model
    # "the judge is back on the worker's binding" as the normal case.
    ("loops", "judge_use_case"): "code_tools",
    ("memory", "push_min_confidence"): 0.55,
    ("memory", "vault_mode"): "two_way",
    ("skills", "auto_similarity_threshold"): 0.5,
    ("workflows", "surface_mode_default"): "suggest",
    ("workflows", "match_threshold"): 0.75,
    # workspace_default_mode is enum-constrained (scratch|worktree|in_place|container) — a
    # generated "scratch-x" would (correctly) fall back to `scratch`. `worktree` is the real
    # non-default that proves the field round-trips; `in_place` deliberately is NOT used here,
    # since a test fixture should not be the thing that models "isolation off" as normal.
    ("workflows", "workspace_default_mode"): "worktree",
    ("tools", "projection_rules"): [
        ProjectionRuleConfig(name="t", match_regex="^x", strategy="log")
    ],
    ("packs", "skill_catalogs"): [
        SkillCatalogConfig(
            name="taps", url="https://example.com/index.json", kind="index"
        )
    ],
    ("tools", "group_defaults"): {"background": ["core", "memory"]},
    ("guardrails", "scan_mode"): "block",
    ("resilience", "mid_turn_policy"): "cancel_and_replace",
    # learning.identity_report_cadence is enum-constrained (LV-4: monthly|weekly|off) — the
    # generated "monthly-x" is refused by load() and falls back to the default, which this
    # test correctly reported as a dropped field. `weekly` is the non-default that round-trips;
    # `off` is deliberately not used here, since a fixture should not model "the periodic report
    # is switched off" as the normal case.
    ("learning", "identity_report_cadence"): "weekly",
    ("security", "autonomy_denylist"): [
        {"paths": ["~/.ssh/**"], "actions": ["credential-read"], "verdict": "block"}
    ],
    ("updates", "channel"): "beta",
    ("updates", "auto"): "staged",
}


def _non_default(section: str, name: str, default):
    """Produce a valid value that differs from *default*."""
    if (section, name) in _SPECIAL:
        return _SPECIAL[(section, name)]
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 7
    if isinstance(default, float):
        return default + 1.5
    if isinstance(default, str):
        return f"{default}-x" if default else "test-value"
    if isinstance(default, list):
        return list(default) + ["extra-item"]
    raise AssertionError(
        f"no non-default rule for {section}.{name} ({type(default).__name__}) — "
        f"add a _SPECIAL entry"
    )


def _mutate_leaves(section: str, obj, prefix: str = "") -> dict:
    """Set every leaf of a section dataclass to a non-default value.

    Returns {dotted_path: expected_value} for later comparison. Recurses into
    nested dataclasses (e.g. security.egress).
    """
    expected: dict = {}
    for f in fields(obj):
        default = getattr(obj, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(default) and not isinstance(default, type):
            expected.update(_mutate_leaves(section, default, prefix=f"{path}."))
            continue
        value = _non_default(section, path, default)
        assert value != default, f"{section}.{path}: test value equals default"
        setattr(obj, f.name, value)
        expected[path] = value
    return expected


def _read_leaf(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def test_every_leaf_field_survives_save_load(cfg_file):
    """save() → load() must return every leaf field unchanged."""
    cfg = AppConfig()
    expected: dict[str, dict] = {}
    for section in _SECTIONS:
        expected[section] = _mutate_leaves(section, getattr(cfg, section))
    cfg.auto_update = False
    cfg.timezone = "Europe/Berlin"
    cfg.snapshot_dir = "test-value"
    cfg.observe_max_messages = 207
    cfg.observe_ttl_hours = 169.5
    cfg.save()

    loaded = AppConfig.load()

    diffs: list[str] = []
    for section, leaves in expected.items():
        for dotted, want in leaves.items():
            got = _read_leaf(getattr(loaded, section), dotted)
            if got != want:
                diffs.append(f"{section}.{dotted}: saved {want!r} but loaded {got!r}")
    for name, want in [
        ("auto_update", False),
        ("timezone", "Europe/Berlin"),
        ("snapshot_dir", "test-value"),
        ("observe_max_messages", 207),
        ("observe_ttl_hours", 169.5),
    ]:
        got = getattr(loaded, name)
        if got != want:
            diffs.append(f"{name}: saved {want!r} but loaded {got!r}")
    assert not diffs, "load() drops saved fields:\n" + "\n".join(diffs)


def test_evals_editable_allowlist_excludes_the_capture_flag():
    """EVALUATION-SUBSTRATE §10 — the runtime-editable evals subset is in the PATCH
    allowlist, but the privacy-sensitive input-capture flag is deliberately NOT
    (mirroring external_access.mcp.allow_remote's exclusion)."""
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    assert "evals.enabled" in _EDITABLE_CONFIG
    assert "evals.study_default_k" in _EDITABLE_CONFIG
    assert "evals.judge_agreement_floor" in _EDITABLE_CONFIG
    assert "evals.ablation_cadence_days" in _EDITABLE_CONFIG
    assert "evals.default_budget_usd" in _EDITABLE_CONFIG
    assert "evals.bakeoff_capture_enabled" not in _EDITABLE_CONFIG


def test_load_fallbacks_match_dataclass_defaults(cfg_file):
    """An empty config section must load exactly the dataclass defaults.

    Guards the default-drift class (memory.auto_promote_every_n was 10 in the
    dataclass but 5 in load()'s .get() fallback): loading {} must equal
    constructing AppConfig() for every leaf.
    """
    loaded = AppConfig.load()
    pristine = AppConfig()
    diffs: list[str] = []
    for section in _SECTIONS:
        for f in fields(getattr(pristine, section)):
            got = getattr(getattr(loaded, section), f.name)
            want = getattr(getattr(pristine, section), f.name)
            if got != want:
                diffs.append(
                    f"{section}.{f.name}: dataclass default {want!r} "
                    f"but empty-config load gives {got!r}"
                )
    assert not diffs, "load() fallback drift vs dataclass defaults:\n" + "\n".join(
        diffs
    )


def test_updates_defaults_are_release_tracking_and_notify_only():
    """RUM-1: the shipped defaults — stable channel, no pin, notify-only, checks on 12h."""
    u = AppConfig().updates
    assert u.channel == "stable"
    assert u.pin == ""
    assert u.auto == "off"
    assert u.check_enabled is True
    assert u.check_interval_hours == 12
    assert u.last_version == ""


def test_updates_fields_in_editable_allowlist():
    """RUM-1: every field is PATCH-editable (the write path of the round-trip)."""
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["updates.channel"] == {
        "type": "enum",
        "values": ["stable", "beta", "nightly"],
    }
    assert _EDITABLE_CONFIG["updates.auto"] == {
        "type": "enum",
        "values": ["off", "staged"],
    }
    assert _EDITABLE_CONFIG["updates.check_enabled"] == {"type": "bool"}
    assert _EDITABLE_CONFIG["updates.check_interval_hours"] == {
        "type": "int",
        "min": 1,
        "max": 168,
    }
    assert _EDITABLE_CONFIG["updates.pin"]["type"] == "str"
    assert _EDITABLE_CONFIG["updates.last_version"]["type"] == "str"


def test_legacy_auto_update_true_maps_to_staged_on_stable(cfg_file):
    """RUM-1 backfill: a home with legacy `auto_update=true` (and no `updates` block)
    loads to `updates.auto="staged"` + `channel="stable"` — the existing unattended-update
    git user stops riding raw main and starts riding stable release tags."""
    cfg_file.write_text(json.dumps({"auto_update": True}), encoding="utf-8")
    u = AppConfig.load().updates
    assert u.auto == "staged"
    assert u.channel == "stable"


def test_legacy_auto_update_false_maps_to_off(cfg_file):
    """RUM-1 backfill (known-false case): `auto_update=false` maps to notify-only."""
    cfg_file.write_text(json.dumps({"auto_update": False}), encoding="utf-8")
    assert AppConfig.load().updates.auto == "off"


def test_legacy_update_dev_mode_true_maps_to_nightly(cfg_file):
    """RUM-1 backfill: a home with legacy `dashboard.update_dev_mode=true` loads to
    `channel="nightly"` — the git-only track-main opt-in becomes the nightly channel."""
    cfg_file.write_text(
        json.dumps({"dashboard": {"update_dev_mode": True}}), encoding="utf-8"
    )
    assert AppConfig.load().updates.channel == "nightly"


def test_legacy_update_dev_mode_false_stays_stable(cfg_file):
    """RUM-1 backfill (known-false case): dev-mode off leaves the default stable channel."""
    cfg_file.write_text(
        json.dumps({"dashboard": {"update_dev_mode": False}}), encoding="utf-8"
    )
    assert AppConfig.load().updates.channel == "stable"


def test_explicit_updates_block_wins_over_legacy_flags(cfg_file):
    """RUM-1: an explicit `updates` field always wins over the legacy source, so the
    backfill is a one-time floor and never overrides a chosen value. A backwards mapping
    (legacy overriding the block) would read auto="staged"/channel="nightly" here."""
    cfg_file.write_text(
        json.dumps(
            {
                "auto_update": True,
                "dashboard": {"update_dev_mode": True},
                "updates": {"auto": "off", "channel": "stable"},
            }
        ),
        encoding="utf-8",
    )
    u = AppConfig.load().updates
    assert u.auto == "off"
    assert u.channel == "stable"


def test_every_apps_field_is_patchable_or_has_a_write_path():
    """The wiring point this file CANNOT see: the PATCH allowlist.

    The five points a config field must reach are dataclass+_meta, load(), to_dict(), a
    write path, and (if user-facing) a control. The rails above cover the first three, so a
    field with no `_EDITABLE_CONFIG` entry leaves this file fully green while the Settings
    toggle 400s. `apps.*` is user-facing config with no dedicated PUT, so every field in the
    section must be in the allowlist."""
    from gideon.core.config.loader import AppsConfig
    from gideon.interfaces.dashboard.handlers.core import _EDITABLE_CONFIG

    missing = [
        f.name for f in fields(AppsConfig) if f"apps.{f.name}" not in _EDITABLE_CONFIG
    ]
    assert not missing, f"apps config fields with no PATCH write path: {missing}"
    assert _EDITABLE_CONFIG["apps.registry_source_enabled"]["type"] == "bool"
