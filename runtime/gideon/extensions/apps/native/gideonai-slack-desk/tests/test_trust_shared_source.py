"""Slack handler observes the same global trust posture as the dashboard
(moved from core tests/test_trust_mode.py)."""

import gideon.trust_mode as tm


def test_dashboard_and_slack_desk_share_one_source():
    """Both surfaces observe the same global posture."""
    import slack_desk_runtime.handler as h

    tm.enable_yolo(ttl_secs=1800)
    assert h.is_yolo_mode() is True
    tm.disable_yolo()
    assert h.is_yolo_mode() is False
