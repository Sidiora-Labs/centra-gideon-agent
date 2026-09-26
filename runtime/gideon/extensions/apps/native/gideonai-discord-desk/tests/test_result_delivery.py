"""DiscordDeskDelivery — splitting, edit-streaming, approvals, reactions, links.

All against a recording fake API, so nothing here can reach Discord. The clocks are
injected, so the throttle behaviour is asserted without sleeping. The clauses pinned
here are the ones the wire shape forces: 2000-char splitting, one throttled edit per
interval with a force-flush at stop, the approval button round-trip (prompt, ack,
resolve, components cleared on the final edit) and the deep-link form.
"""

from __future__ import annotations

import asyncio

import pytest

from discord_desk.api import (
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_SUCCESS,
    COMPONENT_ACTION_ROW,
    COMPONENT_BUTTON,
    DISCORD_DESK_MAX_TEXT,
    DiscordDeskApi,
)
from discord_desk.delivery import (
    _EDIT_MIN_INTERVAL,
    INTERACTION_TYPE_COMPONENT,
    DiscordDeskDelivery,
    split_message,
)


class FakeAPI(DiscordDeskApi):
    def __init__(self, *, fail: set[str] | None = None):
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.dms: list[str] = []
        self.uploads: list[dict] = []
        self.acks: list[dict] = []
        self.reactions: list[dict] = []
        self.typing: list[str] = []
        self.users: dict[str, dict] = {}
        self.channels: dict[str, dict] = {}
        self._fail = fail or set()
        self._id = 0

    def _next(self) -> str:
        self._id += 1
        return str(self._id)

    def _boom(self, name: str) -> None:
        if name in self._fail:
            raise RuntimeError(f"{name} failed")

    async def get_gateway_bot(self):
        return {"url": "wss://gateway.discord.gg", "session_start_limit": {"remaining": 1000}}

    async def create_message(self, channel_id, content, *, components=None, message_reference=None):
        self._boom("create_message")
        mid = self._next()
        self.sent.append({"channel_id": channel_id, "content": content,
                          "components": components, "id": mid})
        return {"id": mid}

    async def edit_message(self, channel_id, message_id, content, *, components=None):
        self._boom("edit_message")
        self.edits.append({"channel_id": channel_id, "message_id": message_id,
                           "content": content, "components": components})
        return {"id": message_id}

    async def create_dm(self, user_id):
        self._boom("create_dm")
        self.dms.append(str(user_id))
        return {"id": f"dm-{user_id}", "type": 1}

    async def upload_file(self, channel_id, file_path, *, filename="", content=""):
        mid = self._next()
        self.uploads.append({"channel_id": channel_id, "path": file_path,
                             "filename": filename, "content": content})
        return {"id": mid}

    async def get_channel(self, channel_id):
        self._boom("get_channel")
        return self.channels.get(str(channel_id), {"id": str(channel_id), "name": "general"})

    async def get_user(self, user_id):
        self._boom("get_user")
        return self.users.get(str(user_id), {"id": str(user_id), "username": "someone"})

    async def create_interaction_response(self, interaction_id, interaction_token, *,
                                          callback_type=6):
        self._boom("create_interaction_response")
        self.acks.append({"id": interaction_id, "token": interaction_token,
                          "type": callback_type})

    async def add_reaction(self, channel_id, message_id, emoji):
        self._boom("add_reaction")
        self.reactions.append({"channel_id": channel_id, "message_id": message_id,
                               "emoji": emoji})

    async def trigger_typing(self, channel_id):
        self._boom("trigger_typing")
        self.typing.append(str(channel_id))


def _delivery(owner="42", **kwargs):
    return DiscordDeskDelivery(FakeAPI(**kwargs), owner)


class TestSplitMessage:
    def test_short_text_is_one_part(self):
        assert split_message("hi") == ["hi"]

    def test_empty_text_is_no_parts(self):
        assert split_message("") == []

    def test_splits_at_2000(self):
        parts = split_message("x" * 5000)
        assert len(parts) == 3
        assert all(len(p) <= DISCORD_DESK_MAX_TEXT for p in parts)

    def test_prefers_newline_boundaries(self):
        text = ("a" * 1500) + "\n" + ("b" * 1000)
        parts = split_message(text)
        assert parts[0] == "a" * 1500
        assert parts[1] == "b" * 1000

    def test_hard_splits_when_no_newline(self):
        parts = split_message("y" * 2500)
        assert len(parts[0]) == DISCORD_DESK_MAX_TEXT


