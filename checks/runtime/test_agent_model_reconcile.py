"""Built-in / pinned agent model: auto-reconcile + editable (#53).

Two behaviors:
  * Runtime auto-reconcile — an agent pinning a model that's no longer in the
    active chat set falls back to the chat binding instead of handing a dead id
    to the client (``provider_bridge._reconcile_agent_model``).
  * Editable — reserved system agents stay locked EXCEPT their ``model`` field,
    so a user can swap which model a built-in agent runs on.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from gideon.extensions.providers import provider_bridge as pb


def test_reconcile_keeps_active_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "native:glm-5"})
    assert pb._reconcile_agent_model("glm-5") == "glm-5"


def test_reconcile_drops_stale_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    assert pb._reconcile_agent_model("glm-5") == ""


def test_reconcile_empty_passes_through(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    assert pb._reconcile_agent_model("") == ""


def test_reconcile_noop_when_no_active_models(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: set())
    assert pb._reconcile_agent_model("glm-5") == "glm-5"


def test_reconcile_accepts_qualified_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "myprov:glm-5"})
    assert pb._reconcile_agent_model("myprov:glm-5") == "myprov:glm-5"


def _use_cases_refs(monkeypatch, refs, known):
    """Patch the use_cases module that provider_bridge imports lazily."""
    import gideon.extensions.providers.use_cases as uc

    monkeypatch.setattr(uc, "active_model_refs", lambda use_case="chat": list(refs))
    monkeypatch.setattr(uc, "_known_provider_names", lambda: set(known))


def test_fallback_model_agrees_with_hinted_provider(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    monkeypatch.setattr(
        pb,
        "_active_chat_model_ids",
        lambda: {
            "global.anthropic.claude-opus-4-8",
            "Bedrock:global.anthropic.claude-opus-4-8",
            "glm-5.2",
            "Alibaba:glm-5.2",
        },
    )
    assert (
        pb._fallback_chat_model(provider_hint="Bedrock")
        == "global.anthropic.claude-opus-4-8"
    )


def test_fallback_model_hint_discriminates_per_provider(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        [
            "Bedrock:global.anthropic.claude-opus-4-8",
            "Alibaba:glm-5.2",
            "Anthropic:claude-opus-4-8",
        ],
        {"Bedrock", "Alibaba", "Anthropic"},
    )
    assert pb._fallback_chat_model(provider_hint="Alibaba") == "glm-5.2"
    assert pb._fallback_chat_model(provider_hint="Anthropic") == "claude-opus-4-8"


def test_provider_entry_name_is_first_resolvable_ref(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    assert pb._provider_entry_name(None) == "Bedrock"


def test_provider_entry_name_skips_uninstalled_first_ref(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Alibaba"},
    )
    assert pb._provider_entry_name(None) == "Alibaba"


def test_fallback_model_default_agent_pin_ignored_when_provider_disagrees(monkeypatch):
    """A default-agent pin naming a DIFFERENT provider than the hint must not be
    returned (it would send that provider's id to the hinted client)."""
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    monkeypatch.setattr(
        pb,
        "_active_chat_model_ids",
        lambda: {
            "global.anthropic.claude-opus-4-8",
            "Bedrock:global.anthropic.claude-opus-4-8",
            "glm-5.2",
            "Alibaba:glm-5.2",
        },
    )

    class _Prof:
        model = "Alibaba:glm-5.2"

    import gideon.core.config.loader as loader

    class _Cfg:
        agents = {"default": _Prof()}

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(lambda: _Cfg()))
    monkeypatch.setattr(
        "gideon.engine.agents.defaults.default_agent_name", lambda cfg: "default"
    )
    assert (
        pb._fallback_chat_model(provider_hint="Bedrock")
        == "global.anthropic.claude-opus-4-8"
    )


def _put(name: str, body: dict):
    from gideon.interfaces.dashboard.handlers import agents as H

    req = make_mocked_request(
        "PUT",
        f"/api/agents/{name}",
        headers={"Content-Type": "application/json"},
        match_info={"name": name},
    )
    req._read_bytes = json.dumps(body).encode()
    return asyncio.run(H.api_gideon_agent_update(req)), H


def test_reserved_agent_rejects_non_model_edit(monkeypatch, tmp_path):
    from gideon.engine.agents.defaults import LITE_AGENT_NAME

    resp, _H = _put(LITE_AGENT_NAME, {"system_prompt": "hacked", "model": "x"})
    assert resp.status == 403
    assert "only its model" in json.loads(resp.body)["error"]


def test_reserved_agent_allows_model_only_edit(monkeypatch, tmp_path):
    """A model-only body is NOT rejected by the reserved guard (it proceeds to the
    normal load/update path)."""
    from gideon.engine.agents.defaults import LITE_AGENT_NAME

    resp, _H = _put(LITE_AGENT_NAME, {"model": "glm-6"})
    assert resp.status != 403
