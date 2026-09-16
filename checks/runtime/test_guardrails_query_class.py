"""MODEL-ROUTING-TELEMETRY §2 / MRT-1b — query_class threaded onto the attempt audit.

The ModelCallGuard classifies the current call (via the pure MRT-1a classifier) and stamps the
resulting query_class onto every model_calls.jsonl attempt row, so the telemetry stats layer can
fold per (use_case, query_class). Classification is fail-open — a broken classifier leaves the
class "" and never breaks a model call.
"""

from __future__ import annotations

import pytest

from checks.runtime.test_guardrails_model_call import FakeProvider as _BaseFake
from gideon.integrations.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from gideon.security.guardrails import model_call as mc
from gideon.security.guardrails.audit import AttemptRecord, read_recent
from gideon.security.guardrails.model_call import ModelCallGuard, _joined_content


class FakeProvider(_BaseFake):
    async def stream_command(self, command):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._text)
        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=self._tokens[0],
            output_tokens=self._tokens[1],
        )

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._text)
        yield LLMEvent(
            kind=EVENT_COMPLETE,
            input_tokens=self._tokens[0],
            output_tokens=self._tokens[1],
        )


async def _drain(agen):
    out = ""
    async for ev in agen:
        if ev.kind == EVENT_TEXT_CHUNK:
            out += ev.text
        elif ev.kind == EVENT_COMPLETE:
            break
    await agen.aclose()
    return out


class TestQueryClassStamped:
    @pytest.mark.asyncio
    async def test_stream_stamps_query_class(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
        guard = ModelCallGuard(
            FakeProvider(), use_case="chat", provider_name="P", model="m"
        )
        await guard.start()
        await _drain(guard.stream("```python\ndef f():\n    return 1\n```"))
        rows = read_recent()
        assert len(rows) == 1 and rows[0]["query_class"] == "code"

    @pytest.mark.asyncio
    async def test_short_chat_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
        guard = ModelCallGuard(
            FakeProvider(), use_case="chat", provider_name="P", model="m"
        )
        await guard.start()
        await _drain(guard.stream("hi there"))
        assert read_recent()[0]["query_class"] == "short_chat"

    @pytest.mark.asyncio
    async def test_use_case_prior_flows_into_classification(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
        guard = ModelCallGuard(
            FakeProvider(), use_case="code_tools", provider_name="P", model="m"
        )
        await guard.start()
        await _drain(guard.stream("make it faster"))
        assert read_recent()[0]["query_class"] == "code"

    @pytest.mark.asyncio
    async def test_complete_classifies_joined_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
        guard = ModelCallGuard(
            FakeProvider(), use_case="chat", provider_name="P", model="m"
        )
        await guard.start()
        msgs = [{"role": "user", "content": "please summarize this document, tl;dr"}]
        await _drain(guard.complete(msgs))
        assert read_recent()[0]["query_class"] == "summarize"

    @pytest.mark.asyncio
    async def test_stream_command_classifies(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
        guard = ModelCallGuard(
            FakeProvider(), use_case="chat", provider_name="P", model="m"
        )
        await guard.start()
        await _drain(guard.stream_command("return the result as json"))
        assert read_recent()[0]["query_class"] == "extract_structured"


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_broken_classifier_leaves_empty_class_not_a_crash(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)

        def boom(*a, **k):
            raise RuntimeError("classifier exploded")

        monkeypatch.setattr("gideon.engine.routing.classifier.classify_query", boom)
        guard = ModelCallGuard(
            FakeProvider(), use_case="chat", provider_name="P", model="m"
        )
        await guard.start()
        assert await _drain(guard.stream("hi")) == "ok"
        rows = read_recent()
        assert len(rows) == 1 and rows[0].get("query_class", "") == ""


class TestRecordAndJoin:
    def test_attempt_record_carries_query_class_column(self):
        r = AttemptRecord(
            audit_id="a",
            ts=0.0,
            use_case="chat",
            provider="P",
            model="m",
            attempt=1,
            query_class="code",
        )
        import json

        line = json.loads(r.to_json_line())
        assert line["query_class"] == "code"

    def test_query_class_defaults_empty(self):
        r = AttemptRecord(
            audit_id="a", ts=0.0, use_case="c", provider="P", model="m", attempt=1
        )
        assert r.query_class == ""

    def test_joined_content_from_string_and_blocks(self):
        msgs = [
            {"role": "system", "content": "ignore me"},
            {"role": "user", "content": "plain string"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "block text"}, {"type": "image"}],
            },
        ]
        joined = _joined_content(msgs)
        assert "plain string" in joined and "block text" in joined
        assert "ignore me" not in joined

    def test_joined_content_tolerates_junk(self):
        assert _joined_content([{"bogus": 1}, "notadict"]) == ""  # type: ignore[list-item]
        assert _joined_content([]) == ""


assert hasattr(mc, "ModelCallGuard")