class TestTextDelivery:
    @pytest.mark.asyncio
    async def test_deliver_text_sends_markdown_as_is(self):
        """Discord renders standard markdown — no escaping layer, unlike Telegram."""
        d = _delivery()
        mid = await d.deliver_text("500", "Hello. **bold**")
        assert d._api.sent[0]["content"] == "Hello. **bold**"
        assert mid == d._api.sent[0]["id"]

    @pytest.mark.asyncio
    async def test_deliver_text_redacts_credentials(self):
        d = _delivery()
        await d.deliver_text("500", "token sk-ABC123DEF456GHI789JKL012MNO345PQR")
        assert "sk-ABC123DEF456GHI789JKL012MNO345PQR" not in d._api.sent[0]["content"]

    @pytest.mark.asyncio
    async def test_deliver_text_splits_long_body(self):
        d = _delivery()
        await d.deliver_text("500", "x" * 5000)
        assert len(d._api.sent) == 3
        assert all(len(s["content"]) <= DISCORD_DESK_MAX_TEXT for s in d._api.sent)

    @pytest.mark.asyncio
    async def test_deliver_notification_titles_and_redacts(self):
        d = _delivery()
        await d.deliver_notification("500", "Heartbeat", "all good sk-ABC123DEF456GHI789JKL012MNO")
        body = d._api.sent[0]["content"]
        assert "**Heartbeat**" in body
        assert "sk-ABC123DEF456GHI789JKL012MNO" not in body

    @pytest.mark.asyncio
    async def test_deliver_cron_result_headers_the_first_part_only(self):
        d = _delivery()
        await d.deliver_cron_result("500", "nightly", "job-1", "x" * 3000)
        assert "**Cron: nightly**" in d._api.sent[0]["content"]
        assert "**Cron: nightly**" not in d._api.sent[1]["content"]
        assert all(len(s["content"]) <= DISCORD_DESK_MAX_TEXT for s in d._api.sent)

    @pytest.mark.asyncio
    async def test_deliver_subagent_reply_appends_timing_footer(self):
        d = _delivery()
        await d.deliver_subagent_reply("500", "done", elapsed_secs=3.25)
        assert d._api.sent[0]["content"] == "done"
        assert d._api.sent[-1]["content"] == "_took 3.2s_"

    @pytest.mark.asyncio
    async def test_deliver_subagent_reply_without_elapsed_has_no_footer(self):
        d = _delivery()
        await d.deliver_subagent_reply("500", "done")
        assert len(d._api.sent) == 1

    @pytest.mark.asyncio
    async def test_deliver_rich_attaches_components_when_the_payload_carries_components(self):
        d = _delivery()
        rows = [{"type": COMPONENT_ACTION_ROW, "components": []}]
        await d.deliver_rich("500", {"components": rows}, "fallback")
        assert d._api.sent[0]["components"] == rows

    @pytest.mark.asyncio
    async def test_deliver_rich_falls_back_for_foreign_payload(self):
        """A Slack Block Kit payload isn't renderable here — use the fallback text."""
        d = _delivery()
        await d.deliver_rich("500", {"blocks": [{"type": "section"}]}, "plain fallback")
        assert d._api.sent[0]["content"] == "plain fallback"
        assert d._api.sent[0]["components"] is None

    @pytest.mark.asyncio
    async def test_deliver_chat_mirror_renders_options_as_buttons(self):
        d = _delivery()
        await d.deliver_chat_mirror("500", "Pick one\n[OPTIONS: Yes | No]")
        rows = d._api.sent[-1]["components"]
        buttons = rows[0]["components"]
        assert [b["label"] for b in buttons] == ["Yes", "No"]
        assert [b["custom_id"] for b in buttons] == ["opt:0", "opt:1"]
        assert all(b["type"] == COMPONENT_BUTTON for b in buttons)

    @pytest.mark.asyncio
    async def test_deliver_chat_mirror_chunks_options_five_per_row(self):
        """Discord allows at most 5 buttons per action row."""
        d = _delivery()
        opts = " | ".join(f"o{i}" for i in range(7))
        await d.deliver_chat_mirror("500", f"pick\n[OPTIONS: {opts}]")
        rows = d._api.sent[-1]["components"]
        assert [len(r["components"]) for r in rows] == [5, 2]

    @pytest.mark.asyncio
    async def test_deliver_chat_mirror_without_options_sends_no_buttons(self):
        d = _delivery()
        await d.deliver_chat_mirror("500", "just text")
        assert all(s["components"] is None for s in d._api.sent)


