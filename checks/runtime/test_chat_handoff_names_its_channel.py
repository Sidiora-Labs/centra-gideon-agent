"""Compaction preserves the exact channel thread while replacing an agent session."""

from gideon.engine.session_map import SessionMap


def test_forget_session_id_keeps_channel_thread_across_reload(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))

    links = SessionMap()
    key = "dashboard:chat-123"
    links.set(key, "agent-session-old", provider="slack")
    links.set_channel_link(key, "thread-456", "channel-789")

    links.forget_session_id(key)

    assert links.get(key) is None
    assert links.get_channel_link(key) == ("thread-456", "channel-789")
    assert links.get_session_for_thread("thread-456") == key

    restored = SessionMap()
    assert restored.get(key) is None
    assert restored.get_channel_link(key) == ("thread-456", "channel-789")
    assert restored.get_session_for_thread("thread-456") == key
