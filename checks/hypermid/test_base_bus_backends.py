from __future__ import annotations

import os
import socket
from urllib.parse import urlparse


def test_nats_gate_targets_a_real_jetstream_server() -> None:
    endpoint = os.environ["HYPERMID_TEST_NATS_URL"]
    parsed = urlparse(endpoint)
    assert parsed.scheme == "nats" and parsed.hostname and parsed.port
    with socket.create_connection((parsed.hostname, parsed.port), timeout=2) as connection:
        info = connection.recv(4096)
        assert info.startswith(b"INFO ") and b'"jetstream":true' in info
        connection.sendall(b'CONNECT {"verbose":false,"pedantic":true}\r\nPING\r\n')
        response = connection.recv(4096)
        if b"PONG" not in response:
            response += connection.recv(4096)
        assert b"PONG" in response
