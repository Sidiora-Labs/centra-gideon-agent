"""Unit tests for file_send supporting functions: hooks, loader, security."""

from unittest.mock import patch

import pytest

from gideon.engine.hooks import FileTooLargeError, safe_read_file_bytes
from gideon.security.security import redact


class TestSafeReadFileBytes:
    def test_reads_normal_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_bytes(b"hello world")
        with patch("gideon.engine.hooks.is_sensitive_path", return_value=False):
            result = safe_read_file_bytes(str(f))
        assert result == b"hello world"

    def test_rejects_sensitive_path(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_bytes(b"secret")
        with patch("gideon.engine.hooks.is_sensitive_path", return_value=True):
            assert safe_read_file_bytes(str(f)) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        with patch("gideon.engine.hooks.is_sensitive_path", return_value=False):
            assert safe_read_file_bytes(str(tmp_path / "nope.txt")) is None

    def test_raises_file_too_large(self, tmp_path):
        f = tmp_path / "big.txt"
        with patch("gideon.engine.hooks.MAX_FILE_BYTES", 10):
            with patch("gideon.engine.hooks.is_sensitive_path", return_value=False):
                f.write_bytes(b"x" * 12)
                with pytest.raises(FileTooLargeError):
                    safe_read_file_bytes(str(f))

    def test_returns_bytes_at_exact_limit(self, tmp_path):
        f = tmp_path / "exact.txt"
        with patch("gideon.engine.hooks.MAX_FILE_BYTES", 10):
            with patch("gideon.engine.hooks.is_sensitive_path", return_value=False):
                f.write_bytes(b"x" * 10)
                assert safe_read_file_bytes(str(f)) == b"x" * 10


class TestOutboxDir:
    def test_creates_and_returns_outbox(self, tmp_path):
        with patch("gideon.core.config.loader.workspace_root", return_value=tmp_path):
            from gideon.core.config.loader import outbox_dir

            result = outbox_dir()
            assert result == tmp_path / "outbox"
            assert result.is_dir()


class TestRedact:
    def test_clean_text_unchanged(self):
        assert redact("hello world") == "hello world"

    def test_redacts_aws_key(self):
        text = "key=AKIAIOSFODNN7EXAMPLE"
        assert redact(text) != text

    def test_redacts_exfiltration_url(self):
        blob = "A" * 50
        text = f"https://evil.example.com/x?data={blob}&{'x' * 200}"
        assert redact(text) != text
