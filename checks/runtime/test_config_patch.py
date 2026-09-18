"""Tests for PATCH /api/config/gideon validators (enum, int, float, bool, str)."""

import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _make_app() -> web.Application:
    from gideon.interfaces.dashboard.handlers import api_gideon_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)
    return app


def _seed_config() -> dict:
    return {
        "agents": {
            "gideon": {
                "provider_agent": "gideon",
                "workspace": "default",
                "memory_store": "default",
            }
        },
        "default_agent": "gideon",
        "session": {"pool_agent": "", "timeout_secs": 3600, "autocompact_pct": 50.0},
        "agent": {"approval_mode": "auto", "sandbox": "auto"},
        "auto_update": False,
    }


@pytest.fixture
def tmp_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_seed_config()), encoding="utf-8")
    with patch("gideon.core.config.loader.config_path", return_value=cfg_path):
        yield cfg_path


async def _patch(client, path, value):
    return await client.patch("/api/config/gideon", json={"path": path, "value": value})


class TestPatchGeneral:
    @pytest.mark.asyncio
    async def test_unknown_field_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "nonexistent.field", "x")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_json_body_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch(
                "/api/config/gideon",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400


class TestEnumValidator:
    @pytest.mark.asyncio
    async def test_valid_enum_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", "interactive")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_invalid_enum_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", "bogus")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_enum_wrong_type_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", 123)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_nested_3part_path_writes_correctly(self, tmp_config) -> None:
        """P25: `dashboard.terminal.persist` is a 3-part nested path — the writer must
        create the intermediate `dashboard`/`terminal` objects and set the leaf, NOT
        clobber `data['dashboard']` with the bool. Guards the nested-path writer."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "dashboard.terminal.persist", True)
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text(encoding="utf-8"))
            assert saved["dashboard"]["terminal"]["persist"] is True
            assert isinstance(saved["dashboard"], dict)

    @pytest.mark.asyncio
    async def test_nested_3part_preserves_sibling_keys(self, tmp_config) -> None:
        """Setting the nested leaf must not drop a pre-existing sibling under the same
        parent (e.g. dashboard.terminal.enabled stays when persist is added)."""
        import json as _json

        data = _json.loads(tmp_config.read_text(encoding="utf-8"))
        data.setdefault("dashboard", {}).setdefault("terminal", {})["enabled"] = True
        tmp_config.write_text(_json.dumps(data), encoding="utf-8")
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "dashboard.terminal.persist", True)
            assert resp.status == 200
            saved = _json.loads(tmp_config.read_text(encoding="utf-8"))
            assert saved["dashboard"]["terminal"]["enabled"] is True
            assert saved["dashboard"]["terminal"]["persist"] is True


class TestIntValidator:
    @pytest.mark.asyncio
    async def test_valid_int_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", 120)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_int_below_min_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", -1)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_int_above_max_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", 100000)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_int_non_numeric_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.timeout_secs", "abc")
            assert resp.status == 400


class TestFloatValidator:
    @pytest.mark.asyncio
    async def test_valid_float_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 25.0)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_float_below_min_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 1.0)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_above_max_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", 95.0)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_nan_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", float("nan"))
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_float_non_numeric_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.autocompact_pct", "abc")
            assert resp.status == 400


class TestBoolValidator:
    @pytest.mark.asyncio
    async def test_valid_bool_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "auto_update", True)
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_bool_non_bool_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "auto_update", "true")
            assert resp.status == 400


class TestStrValidator:
    @pytest.mark.asyncio
    async def test_valid_agent_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "gideon")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_empty_string_passes(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_non_string_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", 123)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_exceeds_max_len_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "a" * 257)
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_400(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "session.pool_agent", "nonexistent")
            assert resp.status == 400
            data = await resp.json()
            assert "invalid value" in data["error"]


class TestEgressValidator:
    @pytest.mark.asyncio
    async def test_valid_egress_persists(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {
                    "allow_hosts": ["nas.local"],
                    "deny_hosts": ["evil.com"],
                    "allow_private": True,
                },
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert saved == {
                "allow_hosts": ["nas.local"],
                "deny_hosts": ["evil.com"],
                "allow_private": True,
            }

    @pytest.mark.asyncio
    async def test_rejects_url_host(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {
                    "allow_hosts": ["http://evil.com/x"],
                    "deny_hosts": [],
                    "allow_private": False,
                },
            )
            assert resp.status == 400
            assert "bare domain" in (await resp.json())["error"]

    @pytest.mark.asyncio
    async def test_rejects_non_dict(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "security.egress", ["not", "a", "dict"])
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_rejects_non_bool_private(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {"allow_hosts": [], "deny_hosts": [], "allow_private": "yes"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_strips_unknown_keys(self, tmp_config) -> None:
        """Only the three known keys are persisted — a stray field can't be smuggled in."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "security.egress",
                {
                    "allow_hosts": [],
                    "deny_hosts": [],
                    "allow_private": False,
                    "evil": "x",
                },
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["security"]["egress"]
            assert "evil" not in saved


