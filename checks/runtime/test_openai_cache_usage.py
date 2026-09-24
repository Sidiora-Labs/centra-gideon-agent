import pytest
from openai.types.chat import ChatCompletionChunk

from gideon.integrations.llm.openai import _ChatDecoder
from gideon.operations.pricing import TokenCharge


def chunk(prompt, cached, *, details=True):
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": 40,
        "total_tokens": prompt + 40,
    }
    if details:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return ChatCompletionChunk.model_validate(
        {
            "id": "completion",
            "created": 1,
            "model": "compatible-model",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": usage,
        }
    )


@pytest.mark.parametrize("cached", [0, 600, 1000])
@pytest.mark.parametrize("wire_dict", [False, True])
def test_cache_buckets_context_and_cost(cached, wire_dict):
    decoder = _ChatDecoder()
    frame = chunk(1000, cached)
    decoder.feed(frame.model_dump() if wire_dict else frame)
    event = decoder.usage.terminal(decoder.context_usage_pct("compatible-model"))
    assert event.input_tokens == 1000 - cached
    assert event.cache_read_tokens == cached
    assert event.cache_creation_tokens == 0
    assert (
        event.input_tokens + event.cache_read_tokens + event.cache_creation_tokens
        == 1000
    )
    assert event.output_tokens == 40
    assert event.context_usage_pct == pytest.approx(1000 / 128000 * 100)
    charge = TokenCharge(
        event.input_tokens,
        event.output_tokens,
        event.cache_read_tokens,
        event.cache_creation_tokens,
    )
    assert charge.at(
        {"in": 10, "out": 20, "cache_read": 1, "cache_write": 12}
    ) == pytest.approx(((1000 - cached) * 10 + 40 * 20 + cached) / 1000000)


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"cached_tokens": None},
        {"cached_tokens": "invalid"},
        {"cached_tokens": -2},
        {"cached_tokens": True},
    ],
)
def test_missing_or_invalid_cache_usage_remains_uncached(details):
    decoder = _ChatDecoder()
    decoder.feed({"usage": {"prompt_tokens": 1000, "prompt_tokens_details": details}})
    assert decoder.usage.input_tokens == 1000
    assert decoder.usage.cache_read_tokens == 0


def test_absent_sdk_details_and_usage_chunks():
    decoder = _ChatDecoder()
    decoder.feed(chunk(1000, 0, details=False))
    decoder.feed({"choices": [], "usage": None})
    assert decoder.usage.input_tokens == 1000
    assert decoder.usage.cache_read_tokens == 0


def test_repeated_usage_snapshots_do_not_subtract_cache_twice():
    decoder = _ChatDecoder()
    for _ in range(2):
        decoder.feed(chunk(1000, 600))
    assert decoder.usage.input_tokens == 400
    assert decoder.usage.cache_read_tokens == 600
    decoder.feed(chunk(1000, 0, details=False))
    assert decoder.usage.input_tokens == 1000
    assert decoder.usage.cache_read_tokens == 0
    decoder.feed(chunk(0, 0))
    assert decoder.usage.input_tokens == 0
    assert decoder.context_usage_pct("compatible-model") is None


def test_cache_reads_cannot_exceed_the_reported_prompt():
    decoder = _ChatDecoder()
    decoder.feed(chunk(1000, 1500))
    assert decoder.usage.input_tokens == 0
    assert decoder.usage.cache_read_tokens == 1000
    assert decoder.context_usage_pct("compatible-model") == pytest.approx(
        1000 / 128000 * 100
    )
