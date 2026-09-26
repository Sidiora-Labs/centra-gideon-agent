"""slack_desk_runtime.format re-export identity — the app must re-export core
textfmt objects, not re-implement them (moved from core tests/test_textfmt.py)."""

from gideon.textfmt import extract_options, strip_thinking_tags


def test_slack_desk_format_reexports_are_identical():
    """slack.format must re-export the SAME objects, not re-implement them."""
    from slack_desk_runtime import format as slack_desk_format

    assert slack_desk_format.extract_options is extract_options
    assert slack_desk_format.strip_thinking_tags is strip_thinking_tags
