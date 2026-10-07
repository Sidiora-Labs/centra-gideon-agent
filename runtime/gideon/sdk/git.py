"""Credential-safe Git operations for SDK callers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from gideon.sdk.credentials import CredentialStore
from gideon.security.net.git import git_argv, git_child_env

_TOKEN_ENV = "GIDEON_SDK_GIT_TOKEN"
_ASKPASS = '#!/usr/bin/env python3\nimport os, sys\nprompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""\nif "username" in prompt:\n    print("x-access-token")\nelif "password" in prompt:\n    print(os.environ.get("GIDEON_SDK_GIT_TOKEN", ""))\n'
_REMOTE_COMMANDS = frozenset(
    {"clone", "fetch", "pull", "push", "ls-remote", "send-pack", "upload-pack"}
)
_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
)


def _validate_remote_urls(args: Sequence[str]) -> None:
    for value in args:
        if "://" not in value:
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "Authenticated SDK Git operations require HTTPS URLs without embedded credentials"
            )


def _subcommand(args: Sequence[str]) -> str | None:
    index = 0
    while index < len(args):
        value = args[index]
        if value in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
        elif value == "--":
            index += 1
            return args[index] if index < len(args) else None
        elif value.startswith("-"):
            index += 1
        else:
            return value
    return None


def _uses_remote(args: Sequence[str]) -> bool:
    command = _subcommand(args)
    if command == "remote":
        return len(args) > 1 and args[1] in {"prune", "update"}
    if command == "submodule":
        return len(args) > 1 and args[1] == "update"
    return command in _REMOTE_COMMANDS


def run_git(
    args: Sequence[str],
    *,
    credential: str | None = None,
    credentials: CredentialStore | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 60,
    git: str = "git",
    ca_bundle: str | os.PathLike[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run local Git neutrally and resolve a named token only for remote operations.

    The token is never interpolated into argv, Git configuration, a URL, or a file. It is
    present only in the short-lived child environment while remote Git may need authentication.
    """
    if not args:
        raise ValueError("Git arguments are required")
    remote = _uses_remote(args)
    token = None
    env = git_child_env(site="sdk-git")
    if remote:
        if not isinstance(credential, str) or not credential.strip():
            raise ValueError(
                "A stored credential name is required for remote Git operations"
            )
        if not isinstance(credentials, CredentialStore):
            raise TypeError("Remote Git operations require a Gideon CredentialStore")
        token = credentials.resolve(credential).secret
        if not token:
            raise ValueError("The requested Git credential is unavailable")
        _validate_remote_urls(args)
        if any(token in value for value in args):
            raise ValueError("Pass a credential name, not a token")
        env[_TOKEN_ENV] = token
        env["GIT_TERMINAL_PROMPT"] = "0"
        if ca_bundle is not None:
            env["GIT_SSL_CAINFO"] = os.fspath(ca_bundle)

    argv = git_argv(list(args), https_only=remote, git=git)
    try:
        if remote:
            with tempfile.TemporaryDirectory(prefix="gideon-sdk-git-") as helper_dir:
                helper = Path(helper_dir) / "askpass"
                helper.write_text(_ASKPASS, encoding="utf-8")
                helper.chmod(0o700)
                env["GIT_ASKPASS"] = str(helper)
                result = _run(argv, cwd=cwd, env=env, timeout=timeout)
        else:
            result = _run(argv, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        output = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        error = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        if token:
            output, error = output.replace(token, "[redacted]"), error.replace(
                token, "[redacted]"
            )
        raise subprocess.TimeoutExpired(
            argv, timeout, output=output, stderr=error
        ) from None

    stdout, stderr = result.stdout or "", result.stderr or ""
    if token:
        stdout, stderr = stdout.replace(token, "[redacted]"), stderr.replace(
            token, "[redacted]"
        )
    return subprocess.CompletedProcess(
        argv,
        result.returncode,
        stdout,
        stderr,
    )


def _run(argv: list[str], *, cwd, env: dict[str, str], timeout: float):
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


__all__ = ["run_git"]
