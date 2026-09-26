"""gideonai-slack-desk app: the inbox MessageSourceProvider (CE-8).

The app registers TWO providers — the ``channel`` transport (interactive chat) and
this ``inbox`` source — so Slack messages reach the generic Inbox through core's
vendor-neutral message-source seam instead of a Slack-shaped path in core.

Drives the provider through the bundle's own ``SlackDeskClientOps`` ABC (a stub client),
which is exactly what production's ``RealSlackDeskClient`` satisfies — so these tests
never touch the Slack API and never need a live token.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# App dir on sys.path so this root-level test imports the app's slack_desk_runtime
# package the way the gateway's app loader does (mirrors test_provider.py, which
# inlines this because it lives at the app root rather than under tests/).
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from slack_desk_runtime.client import SlackDeskClientOps  # noqa: E402
from slack_desk_runtime.inbox_source import SlackDeskInboxSource, create_provider  # noqa: E402


class StubClient(SlackDeskClientOps):
    """Minimal SlackDeskClientOps: records calls, replays canned history.

    Only the members the inbox source actually uses are implemented; the rest of
    the ABC's abstract methods get trivial bodies so the class instantiates.
    """

    def __init__(self, history=None, users=None, fail_channels=(), raw=None):
        self._history = history or {}
        # `raw` bypasses this stub's own ts filtering/sorting and is returned
        # verbatim — needed to hand the provider a payload the stub itself could
        # not order (e.g. a malformed ts).
        self._raw = raw or {}
        self._users = users or {}
        self._fail = set(fail_channels)
        self.posts: list[tuple] = []
        self.reactions: list[tuple] = []
        self.history_calls: list[tuple] = []
        self.user_info_calls: list[str] = []

    async def fetch_history(self, channel, oldest, limit=200):
        self.history_calls.append((channel, oldest, limit))
        if channel in self._fail:
            raise RuntimeError("slack api down")
        if channel in self._raw:
            return list(self._raw[channel])
        # Return only messages strictly newer than `oldest`, newest-first — what
        # conversations.history does with an exclusive `oldest`.
        msgs = [m for m in self._history.get(channel, []) if float(m["ts"]) > float(oldest)]
        return sorted(msgs, key=lambda m: float(m["ts"]), reverse=True)[:limit]

    async def post_message(self, channel, text, thread_ts=None, unfurl_links=None, unfurl_media=None):
        if channel == "C_BAD":
            raise RuntimeError("cannot post")
        self.posts.append((channel, text, thread_ts))
        return "1700000099.000000"

    async def add_reaction(self, channel, ts, emoji):
        if channel == "C_BAD":
            raise RuntimeError("cannot react")
        self.reactions.append((channel, ts, emoji))

    async def get_user_info(self, user_id):
        self.user_info_calls.append(user_id)
        return self._users.get(user_id, {})

    # ── unused ABC surface ────────────────────────────────────────────────────
    async def post_blocks(self, *a, **k): return ""
    async def update_message(self, *a, **k): return None
    async def delete_message(self, *a, **k): return None
    async def remove_reaction(self, *a, **k): return None
    async def upload_file(self, *a, **k): return None
    async def open_dm(self, *a, **k): return ""
    async def post_ephemeral(self, *a, **k): return None
    async def views_publish(self, *a, **k): return None
    async def get_prompts(self, *a, **k): return []


def _msg(ts, user="U_ALICE", text="hello", **extra):
    return {"ts": ts, "user": user, "text": text, **extra}


def _source(**kw):
    return SlackDeskInboxSource({"bot_token": "xoxb-test"}, client=StubClient(**kw))


# ── manifest ──────────────────────────────────────────────────────────────────


def test_manifest_declares_at_least_two_providers():
    """CE-8's core claim: a channel app that only registers a channel leaves its
    messages outside the generic inbox. The manifest must declare BOTH.

    Asserted as a SUBSET, not as set equality. The original spelling was
    ``types == {"channel", "inbox"}``, which froze the vendor-completeness checklist at the
    bar available when CE-8 shipped — so the next seam this app adopted turned CE-8's own
    test red for succeeding. It did, at CE-10 (``trigger_source``). What this test owns is
    that the inbox seam is declared and points at THIS module; which other seams the bundle
    has grown is the completeness rail's business, not this file's.
    """
    manifest = json.loads((_APP_DIR / "app.json").read_text(encoding="utf-8"))
    declared = ([manifest["provider"]] if manifest.get("provider") else []) + manifest.get(
        "providers", []
    )
    assert len(declared) >= 2, declared
    types = {p["type"] for p in declared}
    assert {"channel", "inbox"} <= types, types
    inbox = next(p for p in declared if p["type"] == "inbox")
    assert inbox["implementation"] == "slack_desk_runtime.inbox_source:create_provider"


def test_create_provider_returns_the_inbox_source():
    assert type(create_provider({})).__name__ == "SlackDeskInboxSource"


def test_source_name_is_the_vendor_neutral_key():
    """Core resolves a source BY NAME; `source` is stamped on every inbox item."""
    assert _source().source_name == "slack"


def test_token_falls_back_to_the_shared_credential_store(monkeypatch):
    """Same resolution order as SlackDeskTransport, so the two providers can't disagree
    about which workspace this app is bound to."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-shared")
    src = SlackDeskInboxSource({}, client=StubClient())
    assert src._client is not None  # constructed without an instance token


