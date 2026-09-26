"""MailDeskDelivery: threading headers, three-message continuity, the streaming no-op
agreement, the reply-token approval, redaction, and attachments.

Every send goes to an injected :class:`FakeSmtpServer`, so the assertions read the precise
headers that would have gone on the wire — the threading agreement IS a header agreement.

The atom's headline requirement lives in :class:`TestThreeMessageContinuity`: the third
message's ``References`` must contain BOTH prior ids, in order.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gideon.sdk.channel import ProviderSettings

from mail_desk_runtime.delivery import (
    APPROVE_WORD,
    DENY_WORD,
    MAX_THREADS,
    MailDeskDelivery,
    ThreadStore,
)
from mail_desk_runtime.mime import parse_inbound
from mail_desk_fakes import FakeSmtpServer, build_message

AGENT = "agent@example.com"
BOB = "bob@example.com"

#: A credential-shaped string for the redaction assertions, ASSEMBLED AT RUNTIME.
#: Written as a literal it trips secret scanners (measured — one flagged this file), which
#: costs a real review cycle instead of a string that was never a key. The concatenation keeps
#: the value the redactor must match while keeping the literal out of the file.
FAKE_KEY = "sk-" + "ant-api03-" + "A" * 24
FAKE_KEY_TAIL = "A" * 24


@pytest.fixture
def wired(tmp_path):
    """A delivery instead of a double SMTP sink, with thread state in a tmp file."""
    smtp = FakeSmtpServer()
    store = ThreadStore(path_provider=lambda: tmp_path / "threads.json")
    delivery = MailDeskDelivery(smtp, AGENT, owner_id=AGENT, threads=store)
    return delivery, smtp, store


def _inbound(
    *, message_id: str, in_reply_to: str = "", references: str = "", subject: str = "Question",
    from_addr: str = BOB, body: str = "hello",
):
    raw = build_message(
        from_addr=from_addr, to_addr=AGENT, subject=subject, message_id=message_id,
        in_reply_to=in_reply_to, references=references, plain=body,
    )
    mail = parse_inbound(raw, 1)
    assert mail is not None
    return mail


class TestThreadingHeaders:
    @pytest.mark.asyncio
    async def test_reply_sets_in_reply_to_and_references(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<m1@example.com>"))
        await delivery.deliver_text(BOB, "the answer", "<m1@example.com>")

        assert smtp.header("In-Reply-To") == "<m1@example.com>"
        assert smtp.header("References") == "<m1@example.com>"
        assert smtp.header("To") == BOB
        assert smtp.header("From") == AGENT

    @pytest.mark.asyncio
    async def test_subject_is_re_prefixed_from_the_thread(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<m1@example.com>", subject="Deploy plan"))
        await delivery.deliver_text(BOB, "ok", "<m1@example.com>")
        assert smtp.header("Subject") == "Re: Deploy plan"

    @pytest.mark.asyncio
    async def test_no_double_re_prefix_on_a_reply_to_a_reply(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<m1@x>", subject="Re: Deploy plan"))
        await delivery.deliver_text(BOB, "ok", "<m1@x>")
        assert smtp.header("Subject") == "Re: Deploy plan"

    @pytest.mark.asyncio
    async def test_a_fresh_message_carries_no_threading_headers(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_text(BOB, "unprompted note")
        assert smtp.last["In-Reply-To"] is None
        assert smtp.last["References"] is None

    @pytest.mark.asyncio
    async def test_every_send_gets_a_unique_message_id(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_text(BOB, "one")
        await delivery.deliver_text(BOB, "two")
        ids = {smtp.header("Message-ID", 0), smtp.header("Message-ID", 1)}
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_deliver_text_returns_the_sent_message_id(self, wired):
        delivery, smtp, _ = wired
        returned = await delivery.deliver_text(BOB, "hi")
        assert returned == smtp.header("Message-ID")


class TestThreeMessageContinuity:
    """The atom's explicit bar: thread continuity across THREE messages via session_map.

    The chain here is inbound → our reply → their reply → our reply. The third message's
    ``References`` must contain both prior ids, in order, or a mail client splits the
    conversation into separate threads."""

    @pytest.mark.asyncio
    async def test_third_message_references_both_prior_ids_in_order(self, wired):
        delivery, smtp, _ = wired

        # 1. Bob opens the thread.
        first = _inbound(message_id="<m1@example.com>", subject="Plan")
        delivery.note_inbound(first)
        root = first.thread_root
        assert root == "<m1@example.com>"

        # 2. We reply — In-Reply-To m1, References [m1].
        reply1_id = await delivery.deliver_text(BOB, "first answer", root)
        assert smtp.header("In-Reply-To") == "<m1@example.com>"
        assert smtp.header("References").split() == ["<m1@example.com>"]

        # 3. Bob replies to OUR message, quoting the chain the way a client does.
        second = _inbound(
            message_id="<m2@example.com>", in_reply_to=reply1_id,
            references=f"<m1@example.com> {reply1_id}", subject="Re: Plan",
        )
        assert second.thread_root == root  # same conversation
        delivery.note_inbound(second)

        # 4. We reply again — the third message we send into this thread.
        await delivery.deliver_text(BOB, "second answer", root)
        refs = smtp.header("References").split()
        assert smtp.header("In-Reply-To") == "<m2@example.com>"
        assert "<m1@example.com>" in refs
        assert reply1_id in refs
        assert "<m2@example.com>" in refs
        # Order is the chain order: root first, then each successive parent.
        assert refs.index("<m1@example.com>") < refs.index(reply1_id) < refs.index(
            "<m2@example.com>"
        )

    @pytest.mark.asyncio
    async def test_ids_never_duplicate_across_a_long_chain(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<m1@x>"))
        for _ in range(4):
            await delivery.deliver_text(BOB, "ping", "<m1@x>")
        refs = smtp.header("References").split()
        assert len(refs) == len(set(refs))

    @pytest.mark.asyncio
    async def test_two_threads_with_the_same_person_stay_separate(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<a1@x>", subject="Topic A"))
        delivery.note_inbound(_inbound(message_id="<b1@x>", subject="Topic B"))

        await delivery.deliver_text(BOB, "answer A", "<a1@x>")
        assert smtp.header("References").split() == ["<a1@x>"]
        await delivery.deliver_text(BOB, "answer B", "<b1@x>")
        assert smtp.header("References").split() == ["<b1@x>"]


class TestThreadStorePersistence:
    def test_state_survives_a_new_store_over_the_same_file(self, tmp_path):
        """A cron result threaded onto an existing conversation after a restart would
        otherwise start a NEW thread in the user's client."""
        path = tmp_path / "threads.json"
        first = ThreadStore(path_provider=lambda: path)
        first.note_message("<root@x>", message_id="<m1@x>", subject="S", correspondent=BOB)

        second = ThreadStore(path_provider=lambda: path)
        state = second.get("<root@x>")
        assert state is not None
        assert state.last_message_id == "<m1@x>"
        assert state.subject == "S"
        assert state.correspondent == BOB

    def test_corrupt_file_degrades_to_empty(self, tmp_path):
        path = tmp_path / "threads.json"
        path.write_text("{not json", encoding="utf-8")
        assert ThreadStore(path_provider=lambda: path).get("<root@x>") is None

    def test_non_dict_json_degrades_to_empty(self, tmp_path):
        path = tmp_path / "threads.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert ThreadStore(path_provider=lambda: path).get("<root@x>") is None

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert ThreadStore(path_provider=lambda: tmp_path / "nope.json").get("<x>") is None

    def test_store_is_trimmed_to_the_bound(self, tmp_path):
        path = tmp_path / "threads.json"
        store = ThreadStore(path_provider=lambda: path)
        for i in range(MAX_THREADS + 25):
            store.note_message(f"<r{i}@x>", message_id=f"<m{i}@x>")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == MAX_THREADS
        # The newest survive; the oldest aged out.
        assert f"<r{MAX_THREADS + 24}@x>" in data
        assert "<r0@x>" not in data

    def test_touching_a_thread_keeps_it_warm(self, tmp_path):
        path = tmp_path / "threads.json"
        store = ThreadStore(path_provider=lambda: path)
        store.note_message("<keep@x>", message_id="<m0@x>")
        for i in range(MAX_THREADS):
            store.note_message(f"<r{i}@x>", message_id=f"<m{i}@x>")
            if i % 50 == 0:
                store.note_message("<keep@x>", message_id=f"<k{i}@x>")
        assert store.get("<keep@x>") is not None

    def test_default_path_lands_in_the_apps_data_dir(self):
        """The real (uninjected) path must be under the app's own data dir, which
        survives app updates — a cursor/thread file elsewhere would be wiped."""
        store = ThreadStore()
        assert store._path().name == "threads.json"
        assert store._path().parent.name == "data"
        assert store._path().parent.parent.name == "gideonai-mail-desk"


