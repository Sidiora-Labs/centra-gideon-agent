"""Guarded acquisition uses real HTTP, native pack extraction, and process locks."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import zstandard

from gideon.assurance.codegraph import grammars
from gideon.core.library_environment import configure_library_environment
from gideon.security.guardrails.ceiling import reset_ceiling


@pytest.fixture
def releases(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    ceiling = tmp_path / "ceiling.json"
    ceiling.write_text(
        json.dumps({"version": 1, "scopes": {"egress": {"value": "listed"}}})
    )
    monkeypatch.setenv("GIDEON_CEILING_FILE", str(ceiling))
    (tmp_path / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": ["localhost"]}}})
    )
    reset_ceiling()
    configure_library_environment()
    grammars.forget()
    official = os.environ.get("GIDEON_TEST_GRAMMAR_BUNDLE")
    if not official:
        pytest.skip("a verified official grammar archive fixture is required")
    compressed = Path(official).read_bytes()
    assert (
        hashlib.sha256(compressed).hexdigest()
        == "f72a6cc06efdc70785ebd283ecfae23e650f12211887349898594c78757d0f5b"
    )
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                if Path(member.name).name == "libtree_sitter_python.so":
                    payload = archive.extractfile(member).read()
                    break
            else:
                raise AssertionError("official archive has no Python grammar")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo("./libtree_sitter_python.so")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    bundle = zstandard.ZstdCompressor().compress(raw.getvalue())
    hits = []
    overrides = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            entry = {
                "url": f"http://localhost:{self.server.server_port}/bundle",
                "size": len(bundle),
                "sha256": hashlib.sha256(bundle).hexdigest(),
                **overrides,
            }
            manifest = {
                "version": grammars.pack_version(),
                "platforms": {grammars.platform_key(): entry},
                "languages": {"python": {"group": "all", "size": 0}},
                "groups": {"all": ["python"]},
            }
            body = bundle if self.path == "/bundle" else json.dumps(manifest).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://localhost:{server.server_port}"
    monkeypatch.setattr(grammars, "RELEASES", base)
    monkeypatch.setattr(grammars, "BUNDLE_SCHEMES", frozenset({"http"}))
    yield base, hits, overrides, tmp_path, payload
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    grammars.forget()
    reset_ceiling()


def test_hash_rejected_without_manifest_publication(releases):
    _, hits, entry, _, _ = releases
    entry["sha256"] = "0" * 64
    with pytest.raises(grammars.GrammarUnavailable, match="digest differs"):
        grammars.ensure("python")
    assert len(hits) == 2
    assert not grammars.manifest_path().exists()
    assert not grammars.bundle_path(grammars.manifest_path().parent).exists()


def test_unsupported_language_never_fetches(releases):
    _, hits, _, _, _ = releases
    with pytest.raises(grammars.GrammarUnavailable, match="no grammar"):
        grammars.ensure("not_a_language")
    assert hits == []


def test_actual_processes_share_one_verified_download(releases):
    base, hits, _, home, _ = releases
    code = """import os
from gideon.core.library_environment import configure_library_environment
configure_library_environment()
from tree_sitter_language_pack import configure,PackConfig
configure(PackConfig(cache_dir=os.environ['TREE_SITTER_LANGUAGE_PACK_CACHE_DIR']))
from gideon.assurance.codegraph import grammars
grammars.RELEASES=os.environ['TEST_RELEASES']
grammars.BUNDLE_SCHEMES=frozenset({'http'})
grammars.ensure('python')
from tree_sitter_language_pack import get_parser
tree=get_parser('python').parse(b'def answer():\\n    return 42\\n')
assert not tree.root_node.has_error
assert tree.root_node.named_children[0].type=='function_definition'
print('native-acquisition-finished')
"""
    env = {**os.environ, "TEST_RELEASES": base}
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for child in children:
        out, err = child.communicate(timeout=40)
        assert child.returncode == 0, (out, err)
        assert "native-acquisition-finished" in out
    assert hits.count("/bundle") == 1
    assert len(hits) == 2


def test_operator_refusal_before_network(releases):
    _, hits, _, home, _ = releases
    (home / "config.json").write_text(
        json.dumps({"security": {"egress": {"deny_hosts": ["localhost"]}}})
    )
    with pytest.raises(grammars.GrammarUnavailable, match="Network|network|Denied"):
        grammars.ensure("python")
    assert hits == []


def test_actual_fetch_integrity_and_native_extraction(releases):
    _, hits, _, home, payload = releases
    from gideon.assurance.codegraph.parse import _get_parser

    parser = _get_parser("python")
    tree = parser.parse(b"def answer():\n    return 42\n")
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == "function_definition"
    assert len(hits) == 2
    folder = grammars.manifest_path().parent
    manifest = json.loads(grammars.manifest_path().read_text())
    assert manifest["platforms"][grammars.platform_key()]["url"].startswith("file:")
    extracted = list(folder.rglob("libtree_sitter_python.so"))
    assert len(extracted) == 1 and extracted[0].read_bytes() == payload
    # Warm acquisition fetches nothing and fresh parser instances remain independent.
    second = _get_parser("python")
    assert second is not parser
    assert not second.parse(b"x = 1\n").root_node.has_error
    assert len(hits) == 2
