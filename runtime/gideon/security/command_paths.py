"""Resolve credential-bearing file operands in shell commands without spawning them."""

from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path

_HOME_WORDS = re.compile(
    r"(?:Path\.home\(\)|os\.homedir\(\)|process\.env\.HOME|"
    r"os\.environ\[['\"]HOME['\"]\]|ENV\[['\"]HOME['\"]\])"
)
_PATH_WORD = re.compile(
    r"(?<![\w:])(?:~(?:[+/]|$)|\$HOME(?:/|$)|\$\{HOME\}(?:/|$)|"
    r"%USERPROFILE%(?:/|$)|\$env:USERPROFILE(?:/|$)|/|\.{1,2}/|"
    r"[A-Za-z0-9_.-]+/)[^\s\"'`;|&<>()[\]]+"
)
_BRACES = re.compile(r"\{([^{}]+)\}")


def _expand_braces(value: str) -> tuple[str, ...]:
    match = _BRACES.search(value)
    if not match:
        return (value,)
    alternatives = match.group(1).split(",")
    if len(alternatives) < 2:
        return (value,)
    out: list[str] = []
    for alternative in alternatives:
        out.extend(
            _expand_braces(value[: match.start()] + alternative + value[match.end() :])
        )
        if len(out) >= 128:
            break
    return tuple(out[:128])


def _normalise_home_expressions(command: str) -> str:
    home = str(Path.home())
    text = _HOME_WORDS.sub(home, command)
    gideon_home = os.environ.get("GIDEON_HOME")
    if gideon_home:
        text = re.sub(r"\$\{GIDEON_HOME\}|\$GIDEON_HOME", gideon_home, text)
    text = re.sub(r"%USERPROFILE%|\$env:USERPROFILE", home, text, flags=re.I)
    text = re.sub(r"\$\{HOME\}|\$HOME", home, text)
    quoted_suffix = r"(['\"])(/[^'\"]+)\1"
    text = re.sub(
        re.escape(home) + r"\s*\+\s*" + quoted_suffix,
        lambda match: home + match.group(2),
        text,
    )
    text = re.sub(
        re.escape(home) + r"\s*/\s*(['\"])([^'\"]+)\1",
        lambda match: str(Path(home) / match.group(2)),
        text,
    )
    return text


def _path_candidates(command: str) -> tuple[str, ...]:
    text = _normalise_home_expressions(command)
    return tuple(match.group(0) for match in _PATH_WORD.finditer(text))


def _resolve_path(raw: str, cwd: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    candidate = Path(expanded)
    return (candidate if candidate.is_absolute() else cwd / candidate).resolve()


def _glob_paths(raw: str, cwd: Path) -> tuple[Path, ...]:
    out: list[Path] = []
    for brace in _expand_braces(raw):
        expanded = os.path.expandvars(os.path.expanduser(brace))
        pattern = expanded if os.path.isabs(expanded) else str(cwd / expanded)
        matches = glob.glob(pattern)
        if matches:
            out.extend(Path(item).resolve() for item in matches)
        else:
            out.append(_resolve_path(brace, cwd))
    return tuple(out)


def sensitive_command_paths(
    command: str, *, cwd: str | os.PathLike[str] | None = None
) -> bool:
    """Return whether a literal, expanded, or relative command path is sensitive.

    This is a conservative lexical screen. Dynamic path construction remains the job of
    the existing OS sandbox; the check itself never creates or opens any path.
    """
    if not isinstance(command, str) or "\x00" in command:
        return True
    try:
        if cwd:
            execution_cwd = cwd
        else:
            execution_cwd = ""
            try:
                from gideon.engine.agents.native.builtin_tools import _CURRENT_CWD

                execution_cwd = _CURRENT_CWD.get()
            except (ImportError, AttributeError):
                pass
        base = (
            Path(execution_cwd).expanduser().resolve()
            if execution_cwd
            else Path.cwd().resolve()
        )
        from gideon.security.security import SensitivePaths

        sensitive = SensitivePaths()
        for raw in _path_candidates(command):
            for resolved in _glob_paths(raw, base):
                if sensitive.contains(str(resolved)):
                    return True
        # A quoted path in a language one-liner can omit a slash only when it is a
        # basename; resolve those string literals relative to the real command cwd.
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return True
        for token in tokens:
            if token.startswith("-") or "=" in token:
                continue
            if (base / token).exists() and sensitive.contains(str((base / token).resolve())):
                return True
    except (OSError, RuntimeError, ValueError):
        return True
    return False