class TestOpenDM:
    @pytest.mark.asyncio
    async def test_opens_and_returns_the_channel_id(self):
        """A Discord user id is NOT a channel id — the DM must be opened."""
        d = _delivery()
        assert await d.open_dm("42") == "dm-42"
        assert d._api.dms == ["42"]

    @pytest.mark.asyncio
    async def test_result_is_cached(self):
        d = _delivery()
        await d.open_dm("42")
        await d.open_dm("42")
        assert d._api.dms == ["42"]  # only one REST call

    @pytest.mark.asyncio
    async def test_empty_user_id_returns_empty(self):
        d = _delivery()
        assert await d.open_dm("") == ""
        assert d._api.dms == []

    @pytest.mark.asyncio
    async def test_failure_degrades_to_empty(self):
        d = _delivery(fail={"create_dm"})
        assert await d.open_dm("42") == ""


class TestBuildThreadLink:
    def test_dm_shape_uses_at_me(self):
        """A DM has no guild, and Discord's own link form for that is literally @me."""
        d = _delivery()
        assert d.build_thread_link("dm-42", "99") == "https://discord.com/channels/@me/dm-42/99"

    def test_guild_shape_uses_the_guild_id(self):
        d = _delivery()
        d.note_channel_guild("700", "g1")
        assert d.build_thread_link("700", "99") == "https://discord.com/channels/g1/700/99"

    def test_without_message_id_links_the_channel(self):
        d = _delivery()
        d.note_channel_guild("700", "g1")
        assert d.build_thread_link("700", "") == "https://discord.com/channels/g1/700"

    def test_empty_channel_has_no_link(self):
        d = _delivery()
        assert d.build_thread_link("", "1") == ""

    def test_guild_map_is_per_instance(self):
        """Two transports must not share a channel→guild map."""
        a, b = _delivery(), _delivery()
        a.note_channel_guild("700", "g1")
        assert b.build_thread_link("700", "9") == "https://discord.com/channels/@me/700/9"


class TestIdentityResolution:
    @pytest.mark.asyncio
    async def test_resolve_user_name_prefers_global_name(self):
        d = _delivery()
        d._api.users["7"] = {"id": "7", "username": "ada_h", "global_name": "Ada"}
        assert await d.resolve_user_name("7") == "Ada"

    @pytest.mark.asyncio
    async def test_resolve_user_name_falls_back_to_username(self):
        d = _delivery()
        d._api.users["7"] = {"id": "7", "username": "ada_h"}
        assert await d.resolve_user_name("7") == "ada_h"

    @pytest.mark.asyncio
    async def test_resolve_user_name_degrades_to_the_id(self):
        d = _delivery(fail={"get_user"})
        assert await d.resolve_user_name("7") == "7"

    @pytest.mark.asyncio
    async def test_resolve_user_profile_degrades_to_the_id(self):
        d = _delivery(fail={"get_user"})
        assert await d.resolve_user_profile("7") == {"id": "7"}

    @pytest.mark.asyncio
    async def test_channel_info_reports_dm_for_type_1(self):
        d = _delivery()
        d._api.channels["dm-1"] = {"id": "dm-1", "type": 1}
        info = await d.channel_info("dm-1")
        assert info["is_im"] is True

    @pytest.mark.asyncio
    async def test_channel_info_reports_guild_channel(self):
        d = _delivery()
        d._api.channels["700"] = {"id": "700", "type": 0, "name": "general", "guild_id": "g1"}
        info = await d.channel_info("700")
        assert info == {"name": "general", "is_im": False, "guild_id": "g1"}

    @pytest.mark.asyncio
    async def test_channel_info_degrades(self):
        d = _delivery(fail={"get_channel"})
        assert await d.channel_info("700") == {"name": "700", "is_im": False}

    def test_list_reply_channels_is_minimal(self):
        assert _delivery().list_reply_channels() == [{"id": "dm", "name": "Direct Message"}]

    def test_is_tracked_channel_delegates_to_core_seam(self):
        # No tracked channels in the isolated home → False, no crash.
        assert _delivery().is_tracked_channel("700") is False


