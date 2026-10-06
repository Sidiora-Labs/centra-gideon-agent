from __future__ import annotations

import os
from pathlib import Path

import pytest

from gideon.extensions.apps.staging import UnsafeBundleError, survey


def test_outbound_link_is_refused_and_internal_link_stays_a_link(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-secret"
    outside.write_text("must not be read", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "payload.py").write_text("value = 1\n", encoding="utf-8")
    os.symlink("payload.py", app / "alias.py")
    staged = survey(app).copy_to(tmp_path / "safe-stage")
    assert os.readlink(staged / "alias.py") == "payload.py"
    os.symlink(outside, app / "escape.py")
    with pytest.raises(UnsafeBundleError, match="outside"):
        survey(app)


def test_special_files_are_refused(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    os.mkfifo(app / "pipe")
    with pytest.raises(UnsafeBundleError, match="special file"):
        survey(app)
