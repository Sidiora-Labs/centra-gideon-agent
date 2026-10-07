"""Approval briefs retain native authority, additive metadata and shared facet copy.

The approval seam is exercised through the production coordinator and local Telegram
transport. Backend classification establishes facets; the console consumes those facets.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import test_channel_offered_answers as channel_fixtures
from test_channel_offered_answers import press

from gideon.integrations.llm_helpers import LLMEvent
from gideon.security.approval_brief import (
    APPROVAL_BRIEF_META_KEY,
    BLAST_RADIUS_FACET_ORDER,
    FACET_COPY,
    attach_approval_brief,
    blast_radius_line,
    compose_approval_brief,
    derive_blast_radius,
    established_facets,
)
from gideon.security.command_effects import CommandEffects

isolated = channel_fixtures.isolated
native = channel_fixtures.native

_TS_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "apps/console/src/features/chat/approvalMeta.ts"
)


def _make_gateway(native, monkeypatch):
    from gideon.core.config import AppConfig
    from gideon.engine.gateway import RuntimeCoordinator

    assert urlsplit(native.api.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
    monkeypatch.delenv("GIDEON_DISABLE_LIVE_WRITES", raising=False)
    gateway = RuntimeCoordinator(AppConfig.load())
    gateway.sessions = native.directory
    gateway.dashboard_state = native.state
    return gateway


def _event(title: str, **kw) -> LLMEvent:
    return LLMEvent(kind="permission_request", request_id="req1", title=title, **kw)


async def _drive(gateway, event, native) -> bool:
    """Drive the native approval callback and answer its actual local prompt."""
    approve_fn = gateway._interactive_approval("subagent", lambda _: native.session.key)
    task = asyncio.create_task(approve_fn(event, native.session.key))
    try:
        async with asyncio.timeout(5):
            while (
                not native.delivery.pending
                or event.request_id not in native.state._pending_approvals
            ):
                if task.done():
                    raise AssertionError(
                        f"approval ended before prompting: {task.result()}"
                    )
                await asyncio.sleep(0.01)
        key, pending = next(iter(native.delivery.pending.items()))
        await native.delivery.resolve_callback(press(key, pending, "approved"))
        return await asyncio.wait_for(task, timeout=5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestCallSiteCarriesTheBrief:
    """The brief reaches the channel through a real request_approval call."""

    @pytest.mark.asyncio
    async def test_channel_payload_carries_tool_and_blast_radius_line(
        self, native, monkeypatch
    ) -> None:
        """done_when: tool + blast-radius line arrive on the request_approval payload."""
        gateway = _make_gateway(native, monkeypatch)
        event = _event("web_fetch")
        assert await _drive(gateway, event, native) is True

        assert any(method == "sendMessage" for method, _ in native.calls)
        delivered = event
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]

        assert brief["tool"] == "web_fetch"
        assert brief["blastRadiusLine"] == "uses the network"
        assert brief["blastRadius"]["network"] is True

    @pytest.mark.asyncio
    async def test_channel_payload_carries_the_effective_risk(
        self, native, monkeypatch
    ) -> None:
        """`risk` is the EFFECTIVE per-invocation risk, not the DECLARED risk_level.

        A read-only `bash` call is declared destructive and resolves to safe. The
        channel never had that resolution before; the dashboard already showed it.
        """
        gateway = _make_gateway(native, monkeypatch)
        event = _event(
            "bash",
            tool_kind="execute",
            risk_level="destructive",
            tool_input={"command": "ls -la"},
        )
        assert await _drive(gateway, event, native) is True

        delivered = event
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert event.risk_level == "destructive"
        assert brief["risk"] == "safe"
        assert brief["blastRadius"] == {
            "writes": False,
            "network": False,
            "shell": False,
            "saysReadOnly": False,
            "readOnly": True,
        }
        assert brief["blastRadiusLine"] == "reads only"

    @pytest.mark.asyncio
    async def test_a_mutating_command_does_not_claim_read_only(
        self, native, monkeypatch
    ) -> None:
        """The screening verdict OU-8 left unwired now reaches the brief."""
        gateway = _make_gateway(native, monkeypatch)
        event = _event(
            "bash",
            tool_kind="execute",
            risk_level="destructive",
            tool_input={"command": "rm -rf build"},
        )
        assert await _drive(gateway, event, native) is True

        delivered = event
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert brief["blastRadius"]["readOnly"] is False
        assert brief["risk"] == "destructive"

    @pytest.mark.asyncio
    async def test_nothing_established_ships_no_blast_radius_at_all(
        self, native, monkeypatch
    ) -> None:
        """An unrecognizable tool name yields a brief with NO blast-radius keys.

        Not an all-false object: on a phone that renders as "no writes, no network, no
        shell, not read-only" — a confident all-clear from zero evidence.
        """
        gateway = _make_gateway(native, monkeypatch)
        event = _event("frobnicate_xyzzy")
        assert await _drive(gateway, event, native) is True

        delivered = event
        brief = delivered.tool_meta[APPROVAL_BRIEF_META_KEY]
        assert brief["tool"] == "frobnicate_xyzzy"
        assert "blastRadius" not in brief
        assert "blastRadiusLine" not in brief

    @pytest.mark.asyncio
    async def test_dashboard_fallback_gets_no_brief_argument(
        self, native, monkeypatch
    ) -> None:
        from gideon.security.approval_answer import YOU

        gateway = _make_gateway(native, monkeypatch)
        gateway._channel_delivery = None
        event = _event("web_fetch")
        approve_fn = gateway._interactive_approval(
            "subagent", lambda _: native.session.key
        )
        task = asyncio.create_task(approve_fn(event, native.session.key))
        try:
            for _ in range(200):
                if native.state._pending_approvals:
                    break
                await asyncio.sleep(0.002)
            entry = native.state._pending_approvals["req1"]
            assert APPROVAL_BRIEF_META_KEY not in entry
            assert entry["tool"] == "web_fetch"
            assert entry["blast_radius"]["network"] is True
            assert native.state.resolve_approval("req1", True, by=YOU)
            assert await asyncio.wait_for(task, timeout=5) is True
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class TestAdditiveOnly:
    @pytest.mark.asyncio
    async def test_channel_consumer_uses_existing_keyword_contract(
        self, native, monkeypatch
    ) -> None:
        gateway = _make_gateway(native, monkeypatch)
        event = _event("web_fetch")
        signature = inspect.signature(native.delivery.request_approval)
        assert "brief" not in signature.parameters
        assert {"source", "parent_session_key", "sessions", "on_prompted"} <= set(
            signature.parameters
        )
        assert await _drive(gateway, event, native) is True
        brief = event.tool_meta[APPROVAL_BRIEF_META_KEY]
        prompts = [
            payload["text"]
            for method, payload in native.calls
            if method == "sendMessage"
        ]
        assert any(
            brief["tool"] in text and brief["summary"] in text for text in prompts
        )

    @pytest.mark.asyncio
    async def test_channel_consumer_rejects_an_unrecognized_brief_keyword(
        self, native
    ) -> None:
        with pytest.raises(TypeError):
            await native.delivery.request_approval(
                _event("web_fetch"), source="subagent", brief={"tool": "web_fetch"}
            )
        assert not native.calls

    @pytest.mark.asyncio
    async def test_preexisting_tool_meta_keys_survive(
        self, native, monkeypatch
    ) -> None:
        """The brief is added BESIDE existing meta, never in place of it."""
        gateway = _make_gateway(native, monkeypatch)
        event = _event(
            "web_fetch", tool_meta={"ok": False, "content_type": "text/plain"}
        )
        assert await _drive(gateway, event, native) is True

        delivered = event
        assert delivered.tool_meta["ok"] is False
        assert delivered.tool_meta["content_type"] == "text/plain"
        assert APPROVAL_BRIEF_META_KEY in delivered.tool_meta

    def test_vacuity_replacing_tool_meta_would_red_that_rail(self) -> None:
        """VACUITY PROOF: the surviving-keys rail reds if the stamp replaced the dict."""
        original = {"ok": False, "content_type": "text/plain"}
        event = _event("web_fetch", tool_meta=dict(original))

        assert attach_approval_brief(event) is not None
        assert dict(event.tool_meta, **{APPROVAL_BRIEF_META_KEY: None}) != original
        for key, value in original.items():
            assert event.tool_meta[key] == value

        replaced = _event("web_fetch", tool_meta=dict(original))
        replaced.tool_meta = {APPROVAL_BRIEF_META_KEY: compose_approval_brief(replaced)}
        for key in original:
            assert key not in replaced.tool_meta

    def test_no_field_on_the_event_is_rewritten(self) -> None:
        """Every other event field is byte-identical after the stamp."""
        import dataclasses

        event = _event("bash", tool_kind="execute", risk_level="destructive")
        before = {
            f.name: getattr(event, f.name)
            for f in dataclasses.fields(event)
            if f.name != "tool_meta"
        }
        attach_approval_brief(event)
        after = {
            f.name: getattr(event, f.name)
            for f in dataclasses.fields(event)
            if f.name != "tool_meta"
        }
        assert before == after

    def test_an_event_that_cannot_carry_meta_is_left_alone(self) -> None:
        """No dict ``tool_meta`` → nothing stamped, nothing raised."""

        class Bare:
            title = "web_fetch"

        assert attach_approval_brief(Bare()) is None

    def test_an_event_with_no_tool_identity_gets_no_brief(self) -> None:
        assert compose_approval_brief(_event("")) is None


def _ts_text() -> str:
    assert _TS_SOURCE.is_file(), f"OU-7's module moved: {_TS_SOURCE}"
    return _TS_SOURCE.read_text(encoding="utf-8")


def _ts_string_array(name: str) -> list[str]:
    """Pull a `const NAME ... = [ 'a', 'b' ]` string array out of the TypeScript."""
    match = re.search(rf"const {name}\b[^=]*=\s*\[(.*?)\]", _ts_text(), re.DOTALL)
    assert match, f"could not find {name} in {_TS_SOURCE.name}"
    return re.findall(r"'([^']*)'", match.group(1))


def _ts_facet_copy() -> dict[str, dict[str, str]]:
    block = re.search(r"const FACET_COPY\b.*?\n\}", _ts_text(), re.DOTALL)
    assert block, "could not find FACET_COPY"
    found = re.findall(
        r"(\w+):\s*\{\s*label:\s*'([^']*)',\s*detail:\s*'([^']*)'\s*\}", block.group(0)
    )
    return {k: {"label": label, "detail": detail} for k, label, detail in found}


class TestOneVocabularyAcrossLanguages:
    """The channel brief and the dashboard chips must say the same words.

    OU-7 put the facet words beside the derivation precisely so three surfaces could
    not invent three vocabularies. The channel brief is composed in Python, so the
    agreement is enforced here instead of by a compiler.
    """

    def test_the_parser_is_not_vacuous(self) -> None:
        assert len(_ts_facet_copy()) == 5
        assert len(_ts_string_array("BLAST_RADIUS_FACET_ORDER")) == 5

    def test_frontend_consumes_authoritative_facets_without_name_hints(self) -> None:
        text = _ts_text()
        assert "return decodeBlastRadius(input.blastRadius)" in text
        for name in (
            "SHELL_HINTS",
            "NETWORK_HINTS",
            "DESTRUCTIVE_HINTS",
            "READ_VERB_HINTS",
            "WRITE_HINTS",
        ):
            assert name not in text

    def test_facet_words_are_the_frontends_verbatim(self) -> None:
        assert _ts_facet_copy() == FACET_COPY

    def test_render_order_agrees_with_the_frontend(self) -> None:
        assert _ts_string_array("BLAST_RADIUS_FACET_ORDER") == list(
            BLAST_RADIUS_FACET_ORDER
        )

    def test_every_facet_has_words(self) -> None:
        """A fifth facet cannot be silently dropped from the brief."""
        assert set(BLAST_RADIUS_FACET_ORDER) == set(FACET_COPY)

    @pytest.mark.parametrize(
        "tool", ["schedule", "file_write", "artifact_create", "git_commit"]
    )
    def test_declared_write_words_describe_writes(self, tool: str) -> None:
        radius = derive_blast_radius(tool)
        assert radius is not None and radius["writes"] is True
        assert radius["readOnly"] is False


class TestHonestyContract:
    def test_nothing_established_returns_none_not_all_false(self) -> None:
        assert derive_blast_radius("frobnicate_xyzzy") is None

    def test_an_established_write_never_claims_read_only(self) -> None:
        radius = derive_blast_radius("file_write", risk="safe")
        assert radius == {
            "writes": True,
            "network": False,
            "shell": False,
            "saysReadOnly": False,
            "readOnly": False,
        }

    def test_a_negative_screening_verdict_rules_the_read_claim_out(self) -> None:
        radius = derive_blast_radius(
            "bash", risk="safe", effects=CommandEffects(unread=True)
        )
        assert radius is not None and radius["readOnly"] is False
        assert radius["shell"] is True

    def test_an_unknown_risk_level_is_no_evidence(self) -> None:
        assert derive_blast_radius("do_thing", risk="apocalyptic") is None

    def test_a_read_verb_does_not_establish_safety_from_the_name(self) -> None:
        radius = derive_blast_radius("schedule_list")
        assert radius is not None and radius["writes"] is True
        assert radius["readOnly"] is False

    def test_a_destructive_verb_wins_outright(self) -> None:
        radius = derive_blast_radius("memory_forget", risk="safe")
        assert radius is not None and radius["writes"] is True

    def test_an_mcp_prefix_is_stripped_before_matching(self) -> None:
        radius = derive_blast_radius("mcp/some-server/web_fetch")
        assert radius is not None and radius["network"] is True

    def test_established_facets_shows_only_positives(self) -> None:
        facets = established_facets(
            {"writes": True, "network": False, "shell": True, "readOnly": False}
        )
        assert [f["key"] for f in facets] == ["writes", "shell"]

    def test_established_facets_of_nothing_is_empty(self) -> None:
        assert established_facets(None) == []

    def test_the_line_is_empty_when_nothing_is_established(self) -> None:
        assert blast_radius_line(None) == ""

    def test_the_line_follows_the_declared_render_order(self) -> None:
        line = blast_radius_line(
            {"writes": True, "network": True, "shell": True, "readOnly": False}
        )
        assert line == "writes files, runs a command, uses the network"

    def test_an_unrecognized_tool_never_claims_reads_only(self) -> None:
        """The trap this atom fell into once, kept shut.

        ``classify_invocation``'s name-fallback branch answers READ_ONLY for any name
        with no mutating hint. Wiring it in as a "screening verdict" made EVERY unknown
        tool arrive on the phone claiming "reads only" — a positive claim from zero
        evidence. The brief now takes only the effective risk, which floors an unknown
        name at ``caution``.
        """
        for unknown in ("frobnicate_xyzzy", "quux", "mcp/server/do_thing"):
            brief = compose_approval_brief(_event(unknown))
            assert brief is not None
            assert brief["risk"] == "caution", unknown
            assert "blastRadius" not in brief, unknown
            assert "blastRadiusLine" not in brief, unknown

    @pytest.mark.parametrize(
        "tool", ["artifact_widget_create", "createWidget", "mcp/server/artifact-create"]
    )
    def test_whole_write_words_never_become_substring_read_claims(
        self, tool: str
    ) -> None:
        radius = derive_blast_radius(tool, risk="caution")
        assert radius is not None and radius["writes"] is True
        assert radius["readOnly"] is False
        assert "get" in "widget"

    @pytest.mark.parametrize("tool", ["list_commits", "curling_scores", "widget"])
    def test_name_fragments_do_not_invent_facets(self, tool: str) -> None:
        assert derive_blast_radius(tool, risk="caution") is None

    def test_untrusted_read_label_remains_a_server_claim(self) -> None:
        radius = derive_blast_radius(
            "read_file", risk="caution", annotations={"readOnlyHint": True}
        )
        assert radius is not None and radius["saysReadOnly"] is True
        assert radius["readOnly"] is False

    def test_effective_safe_read_label_establishes_a_read(self) -> None:
        radius = derive_blast_radius(
            "read_file", risk="safe", annotations={"readOnlyHint": True}
        )
        assert radius is not None and radius["readOnly"] is True
        assert radius["saysReadOnly"] is False

    def test_the_composer_never_inspects_a_command_string(self) -> None:
        """Screening stays owned by task_modes — this module re-implements none of it.

        Checked over the parsed IDENTIFIERS, not the text: the module's own prose names
        ``is_read_only_bash`` while explaining who owns it, and a text scan reads
        comments (which is how this rail was vacuous on its first draft).
        """
        import ast

        source = (
            Path(__file__).resolve().parents[2]
            / "runtime/gideon/security/approval_brief.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for owned_elsewhere in (
            "is_read_only_bash",
            "extract_bash_command",
            "classify_invocation",
        ):
            assert owned_elsewhere not in referenced
        assert "resolve_effective_risk" in referenced