class TestReactionsAndTyping:
    """Both are declared True in capabilities(), so both must actually work."""

    @pytest.mark.asyncio
    async def test_add_reaction_hits_the_api(self):
        d = _delivery()
        assert await d.add_reaction("500", "9", "✅") is True
        assert d._api.reactions == [{"channel_id": "500", "message_id": "9", "emoji": "✅"}]

    @pytest.mark.asyncio
    async def test_add_reaction_failure_is_reported_not_raised(self):
        d = _delivery(fail={"add_reaction"})
        assert await d.add_reaction("500", "9", "✅") is False

    @pytest.mark.asyncio
    async def test_show_typing_hits_the_api(self):
        d = _delivery()
        assert await d.show_typing("500") is True
        assert d._api.typing == ["500"]

    @pytest.mark.asyncio
    async def test_show_typing_failure_is_reported_not_raised(self):
        d = _delivery(fail={"trigger_typing"})
        assert await d.show_typing("500") is False


class TestUploadAttachment:
    @pytest.mark.asyncio
    async def test_uploads_with_caption(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b")
        d = _delivery()
        mid = await d.upload_attachment("500", str(f), initial_comment="look")
        assert d._api.uploads[0]["content"] == "look"
        assert mid == "1"

    @pytest.mark.asyncio
    async def test_caption_is_redacted(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b")
        d = _delivery()
        await d.upload_attachment("500", str(f), title="key sk-ABC123DEF456GHI789JKL012MNO345")
        assert "sk-ABC123DEF456GHI789JKL012MNO345" not in d._api.uploads[0]["content"]


class TestStreamThrottle:
    @pytest.mark.asyncio
    async def test_throttles_to_one_edit_per_interval_then_flushes_exact_final(self):
        d = _delivery()
        clock = {"t": 100.0}
        d._now = lambda: clock["t"]

        sts = await d.start_stream("500", initial_text="…")  # send #1
        assert d._api.sent[0]["id"] == sts
        assert d._api.edits == []

        # First append well within the interval → throttled (no edit).
        clock["t"] = 100.5
        await d.append_stream_task("500", sts, "t1", "Step one", "in_progress")
        assert d._api.edits == []

        # Second append still inside the interval → still throttled.
        clock["t"] = 100.9
        await d.append_stream_task("500", sts, "t2", "Step two", "in_progress")
        assert d._api.edits == []

        # Past the interval → exactly one edit fires, carrying the latest pending text.
        clock["t"] = 100.0 + _EDIT_MIN_INTERVAL + 0.01
        await d.append_stream_task("500", sts, "t3", "Step three", "complete")
        assert len(d._api.edits) == 1
        assert "Step three" in d._api.edits[-1]["content"]

        # stop_stream force-flushes even inside the interval — the exact final text.
        clock["t"] += 0.001  # basically no time passed
        await d.append_stream_task("500", sts, "t4", "Final step", "complete")  # throttled away
        assert len(d._api.edits) == 1  # confirm it was throttled
        await d.stop_stream("500", sts)
        assert len(d._api.edits) == 2  # forced flush
        assert "Final step" in d._api.edits[-1]["content"]

    @pytest.mark.asyncio
    async def test_stream_edit_is_capped_at_the_message_limit(self):
        d = _delivery()
        clock = {"t": 100.0}
        d._now = lambda: clock["t"]
        sts = await d.start_stream("500", initial_text="x" * 1990)
        clock["t"] += 10
        await d.append_stream_task("500", sts, "t1", "y" * 100, "complete")
        assert len(d._api.edits[-1]["content"]) <= DISCORD_DESK_MAX_TEXT

    @pytest.mark.asyncio
    async def test_stop_unknown_stream_is_noop(self):
        d = _delivery()
        await d.stop_stream("500", "999")  # never started
        assert d._api.edits == []

    @pytest.mark.asyncio
    async def test_append_to_unknown_stream_is_noop(self):
        d = _delivery()
        await d.append_stream_task("500", "999", "t", "title", "complete")
        assert d._api.edits == []

    @pytest.mark.asyncio
    async def test_failed_edit_does_not_raise_out(self):
        d = _delivery(fail={"edit_message"})
        clock = {"t": 100.0}
        d._now = lambda: clock["t"]
        sts = await d.start_stream("500", initial_text="…")
        clock["t"] += 10
        await d.append_stream_task("500", sts, "t1", "step", "complete")
        await d.stop_stream("500", sts)  # no raise


class _Event:
    def __init__(self, request_id="req1", title="delete files"):
        self.request_id = request_id
        self.title = title


class TestApprovalRoundTrip:
    @pytest.mark.asyncio
    async def test_full_round_trip(self):
        """post prompt → INTERACTION_CREATE → future resolves → acked → buttons gone."""
        d = _delivery(owner="42")

        task = asyncio.ensure_future(d.request_approval(_Event("reqX", "rm -rf"), source="tool"))
        await asyncio.sleep(0)  # let it open the DM
        await asyncio.sleep(0)  # …and post the prompt
        await asyncio.sleep(0)

        # It prompted the owner's DM (opened, not the raw user id) with two buttons.
        prompt = d._api.sent[-1]
        assert prompt["channel_id"] == "dm-42"
        buttons = prompt["components"][0]["components"]
        assert {b["custom_id"] for b in buttons} == {"approve:reqX", "deny:reqX"}
        assert [b["style"] for b in buttons] == [BUTTON_STYLE_SUCCESS, BUTTON_STYLE_DANGER]

        # The press arrives as an INTERACTION_CREATE and resolves the same future.
        await d.resolve_interaction({
            "id": "i1", "token": "itok", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "approve:reqX"},
        })
        assert await asyncio.wait_for(task, timeout=1.0) is True

        # The interaction was acknowledged (Discord's 3-second window).
        assert d._api.acks[-1]["id"] == "i1"
        assert d._api.acks[-1]["token"] == "itok"

        # And the prompt was edited to the outcome WITH its buttons stripped — a
        # decided request must not leave a clickable Approve behind.
        final = d._api.edits[-1]
        assert "Approved" in final["content"]
        assert final["components"] == []

    @pytest.mark.asyncio
    async def test_deny_resolves_false_and_strips_buttons(self):
        d = _delivery(owner="42")
        task = asyncio.ensure_future(d.request_approval(_Event("reqY"), source="tool"))
        for _ in range(4):
            await asyncio.sleep(0)
        await d.resolve_interaction({
            "id": "i2", "token": "t2", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "deny:reqY"},
        })
        assert await asyncio.wait_for(task, timeout=1.0) is False
        assert "Rejected" in d._api.edits[-1]["content"]
        assert d._api.edits[-1]["components"] == []

    @pytest.mark.asyncio
    async def test_timeout_defaults_to_rejected(self, monkeypatch):
        """Fail closed: an unanswered approval is a rejection, never an approval."""
        monkeypatch.setattr("discord_desk.delivery._APPROVAL_TIMEOUT", 0.01)
        d = _delivery(owner="42")
        assert await d.request_approval(_Event("reqZ"), source="tool") is False
        assert "Rejected" in d._api.edits[-1]["content"]

    @pytest.mark.asyncio
    async def test_prompt_title_is_redacted(self):
        d = _delivery(owner="42")
        task = asyncio.ensure_future(
            d.request_approval(
                _Event("reqR", "push sk-ABC123DEF456GHI789JKL012MNO345"), source="tool"
            )
        )
        for _ in range(4):
            await asyncio.sleep(0)
        assert "sk-ABC123DEF456GHI789JKL012MNO345" not in d._api.sent[-1]["content"]
        await d.resolve_interaction({
            "id": "i", "token": "t", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "deny:reqR"},
        })
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_prompts_the_linked_channel_when_there_is_one(self):
        class Sessions:
            def get_channel(self, key):
                return "700"

        d = _delivery(owner="42")
        task = asyncio.ensure_future(
            d.request_approval(_Event("reqL"), source="tool",
                               parent_session_key="s1", sessions=Sessions())
        )
        for _ in range(4):
            await asyncio.sleep(0)
        assert d._api.sent[-1]["channel_id"] == "700"
        assert d._api.dms == []  # no DM needed
        await d.resolve_interaction({
            "id": "i", "token": "t", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "deny:reqL"},
        })
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_no_owner_no_channel_returns_none(self):
        """None lets the gateway fall back to the dashboard prompt."""
        d = _delivery(owner="")
        assert await d.request_approval(_Event(), source="tool") is None

    @pytest.mark.asyncio
    async def test_on_prompted_hook_receives_the_pending(self):
        seen = {}
        d = _delivery(owner="42")
        task = asyncio.ensure_future(
            d.request_approval(_Event("reqH"), source="tool",
                               on_prompted=lambda p: seen.setdefault("p", p))
        )
        for _ in range(4):
            await asyncio.sleep(0)
        assert seen["p"].request_id == "reqH"
        # A dashboard click resolves the very same future.
        seen["p"].future.set_result("approved")
        assert await asyncio.wait_for(task, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_raising_on_prompted_hook_does_not_break_the_prompt(self):
        d = _delivery(owner="42")

        def boom(pending):
            raise RuntimeError("hook blew up")

        task = asyncio.ensure_future(
            d.request_approval(_Event("reqB"), source="tool", on_prompted=boom)
        )
        for _ in range(4):
            await asyncio.sleep(0)
        await d.resolve_interaction({
            "id": "i", "token": "t", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "approve:reqB"},
        })
        assert await asyncio.wait_for(task, timeout=1.0) is True


