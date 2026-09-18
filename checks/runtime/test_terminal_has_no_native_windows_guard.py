"""The terminal panel is POSIX-only BY DECISION, and this rail is what keeps it that way.

## What was actually found (the "revert" this enforces)

`docs/architecture/WINDOWS_NATIVE_AUDIT.md` §4 costs a native-Windows terminal and
answers **no-go**: keep Windows on Docker Desktop and WSL2, where the real POSIX code
runs with its guarantees intact. The audit is explicit that it "writes **no**
implementation code" — that is the soul guardrail of that rung — and §4 states as a
FINDING that the handler has "no `sys.platform` guard and no third-party PTY
dependency".

History agrees. Every revision of this module that has ever existed (including its
pre-restructure path `runtime/gideon/dashboard/handlers/terminal.py`) imports `fcntl`,
`pty` and `termios` unconditionally at module load; no revision has ever contained
`sys.platform`, `platform.system`, `os.name`, a conditional import, or a PTY shim
(`pywinpty`, `winpty`, `pexpect`, `ptyprocess`, `msvcrt`). The only occurrence of the
word "Windows" in the file is a docstring on `_tmux_available` noting that tmux has no
Windows build — a fact about tmux, not a branch. So there was no partial guard left to
delete; the deliverable is this rail, which makes the decision enforced rather than
merely recorded.

## Why a guard would be a defect and not a courtesy

An earlier precursor proposed fail-closed guards around the terminal facilities that do
not exist outside POSIX — wrapping the imports so the page degrades instead of raising.
That proposal is superseded. A guard here buys nothing a supported configuration ever
sees (the two supported Windows paths run a real Linux kernel, where these imports
succeed) and costs something real: an `ImportError` swallowed at load turns "this build
cannot serve a terminal" into a runtime `NameError` deep inside `_spawn`, and a
`sys.platform` branch is a second, untested PTY path that no CI leg exercises. The
import crash the audit describes is the honest signal.

## Preserved alongside

`api_terminal_delete`'s unknown-session answer is the shared structured
`json_error("not_found", status=404)` envelope. It is asserted here too, because "remove
the Windows work" must not take the error-response work out with it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import gideon

TERMINAL = (
    Path(gideon.__file__).parent
    / "interfaces"
    / "dashboard"
    / "handlers"
    / "terminal.py"
)

#: The POSIX-only stdlib modules the PTY path is built on. They are the imports a
#: native-Windows guard would have had to wrap, so they are what this rail watches.
POSIX_ONLY = {"fcntl", "pty", "termios"}

#: A branch on any of these is a platform discriminator: the shape of a native-Windows guard.
PLATFORM_ATTRS = {("sys", "platform"), ("platform", "system"), ("os", "name")}

#: Windows PTY shims. None is in `pyproject.toml`; importing one would be the port starting.
WINDOWS_PTY_SHIMS = {"pywinpty", "winpty", "pexpect", "ptyprocess", "msvcrt", "conpty"}

#: Platform tokens a guard compares against. Deliberately NOT the bare word "Windows",
#: which appears in `_tmux_available`'s docstring as a fact about tmux.
PLATFORM_TOKENS = {"win32", "cygwin", "msys"}


def _tree() -> ast.Module:
    return ast.parse(TERMINAL.read_text(encoding="utf-8"))


def _imported_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    return {(node.module or "").split(".")[0]}


class TestTheRailIsLookingAtSomething:
    """A rail that inspects an empty or moved file must never read as clean."""

    def test_the_module_exists_and_parses(self) -> None:
        assert TERMINAL.is_file(), f"terminal handler is not at {TERMINAL}"
        assert _tree().body, "terminal.py parsed to an empty module"

    def test_the_posix_pty_path_is_still_the_thing_being_guarded(self) -> None:
        """The three symbols whose absence would make every assertion below vacuous."""
        src = TERMINAL.read_text(encoding="utf-8")
        for marker in ("pty.openpty(", "fcntl.ioctl(", "termios.TIOCSWINSZ"):
            assert marker in src, (
                f"{marker} is gone from terminal.py — the POSIX PTY path moved, so this "
                f"rail is watching the wrong file. Re-point it before trusting it."
            )


class TestNoNativeWindowsGuard:
    def test_the_posix_imports_are_module_level(self) -> None:
        """Nested in an `if`/`try`/function = a guard. They must be top-level statements."""
        tree = _tree()
        top_level = {
            name
            for stmt in tree.body
            if isinstance(stmt, (ast.Import, ast.ImportFrom))
            for name in _imported_names(stmt)
        }
        missing = sorted(POSIX_ONLY - top_level)
        assert not missing, (
            f"{missing} is no longer imported at terminal.py's top level. A native-Windows "
            f"guard is exactly this move; the platform decision (WINDOWS_NATIVE_AUDIT.md §4, "
            f"no-go) is that these imports stay unconditional."
        )

    def test_the_posix_imports_are_unconditional(self) -> None:
        """No `try:`/`if:`/`with:` wrapper anywhere in the module imports them."""
        wrapped: list[str] = []
        for node in ast.walk(_tree()):
            if not isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
                continue
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    for name in _imported_names(child) & POSIX_ONLY:
                        wrapped.append(f"{name} (line {child.lineno})")
        assert not wrapped, (
            "a POSIX-only import is conditional in terminal.py: "
            + ", ".join(wrapped)
            + ". The superseded precursor proposed exactly this fail-closed wrapping; the "
            "final decision is an honest import crash off POSIX, not a degraded page."
        )

    def test_no_import_error_is_swallowed(self) -> None:
        """`except ImportError` is the precursor's shape — a page that degrades silently."""
        offenders = [
            handler.lineno
            for node in ast.walk(_tree())
            if isinstance(node, ast.Try)
            for handler in node.handlers
            if handler.type is not None
            and "ImportError"
            in ast.dump(handler.type)  # Name, Tuple or Attribute alike
        ]
        assert not offenders, (
            f"terminal.py catches ImportError at line(s) {offenders}. A swallowed import "
            f"failure turns 'no terminal on this platform' into a NameError inside _spawn."
        )

    def test_no_platform_discriminator(self) -> None:
        """No `sys.platform`, `platform.system()` or `os.name` anywhere in the module."""
        found = [
            f"{node.value.id}.{node.attr} (line {node.lineno})"
            for node in ast.walk(_tree())
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) in PLATFORM_ATTRS
        ]
        assert not found, (
            "terminal.py branches on the platform: "
            + ", ".join(found)
            + ". Native-Windows support is a no-go (WINDOWS_NATIVE_AUDIT.md), so a second "
            "PTY path here is untested code no CI leg covers."
        )

    def test_no_platform_token_is_compared_against(self) -> None:
        tokens = sorted(
            {
                node.value
                for node in ast.walk(_tree())
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().lower() in PLATFORM_TOKENS
            }
        )
        assert not tokens, f"terminal.py carries platform tokens {tokens}"

    def test_no_windows_pty_shim_is_imported(self) -> None:
        shims = sorted(
            {
                name
                for node in ast.walk(_tree())
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for name in _imported_names(node) & WINDOWS_PTY_SHIMS
            }
        )
        assert not shims, (
            f"terminal.py imports {shims}. None of these is a declared dependency; pulling "
            f"one in is the native-Windows port starting, which is a no-go decision."
        )


class TestTheErrorResponseWorkSurvives:
    """req 32 ac 2: unrelated terminal error-response work is preserved, not reverted with it."""

    def test_the_handler_still_emits_the_shared_structured_envelope(self) -> None:
        tree = _tree()
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "gideon.http_errors"
            and any(a.name == "json_error" for a in node.names)
            for node in ast.walk(tree)
        )
        called = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "json_error"
            for node in ast.walk(tree)
        )
        assert imported and called, (
            "terminal.py no longer routes a failure through gideon.http_errors.json_error. "
            "The structured not-found on DELETE is separate from the platform decision and "
            "must not be reverted alongside it."
        )