class TestStreamingIsAbsent:
    """C3: the streaming trio is MUST-NOT on this channel. Explicit no-ops."""

    @pytest.mark.asyncio
    async def test_start_stream_returns_empty_and_sends_nothing(self, wired):
        delivery, smtp, _ = wired
        assert await delivery.start_stream(BOB, "<m1@x>", initial_text="Thinking…") == ""
        assert smtp.sent == []  # a mail per token would be absurd

    @pytest.mark.asyncio
    async def test_append_and_stop_are_no_ops(self, wired):
        delivery, smtp, _ = wired
        assert await delivery.append_stream_task(BOB, "", "t1", "Reading", "in_progress") is None
        assert await delivery.stop_stream(BOB, "") is None
        assert smtp.sent == []

    @pytest.mark.asyncio
    async def test_core_mirror_path_shape_holds(self, wired):
        """Core does ``ts = await start_stream(...) or ""`` and then guards every later
        stream call on a truthy ts. Returning "" is what makes that guard skip us."""
        delivery, smtp, _ = wired
        ts = await delivery.start_stream(BOB, "<m1@x>") or ""
        assert not ts
        if ts:  # pragma: no cover - the point is that this branch never runs
            await delivery.stop_stream(BOB, ts)
        assert smtp.sent == []


class TestDeliveryMethods:
    @pytest.mark.asyncio
    async def test_deliver_rich_sends_an_html_alternative(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_rich(BOB, {"html": "<p>rich</p>"}, "plain fallback")
        types = {p.get_content_type() for p in smtp.last.walk() if not p.is_multipart()}
        assert types == {"text/plain", "text/html"}

    @pytest.mark.asyncio
    async def test_deliver_rich_falls_back_to_plain_for_an_opaque_payload(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_rich(BOB, {"blocks": [{"type": "section"}]}, "plain fallback")
        assert smtp.last.get_content_type() == "text/plain"
        assert "plain fallback" in smtp.body_text()

    @pytest.mark.asyncio
    async def test_cron_result_is_subject_tagged(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_cron_result(BOB, "nightly", "job-1", "all green")
        assert "nightly" in smtp.header("Subject")
        assert "all green" in smtp.body_text()

    @pytest.mark.asyncio
    async def test_notification_is_subject_tagged(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_notification(BOB, "Heartbeat", "still alive")
        assert "Heartbeat" in smtp.header("Subject")

    @pytest.mark.asyncio
    async def test_chat_mirror_renders_options_as_a_reply_list(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_chat_mirror(BOB, "Pick one\n[OPTIONS: yes | no]")
        body = smtp.body_text()
        assert "1. yes" in body and "2. no" in body
        assert "Reply with one of" in body

    @pytest.mark.asyncio
    async def test_chat_mirror_without_options_sends_plain_text(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_chat_mirror(BOB, "just a reply")
        assert "just a reply" in smtp.body_text()
        assert "Reply with one of" not in smtp.body_text()

    @pytest.mark.asyncio
    async def test_subagent_reply_carries_the_elapsed_footer(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_subagent_reply(BOB, "done", elapsed_secs=12.5)
        assert "took 12.5s" in smtp.body_text()

    @pytest.mark.asyncio
    async def test_subagent_reply_without_timing_has_no_footer(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_subagent_reply(BOB, "done")
        assert "took" not in smtp.body_text()

    @pytest.mark.asyncio
    async def test_open_dm_returns_the_address_and_refuses_a_non_address(self, wired):
        delivery, _, _ = wired
        assert await delivery.open_dm(BOB) == BOB
        assert await delivery.open_dm("U12345") == ""
        assert await delivery.open_dm("") == ""

    @pytest.mark.asyncio
    async def test_channel_info_reports_a_dm(self, wired):
        delivery, _, _ = wired
        info = await delivery.channel_info(BOB)
        assert info["is_im"] is True

    @pytest.mark.asyncio
    async def test_resolve_user_name_uses_a_remembered_display_name(self, wired):
        delivery, _, _ = wired
        delivery.note_inbound(
            _inbound(message_id="<m1@x>", from_addr="Bob Smith <bob@example.com>")
        )
        assert await delivery.resolve_user_name(BOB) == "Bob Smith"

    @pytest.mark.asyncio
    async def test_resolve_user_name_falls_back_to_the_address(self, wired):
        delivery, _, _ = wired
        assert await delivery.resolve_user_name("nobody@example.com") == "nobody@example.com"

    @pytest.mark.asyncio
    async def test_resolve_user_profile_shape(self, wired):
        delivery, _, _ = wired
        profile = await delivery.resolve_user_profile(BOB)
        assert profile["email"] == BOB and profile["id"] == BOB

    def test_list_reply_channels_offers_the_owner_address(self, wired):
        delivery, _, _ = wired
        assert delivery.list_reply_channels() == [{"id": AGENT, "name": AGENT}]

    def test_list_reply_channels_is_empty_without_an_owner(self):
        delivery = MailDeskDelivery(FakeSmtpServer(), AGENT, owner_id="")
        assert delivery.list_reply_channels() == []

    def test_is_tracked_channel_delegates_to_the_core_seam(self, wired):
        from gideon.sdk.channel import track

        delivery, _, _ = wired
        assert delivery.is_tracked_channel(BOB) is False
        track("mail-desk", BOB, "Bob")
        assert delivery.is_tracked_channel(BOB) is True


class TestBuildThreadLink:
    def test_mid_anchor_for_a_message_id(self, wired):
        delivery, _, _ = wired
        assert delivery.build_thread_link(BOB, "<m1@example.com>") == "mid:m1@example.com"

    def test_percent_encodes_unsafe_characters(self, wired):
        delivery, _, _ = wired
        link = delivery.build_thread_link(BOB, "<a b/c@example.com>")
        assert " " not in link and "/" not in link.removeprefix("mid:").split("@")[0]

    def test_empty_without_a_message_id(self, wired):
        delivery, _, _ = wired
        assert delivery.build_thread_link(BOB, "") == ""
        assert delivery.build_thread_link("", "") == ""


class TestRecipientResolution:
    @pytest.mark.asyncio
    async def test_channel_id_is_used_when_it_is_an_address(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_text("carol@example.com", "hi")
        assert smtp.header("To") == "carol@example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_threads_correspondent(self, wired):
        delivery, smtp, _ = wired
        delivery.note_inbound(_inbound(message_id="<m1@x>", from_addr="dave@example.com"))
        await delivery.deliver_text("", "hi", "<m1@x>")
        assert smtp.header("To") == "dave@example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_owner(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_text("not-an-address", "hi")
        assert smtp.header("To") == AGENT

    @pytest.mark.asyncio
    async def test_no_recipient_at_all_sends_nothing(self):
        smtp = FakeSmtpServer()
        delivery = MailDeskDelivery(smtp, AGENT, owner_id="")
        assert await delivery.deliver_text("nope", "hi") == ""
        assert smtp.sent == []


class TestRedaction:
    @pytest.mark.asyncio
    async def test_credentials_never_reach_the_wire(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_text(BOB, f"the key is {FAKE_KEY}")
        assert FAKE_KEY_TAIL not in smtp.body_text()

    @pytest.mark.asyncio
    async def test_redaction_covers_the_html_alternative_too(self, wired):
        delivery, smtp, _ = wired
        await delivery.deliver_rich(BOB, {"html": f"<p>{FAKE_KEY}</p>"}, FAKE_KEY)
        assert FAKE_KEY_TAIL not in smtp.last.as_string()


class TestSendFailure:
    @pytest.mark.asyncio
    async def test_a_refused_send_returns_empty_and_does_not_raise(self):
        smtp = FakeSmtpServer(fail=True)
        delivery = MailDeskDelivery(smtp, AGENT, owner_id=AGENT)
        assert await delivery.deliver_text(BOB, "hi") == ""

    @pytest.mark.asyncio
    async def test_a_refused_send_does_not_advance_the_thread_chain(self, tmp_path):
        """A message that never left must not be recorded as the thread's newest link, or
        the next reply would reference an id no client has ever seen."""
        smtp = FakeSmtpServer(fail_after=1)
        store = ThreadStore(path_provider=lambda: tmp_path / "threads.json")
        delivery = MailDeskDelivery(smtp, AGENT, owner_id=AGENT, threads=store)
        delivery.note_inbound(_inbound(message_id="<m1@x>"))
        good_id = await delivery.deliver_text(BOB, "lands", "<m1@x>")
        assert await delivery.deliver_text(BOB, "refused", "<m1@x>") == ""
        state = store.get("<m1@x>")
        assert state is not None
        assert state.last_message_id == good_id


class TestApprovalReplyToken:
    class _Event:
        def __init__(self, request_id="req-1", title="Run rm -rf /tmp/x"):
            self.request_id = request_id
            self.title = title

    @pytest.mark.asyncio
    async def test_prompt_carries_both_verbs_and_a_token(self, wired):
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        body = smtp.body_text()
        assert APPROVE_WORD in body and DENY_WORD in body
        token = next(iter(delivery._pending))
        assert token in body
        assert delivery.resolve_reply_token(f"{APPROVE_WORD} {token}") is True
        assert await asyncio.wait_for(task, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_deny_resolves_rejected(self, wired):
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        token = next(iter(delivery._pending))
        assert delivery.resolve_reply_token(f"please {DENY_WORD} {token} thanks") is True
        assert await asyncio.wait_for(task, timeout=1.0) is False

    @pytest.mark.asyncio
    async def test_the_verb_alone_decides_nothing(self, wired):
        """An unrelated mail containing "approve" must not approve anything — the token
        has to be present alongside the verb."""
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        assert delivery.resolve_reply_token("sure, approve it") is False
        assert not task.done()
        token = next(iter(delivery._pending))
        delivery.resolve_reply_token(f"{APPROVE_WORD} {token}")
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_the_token_alone_decides_nothing(self, wired):
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        token = next(iter(delivery._pending))
        assert delivery.resolve_reply_token(f"about request {token}") is False
        assert not task.done()
        delivery.resolve_reply_token(f"{DENY_WORD} {token}")
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_deny_wins_when_a_body_carries_both(self, wired):
        """A request to stop must never be read as consent."""
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        token = next(iter(delivery._pending))
        delivery.resolve_reply_token(f"{APPROVE_WORD} {token}\n> {DENY_WORD} {token}")
        assert await asyncio.wait_for(task, timeout=1.0) is False

    @pytest.mark.asyncio
    async def test_case_insensitive_reply(self, wired):
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(delivery.request_approval(self._Event(), source="tool"))
        await asyncio.sleep(0)
        token = next(iter(delivery._pending))
        assert delivery.resolve_reply_token(f"{APPROVE_WORD.lower()} {token.lower()}") is True
        assert await asyncio.wait_for(task, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_returns_none_without_an_address_to_prompt(self):
        delivery = MailDeskDelivery(FakeSmtpServer(), AGENT, owner_id="")
        assert await delivery.request_approval(self._Event(), source="tool") is None

    @pytest.mark.asyncio
    async def test_a_failed_prompt_send_returns_none_and_leaves_no_pending(self):
        delivery = MailDeskDelivery(FakeSmtpServer(fail=True), AGENT, owner_id=AGENT)
        assert await delivery.request_approval(self._Event(), source="tool") is None
        assert delivery._pending == {}

    @pytest.mark.asyncio
    async def test_on_prompted_hook_receives_the_pending_record(self, wired):
        delivery, smtp, _ = wired
        seen = []
        task = asyncio.ensure_future(
            delivery.request_approval(self._Event(), source="tool", on_prompted=seen.append)
        )
        # `on_prompted` fires only AFTER the send, and `_send` hops to a real worker
        # thread (`asyncio.to_thread`). One `sleep(0)` yields once, which cannot span
        # that hop — it passed on a fast machine and failed under CI's slower
        # scheduling. Poll for the hook instead of guessing a tick count.
        deadline = asyncio.get_running_loop().time() + 1.0
        while not seen:
            assert asyncio.get_running_loop().time() < deadline, "on_prompted never fired"
            await asyncio.sleep(0.001)
        assert seen[0].request_id == "req-1"
        delivery.resolve_reply_token(f"{DENY_WORD} {seen[0].token}")
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_the_prompt_title_is_redacted(self, wired):
        delivery, smtp, _ = wired
        task = asyncio.ensure_future(
            delivery.request_approval(self._Event(title=f"echo {FAKE_KEY}"), source="tool")
        )
        await asyncio.sleep(0)
        assert FAKE_KEY_TAIL not in smtp.last.as_string()
        token = next(iter(delivery._pending))
        delivery.resolve_reply_token(f"{DENY_WORD} {token}")
        await asyncio.wait_for(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_no_pending_approvals_means_no_match(self, wired):
        delivery, _, _ = wired
        assert delivery.resolve_reply_token("APPROVE DEADBEEF") is False

    @pytest.mark.asyncio
    async def test_prompt_threads_onto_the_session_channel(self, wired):
        delivery, smtp, _ = wired

        class Sessions:
            def get_channel_link(self, key):
                return "<m1@x>", "carol@example.com"

        delivery.note_inbound(_inbound(message_id="<m1@x>", from_addr="carol@example.com"))
        task = asyncio.ensure_future(
            delivery.request_approval(
                self._Event(), source="tool", parent_session_key="dashboard:chat-1",
                sessions=Sessions(),
            )
        )
        await asyncio.sleep(0)
        assert smtp.header("To") == "carol@example.com"
        assert smtp.header("In-Reply-To") == "<m1@x>"
        token = next(iter(delivery._pending))
        delivery.resolve_reply_token(f"{DENY_WORD} {token}")
        await asyncio.wait_for(task, timeout=1.0)


class TestAttachments:
    @pytest.mark.asyncio
    async def test_file_becomes_a_mime_part(self, wired, tmp_path):
        delivery, smtp, _ = wired
        path = tmp_path / "report.txt"
        path.write_text("the report", encoding="utf-8")
        await delivery.upload_attachment(BOB, str(path), initial_comment="see attached")
        names = [p.get_filename() for p in smtp.last.walk() if p.get_filename()]
        assert names == ["report.txt"]
        assert "see attached" in smtp.body_text()

    @pytest.mark.asyncio
    async def test_explicit_filename_wins(self, wired, tmp_path):
        delivery, smtp, _ = wired
        path = tmp_path / "tmp1234"
        path.write_bytes(b"data")
        await delivery.upload_attachment(BOB, str(path), filename="pretty.csv")
        names = [p.get_filename() for p in smtp.last.walk() if p.get_filename()]
        assert names == ["pretty.csv"]

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty_without_sending(self, wired, tmp_path):
        delivery, smtp, _ = wired
        assert await delivery.upload_attachment(BOB, str(tmp_path / "nope.bin")) == ""
        assert smtp.sent == []


class TestSettingsIntegrationForDelivery:
    def test_provider_settings_round_trip_under_the_isolated_home(self):
        """Sanity that the tmp home really is where the app store lands (the conftest
        isolation agreement) — a leak here would write into the real ~/.gideon."""
        ProviderSettings.update("gideonai-mail-desk", {"folder": "Agent"})
        assert ProviderSettings.load("gideonai-mail-desk")["folder"] == "Agent"