class TestProjectionRulesValidator:
    def teardown_method(self):
        from gideon.integrations.tool_providers.projection import set_user_rules

        set_user_rules([])

    @pytest.mark.asyncio
    async def test_valid_rules_persist_and_apply_live(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {"name": "acme", "match_regex": r"^\[ACME\]", "strategy": "log"},
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"]
            assert saved[0]["name"] == "acme"
            assert saved[0]["match_regex"] == r"^\[ACME\]"
            assert saved[0]["strategy"] == "log"
        from gideon.integrations.tool_providers.projection import infer_content_type

        assert infer_content_type("[ACME] boot\nstep\n") == "log"

    @pytest.mark.asyncio
    async def test_rejects_invalid_regex(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "(", "strategy": "log"}],
            )
            assert resp.status == 400
            assert "regex" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_unknown_strategy(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "foo", "strategy": "nonsense"}],
            )
            assert resp.status == 400
            assert "strategy" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_non_list(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "tools.projection_rules", {"not": "a list"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_strips_unknown_keys(self, tmp_config) -> None:
        """Only allowlisted rule fields persist — a stray field can't be smuggled in."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {
                        "name": "acme",
                        "match_regex": "foo",
                        "strategy": "test",
                        "evil": "x",
                    },
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"][0]
            assert "evil" not in saved
            allowed = {
                "name",
                "match_regex",
                "strategy",
                "head",
                "tail",
                "keep",
                "skip",
                "count",
            }
            assert set(saved) <= allowed

    @pytest.mark.asyncio
    async def test_op_fields_validate_and_persist(self, tmp_config) -> None:
        """Rule ops v2 (§2.3): head/tail ints + keep/skip/count regexes round-trip;
        a bad op regex or negative count is rejected at the boundary."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(
                c,
                "tools.projection_rules",
                [
                    {
                        "name": "ops",
                        "match_regex": r"^\[SVC\]",
                        "strategy": "log",
                        "head": 5,
                        "tail": 3,
                        "skip": r"^DEBUG",
                        "count": r"^heartbeat",
                    },
                ],
            )
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())["tools"]["projection_rules"][0]
            assert saved["head"] == 5 and saved["tail"] == 3
            assert saved["skip"] == r"^DEBUG" and saved["count"] == r"^heartbeat"
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "ok", "strategy": "log", "keep": "("}],
            )
            assert resp.status == 400
            resp = await _patch(
                c,
                "tools.projection_rules",
                [{"name": "x", "match_regex": "ok", "strategy": "log", "head": -1}],
            )
            assert resp.status == 400


