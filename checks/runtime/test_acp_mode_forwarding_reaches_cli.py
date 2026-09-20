"""AAP-5 §2.2: the host-authority permission mode must reach the CLI in the CLI's
OWN vocabulary, and a refusal must never be silent.

Both halves of this file guard a measured live failure, not a hypothetical:

1. **Vocabulary.** ``permission_authority.HOST_AUTHORITY_MODE`` is ``"default"`` —
   claude-code's spelling. codex-acp's ``configId="mode"`` options are
   ``read-only`` / ``agent`` / ``agent-full-access`` (read off a live ``session/new``
   snapshot). Forwarding ``"default"`` verbatim got ``-32602 Invalid params`` on every
   codex session, which left codex on its own ``agent`` mode — "Read and edit files,
   and run commands", i.e. exactly the CLI-is-its-own-authority state §2.2 exists to
   leave. Measured A/B on one live prompt ("write a file in the workspace"): with the
   untranslated mode the write reported ``ungated`` and executed; with ``read-only``
   the same write raised ``session/request_permission`` and parked on the host gate.

2. **Silence.** ``AcpConnection.send_request`` registers the pending future and
   returns WITHOUT awaiting; every ``session/set_*`` site discarded it, so the
   ``-32602`` was read by nobody. A refused permission mode that logs nothing is
   worse than one that fails loudly, because the audit trail says "mode forwarded".
"""

import asyncio
import logging

import pytest

from gideon.integrations.acp.client import AcpClient
from gideon.integrations.acp.dialect import (
    ClaudeCodeDialect,
    CodexDialect,
    DefaultDialect,
    get_dialect,
)
from gideon.integrations.acp.permission_authority import (
    AUTO_APPROVE_MODES,
    HOST_AUTHORITY_MODE,
    PASSTHROUGH_MODES,
    sanitize_mode,
)
from gideon.integrations.acp.types import JsonRpcMessage

CODEX_NATIVE_MODES = {"read-only", "agent", "agent-full-access"}

CLAUDE_NATIVE_MODES = {
    "auto",
    "default",
    "acceptEdits",
    "plan",
    "dontAsk",
    "bypassPermissions",
}


def _sent_mode(dialect, mode: str) -> str | None:
    """The ``value`` this dialect would actually put on the wire, or None for no frame."""
    req = dialect.set_mode_request(session_id="s1", mode=mode)
    return None if req is None else req.params["value"]


def test_codex_never_receives_the_canonical_mode_verbatim():
    """The exact regression: ``default`` on codex was answered ``-32602`` and ignored."""
    sent = _sent_mode(CodexDialect(), HOST_AUTHORITY_MODE)
    assert sent != HOST_AUTHORITY_MODE, (
        "codex-acp does not define a 'default' mode; forwarding it verbatim is the "
        "-32602 that left every codex session self-approving"
    )
    assert (
        sent == "read-only"
    ), "codex's most restrictive mode is the host-authority one"


def test_codex_host_authority_mode_is_its_most_restrictive():
    """``read-only`` is the only codex mode under which the host gates file edits."""
    assert _sent_mode(CodexDialect(), HOST_AUTHORITY_MODE) == "read-only"
    assert _sent_mode(CodexDialect(), HOST_AUTHORITY_MODE) != "agent"


def test_every_mode_the_authority_can_emit_is_a_value_codex_declares():
    """``sanitize_mode`` emits ``default``/``plan``, or (unattended) any auto-approve
    alias verbatim. Every one of them must translate into codex's option set — an
    untranslated value is silently dropped by the adapter, not clamped by it."""
    emitted = set(PASSTHROUGH_MODES) | set(AUTO_APPROVE_MODES)
    for mode in sorted(emitted):
        sent = _sent_mode(CodexDialect(), mode)
        assert sent in CODEX_NATIVE_MODES, f"{mode!r} → {sent!r} is not a codex mode"


def test_unknown_mode_fails_closed_on_codex():
    """An uninterpretable mode resolves to the RESTRICTIVE end. Neither ``agent`` (the
    permissive default) nor "send nothing" is acceptable: both leave codex as its own
    permission authority, which is the bug."""
    sent = _sent_mode(CodexDialect(), "some-mode-no-host-release-knows")
    assert sent == "read-only"


