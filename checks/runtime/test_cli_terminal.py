"""Gateway event handling in the interactive terminal client."""

from gideon.interfaces.cli.terminal import GatewayClient, TerminalState


def test_terminal_keeps_live_turn_and_approval_in_its_session():
    state = TerminalState(session="terminal-one", running=True)
    state.event({"type": "chat_chunk", "data": {"session": "terminal-other", "content": "wrong"}})
    state.event({"type": "chat_chunk", "data": {"session": "terminal-one", "content": "hello"}})
    state.event({"type": "chat_chunk", "data": {"session": "terminal-one", "content": " world"}})
    state.event({"type": "tool_call", "data": {"session": "terminal-one", "tool": "read_file", "purpose": "inspect"}})
    state.event({"type": "approval", "data": {"session": "terminal-one", "id": "permit-1", "tool": "write_file", "tool_input": "file.txt"}})
    assert state.pending and state.pending["id"] == "permit-1"
    state.event({"type": "approval_resolved", "data": {"id": "permit-1", "decision": "rejected"}})
    state.event({"type": "chat_done", "data": {"session": "terminal-one"}})
    assert state.pending is None
    assert state.running is False
    assert "Gideon: hello world" in state.lines
    assert "Tool: read_file inspect" in state.lines
    assert "Approval rejected" in state.lines
    assert not any("wrong" in line for line in state.lines)


def test_local_gateway_token_is_confined_to_the_requested_origin():
    client = GatewayClient("http://127.0.0.1:6777", token="token/with?characters")
    assert client.url_for("/api/chat?ws=1") == (
        "http://127.0.0.1:6777/api/chat?ws=1&token=token%2Fwith%3Fcharacters"
    )