class TestEngagementRankingFlag:
    @pytest.mark.asyncio
    async def test_patch_writes_nested_inbox_flag(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "inbox.engagement_ranking_enabled", True)
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            assert saved["inbox"]["engagement_ranking_enabled"] is True

    @pytest.mark.asyncio
    async def test_patch_rejects_non_bool(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "inbox.engagement_ranking_enabled", "yes")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_half_life_float_bounds(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            assert (
                await _patch(c, "inbox.engagement_half_life_days", 6.5)
            ).status == 200
            assert (
                await _patch(c, "inbox.engagement_half_life_days", -1.0)
            ).status == 400
            assert (
                await _patch(c, "inbox.engagement_half_life_days", 999.0)
            ).status == 400

    def test_flag_loads_from_config_json_not_just_default(self, tmp_path) -> None:
        """The load-map leg: a value in config.json must actually reach AppConfig — the
        exact gap the two-maps footgun creates (field on the dataclass but absent from
        AppConfig.load → always the default)."""
        from gideon.core.config.loader import AppConfig

        cfg = _seed_config()
        cfg["inbox"] = {
            "enabled": True,
            "engagement_ranking_enabled": True,
            "engagement_half_life_days": 3.25,
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with patch("gideon.core.config.loader.config_path", return_value=p):
            loaded = AppConfig.load()
        assert loaded.inbox.engagement_ranking_enabled is True
        assert loaded.inbox.engagement_half_life_days == 3.25
        assert loaded.to_dict()["inbox"]["engagement_ranking_enabled"] is True


class TestBotNamePatch:
    @pytest.mark.asyncio
    async def test_sanitized_before_write(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", "**{Astra}** <script>")
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            assert saved["agent"]["bot_name"] == "Astra script"

    @pytest.mark.asyncio
    async def test_plain_name_passes_through(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.bot_name", "Astra")
            assert resp.status == 200
            saved = json.loads(tmp_config.read_text())
            assert saved["agent"]["bot_name"] == "Astra"

    @pytest.mark.asyncio
    async def test_over_50_chars_rejected(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            assert (await _patch(c, "agent.bot_name", "x" * 51)).status == 400


class TestAppsRegistrySource:
    """A real PATCH round-trip for the seeding flag: allowlisted → persisted → reloaded.

    The `_EDITABLE_CONFIG` entry is the wiring point `test_config_roundtrip.py` cannot see
    (it checks dataclass/load/to_dict only), and the toggle in Settings › Apps is useless
    without it — so it gets driven end to end here rather than asserted structurally."""

    @pytest.mark.asyncio
    async def test_patch_persists_and_reloads(self, tmp_config) -> None:
        from gideon.core.config.loader import AppConfig

        assert AppConfig.load().apps.registry_source_enabled is True
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "apps.registry_source_enabled", False)
            assert resp.status == 200, await resp.text()
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["apps"]["registry_source_enabled"] is False
        assert AppConfig.load().apps.registry_source_enabled is False

    @pytest.mark.asyncio
    async def test_patch_rejects_a_non_bool(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "apps.registry_source_enabled", "sure")
            assert resp.status == 400
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "apps" not in raw or "registry_source_enabled" not in raw.get("apps", {})


class TestLogLevelAppliesLive:
    @pytest.mark.asyncio
    async def test_patching_log_level_sets_the_live_logger(self, tmp_config) -> None:
        import logging

        lg = logging.getLogger("gideon")
        prior = lg.level
        try:
            lg.setLevel(logging.WARNING)
            async with TestClient(TestServer(_make_app())) as c:
                resp = await _patch(c, "agent.log_level", "DEBUG")
                assert resp.status == 200
            assert lg.level == logging.DEBUG
            saved = json.loads(tmp_config.read_text(encoding="utf-8"))
            assert saved["agent"]["log_level"] == "DEBUG"
        finally:
            lg.setLevel(prior)


def _make_app_with_state(state) -> web.Application:
    app = _make_app()
    app["state"] = state
    return app


class _FakeState:
    """Just the two members the yolo seam touches — enable/disable recording."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def enable_yolo(self, *, from_config: bool = False) -> None:
        self.calls.append(("enable", from_config))

    def disable_yolo(self) -> None:
        self.calls.append(("disable",))


class TestYoloAppliesLive:
    @pytest.mark.asyncio
    async def test_turning_yolo_off_revokes_the_bypass_immediately(
        self, tmp_config
    ) -> None:
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch(c, "agent.yolo", False)
            assert resp.status == 200
        assert ("disable",) in state.calls

    @pytest.mark.asyncio
    async def test_sel_down_fails_the_whole_patch_closed(self, tmp_config) -> None:
        state = _FakeState()
        with patch("gideon.security.sel.sel", side_effect=RuntimeError("sel down")):
            async with TestClient(TestServer(_make_app_with_state(state))) as c:
                resp = await _patch(c, "agent.yolo", True)
                assert resp.status == 500
        assert state.calls == []

    @pytest.mark.asyncio
    async def test_turning_yolo_on_applies_live_with_startup_semantics(
        self, tmp_config
    ) -> None:
        state = _FakeState()
        async with TestClient(TestServer(_make_app_with_state(state))) as c:
            resp = await _patch(c, "agent.yolo", True)
            assert resp.status == 200
        assert ("enable", True) in state.calls
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["agent"]["yolo"] is True

    @pytest.mark.asyncio
    async def test_no_state_does_not_break_the_config_write(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.yolo", True)
            assert resp.status == 200
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["agent"]["yolo"] is True


class TestSelectorErrors:
    """A malformed `path` is its own fault, distinct from an unknown field.

    All four used to collapse into one 400 reading `field not editable: <path>` — and a
    JSON list or object in `path` never even got that far: `_EDITABLE_CONFIG.get(<list>)`
    raised an unhandled `TypeError: unhashable type` and the route answered 500. So a
    caller that forgot the key, sent an empty one, or sent the wrong shape was told its
    field was not editable, which is advice for a different mistake.
    """

    @pytest.mark.asyncio
    async def test_a_missing_path_says_the_request_names_no_field(
        self, tmp_config
    ) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.patch("/api/config/gideon", json={"value": True})
            assert resp.status == 400
            err = (await resp.json())["error"]
            assert err["code"] == "config_path_required"
            assert "path" in err["message"]

    @pytest.mark.asyncio
    async def test_a_blank_path_is_its_own_code(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "", True)
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "config_path_blank"

    @pytest.mark.asyncio
    async def test_a_whitespace_only_path_is_blank_too(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "   ", True)
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "config_path_blank"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_path", [["agent", "yolo"], {"path": "agent.yolo"}, 7, True, None]
    )
    async def test_a_wrong_typed_path_is_a_400_not_a_500(
        self, tmp_config, bad_path
    ) -> None:
        """The list and dict cases are the ones that used to reach `dict.get` unhashable."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, bad_path, True)
            assert resp.status == 400
            err = (await resp.json())["error"]
            assert err["code"] == "config_path_type_invalid"
            assert type(bad_path).__name__ in err["message"]

    @pytest.mark.asyncio
    async def test_the_three_selector_codes_are_distinct(self, tmp_config) -> None:
        async with TestClient(TestServer(_make_app())) as c:
            missing = await c.patch("/api/config/gideon", json={"value": True})
            blank = await _patch(c, "", True)
            wrong = await _patch(c, ["agent.yolo"], True)
            codes = {(await r.json())["error"]["code"] for r in (missing, blank, wrong)}
        assert len(codes) == 3, f"selector faults collapsed into {codes}"

    @pytest.mark.asyncio
    async def test_an_unknown_field_keeps_the_existing_rejection(
        self, tmp_config
    ) -> None:
        """ac 21.2: a genuinely unknown field is still refused, and still by prose."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "nonexistent.field", "x")
            assert resp.status == 400
            assert (await resp.json())[
                "error"
            ] == "field not editable: nonexistent.field"

    @pytest.mark.asyncio
    async def test_a_valid_patch_still_writes(self, tmp_config) -> None:
        """Vacuity: the selector gate must not have turned PATCH into a refusal machine."""
        async with TestClient(TestServer(_make_app())) as c:
            resp = await _patch(c, "agent.approval_mode", "interactive")
            assert resp.status == 200
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["agent"]["approval_mode"] == "interactive"

    @pytest.mark.asyncio
    async def test_no_selector_fault_writes_anything(self, tmp_config) -> None:
        before = tmp_config.read_text(encoding="utf-8")
        async with TestClient(TestServer(_make_app())) as c:
            await c.patch("/api/config/gideon", json={"value": True})
            await _patch(c, "", True)
            await _patch(c, ["agent.yolo"], True)
        assert tmp_config.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_every_selector_code_is_registered(self, tmp_config) -> None:
        """The wire codes are a contract, so they carry a registry row."""
        from gideon.http_errors import HTTP_ERROR_CODES

        for code in (
            "config_path_required",
            "config_path_blank",
            "config_path_type_invalid",
        ):
            assert HTTP_ERROR_CODES[code].strip()


class TestCliSharesTheSelectorBoundary:
    """`gideon config set` reaches the SAME `_EDITABLE_CONFIG` allowlist.

    It cannot reproduce the wrong-typed fault (argparse hands it a string), and it
    already refuses an absent or blank key before any write — but it looks the key up in
    the dashboard's allowlist, so that lookup is exercised here with the shapes the API
    now rejects, to prove the shared table cannot raise on the CLI side either.
    """

    def _set(self, key, value):
        import argparse

        from gideon.interfaces.cli.config import _config_cmd

        return _config_cmd(
            argparse.Namespace(config_action="set", key=key, value=value, file=None)
        )

    def test_a_blank_key_is_refused_before_any_write(self, tmp_config) -> None:
        before = tmp_config.read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            self._set("", "true")
        assert exc.value.code == 1
        assert tmp_config.read_text(encoding="utf-8") == before

    def test_a_missing_value_is_refused_before_any_write(self, tmp_config) -> None:
        before = tmp_config.read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            self._set("agent.approval_mode", None)
        assert exc.value.code == 1
        assert tmp_config.read_text(encoding="utf-8") == before

    def test_the_shared_allowlist_lookup_never_raises_on_a_bad_key(self) -> None:
        from gideon.interfaces.cli.config import _editable_spec

        assert _editable_spec("agent.approval_mode") is not None
        for bad in ("", "   ", ["agent.yolo"], {"a": 1}, 7, None):
            assert _editable_spec(bad) is None  # type: ignore[arg-type]

    def test_an_unknown_key_still_exits_nonzero(self, tmp_config) -> None:
        with pytest.raises(SystemExit) as exc:
            self._set("nonexistent.field", "x")
        assert exc.value.code == 1
