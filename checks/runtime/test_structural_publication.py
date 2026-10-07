import json
import os

import pytest

from gideon.core import atomic_write as publication


def test_stream_commit_and_abort(tmp_path):
    target = tmp_path / "record"
    target.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        with publication.atomic_stream(target, fsync=True, mode=0o600) as stream:
            stream.write(b"aborted")
            raise RuntimeError("abort")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]
    with publication.atomic_stream(target, fsync=True, mode=0o600) as stream:
        stream.write(b"new")
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == 0o600


def test_directory_publication_restores_backup_on_failed_swap(tmp_path, monkeypatch):
    destination, stage, backup = (
        tmp_path / name for name in ("destination", "stage", "backup")
    )
    destination.mkdir()
    (destination / "old").write_text("retained")
    stage.mkdir()
    (stage / "new").write_text("new")
    replace = os.replace
    calls = 0

    def failing_swap(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("publication interrupted")
        return replace(source, target)

    monkeypatch.setattr(publication.os, "replace", failing_swap)
    with pytest.raises(OSError, match="publication interrupted"):
        publication.atomic_directory_publish(stage, destination, backup=backup)
    assert (destination / "old").read_text() == "retained"
    assert (stage / "new").read_text() == "new"
    assert not backup.exists()
    monkeypatch.setattr(publication.os, "replace", replace)
    publication.atomic_directory_publish(stage, destination, backup=backup)
    assert (destination / "new").read_text() == "new"
    assert (backup / "old").read_text() == "retained"


def test_error_envelopes_preserve_wire_protocols():
    from gideon.integrations.inbound.a2a import _rpc_error
    from gideon.interfaces.dashboard.handlers.experiments import _error

    assert _rpc_error("request-1", -32600, "invalid") == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "error": {"code": -32600, "message": "invalid"},
    }
    response = _error("conflict", "changed", 409)
    assert response.status == 409
    assert json.loads(response.body)["error"] == {
        "code": "conflict",
        "message": "changed",
    }