def test_claude_code_vocabulary_is_the_canonical_one():
    """claude-code accepted ``default`` as sent (measured: it replies with the echoed
    configOptions, no error), so its translation is identity — and the canonical
    vocabulary must stay a subset of what it declares."""
    for mode in sorted(PASSTHROUGH_MODES):
        assert _sent_mode(ClaudeCodeDialect(), mode) == mode
    assert PASSTHROUGH_MODES <= CLAUDE_NATIVE_MODES


def test_default_dialect_sends_no_mode_frame():
    """kiro-cli speaks the default dialect and exposes NO permission-mode axis (its
    ``availableModes`` are agent personas). No frame is the correct outcome — §2.6's
    "kiro plans by host enforcement" — and it must not become a fabricated one."""
    assert (
        DefaultDialect().set_mode_request(session_id="s1", mode=HOST_AUTHORITY_MODE)
        is None
    )


def test_empty_mode_sends_no_frame_on_any_dialect():
    """Empty = "keep the adapter's own default". Only reachable from a caller that
    bypasses ``sanitize_mode`` (which never returns empty), so it stays a no-op."""
    for dialect in (DefaultDialect(), ClaudeCodeDialect(), CodexDialect()):
        assert dialect.set_mode_request(session_id="s1", mode="") is None


@pytest.mark.parametrize("cli", ["claude-code", "codex"])
def test_registered_zed_dialects_translate_before_sending(cli):
    """Reached through the registry the bundles actually use, not the class directly."""
    sent = _sent_mode(get_dialect(cli), HOST_AUTHORITY_MODE)
    assert sent is not None
    assert sent in (CODEX_NATIVE_MODES | CLAUDE_NATIVE_MODES)


def test_the_authority_still_clamps_before_the_dialect_translates():
    """Translation is not a widening path: an auto-approve mode from an ATTENDED
    session is clamped to the authority mode first, so codex gets ``read-only`` —
    the dialect only ever translates what the authority already approved."""
    decision = sanitize_mode("bypassPermissions", unattended=False)
    assert decision.downgraded
    assert _sent_mode(CodexDialect(), decision.mode) == "read-only"
    unattended = sanitize_mode("bypassPermissions", unattended=True)
    assert not unattended.downgraded
    assert _sent_mode(CodexDialect(), unattended.mode) == "agent-full-access"


def _client(tmp_path) -> AcpClient:
    return AcpClient(work_dir=tmp_path, dialect=CodexDialect())


@pytest.mark.asyncio
async def test_adapter_rejection_is_logged(tmp_path, caplog):
    """The measured failure mode: the adapter answers ``-32602`` and the host says
    nothing, so a refused mode reads exactly like an applied one."""
    client = _client(tmp_path)
    params = {"sessionId": "s1", "configId": "mode", "value": "read-only"}
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(
        JsonRpcMessage(id=3, error={"code": -32602, "message": "Invalid params"})
    )
    with caplog.at_level(logging.WARNING, logger="gideon.integrations.acp.client"):
        client._watch_dialect_reply("session/set_config_option", params, 3, fut)
        await asyncio.sleep(0)
    assert "REJECTED" in caplog.text
    assert "-32602" in caplog.text
    assert "mode" in caplog.text


@pytest.mark.asyncio
async def test_accepted_reply_is_not_logged(tmp_path, caplog):
    """The success path must stay quiet, or the warning stops meaning anything."""
    client = _client(tmp_path)
    params = {"sessionId": "s1", "configId": "mode", "value": "read-only"}
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(JsonRpcMessage(id=3, result={"configOptions": []}))
    with caplog.at_level(logging.WARNING, logger="gideon.integrations.acp.client"):
        client._watch_dialect_reply("session/set_config_option", params, 3, fut)
        await asyncio.sleep(0)
    assert "REJECTED" not in caplog.text


@pytest.mark.asyncio
async def test_unanswered_send_stays_best_effort(tmp_path, caplog):
    """A cancelled/never-answered future must not raise out of the callback — the
    send is deliberately fire-and-forget so the handshake pays no latency for it."""
    client = _client(tmp_path)
    params = {"sessionId": "s1", "configId": "mode", "value": "read-only"}
    fut = asyncio.get_running_loop().create_future()
    fut.set_exception(RuntimeError("process gone"))
    with caplog.at_level(logging.WARNING, logger="gideon.integrations.acp.client"):
        client._watch_dialect_reply("session/set_config_option", params, 3, fut)
        await asyncio.sleep(0)
    assert "REJECTED" not in caplog.text
