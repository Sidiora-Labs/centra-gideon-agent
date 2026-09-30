"""Credential-backed SDK Git login uses real Git and a local HTTPS server."""

from __future__ import annotations

import base64
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gideon.sdk.credentials import CredentialStore
from gideon.sdk.git import run_git


def test_token_auth_never_exposes_token_in_argv_or_git_config(tmp_path, caplog):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is required to exercise Git over a local trusted HTTPS endpoint")

    token = "sdk-token-never-write-this-value"
    store_home = tmp_path / "credentials"
    store_home.mkdir()
    (store_home / "credentials.json").write_text(
        '{"github":{"type":"static_token","value_ref":"github"}}\n',
        encoding="utf-8",
    )
    (store_home / ".env").write_text(f"github={token}\n", encoding="utf-8")
    (store_home / ".env").chmod(0o600)
    credentials = CredentialStore(store_home)

    cert = tmp_path / "server.pem"
    key = tmp_path / "server-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    repository_root = tmp_path / "served"
    repository_root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", str(repository_root / "private.git")],
        check=True,
    )
    authenticated = threading.Event()

    class GitHTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            self._git_backend()

        def do_POST(self):
            self._git_backend()

        def _git_backend(self):
            expected = "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            authenticated.set()
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path, _, query = self.path.partition("?")
            backend_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(repository_root),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_PROJECT_ROOT": str(repository_root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "x-access-token",
                "REMOTE_ADDR": "127.0.0.1",
            }
            response = subprocess.run(
                ["git", "http-backend"],
                input=body,
                env=backend_env,
                capture_output=True,
                check=True,
            ).stdout
            separator = min(
                position
                for position in (response.find(b"\r\n\r\n"), response.find(b"\n\n"))
                if position >= 0
            )
            header_text = response[:separator].decode()
            response_body = response[separator:].lstrip(b"\r\n")
            status = 200
            headers = []
            for line in header_text.splitlines():
                name, _, value = line.partition(":")
                if name.lower() == "status":
                    status = int(value.split()[0])
                elif name:
                    headers.append((name, value.strip()))
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), GitHTTP)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://127.0.0.1:{server.server_port}/private.git"
    # Capture the operation's result without replacing or intercepting its Git subprocess.
    outcome = {}

    def perform():
        try:
            outcome["result"] = run_git(
                ["ls-remote", url], credential="github", credentials=credentials,
                timeout=15, ca_bundle=cert,
            )
        except BaseException as exc:  # surfaced below in the test thread
            outcome["failure"] = exc

    worker = threading.Thread(target=perform, daemon=True)
    try:
        worker.start()
        assert authenticated.wait(10), "Git did not authenticate to the local Git HTTP backend"
        for pid in os.listdir("/proc"):
            if not pid.isdecimal():
                continue
            try:
                command_line = Path("/proc", pid, "cmdline").read_bytes()
            except OSError:
                continue
            assert token.encode() not in command_line
        worker.join(20)
        assert not worker.is_alive(), "Git operation did not terminate"
        failure = outcome.get("failure")
        result = outcome.get("result")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if failure is not None:
        raise failure
    assert result is not None
    assert result.returncode == 0, result.stderr
    assert token not in " ".join(result.args)
    assert token not in result.stdout + result.stderr + caplog.text

    local_repo = tmp_path / "local-repo"
    subprocess.run(["git", "init", "-q", str(local_repo)], check=True)
    local = run_git(
        ["status", "--short"],
        credential="missing-credential-must-not-be-resolved",
        credentials=credentials,
        cwd=local_repo,
    )
    assert local.returncode == 0, local.stderr
    assert token not in " ".join(local.args)
    assert token not in local.stdout + local.stderr + (local_repo / ".git" / "config").read_text()
