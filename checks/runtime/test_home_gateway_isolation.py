"""Real subprocess/HTTP proof confined to temporary homes and ephemeral sockets."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from gideon.engine import gateway_base
from gideon.engine.home_gateway import HomeGatewayMismatch, open_loopback, require_home_gateway


class HomeGatewayIsolation(unittest.TestCase):
    def test_process_claim_and_local_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            old = dict(os.environ)
            os.environ["GIDEON_HOME"] = str(home)
            try:
                claim = gateway_base.claim_home()
                inode = (home / gateway_base.CLAIM_FILE).stat().st_ino
                command = [sys.executable, "-c", "from gideon.engine.gateway_base import claim_home; claim_home()"]
                blocked = subprocess.run(command, capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(b"already owns", blocked.stderr)
                # Seed replacement must retain the kernel-locked inode.
                from gideon.operations.seed import SeedDestination
                (home / "old").write_text("discard")
                SeedDestination(True).prepare()
                self.assertEqual((home / gateway_base.CLAIM_FILE).stat().st_ino, inode)
                self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
                claim.close()
                self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
                # A hard-linked or symlinked lock is never chmodded or claimed.
                lock = home / gateway_base.CLAIM_FILE
                lock.unlink()
                victim = Path(directory) / "victim"
                victim.write_text("unchanged")
                victim.chmod(0o644)
                os.link(victim, lock)
                with self.assertRaises(gateway_base.HomeAlreadyClaimed):
                    gateway_base.claim_home()
                self.assertEqual(victim.stat().st_mode & 0o777, 0o644)
                lock.unlink()
                lock.symlink_to(victim)
                with self.assertRaises(OSError):
                    gateway_base.claim_home()
                lock.unlink()

                seen = []
                fingerprint = gateway_base.home_fingerprint()
                facts = gateway_base.process_facts(os.getpid())
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        seen.append(dict(self.headers))
                        if self.path == "/redirect":
                            self.send_response(302)
                            self.send_header("Location", "http://example.invalid/leak")
                            self.end_headers()
                            return
                        payload = dict(home_fingerprint=fingerprint, process_identity=facts.identity,
                                       pid=os.getpid(), port=self.server.server_port)
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(json.dumps(payload).encode())
                    def log_message(self, *args):
                        pass
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    port = server.server_port
                    os.environ["http_proxy"] = "http://127.0.0.1:1"
                    os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
                    os.environ["no_proxy"] = ""
                    self.assertEqual(require_home_gateway(port), port)
                    self.assertFalse(any("X-Local-Secret" in headers or "Authorization" in headers for headers in seen))
                    other = Path(directory) / "other"
                    other.mkdir()
                    os.environ["GIDEON_HOME"] = str(other)
                    with self.assertRaises(HomeGatewayMismatch):
                        require_home_gateway(port)
                    os.environ["GIDEON_HOME"] = str(home)
                    with self.assertRaises(HomeGatewayMismatch):
                        open_loopback(f"http://127.0.0.1:{port}/redirect")
                    with self.assertRaises(HomeGatewayMismatch):
                        open_loopback("http://example.invalid:80/leak")
                    gateway_base.BoundGateway(port, os.getpid(), "wrong-process").write(home / gateway_base.RUNTIME_FILE)
                    gateway_base.unpublish()
                    self.assertTrue((home / gateway_base.RUNTIME_FILE).exists())
                    gateway_base.BoundGateway(port, os.getpid(), facts.identity).write(home / gateway_base.RUNTIME_FILE)
                    gateway_base.unpublish()
                    self.assertFalse((home / gateway_base.RUNTIME_FILE).exists())
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
            finally:
                if gateway_base._claim:
                    gateway_base._claim.close()
                os.environ.clear()
                os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
