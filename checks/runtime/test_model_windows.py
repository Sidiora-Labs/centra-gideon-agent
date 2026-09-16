"""``model_context_window`` id matching (model_windows.py).

The lookup resolves a model id to its context window through tiers: exact, then a
separator split (a ``Provider:`` qualifier whose id is the TAIL, OR Ollama's
``family:tag`` whose family is the HEAD), then loose containment, then the conservative
default. The regression pinned here: an Ollama ``family:tag`` id — the normal way Ollama
names a model, e.g. ``llama3.1:8b`` — must resolve to its FAMILY's window, not fall
through to the 200k default and hand a local model a window larger than it actually has.
"""

from __future__ import annotations

import pytest

from gideon.integrations.model_windows import (
    DEFAULT_CONTEXT_WINDOW,
    model_context_window,
)


class TestOllamaFamilyTag:
    @pytest.mark.parametrize("family", ["llama3.1", "qwen2.5", "mistral"])
    @pytest.mark.parametrize("tag", ["8b", "0.5b-instruct-q4_0", "7b", "latest"])
    def test_family_tag_resolves_to_the_family_window(self, family, tag):
        tagged = model_context_window(f"{family}:{tag}")
        assert tagged == model_context_window(family)
        assert tagged != DEFAULT_CONTEXT_WINDOW


class TestProviderQualifiedStillResolves:
    def test_provider_prefix_uses_the_tail(self):
        bare = model_context_window("claude-opus-4.8")
        assert bare != DEFAULT_CONTEXT_WINDOW
        assert model_context_window("Bedrock:claude-opus-4.8") == bare

    def test_dated_loose_variant_still_matches(self):
        assert model_context_window(
            "global.anthropic.claude-opus-4-8"
        ) == model_context_window("claude-opus-4.8")


class TestFallbacks:
    def test_unknown_tagged_model_is_the_default(self):
        assert model_context_window("no-such-model-xyz:9000") == DEFAULT_CONTEXT_WINDOW

    def test_empty_or_none_is_the_default(self):
        assert model_context_window("") == DEFAULT_CONTEXT_WINDOW
        assert model_context_window(None) == DEFAULT_CONTEXT_WINDOW

    def test_custom_default_is_honoured_for_an_unknown_model(self):
        assert model_context_window("no-such-model", default=4096) == 4096


def test_table_reader_filters_metadata_and_invalid_types_using_real_files(tmp_path):
    import json

    from gideon.integrations.model_windows import _read_windows

    path = tmp_path / "windows.json"
    path.write_text(
        json.dumps(
            {
                "_comment": "catalog",
                "integer": 2048,
                "float": 4096.9,
                "string": "8192",
                "missing": None,
            }
        )
    )
    assert _read_windows(path) == {"integer": 2048, "float": 4096}
    path.write_text("[]")
    assert _read_windows(path) == {}
    path.write_bytes(b"\xff")
    assert _read_windows(path) == {}
    assert _read_windows(tmp_path / "absent.json") == {}


def test_resolution_tiers_and_equal_specificity_preserve_catalog_order():
    from gideon.integrations.model_windows import _resolve_window

    catalog = {"model": 10, "model-v2": 20, "Model-v2": 30, "family": 40}
    assert _resolve_window("Model-v2", catalog, 99) == 30
    assert _resolve_window("family:model-v2", catalog, 99) == 20
    assert _resolve_window("family:quantized", catalog, 99) == 40
    assert _resolve_window("Provider:model/v2/model-v2-date", catalog, 99) == 10
    assert _resolve_window("Provider:version/model-v2-date", catalog, 99) == 20
    assert _resolve_window("MODEL-V2-date", catalog, 99) == 20
    assert _resolve_window("unknown", catalog, 99) == 99
