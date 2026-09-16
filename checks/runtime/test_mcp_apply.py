"""Tests for the MCP-config apply handlers — per-scope entry writes and name validation.

Covers ``_set_gideon_entry`` / ``_set_scope_entry`` (writing an MCP server
entry into the gideon or a global scope file), the ``/api/mcp/apply``
endpoint, and rejection of hostile server names (path traversal, argv/shell
injection, length cap).
"""

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web


def _make_request(body: dict) -> MagicMock:
    """Build a fake aiohttp request for the api_mcp_apply handler."""
    state = MagicMock()
    state._background_tasks = set()
    request = MagicMock(spec=web.Request)
    request.app = {"state": state}

    async def _json() -> dict:
        return body

    request.json = _json
    return request


class TestSetGideonEntry:
    def test_adds_entry_when_enabling_with_spec(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_gideon_entry("srv", enabled=True, spec={"command": "x"})
        assert action == "added"
        assert json.loads(mc_path.read_text())["mcpServers"]["srv"] == {"command": "x"}

    def test_disables_existing(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        mc_path.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}))
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_gideon_entry("srv", enabled=False)
        assert action == "disabled"
        assert json.loads(mc_path.read_text())["mcpServers"]["srv"]["disabled"] is True

    def test_enabling_disabled_removes_flag(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        mc_path.write_text(
            json.dumps({"mcpServers": {"srv": {"command": "x", "disabled": True}}})
        )
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_gideon_entry("srv", enabled=True)
        assert action == "enabled"
        assert "disabled" not in json.loads(mc_path.read_text())["mcpServers"]["srv"]

    def test_disabling_missing_with_spec_seeds_entry(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        action = mcp_mod._set_gideon_entry("srv", enabled=False, spec={"command": "x"})
        assert action == "disabled"
        entry = json.loads(mc_path.read_text())["mcpServers"]["srv"]
        assert entry == {"command": "x", "disabled": True}


class TestSetScopeEntry:
    def test_adds_when_enabling_absent(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "global_mcp.json"
        action = mcp_mod._set_scope_entry(
            cfg_path, "srv", enabled=True, spec={"command": "c"}
        )
        assert action == "added"
        assert json.loads(cfg_path.read_text())["mcpServers"]["srv"] == {"command": "c"}

    def test_removes_when_disabling_present(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "global_mcp.json"
        cfg_path.write_text(
            json.dumps(
                {"mcpServers": {"srv": {"command": "c"}, "other": {"command": "y"}}}
            )
        )
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=False)
        assert action == "removed"
        servers = json.loads(cfg_path.read_text())["mcpServers"]
        assert "srv" not in servers
        assert "other" in servers

    def test_enabling_already_present_noop(self, tmp_path):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "global_mcp.json"
        cfg_path.write_text(json.dumps({"mcpServers": {"srv": {"command": "c"}}}))
        action = mcp_mod._set_scope_entry(
            cfg_path, "srv", enabled=True, spec={"command": "c"}
        )
        assert action == "noop"

    def test_disabling_absent_noop(self, tmp_path):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "global_mcp.json"
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=False)
        assert action == "noop"

    def test_enabling_without_spec_missing(self, tmp_path, monkeypatch):
        """When no spec can be found anywhere, the helper returns missing_spec."""
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        cfg_path = tmp_path / "global_mcp.json"
        monkeypatch.setattr(mcp_mod, "_find_server_spec_anywhere", lambda name: None)
        action = mcp_mod._set_scope_entry(cfg_path, "srv", enabled=True)
        assert action == "missing_spec"
        assert not cfg_path.exists()


class TestApplyEndpoint:
    @pytest.mark.asyncio
    async def test_preservation_global_to_gideon(self, tmp_path, monkeypatch):
        """Turning agent config off when server was only in agent config copies to Gideon first."""  # noqa: E501
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        global_path = tmp_path / "global_mcp.json"
        cc_path = tmp_path / "cc_global.json"
        agent_path = tmp_path / "gideon_agent.json"

        global_path.write_text(
            json.dumps(
                {"mcpServers": {"generic-mcp": {"command": "slack", "args": []}}}
            )
        )
        agent_path.write_text(json.dumps({"mcpServers": {}}))

        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", global_path)
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", cc_path)
        monkeypatch.setattr(
            mcp_mod,
            "_find_server_spec_anywhere",
            lambda name: (
                {"command": "slack", "args": []} if name == "generic-mcp" else None
            ),
        )
        import gideon.engine.agent

        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", lambda: None)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request(
            {
                "changes": [
                    {
                        "name": "generic-mcp",
                        "gideon": True,
                        "globalMcp": False,
                        "ccGlobal": False,
                    }
                ]
            }
        )
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert body["applied"] == 1

        pc = json.loads(mc_path.read_text())
        assert "generic-mcp" in pc["mcpServers"]
        assert pc["mcpServers"]["generic-mcp"].get("disabled") is not True

        k = json.loads(global_path.read_text())
        assert "generic-mcp" not in k["mcpServers"]

    @pytest.mark.asyncio
    async def test_uninstall_removes_from_all_three(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        global_path = tmp_path / "global_mcp.json"
        cc_path = tmp_path / "cc_global.json"
        for p in (mc_path, global_path, cc_path):
            p.write_text(json.dumps({"mcpServers": {"foo": {"command": "f"}}}))

        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", global_path)
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", cc_path)
        monkeypatch.setattr(mcp_mod.shutil, "which", lambda _name: None)

        import gideon.engine.agent

        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", lambda: None)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request({"changes": [{"name": "foo", "uninstall": True}]})
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)
        assert body["ok"] is True

        for p in (mc_path, global_path, cc_path):
            data = json.loads(p.read_text())
            assert "foo" not in data["mcpServers"], f"foo still in {p}"

    @pytest.mark.asyncio
    async def test_calls_rebuild_agent_config_once(self, tmp_path, monkeypatch):
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        monkeypatch.setattr(
            mcp_mod, "_canonical_mcp_json", lambda: tmp_path / "mc.json"
        )
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", tmp_path / "global_mcp.json")
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", tmp_path / "cc.json")
        monkeypatch.setattr(mcp_mod.shutil, "which", lambda _name: None)

        import gideon.engine.agent

        rebuild = MagicMock()
        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", rebuild)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())
        monkeypatch.setattr(
            mcp_mod, "_find_server_spec_anywhere", lambda n: {"command": "x"}
        )

        request = _make_request(
            {
                "changes": [
                    {"name": "a", "gideon": True, "globalMcp": True, "ccGlobal": False},
                    {"name": "b", "gideon": True, "globalMcp": False, "ccGlobal": True},
                    {"name": "c", "uninstall": True},
                ]
            }
        )
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert body["applied"] == 3
        assert rebuild.call_count == 1
        assert body["rebuild"]["ok"] is True

    @pytest.mark.asyncio
    async def test_import_from_claude_code_copies_spec_into_gideon(
        self, tmp_path, monkeypatch
    ):
        """The Tools-page Import action (gideon=True + ccGlobal=True) copies a
        Claude-Code server's spec into ~/.gideon/mcp.json while leaving the
        Claude Code entry intact — so the native loop can run it."""
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "gideon.mcp.json"
        global_path = tmp_path / "global_mcp.json"
        cc_path = tmp_path / "cc_global.json"
        cc_path.write_text(
            json.dumps(
                {"mcpServers": {"cc-srv": {"command": "npx", "args": ["cc-mcp"]}}}
            )
        )

        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", global_path)
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", cc_path)

        import gideon.engine.agent

        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", lambda: None)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request(
            {
                "changes": [
                    {
                        "name": "cc-srv",
                        "gideon": True,
                        "globalMcp": False,
                        "ccGlobal": True,
                    },
                ]
            }
        )
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)
        assert body["ok"] is True

        # Gideon scope now owns a runnable copy of the spec.
        pc = json.loads(mc_path.read_text())["mcpServers"]
        assert pc["cc-srv"]["command"] == "npx"
        assert pc["cc-srv"].get("disabled") is not True
        cc = json.loads(cc_path.read_text())["mcpServers"]
        assert "cc-srv" in cc


class TestHostileNameRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_name",
        [
            "../../etc/passwd",
            "./local",
            "/abs/path",
            "-rf",
            "a b",
            "a\nb",
            "a;rm -rf /",
            "a|whoami",
            "$(echo pwn)",
            "`echo pwn`",
            "a\x00b",
            "",
            "a" * 200,
            "foo/../bar",
        ],
    )
    async def test_rejects_hostile_names(self, tmp_path, monkeypatch, bad_name):
        """Each hostile name should short-circuit with ``error: invalid name``.

        The scope files must NOT be created/touched, and the handler must
        NOT call ``subprocess.run`` or mutate ``rebuild_agent_config``.
        """
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "mc.json"
        global_path = tmp_path / "global_mcp.json"
        cc_path = tmp_path / "cc.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", global_path)
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", cc_path)
        monkeypatch.setattr(
            mcp_mod.shutil,
            "which",
            lambda _name: pytest.fail(
                "shutil.which must not be reached for invalid name"
            ),
        )

        import gideon.engine.agent

        rebuild = MagicMock()
        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", rebuild)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request({"changes": [{"name": bad_name, "gideon": True}]})
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)

        assert body["ok"] is True
        assert len(body["results"]) == 1
        err = body["results"][0].get("error", "")
        assert err in {
            "invalid name",
            "empty name",
        }, f"expected invalid/empty name error for {bad_name!r}, got {body['results'][0]}"
        assert not mc_path.exists()
        assert not global_path.exists()
        assert not cc_path.exists()

    @pytest.mark.asyncio
    async def test_hostile_tool_name_filtered_server_kept(self, tmp_path, monkeypatch):
        """Invalid tool-override names are dropped; the server's scope
        changes still apply, and the handler reports ``tools_rejected``.
        """
        from gideon.interfaces.dashboard.handlers import mcp as mcp_mod

        mc_path = tmp_path / "mc.json"
        monkeypatch.setattr(mcp_mod, "_canonical_mcp_json", lambda: mc_path)
        monkeypatch.setattr(mcp_mod, "_GLOBAL_MCP_JSON", tmp_path / "global_mcp.json")
        monkeypatch.setattr(mcp_mod, "_CC_GLOBAL_JSON", tmp_path / "cc.json")
        monkeypatch.setattr(
            mcp_mod, "_find_server_spec_anywhere", lambda n: {"command": "x"}
        )
        monkeypatch.setattr(mcp_mod.shutil, "which", lambda _n: None)

        import gideon.engine.agent

        monkeypatch.setattr(gideon.engine.agent, "rebuild_agent_config", lambda: None)

        class _NoLock:
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass

        monkeypatch.setattr(mcp_mod, "_get_mcp_lock", lambda: _NoLock())

        request = _make_request(
            {
                "changes": [
                    {
                        "name": "generic-mcp",
                        "gideon": True,
                        "toolOverrides": {
                            "../evil": False,
                            "legit-tool": False,
                        },
                    }
                ]
            }
        )
        resp = await mcp_mod.api_mcp_apply(request)
        body = json.loads(resp.body)

        assert body["ok"] is True
        actions = body["results"][0]["actions"]
        assert actions.get("tools_rejected") == ["../evil"]
        assert "tools" in actions
        assert "legit-tool" in actions["tools"]