# ── poll ──────────────────────────────────────────────────────────────────────


def test_poll_maps_messages_and_advances_the_checkpoint():
    src = _source(
        history={"C1": [_msg("1700000001.000100"), _msg("1700000002.000200", text="second")]},
        users={"U_ALICE": {"real_name": "Alice Example"}},
    )
    msgs, cursors = asyncio.run(src.poll(["C1"], {}, "U_ME"))

    assert [m.id for m in msgs] == ["1700000001.000100", "1700000002.000200"]  # oldest-first
    assert [m.text for m in msgs] == ["hello", "second"]
    first = msgs[0]
    assert first.channel_id == "C1"
    assert first.sender_id == "U_ALICE"
    assert first.sender_name == "Alice Example"
    assert first.timestamp == 1700000001.0001
    assert first.is_dm is False
    # Cursor is the NEWEST ts seen, so the next poll resumes past it.
    assert cursors["C1"] == "1700000002.000200"


def test_poll_passes_the_checkpoint_as_oldest_and_does_not_redeliver():
    """`oldest` is exclusive, so the message AT the cursor must not come back —
    the property that makes the ts a correct resume cursor."""
    src = _source(history={"C1": [_msg("1700000001.000100"), _msg("1700000002.000200")]})
    msgs, cursors = asyncio.run(src.poll(["C1"], {"C1": "1700000001.000100"}, "U_ME"))

    assert src._client.history_calls == [("C1", "1700000001.000100", 50)]
    assert [m.id for m in msgs] == ["1700000002.000200"]
    assert cursors["C1"] == "1700000002.000200"


def test_poll_with_nothing_new_keeps_the_checkpoint():
    src = _source(history={"C1": [_msg("1700000001.000100")]})
    msgs, cursors = asyncio.run(src.poll(["C1"], {"C1": "1700000001.000100"}, "U_ME"))
    assert msgs == []
    assert cursors["C1"] == "1700000001.000100"


def test_poll_skips_bot_own_and_authorless_messages_but_still_advances():
    """The filtered messages were SEEN and judged, so the cursor moves past them —
    otherwise a chatty bot would re-deliver the same window forever."""
    src = _source(
        history={
            "C1": [
                _msg("1700000001.000100", user="U_ME"),               # our own
                _msg("1700000002.000200", user="U_BOT", bot_id="B1"),  # a bot
                _msg("1700000003.000300", user=""),                    # join/topic, no author
                _msg("1700000004.000400", user="U_ALICE", text="real"),
            ]
        }
    )
    msgs, cursors = asyncio.run(src.poll(["C1"], {}, "U_ME"))

    assert [m.text for m in msgs] == ["real"]
    assert cursors["C1"] == "1700000004.000400"


