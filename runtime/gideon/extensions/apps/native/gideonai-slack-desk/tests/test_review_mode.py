"""Tests for review-mode interactions, blocks, client, and handler draft storage."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_desk_runtime.blocks import (
    review_draft_blocks,
    review_edit_modal,
    review_revise_modal,
)
from slack_desk_runtime.handler import (
    _REVIEW_DRAFT_MAX,
    _REVIEW_DRAFT_TTL,
    _review_drafts,
    _review_drafts_get,
    _review_drafts_pop,
    _review_drafts_set,
)
from slack_desk_runtime.interactions import (
    _can_act_on_review_draft,
    _delete_review_placeholder,
    _handle_review_approve,
    _handle_review_cancel,
    _handle_review_edit,
    _handle_review_edit_submit,
    _handle_review_revise,
    _handle_review_revise_submit,
    _parse_draft_key,
)


class TestReviewDraftBlocks:
    def test_basic_structure(self) -> None:
        blocks = review_draft_blocks("hello", "C1|ts1|abc")
        assert len(blocks) == 5
        assert blocks[0]["type"] == "section"
        assert blocks[4]["type"] == "actions"
        elements = blocks[4]["elements"]
        assert len(elements) == 4
        assert elements[0]["action_id"] == "pc_review_approve"
        assert elements[0]["value"] == "C1|ts1|abc"

    def test_truncates_long_text(self) -> None:
        long_text = "x" * 4000
        blocks = review_draft_blocks(long_text, "C1|ts1|abc")
        display = blocks[2]["text"]["text"]
        assert len(display) <= 3000
        assert display.endswith("…")

    def test_short_text_not_truncated(self) -> None:
        blocks = review_draft_blocks("short", "C1|ts1|abc")
        assert blocks[2]["text"]["text"] == "short"


class TestReviewEditModal:
    def test_structure(self) -> None:
        modal = review_edit_modal("draft text", "C1|ts1|abc")
        assert modal["callback_id"] == "pc_review_edit_submit"
        assert modal["private_metadata"] == "C1|ts1|abc"
        inp = modal["blocks"][0]["element"]
        assert inp["initial_value"] == "draft text"

    def test_truncates_initial_value(self) -> None:
        modal = review_edit_modal("x" * 4000, "key")
        assert len(modal["blocks"][0]["element"]["initial_value"]) <= 3000


class TestReviewReviseModal:
    def test_structure(self) -> None:
        modal = review_revise_modal("C1|ts1|abc")
        assert modal["callback_id"] == "pc_review_revise_submit"
        assert modal["private_metadata"] == "C1|ts1|abc"


# ---------------------------------------------------------------------------
# Draft storage helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_drafts():
    """Clear draft storage between tests."""
    _review_drafts.clear()
    yield
    _review_drafts.clear()


REQUESTER = "UREQ"


class TestReviewDraftStorage:
    def test_set_and_get_returns_draft_and_requester(self) -> None:
        _review_drafts_set("k1", "hello", REQUESTER)
        assert _review_drafts_get("k1") == ("hello", REQUESTER)

    def test_pop_returns_draft_and_requester_then_removes(self) -> None:
        _review_drafts_set("k1", "hello", REQUESTER)
        assert _review_drafts_pop("k1") == ("hello", REQUESTER)
        assert _review_drafts_get("k1") == ("", "")

    def test_get_missing_returns_empty_tuple(self) -> None:
        assert _review_drafts_get("missing") == ("", "")

    def test_pop_missing_returns_empty_tuple(self) -> None:
        assert _review_drafts_pop("missing") == ("", "")

    def test_expired_entry_returns_empty(self) -> None:
        _review_drafts["k1"] = ("old", REQUESTER, time.monotonic() - _REVIEW_DRAFT_TTL - 1)
        assert _review_drafts_get("k1") == ("", "")

    def test_expired_entry_popped_returns_empty(self) -> None:
        _review_drafts["k1"] = ("old", REQUESTER, time.monotonic() - _REVIEW_DRAFT_TTL - 1)
        assert _review_drafts_pop("k1") == ("", "")

    def test_evicts_oldest_at_capacity(self) -> None:
        for i in range(_REVIEW_DRAFT_MAX):
            _review_drafts_set(f"k{i}", f"v{i}", REQUESTER)
        assert len(_review_drafts) == _REVIEW_DRAFT_MAX
        _review_drafts_set("overflow", "new", REQUESTER)
        assert len(_review_drafts) == _REVIEW_DRAFT_MAX
        assert _review_drafts_get("overflow") == ("new", REQUESTER)


# ---------------------------------------------------------------------------
# Client post_ephemeral
# ---------------------------------------------------------------------------
class TestPostEphemeral:
    @pytest.mark.asyncio
    async def test_post_ephemeral_with_blocks_and_thread(self) -> None:
        from slack_desk_runtime.client import RealSlackDeskClient

        mock_web = MagicMock()
        mock_web.chat_postEphemeral = AsyncMock()
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = mock_web
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        await client.post_ephemeral("C1", "U1", "fallback", blocks=blocks, thread_ts="ts1")
        mock_web.chat_postEphemeral.assert_awaited_once_with(
            channel="C1", user="U1", text="fallback", blocks=blocks, thread_ts="ts1",
        )

    @pytest.mark.asyncio
    async def test_post_ephemeral_without_optional_params(self) -> None:
        from slack_desk_runtime.client import RealSlackDeskClient

        mock_web = MagicMock()
        mock_web.chat_postEphemeral = AsyncMock()
        client = RealSlackDeskClient.__new__(RealSlackDeskClient)
        client._web = mock_web
        await client.post_ephemeral("C1", "U1", "text")
        mock_web.chat_postEphemeral.assert_awaited_once_with(
            channel="C1", user="U1", text="text",
        )


# ---------------------------------------------------------------------------
# Interaction handlers
# ---------------------------------------------------------------------------

OWNER_ID = "UOWNER"
REQUESTER_ID = "UREQ"  # the user who @mentioned the bot to produce the draft
STRANGER = "USTRANGER"  # neither owner nor requester


def _make_payload(user_id: str = OWNER_ID, **extra) -> dict:
    p = {"user": {"id": user_id}, "response_url": "https://hooks.slack.com/x"}
    p.update(extra)
    return p


def _make_action(draft_key: str = "C1|ts1|abc") -> dict:
    return {"value": draft_key}


@pytest.fixture
def mock_orch():
    """Patch _orch with a mock orchestrator."""
    orch = MagicMock()
    orch.slack_desk = AsyncMock()
    orch.slack_desk.post_message = AsyncMock()
    orch.slack_desk.set_thread_status = AsyncMock()
    orch.slack_desk.views_open = AsyncMock()
    orch.sessions = MagicMock()
    orch.ctx_builder = MagicMock()
    orch.cron_svc = MagicMock()
    orch.conv_log = MagicMock()
    orch.consolidator = MagicMock()
    orch.subagent_mgr = MagicMock()
    orch.task_runner = MagicMock()
    with patch("slack_desk_runtime.interactions._orch", orch):
        yield orch


@pytest.fixture
def owner_patch():
    """Patch is_owner to return True for OWNER_ID only."""
    def _is_owner(uid: str) -> bool:
        return uid == OWNER_ID
    with patch("slack_desk_runtime.interactions.is_owner", side_effect=_is_owner):
        yield


@pytest.fixture
def sel_mock():
    """Patch sel() to capture audit calls."""
    mock_sel = MagicMock()
    mock_log = mock_sel.log_api_access
    with patch("slack_desk_runtime.interactions.sel", return_value=mock_sel):
        yield mock_log


@pytest.fixture
def auth_err_mock():
    """Patch _post_review_auth_error so we can assert it was invoked on denials."""
    mock = AsyncMock()
    with patch("slack_desk_runtime.interactions._post_review_auth_error", mock):
        yield mock


class TestParseKey:
    def test_valid_three_part(self) -> None:
        assert _parse_draft_key("C1|ts1|abc") == ("C1", "ts1", "C1|ts1|abc")

    def test_two_part(self) -> None:
        assert _parse_draft_key("C1|ts1") == ("C1", "ts1", "C1|ts1")

    def test_single_part_returns_none(self) -> None:
        assert _parse_draft_key("C1") is None

    def test_empty_returns_none(self) -> None:
        assert _parse_draft_key("") is None


class TestCanActOnReviewDraft:
    def test_requester_allowed(self, owner_patch) -> None:
        assert _can_act_on_review_draft(REQUESTER_ID, REQUESTER_ID) is True

    def test_owner_allowed(self, owner_patch) -> None:
        assert _can_act_on_review_draft(OWNER_ID, REQUESTER_ID) is True

    def test_stranger_denied(self, owner_patch) -> None:
        assert _can_act_on_review_draft(STRANGER, REQUESTER_ID) is False

    def test_empty_caller_denied(self, owner_patch) -> None:
        assert _can_act_on_review_draft("", REQUESTER_ID) is False


def _has_sel_call(sel_mock, outcome: str) -> bool:
    """Check if sel_mock (log_api_access) was called with given outcome."""
    return any(c.kwargs.get("outcome") == outcome for c in sel_mock.call_args_list)


class TestHandleReviewApprove:
    @pytest.mark.asyncio
    async def test_owner_can_approve(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "safe text", REQUESTER_ID)
        await _handle_review_approve(_make_payload(user_id=OWNER_ID), _make_action())
        mock_orch.slack_desk.post_message.assert_awaited_once()
        assert _has_sel_call(sel_mock, "allowed")

    @pytest.mark.asyncio
    async def test_requester_can_approve_own_draft(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "safe text", REQUESTER_ID)
        await _handle_review_approve(_make_payload(user_id=REQUESTER_ID), _make_action())
        mock_orch.slack_desk.post_message.assert_awaited_once()
        assert _has_sel_call(sel_mock, "allowed")

    @pytest.mark.asyncio
    async def test_stranger_denied_and_notified(
        self, mock_orch, owner_patch, sel_mock, auth_err_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "text", REQUESTER_ID)
        await _handle_review_approve(_make_payload(user_id=STRANGER), _make_action())
        mock_orch.slack_desk.post_message.assert_not_awaited()
        assert _has_sel_call(sel_mock, "denied")
        auth_err_mock.assert_awaited_once()  # feedback given, not silent

    @pytest.mark.asyncio
    async def test_no_orch_returns(self) -> None:
        with patch("slack_desk_runtime.interactions._orch", None):
            await _handle_review_approve(_make_payload(), _make_action())


class TestHandleReviewCancel:
    @pytest.mark.asyncio
    async def test_requester_can_cancel_own_draft(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "text", REQUESTER_ID)
        await _handle_review_cancel(_make_payload(user_id=REQUESTER_ID), _make_action())
        assert _review_drafts_get("C1|ts1|abc") == ("", "")
        assert _has_sel_call(sel_mock, "allowed")

    @pytest.mark.asyncio
    async def test_stranger_denied_and_draft_preserved(
        self, mock_orch, owner_patch, sel_mock, auth_err_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "text", REQUESTER_ID)
        await _handle_review_cancel(_make_payload(user_id=STRANGER), _make_action())
        assert _review_drafts_get("C1|ts1|abc") == ("text", REQUESTER_ID)
        assert _has_sel_call(sel_mock, "denied")
        auth_err_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_orch_returns(self) -> None:
        with patch("slack_desk_runtime.interactions._orch", None):
            await _handle_review_cancel(_make_payload(), _make_action())


class TestHandleReviewEdit:
    @pytest.mark.asyncio
    async def test_requester_opens_modal(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "draft", REQUESTER_ID)
        payload = _make_payload(user_id=REQUESTER_ID, trigger_id="T123")
        await _handle_review_edit(payload, _make_action())
        mock_orch.slack_desk.views_open.assert_awaited_once()
        modal = mock_orch.slack_desk.views_open.call_args[0][1]
        assert modal["callback_id"] == "pc_review_edit_submit"

    @pytest.mark.asyncio
    async def test_stranger_denied_and_notified(
        self, mock_orch, owner_patch, sel_mock, auth_err_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "draft", REQUESTER_ID)
        payload = _make_payload(user_id=STRANGER, trigger_id="T123")
        await _handle_review_edit(payload, _make_action())
        mock_orch.slack_desk.views_open.assert_not_awaited()
        assert _has_sel_call(sel_mock, "denied")
        auth_err_mock.assert_awaited_once()


class TestHandleReviewEditSubmit:
    @pytest.mark.asyncio
    async def test_requester_submit_posts_redacted(
        self, mock_orch, owner_patch, sel_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "original", REQUESTER_ID)
        payload = _make_payload(user_id=REQUESTER_ID, view={
            "private_metadata": "C1|ts1|abc",
            "state": {"values": {
                "pc_review_edit_block": {
                    "pc_review_edit_input": {"value": "edited text"}
                }
            }},
        })
        await _handle_review_edit_submit(payload)
        mock_orch.slack_desk.post_message.assert_awaited_once()
        assert _review_drafts_get("C1|ts1|abc") == ("", "")

    @pytest.mark.asyncio
    async def test_stranger_submit_denied(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "original", REQUESTER_ID)
        payload = _make_payload(user_id=STRANGER, view={
            "private_metadata": "C1|ts1|abc",
            "state": {"values": {
                "pc_review_edit_block": {
                    "pc_review_edit_input": {"value": "hacked text"}
                }
            }},
        })
        await _handle_review_edit_submit(payload)
        mock_orch.slack_desk.post_message.assert_not_awaited()
        assert _has_sel_call(sel_mock, "denied")
        # Draft preserved since denial happens before pop
        assert _review_drafts_get("C1|ts1|abc") == ("original", REQUESTER_ID)


class TestHandleReviewRevise:
    @pytest.mark.asyncio
    async def test_requester_opens_modal(self, mock_orch, owner_patch, sel_mock) -> None:
        _review_drafts_set("C1|ts1|abc", "draft", REQUESTER_ID)
        payload = _make_payload(user_id=REQUESTER_ID, trigger_id="T123")
        await _handle_review_revise(payload, _make_action())
        mock_orch.slack_desk.views_open.assert_awaited_once()
        modal = mock_orch.slack_desk.views_open.call_args[0][1]
        assert modal["callback_id"] == "pc_review_revise_submit"

    @pytest.mark.asyncio
    async def test_stranger_denied_and_notified(
        self, mock_orch, owner_patch, sel_mock, auth_err_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "draft", REQUESTER_ID)
        payload = _make_payload(user_id=STRANGER, trigger_id="T123")
        await _handle_review_revise(payload, _make_action())
        mock_orch.slack_desk.views_open.assert_not_awaited()
        assert _has_sel_call(sel_mock, "denied")
        auth_err_mock.assert_awaited_once()


class TestHandleReviewReviseSubmit:
    @pytest.mark.asyncio
    async def test_requester_submit_spawns_handle_message(
        self, mock_orch, owner_patch, sel_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "original draft", REQUESTER_ID)
        payload = _make_payload(user_id=REQUESTER_ID, view={
            "private_metadata": "C1|ts1|abc",
            "state": {"values": {
                "pc_review_revise_block": {
                    "pc_review_revise_input": {"value": "make it shorter"}
                }
            }},
        })

        # Deterministic: capture fire-and-forget tasks and await them, avoiding
        # a time-based sleep heuristic.
        background_tasks: list[asyncio.Task] = []
        orig_create_task = asyncio.create_task

        def _track_task(coro, **kwargs):
            task = orig_create_task(coro, **kwargs)
            background_tasks.append(task)
            return task

        with patch(
            "slack_desk_runtime.interactions.handle_message", new_callable=AsyncMock,
        ) as mock_hm, patch(
            "slack_desk_runtime.interactions.asyncio.create_task",
            side_effect=_track_task,
        ):
            await _handle_review_revise_submit(payload)
            await asyncio.gather(*background_tasks)
            mock_hm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stranger_submit_denied_draft_preserved(
        self, mock_orch, owner_patch, sel_mock,
    ) -> None:
        _review_drafts_set("C1|ts1|abc", "draft", REQUESTER_ID)
        payload = _make_payload(user_id=STRANGER, view={
            "private_metadata": "C1|ts1|abc",
            "state": {"values": {
                "pc_review_revise_block": {
                    "pc_review_revise_input": {"value": "exfiltrate"}
                }
            }},
        })
        await _handle_review_revise_submit(payload)
        # Draft should NOT be popped since handler returns early
        assert _review_drafts_get("C1|ts1|abc") == ("draft", REQUESTER_ID)
        assert _has_sel_call(sel_mock, "denied")


class TestDeleteReviewPlaceholder:
    @pytest.mark.asyncio
    async def test_clears_thread_status(self, mock_orch) -> None:
        await _delete_review_placeholder("C1", "ts1")
        mock_orch.slack_desk.set_thread_status.assert_awaited_once_with("C1", "ts1", "")

    @pytest.mark.asyncio
    async def test_no_orch_no_crash(self) -> None:
        with patch("slack_desk_runtime.interactions._orch", None):
            await _delete_review_placeholder("C1", "ts1")  # no crash