class TestResolveInteraction:
    @pytest.mark.asyncio
    async def test_unknown_custom_id_is_still_acknowledged(self):
        """Discord shows 'interaction failed' if nothing answers within 3s — ack
        regardless of whether the press was ours."""
        d = _delivery()
        await d.resolve_interaction({
            "id": "i9", "token": "t9", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "approve:ghost"},
        })
        assert d._api.acks[-1]["id"] == "i9"

    @pytest.mark.asyncio
    async def test_non_component_interaction_is_ignored(self):
        """A slash command (type 2) is not ours and must not be answered here."""
        d = _delivery()
        await d.resolve_interaction({"id": "i1", "token": "t", "type": 2,
                                     "data": {"name": "ping"}})
        assert d._api.acks == []

    @pytest.mark.asyncio
    async def test_option_button_press_is_acked_but_resolves_nothing(self):
        d = _delivery()
        await d.resolve_interaction({
            "id": "i3", "token": "t3", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "opt:1"},
        })
        assert d._api.acks[-1]["id"] == "i3"

    @pytest.mark.asyncio
    async def test_failed_ack_does_not_raise_out(self):
        d = _delivery(fail={"create_interaction_response"})
        await d.resolve_interaction({
            "id": "i4", "token": "t4", "type": INTERACTION_TYPE_COMPONENT,
            "data": {"custom_id": "approve:x"},
        })  # no raise

    @pytest.mark.asyncio
    async def test_second_press_on_a_resolved_request_is_harmless(self):
        d = _delivery(owner="42")
        task = asyncio.ensure_future(d.request_approval(_Event("reqD"), source="tool"))
        for _ in range(4):
            await asyncio.sleep(0)
        payload = {"id": "i", "token": "t", "type": INTERACTION_TYPE_COMPONENT,
                   "data": {"custom_id": "approve:reqD"}}
        await d.resolve_interaction(payload)
        assert await asyncio.wait_for(task, timeout=1.0) is True
        await d.resolve_interaction(payload)  # double press — no InvalidStateError
