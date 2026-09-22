"""Tests for sandbox._probe_sandbox_exec."""

import subprocess
from unittest.mock import patch

from gideon.security.sandbox import _probe_sandbox_exec


@patch("gideon.security.sandbox.sys")
def test_non_darwin_returns_false(mock_sys):
    mock_sys.platform = "linux"
    assert _probe_sandbox_exec() is False


@patch("gideon.security.sandbox.subprocess.run")
@patch("gideon.security.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("gideon.security.sandbox.sys")
def test_macos_26_plus_uses_runtime_probe(mock_sys, mock_which, mock_run):
    mock_sys.platform = "darwin"
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    assert _probe_sandbox_exec() is True
    assert mock_run.call_count == 1


@patch("gideon.security.sandbox.sys")
@patch("gideon.security.sandbox.shutil.which", return_value=None)
def test_which_not_found_returns_false(mock_which, mock_sys):
    mock_sys.platform = "darwin"
    assert _probe_sandbox_exec() is False


@patch("gideon.security.sandbox.sys")
@patch("gideon.security.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("gideon.security.sandbox.subprocess.run")
def test_sandbox_exec_works(mock_run, mock_which, mock_sys):
    mock_sys.platform = "darwin"
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    assert _probe_sandbox_exec() is True


@patch("gideon.security.sandbox.sys")
@patch("gideon.security.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("gideon.security.sandbox.subprocess.run")
def test_sandbox_exec_fails_returns_false(mock_run, mock_which, mock_sys):
    mock_sys.platform = "darwin"
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    assert _probe_sandbox_exec() is False


@patch("gideon.security.sandbox.sys")
@patch("gideon.security.sandbox.shutil.which", return_value="/usr/bin/sandbox-exec")
@patch("gideon.security.sandbox.subprocess.run", side_effect=OSError("timeout"))
def test_subprocess_exception_returns_false(mock_run, mock_which, mock_sys):
    mock_sys.platform = "darwin"
    assert _probe_sandbox_exec() is False
