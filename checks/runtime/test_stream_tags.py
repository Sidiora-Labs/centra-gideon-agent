"""Tests for the cross-chunk streaming tag splitter."""

from gideon.integrations.llm.stream_tags import KIND_OUTSIDE, StreamingTagSplitter

_DEFAULT_TAGS = {"think": "thinking"}


def _split(chunks, tags=None):
    """Feed chunks through a fresh splitter; return (kind, text) tuples.

    Segments resolve per feed() call (streaming), so adjacent same-kind spans
    from separate chunks stay separate — that's intended; the consumer maps each
    to an event as it arrives. Use _runs() to assert on contiguous kind runs.
    """
    sp = StreamingTagSplitter(_DEFAULT_TAGS if tags is None else tags)
    out = []
    for c in chunks:
        out.extend(sp.feed(c))
    out.extend(sp.flush())
    return [(s.kind, s.text) for s in out]


def _runs(chunks, tags=None):
    """Collapse the resolved segments into contiguous (kind, text) runs.

    This is the consumer-relevant view: regardless of chunk fragmentation, the
    sequence of kind-runs (and their concatenated text) must be stable.
    """
    runs: list[list] = []
    for kind, text in _split(chunks, tags):
        if runs and runs[-1][0] == kind:
            runs[-1][1] += text
        else:
            runs.append([kind, text])
    return [(k, t) for k, t in runs]


def test_plain_text_passthrough():
    assert _split(["hello world"]) == [(KIND_OUTSIDE, "hello world")]


def test_whole_think_block_single_chunk():
    assert _split(["<think>reasoning</think>answer"]) == [
        ("thinking", "reasoning"),
        (KIND_OUTSIDE, "answer"),
    ]


def test_text_before_and_after_think():
    assert _split(["pre<think>mid</think>post"]) == [
        (KIND_OUTSIDE, "pre"),
        ("thinking", "mid"),
        (KIND_OUTSIDE, "post"),
    ]


def test_open_tag_split_across_chunks():
    assert _split(["a<thi", "nk>r</think>b"]) == [
        (KIND_OUTSIDE, "a"),
        ("thinking", "r"),
        (KIND_OUTSIDE, "b"),
    ]


def test_close_tag_split_across_chunks():
    assert _split(["<think>reason</thi", "nk>done"]) == [
        ("thinking", "reason"),
        (KIND_OUTSIDE, "done"),
    ]


def test_tag_char_by_char():
    stream = list("<think>hi</think>yo")
    assert _runs(stream) == [("thinking", "hi"), (KIND_OUTSIDE, "yo")]


def test_unterminated_tag_flushes_as_text():
    assert _split(["<think>oops never closes"]) == [
        ("thinking", "oops never closes"),
    ]


def test_dangling_open_prefix_flushes_as_text():
    assert _split(["answer<thi"]) == [
        (KIND_OUTSIDE, "answer"),
        (KIND_OUTSIDE, "<thi"),
    ]


def test_lone_angle_bracket_is_literal_text():
    assert _split(["a < b is math"]) == [(KIND_OUTSIDE, "a < b is math")]


def test_case_insensitive_tags():
    assert _split(["<THINK>r</THINK>x"]) == [("thinking", "r"), (KIND_OUTSIDE, "x")]


def test_multiple_think_blocks():
    assert _split(["<think>one</think>mid<think>two</think>end"]) == [
        ("thinking", "one"),
        (KIND_OUTSIDE, "mid"),
        ("thinking", "two"),
        (KIND_OUTSIDE, "end"),
    ]


def test_same_kind_runs_concatenate():
    assert _split(["foo", "bar"]) == [(KIND_OUTSIDE, "foo"), (KIND_OUTSIDE, "bar")]
    assert _runs(["foo", "bar"]) == [(KIND_OUTSIDE, "foobar")]


def test_generalizes_to_other_tags():
    tags = {"memory": "memory", "widget": "widget"}
    assert _split(["a<memory>m</memory>b<widget>w</widget>c"], tags) == [
        (KIND_OUTSIDE, "a"),
        ("memory", "m"),
        (KIND_OUTSIDE, "b"),
        ("widget", "w"),
        (KIND_OUTSIDE, "c"),
    ]


def test_no_tags_configured_is_all_text():
    assert _split(["<think>x</think>"], {}) == [(KIND_OUTSIDE, "<think>x</think>")]


def test_empty_feed_is_noop():
    sp = StreamingTagSplitter({"think": "thinking"})
    assert sp.feed("") == []
    assert sp.flush() == []


def test_every_two_chunk_boundary_has_the_same_semantic_runs():
    text = "lead<THINK>reason<unknown>x</unknown></ThInK>tail<memory>fact</memory>end"
    tags = {"think": "thinking", "memory": "memory"}
    expected = [
        ("text", "lead"),
        ("thinking", "reason<unknown>x</unknown>"),
        ("text", "tail"),
        ("memory", "fact"),
        ("text", "end"),
    ]
    for boundary in range(len(text) + 1):
        assert _runs([text[:boundary], text[boundary:]], tags) == expected


def test_partial_closing_delimiter_is_visible_on_flush_and_state_resets():
    splitter = StreamingTagSplitter({"think": "thinking"})
    assert [(item.kind, item.text) for item in splitter.feed("<think>reason</thi")] == [
        ("thinking", "reason")
    ]
    assert [(item.kind, item.text) for item in splitter.flush()] == [("text", "</thi")]
    assert [(item.kind, item.text) for item in splitter.feed("next")] == [
        ("text", "next")
    ]
    assert splitter.flush() == []


def test_overlapping_candidate_prefixes_and_literal_angles_are_not_lost():
    assert _runs(
        list("a<<think>b</think>c<thing>d</thing>e"),
        {"think": "thinking", "thing": "memory"},
    ) == [
        ("text", "a<"),
        ("thinking", "b"),
        ("text", "c"),
        ("memory", "d"),
        ("text", "e"),
    ]
