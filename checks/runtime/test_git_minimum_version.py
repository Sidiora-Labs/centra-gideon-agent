from __future__ import annotations

import shutil

import pytest

from gideon.security.net.git import MIN_GIT_VERSION, git_version, require_git


def test_available_git_meets_the_neutral_settings_minimum() -> None:
    if shutil.which("git") is None:
        pytest.skip("requires Git")
    version = git_version()
    if version is not None and version < MIN_GIT_VERSION:
        with pytest.raises(OSError, match=r"requires Git 2\.12 or newer"):
            require_git()
    else:
        require_git()
