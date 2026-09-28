import pytest

from gideon.core.config.edit_spec import ConfigValueError, coerce_edit_value


def test_watched_channel_ids_are_trimmed_bounded_and_deduplicated():
    spec = {"type": "channel_ids"}
    assert coerce_edit_value(
        "inbox.watched_channels", [" C1 ", "C1", "C-two"], spec
    ) == ["C1", "C-two"]
    with pytest.raises(ConfigValueError):
        coerce_edit_value("inbox.watched_channels", ["two words"], spec)
    with pytest.raises(ConfigValueError):
        coerce_edit_value("inbox.watched_channels", [""], spec)
