import os
import shutil
from pathlib import Path

import pytest

from gideon.security import sandbox


@pytest.mark.parametrize("missing", ["env", "sandbox-exec"])
def test_missing_system_binary_rejects_caller_path_before_profile(
    monkeypatch, tmp_path, missing
):
    trusted = tmp_path / "system"
    caller = tmp_path / "caller"
    profiles = tmp_path / "profiles"
    for directory in (trusted, caller, profiles):
        directory.mkdir()
    system_env = shutil.which("env", path=os.confstr("CS_PATH"))
    assert system_env is not None
    for name in ("env", "sandbox-exec"):
        (caller / name).symlink_to(system_env)
        if name != missing:
            (trusted / name).symlink_to(system_env)
    monkeypatch.setenv("PATH", str(caller))
    monkeypatch.setattr(os, "confstr", lambda name: str(trusted))
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(profiles))

    with pytest.raises(FileNotFoundError, match=missing):
        sandbox.sandbox_exec_argv(["true"])
    assert sandbox._probe_sandbox_exec() is False
    assert list(profiles.iterdir()) == []


@pytest.mark.parametrize(
    "system_path", [None, "", ".", ":/usr/bin", "/usr/bin:relative"]
)
def test_invalid_system_path_fails_closed(monkeypatch, tmp_path, system_path):
    monkeypatch.setattr(os, "confstr", lambda name: system_path)
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(tmp_path))

    with pytest.raises(RuntimeError, match="CS_PATH"):
        sandbox.sandbox_exec_argv(["true"])
    assert sandbox._probe_sandbox_exec() is False
    assert list(tmp_path.iterdir()) == []


def test_unavailable_confstr_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "confstr")
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(tmp_path))

    with pytest.raises(RuntimeError, match="CS_PATH"):
        sandbox.sandbox_exec_argv(["true"])
    assert sandbox._probe_sandbox_exec() is False
    assert list(tmp_path.iterdir()) == []


def test_system_resolution_ignores_caller_path_and_probe_rejects_real_failure(
    monkeypatch, tmp_path
):
    system_path = os.confstr("CS_PATH")
    env = shutil.which("env", path=system_path)
    reject = shutil.which("false", path=system_path)
    assert env and reject
    trusted = tmp_path / "system"
    caller = tmp_path / "caller"
    profiles = tmp_path / "profiles"
    for directory in (trusted, caller, profiles):
        directory.mkdir()
    (trusted / "env").symlink_to(env)
    (trusted / "sandbox-exec").symlink_to(reject)
    for name in ("env", "sandbox-exec"):
        (caller / name).symlink_to(env)
    monkeypatch.setenv("PATH", str(caller))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "withheld")
    monkeypatch.setattr(os, "confstr", lambda name: str(trusted))
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.tempfile, "tempdir", str(profiles))

    argv, profile = sandbox.sandbox_exec_argv(["echo", "payload"])
    try:
        assert argv[0] == str(trusted / "env")
        assert argv[argv.index("-f") - 1] == str(trusted / "sandbox-exec")
        assert argv[-2:] == ["echo", "payload"]
        assert argv[argv.index("AWS_SECRET_ACCESS_KEY") - 1] == "-u"
        assert "(version 1)" in Path(profile).read_text()
    finally:
        Path(profile).unlink()
    assert sandbox._probe_sandbox_exec() is False
    assert list(profiles.iterdir()) == []