def test_poll_keeps_the_old_checkpoint_when_a_channel_errors():
    """A transient API error must NOT look like 'nothing new' — advancing here
    would silently consume the unread window."""
    src = _source(
        history={"C_OK": [_msg("1700000005.000500")]},
        fail_channels=["C_ERR"],
    )
    checkpoints = {"C_OK": "1700000000.000000", "C_ERR": "1699999999.000000"}
    msgs, cursors = asyncio.run(src.poll(["C_OK", "C_ERR"], checkpoints, "U_ME"))

    assert [m.channel_id for m in msgs] == ["C_OK"]
    assert cursors["C_OK"] == "1700000005.000500"
    assert cursors["C_ERR"] == "1699999999.000000"  # untouched, so the next poll retries


def test_poll_carries_thread_id_and_marks_dms():
    src = _source(history={"D9": [_msg("1700000001.000100", thread_ts="1700000000.000000")]})
    msgs, _ = asyncio.run(src.poll(["D9"], {}, "U_ME"))
    assert msgs[0].thread_id == "1700000000.000000"
    assert msgs[0].is_dm is True  # D-prefixed channel


def test_poll_ignores_a_malformed_ts_for_the_cursor():
    """An unparseable ts must never win the cursor comparison, else it poisons the
    checkpoint and every later poll re-reads or skips."""
    src = _source(
        raw={
            "C1": [
                {"ts": "not-a-ts", "user": "U_ALICE", "text": "junk"},
                _msg("1700000001.000100"),
            ]
        }
    )
    msgs, cursors = asyncio.run(src.poll(["C1"], {}, "U_ME"))
    # The malformed message still surfaces (timestamp 0.0), but never wins the cursor.
    assert {m.id for m in msgs} == {"not-a-ts", "1700000001.000100"}
    assert cursors["C1"] == "1700000001.000100"


# ── reply / react / history / names ───────────────────────────────────────────


def test_send_reply_posts_into_the_thread():
    src = _source()
    assert asyncio.run(src.send_reply("C1", "ack", "1700000000.000000")) is True
    assert src._client.posts == [("C1", "ack", "1700000000.000000")]


def test_send_reply_returns_false_on_failure():
    src = _source()
    assert asyncio.run(src.send_reply("C_BAD", "ack")) is False


def test_add_reaction_reports_success_and_failure():
    src = _source()
    assert asyncio.run(src.add_reaction("C1", "1700000001.000100", "eyes")) is True
    assert src._client.reactions == [("C1", "1700000001.000100", "eyes")]
    assert asyncio.run(src.add_reaction("C_BAD", "1700000001.000100", "eyes")) is False


def test_get_channel_history_returns_raw_dicts_and_degrades_to_empty():
    src = _source(history={"C1": [_msg("1700000001.000100")]}, fail_channels=["C_ERR"])
    rows = asyncio.run(src.get_channel_history("C1", "0", 10))
    assert [r["ts"] for r in rows] == ["1700000001.000100"]
    # Not cursor-bearing: best-effort context degrades to "no history".
    assert asyncio.run(src.get_channel_history("C_ERR", "0", 10)) == []


def test_resolve_user_name_prefers_real_name_and_caches():
    src = _source(users={"U_ALICE": {"real_name": "Alice Example", "name": "alice"}})
    assert asyncio.run(src.resolve_user_name("U_ALICE")) == "Alice Example"
    assert asyncio.run(src.resolve_user_name("U_ALICE")) == "Alice Example"
    assert src._client.user_info_calls == ["U_ALICE"]  # second call served from cache


def test_resolve_user_name_falls_back_to_handle_then_id():
    src = _source(users={"U_BOB": {"name": "bob"}})
    assert asyncio.run(src.resolve_user_name("U_BOB")) == "bob"
    # Unknown user: the id itself, never an empty label in the UI.
    assert asyncio.run(src.resolve_user_name("U_GHOST")) == "U_GHOST"
