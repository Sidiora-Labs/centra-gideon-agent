"""Script author calls retain HTTP error details across the real transport."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from gideon.automation.script_worker import GatewayChannel, ScriptContext


@pytest.fixture
def http_endpoint():
    requests = []
    response = {"status": 200, "body": b'{"ok": true}'}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(response["status"])
            self.send_header("Content-Length", str(len(response["body"])))
            self.end_headers()
            self.wfile.write(response["body"])

        def log_message(self, format, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        context = ScriptContext("scheduled work")
        context._channel = GatewayChannel({"port": server.server_port})
        try:
            yield context, response, requests
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
def test_tool_client_errors_preserve_parsed_details(http_endpoint, status):
    context, response, requests = http_endpoint
    details = {"error": "request rejected", "detail": {"field": "arguments"}}
    response.update(status=status, body=json.dumps(details).encode())

    assert context.call_tool("lookup", {"key": "value"}, "catalog") == {
        **details,
        "ok": False,
        "status": status,
    }
    assert requests == [
        (
            "/api/tools/invoke",
            {
                "tool": "lookup",
                "provider": "catalog",
                "arguments": {"key": "value"},
            },
        )
    ]


def test_notify_preserves_existing_error_envelope(http_endpoint):
    context, response, requests = http_endpoint
    envelope = {
        "ok": False,
        "status": "invalid_destination",
        "error": "unknown channel",
    }
    response.update(status=400, body=json.dumps(envelope).encode())

    assert context.notify("hello", channel="missing") == envelope
    assert requests == [("/api/send-message", {"text": "hello", "channel": "missing"})]


@pytest.mark.parametrize(
    "body",
    [
        b"gateway unavailable",
        b"<html>denied</html>",
        b"[]",
        b"null",
        b'"denied"',
        b"\xff",
    ],
)
def test_non_object_error_bodies_have_stable_envelope(http_endpoint, body):
    context, response, _ = http_endpoint
    response.update(status=403, body=body)

    assert context.notify("hello") == {
        "ok": False,
        "status": 403,
        "error": body.decode("utf-8", errors="replace"),
    }


def test_empty_error_body_uses_http_reason(http_endpoint):
    context, response, _ = http_endpoint
    response.update(status=404, body=b"")

    result = context.call_tool("missing")
    assert result == {"ok": False, "status": 404, "error": "HTTP Error 404: Not Found"}


def test_success_body_is_unchanged(http_endpoint):
    context, response, _ = http_endpoint
    response["body"] = b'{"result": [1, 2]}'

    assert context.call_tool("lookup") == {"result": [1, 2]}
