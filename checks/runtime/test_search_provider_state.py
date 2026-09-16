"""Search wire records and binding persistence using real state and threads."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gideon.core.atomic_write import (
    register_post_write_hook,
    unregister_post_write_hook,
)
from gideon.integrations.search_providers import registry, use_cases
from gideon.integrations.search_providers.base import (
    FetchResult,
    SearchCapabilities,
    SearchHit,
    SearchResult,
)


def test_wire_records_keep_zero_values_and_omit_empty_optional_content():
    hit = SearchHit(
        url="https://example.com", score=0, published_date="", raw_content=""
    )
    assert hit.to_dict() == {
        "url": "https://example.com",
        "title": "",
        "snippet": "",
        "score": 0,
    }
    fetched = FetchResult(url=hit.url, next_index=0)
    assert fetched.to_dict() == {
        "url": hit.url,
        "content": "",
        "title": "",
        "char_count": 0,
        "truncated": False,
        "next_index": 0,
    }
    fetched.next_index = None
    assert "next_index" not in fetched.to_dict()


def test_result_serialization_keeps_hit_order_and_independent_source_lists():
    first = SearchHit(
        "https://one.example", published_date="2026-09-16", raw_content="one"
    )
    second = SearchHit("https://two.example", snippet="two")
    result = SearchResult(
        results=[first, SearchHit(""), second, first],
        answer="Response",
        provider="catalog",
        query="source order",
        depth="deep",
    )
    output = result.to_dict()
    assert output["sources"] == [first.url, second.url]
    assert output["results"] == [hit.to_dict() for hit in result.results]
    assert output["answer"] == "Response"
    assert output["results"][0]["published_date"] == "2026-09-16"
    assert output["results"][0]["raw_content"] == "one"
    output["results"][0]["title"] = "serialized only"
    output["sources"].clear()
    assert first.title == ""
    assert result.sources == [first.url, second.url]
    assert SearchResult().results is not SearchResult().results


def test_capability_payload_does_not_share_mutable_depths():
    capabilities = SearchCapabilities(
        supports_fetch=True, depths=("quick",), keyless=True
    )
    output = capabilities.to_dict()
    assert output == {
        "returns_content": False,
        "returns_answer": False,
        "returns_highlights": False,
        "supports_recency": False,
        "supports_domains": False,
        "supports_fetch": True,
        "depths": ["quick"],
        "keyless": True,
    }
    output["depths"].append("deep")
    assert capabilities.depths == ("quick",)


@pytest.fixture
def bindings_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path / "active_search_providers.json"


def test_legacy_bindings_normalize_without_dropping_unknown_keys(bindings_home):
    bindings_home.write_text(
        json.dumps(
            {
                "search-general": "",
                "search-news": ["news", None, "", 0, False, 7, {"id": "extra"}],
                "future-case": "future",
                "invalid-number": 3,
                "invalid-object": {"provider": "unused"},
            }
        )
    )
    assert use_cases.load_active_search_providers() == {
        "search-general": [""],
        "search-news": ["news", "7", "{'id': 'extra'}"],
        "future-case": ["future"],
    }
    assert use_cases.active_search_provider_names("fetch-article") == [""]


@pytest.mark.parametrize("contents", ["{broken", "[]", '"provider"', "null", "7"])
def test_unreadable_or_non_mapping_binding_documents_are_empty(bindings_home, contents):
    bindings_home.write_text(contents)
    assert use_cases.load_active_search_providers() == {}
    assert use_cases.active_search_provider_names("search-news") == []


def test_binding_fallback_clear_and_saved_order(bindings_home):
    use_cases.set_active_search_provider("search-general", "general")
    use_cases.set_active_search_provider("search-news", "news")
    use_cases.set_active_search_provider("fetch-article", "article")
    use_cases.set_active_search_provider("search-news", "news-replacement")
    assert list(json.loads(bindings_home.read_text())) == [
        "search-general",
        "search-news",
        "fetch-article",
    ]
    assert use_cases.active_search_provider_names("search-financial") == ["general"]
    assert use_cases.active_search_provider_names("future-case") == ["general"]
    use_cases.set_active_search_provider("search-news", "")
    assert use_cases.active_search_provider_names("search-news") == ["general"]
    use_cases.set_active_search_provider("search-general", "")
    assert use_cases.active_search_provider_names("search-news") == []
    assert use_cases.active_search_provider_names("fetch-article") == ["article"]
    assert bindings_home.read_text().endswith("\n")


def test_invalid_binding_preserves_existing_file(bindings_home):
    use_cases.set_active_search_provider("search-general", "general")
    original = bindings_home.read_bytes()
    with pytest.raises(ValueError, match="Invalid search use case: 'invalid'"):
        use_cases.set_active_search_provider("invalid", "unused")
    assert bindings_home.read_bytes() == original


def test_selection_tracks_current_configuration_home(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    for home, provider in ((first, "first"), (second, "second")):
        monkeypatch.setenv("GIDEON_HOME", str(home))
        use_cases.set_active_search_provider("search-general", provider)
    for home, provider in ((first, "first"), (second, "second")):
        monkeypatch.setenv("GIDEON_HOME", str(home))
        assert use_cases.active_search_provider_names("search-financial") == [provider]


def test_concurrent_settings_updates_keep_every_use_case(bindings_home):
    cases = use_cases.SEARCH_USE_CASES
    for generation in range(8):
        use_cases.save_active_search_providers({})
        ready = Barrier(len(cases))

        def bind(name):
            ready.wait(timeout=5)
            use_cases.set_active_search_provider(name, f"{name}:{generation}")

        with ThreadPoolExecutor(max_workers=len(cases)) as workers:
            list(workers.map(bind, cases))
        assert use_cases.load_active_search_providers() == {
            name: [f"{name}:{generation}"] for name in cases
        }


def test_atomic_write_subscribers_can_read_and_extend_bindings(bindings_home):
    observed = []

    def on_write(path):
        if path == bindings_home:
            observed.append(use_cases.load_active_search_providers())
            use_cases.set_active_search_provider("search-news", "subscriber")

    register_post_write_hook(on_write)
    try:
        use_cases.set_active_search_provider("search-general", "general")
    finally:
        unregister_post_write_hook(on_write)
    assert observed == [{"search-general": ["general"]}]
    assert use_cases.load_active_search_providers() == {
        "search-general": ["general"],
        "search-news": ["subscriber"],
    }


@pytest.mark.asyncio
async def test_empty_catalog_keeps_bindings_but_cannot_run_search(
    bindings_home, monkeypatch
):
    monkeypatch.setattr(registry, "_providers", {})
    use_cases.set_active_search_provider("search-general", "missing-extension")
    assert registry.list_providers() == []
    for use_case in use_cases.SEARCH_USE_CASES:
        assert await registry.resolve_search_provider_for_use_case(use_case) is None
        assert registry.can_resolve_search_use_case(use_case) is False
    assert await registry.search_with_fallback("search-news", "query") == (None, False)
    assert use_cases.active_search_provider_names("search-news") == [
        "missing-extension"
    ]
    with pytest.raises(ValueError, match="Unknown search use case: 'invalid'"):
        await registry.resolve_search_provider_for_use_case("invalid")
    assert registry.can_resolve_search_use_case("invalid") is False
