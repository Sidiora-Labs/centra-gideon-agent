"""WS5b — the shared web content extractor (apps/console/extract.py).

Covers sanitization of untrusted HTML, main-content extraction (trafilatura when
present, html2text fallback), title recovery, and graceful empty handling. Does not
hit the network — extraction is pure over an HTML string.
"""

from __future__ import annotations

import pytest

from gideon.integrations.web import extract as ex
from gideon.integrations.web.extract import (
    ExtractedDoc,
    extract_main_content,
    meta_refresh_target,
    sanitize_html,
)

_PAGE = """
<!DOCTYPE html>
<html><head><title>  Real  Title </title></head>
<body>
  <nav>home about contact</nav>
  <script>alert('xss'); window.evil=1;</script>
  <style>.x{color:red}</style>
  <article>
    <h1>The Heading</h1>
    <p>This is the genuine article body with enough words to be treated as the
       main content of the page by a boilerplate-removing extractor.</p>
    <p>A second meaningful paragraph continues the article content here.</p>
  </article>
  <footer>copyright 2026</footer>
</body></html>
"""


def test_sanitize_strips_script_and_style():
    out = sanitize_html(_PAGE)
    assert "alert(" not in out
    assert "window.evil" not in out


def test_sanitize_empty():
    assert sanitize_html("") == ""


def test_meta_refresh_target_parses_quoted_and_unquoted_urls():
    assert (
        meta_refresh_target(
            '<meta HTTP-EQUIV="refresh" content="0; URL=\'/next?a=1\'">'
        )
        == "/next?a=1"
    )
    assert (
        meta_refresh_target(
            "<meta http-equiv=refresh content='2;url=https://example.com/end'>"
        )
        == "https://example.com/end"
    )
    assert meta_refresh_target("<meta name=description content='refresh'>") == ""


def test_extract_returns_main_content():
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert isinstance(doc, ExtractedDoc)
    assert "genuine article body" in doc.text
    assert "home about contact" not in doc.text
    assert "alert(" not in doc.text
    assert doc.char_count == len(doc.text)
    assert doc.extractor in {"trafilatura", "html2text"}


def test_extract_recovers_title():
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert doc.title.strip() != ""


def test_extract_empty_html():
    doc = extract_main_content("")
    assert doc.text == ""
    assert doc.extractor == "raw"


def test_fallback_path_when_trafilatura_absent(monkeypatch):
    monkeypatch.setattr(ex, "_trafilatura", None)
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert doc.extractor == "html2text"
    assert "genuine article body" in doc.text
    assert "alert(" not in doc.text


def test_fallback_title_from_title_tag(monkeypatch):
    monkeypatch.setattr(ex, "_trafilatura", None)
    doc = extract_main_content(
        "<html><head><title>Just A Title</title></head><body><p>hi there friend</p></body></html>"
    )
    assert doc.title == "Just A Title"


def test_sanitize_fallback_without_nh3(monkeypatch):
    monkeypatch.setattr(ex, "_nh3", None)
    with pytest.raises(ex.SanitizerUnavailable, match="html_sanitizer_unavailable"):
        sanitize_html("<p>ok</p><script>bad()</script>")


def test_sanitizer_failure_withholds_markup_from_all_consumers(monkeypatch):
    unsafe = (
        '<article><h1>Safe title for humans</h1><p onclick="steal()">body</p>'
        '<a href="javascript:steal()">open</a><script>steal()</script></article>'
    )
    if ex._nh3 is not None:
        clean = sanitize_html(unsafe)
        assert "onclick" not in clean
        assert "javascript:" not in clean
        assert "steal()" not in clean

    monkeypatch.setattr(ex, "_nh3", None)
    with pytest.raises(ex.SanitizerUnavailable, match="html_sanitizer_unavailable"):
        sanitize_html(unsafe)

    doc = extract_main_content(unsafe)
    assert not doc.ok
    assert doc.extractor == "withheld"
    assert doc.error == "html_sanitizer_unavailable: nh3 is not installed"
    assert doc.text == ""
    assert "steal" not in doc.text

    from gideon.integrations.knowledge_providers.html_dom import parse_html
    from gideon.integrations.knowledge_providers.web_source import (
        _extract_field,
        apply_post_process,
    )

    with pytest.raises(ex.SanitizerUnavailable, match="html_sanitizer_unavailable"):
        apply_post_process(
            unsafe,
            [{"name": "sanitize_html"}],
            page_url="https://example.com",
        )
    with pytest.raises(ex.SanitizerUnavailable, match="html_sanitizer_unavailable"):
        _extract_field(
            parse_html(unsafe),
            {"extractor": "html", "selector": "article"},
            page_url="https://example.com",
            sanitize_default=True,
        )
