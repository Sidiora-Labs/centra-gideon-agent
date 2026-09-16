"""Tests for gideon.core.env."""

import os

from gideon.core.env import _EXTRA_PATH_DIRS, augmented_path


class TestAugmentedPath:
    def test_prepends_extra_dirs_before_base(self) -> None:
        result = augmented_path("/usr/bin")
        dirs = result.split(os.pathsep)
        assert dirs[-1] == "/usr/bin"
        assert any(".local/bin" in d for d in dirs)

    def test_extra_dirs_keep_declared_order(self) -> None:
        result = augmented_path("")
        dirs = result.split(os.pathsep)
        assert len(dirs) == len(_EXTRA_PATH_DIRS)
        local_idx = next(i for i, d in enumerate(dirs) if ".local/bin" in d)
        mise_idx = next(i for i, d in enumerate(dirs) if "mise/shims" in d)
        assert local_idx < mise_idx

    def test_empty_base(self) -> None:
        result = augmented_path("")
        assert result
        assert not result.endswith(os.pathsep)

    def test_no_arg_defaults_empty(self) -> None:
        result = augmented_path()
        assert ".local/bin" in result
