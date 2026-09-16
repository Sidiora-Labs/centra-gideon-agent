"""Parity: the transport ceilings (aiohttp client_max_size + nginx body caps) must
track the upload policy so the per-filetype gate — not the transport — is the real
limit. These run without Docker (static config + module inspection)."""

import re
from pathlib import Path

from gideon.workspace.uploads.policy import max_category_limit, single_post_threshold

_REPO = Path(__file__).resolve().parent.parent.parent
_NGINX = _REPO / "infrastructure" / "docker" / "nginx.conf.template"

_MB = 1024 * 1024
_GB = 1024**3


def _parse_size(tok: str) -> int:
    """nginx size token → bytes: '2g' / '96m' / '512k'."""
    tok = tok.strip().lower()
    mult = {"g": _GB, "m": _MB, "k": 1024}
    if tok and tok[-1] in mult:
        return int(float(tok[:-1]) * mult[tok[-1]])
    return int(tok)


def _nginx_text() -> str:
    return _NGINX.read_text()


class TestNginxUploadCeilings:
    def test_uploads_location_exists(self):
        text = _nginx_text()
        assert "location /api/uploads/" in text, "dedicated /api/uploads/ block missing"

    def test_uploads_body_cap_covers_video(self):
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        m = re.search(r"client_max_body_size\s+(\S+?);", block)
        assert m, "no client_max_body_size in /api/uploads/ block"
        assert _parse_size(m.group(1)) >= max_category_limit()

    def test_uploads_has_long_timeouts(self):
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        for directive in ("proxy_read_timeout", "proxy_send_timeout"):
            m = re.search(rf"{directive}\s+(\d+)s;", block)
            assert (
                m and int(m.group(1)) >= 3600
            ), f"{directive} too low for a 2 GB upload"

    def test_uploads_request_buffering_off(self):
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        assert "proxy_request_buffering off" in block

    def test_general_api_cap_covers_single_post(self):
        text = _nginx_text()
        after = text.split("location /api/uploads/", 1)[1]
        gen = after.split("location /api/ {", 1)[1].split("}", 1)[0]
        m = re.search(r"client_max_body_size\s+(\S+?);", gen)
        assert m, "general /api/ block missing client_max_body_size"
        cap = _parse_size(m.group(1))
        assert cap >= single_post_threshold()


class TestAiohttpCeiling:
    def test_single_post_ceiling_tracks_threshold(self):
        from gideon.interfaces.dashboard.server import _single_post_ceiling

        ceiling = _single_post_ceiling()
        assert ceiling >= single_post_threshold()
        assert ceiling < max_category_limit()
