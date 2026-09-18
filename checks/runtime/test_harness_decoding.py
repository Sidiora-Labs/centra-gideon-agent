"""The harness must survive bytes it cannot decode, and still name the file and the line.

**Measured gap (2026-09-18).** Every subprocess reader in ``checks/harness`` opened with
``text=True`` and nothing else — ``checks/harness/diff.py``'s shared git wrapper, and
``checks/harness/cli.py``'s ``_tracked_files``. ``text=True`` decodes STRICTLY with the
locale codec, so one latin-1 byte checked into any tracked file makes ``subprocess.run``
itself raise ``UnicodeDecodeError`` while reading git's stdout. Not a finding, not a
skipped file: the ``scan``/``run --diff`` process dies before a single check has run, and
the failure names the codec rather than the file that carries the byte.

The same call also asked git to QUOTE non-ASCII paths (``core.quotePath`` defaults on), so
``café.py`` came back as the nine-character literal ``"caf\\303\\251.py"`` — a string no
caller can open, relative to nothing, and impossible to match against the diff's own file
set. A scan that survived the decode would still have mis-attributed every finding in it.

**What these legs pin.** A real git repo, a real non-ASCII filename, real undecodable bytes
in the diff body — no stubbing, because the defect lived in how the subprocess was spawned
and a fake would have spawned it correctly. The two halves are asserted separately:

* the readers *return* rather than raise (``run_git``, ``_tracked_files``, ``compute_diff``),
  and the path they return is the real one;
* the ATTRIBUTION survives — ``compute_diff`` reports the right changed line numbers for
  the file whose body is undecodable, and ``scanner.scan`` reads that same file and reports
  a finding whose ``file``/``line`` still point at the offending line.

The second half is the load-bearing one: ``errors="replace"`` alone is easy to get to a
green "it didn't crash" while every line number has drifted, and a finding at the wrong
line is worse than no finding at all.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from checks.harness import scanner
from checks.harness.cli import _tracked_files
from checks.harness.diff import compute_diff, run_git

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)

ODD_NAME = "wéird nâme.py"

UNDECODABLE = b"value = 1  # \xff\xfe\x80 raw bytes\n"

_IDENT = (
    "-c",
    "user.name=Harness Test",
    "-c",
    "user.email=harness@test.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose HEAD is clean and whose working tree adds undecodable bytes.

    ``compute_diff``'s base resolution falls through ``origin/main`` → ``main`` →
    ``HEAD~1``; with one commit on ``main`` the merge-base is HEAD itself, so the diff
    under test is exactly the uncommitted edit made here.
    """
    root = tmp_path / "odd-bytes"
    root.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=root)
    target = root / ODD_NAME
    target.write_bytes(b"first = 0\nsecond = 0\n")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "base", cwd=root)
    target.write_bytes(b"first = 0\nsecond = 0\n" + UNDECODABLE)
    return root


def test_the_fixture_really_holds_bytes_utf8_cannot_decode(repo: Path) -> None:
    """Vacuity floor. If this file decoded cleanly, every leg below would pass against
    the old strict reader too."""
    raw = (repo / ODD_NAME).read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")


def test_run_git_returns_a_diff_instead_of_raising(repo: Path) -> None:
    """The reader itself: undecodable stdout comes back as text, not as an exception."""
    rc, out, _ = run_git(["diff", "--unified=0", "HEAD"], repo)
    assert rc == 0
    assert ODD_NAME in out, f"the diff does not name the file it is about: {out!r}"
    assert "�" in out, "nothing was replaced — the fixture's bytes never arrived"


def test_the_odd_filename_stays_usable(repo: Path) -> None:
    """``core.quotePath`` off: the path git returns is a path that can be opened.

    With quoting on this is ``"w\\303\\251ird n\\303\\242me.py"`` — a string that resolves
    to no file, so a finding attributed to it names nothing.
    """
    rc, out, _ = run_git(["ls-files"], repo)
    assert rc == 0
    listed = [ln for ln in out.splitlines() if ln.strip()]
    assert listed == [ODD_NAME], f"git returned an unusable path: {listed!r}"
    assert (repo / listed[0]).is_file()

    tracked = _tracked_files(repo)
    assert tracked == [repo / ODD_NAME]
    assert tracked[0].is_file(), "_tracked_files returned a path that does not exist"


def test_compute_diff_keeps_file_and_line_attribution(repo: Path) -> None:
    """The load-bearing leg: the scan continues AND the line numbers are still right.

    Two lines survive from the base commit, so the added line is line 3 on the new side.
    A decode that dropped or re-split anything would move it.
    """
    diff = compute_diff(root=repo)
    assert diff.files == [ODD_NAME]
    assert diff.changed_lines == {ODD_NAME: {3}}
    assert diff.abs_changed_lines(repo) == {repo / ODD_NAME: {3}}


def test_the_scanner_reads_the_file_and_reports_the_right_line(tmp_path: Path) -> None:
    """End of the chain: a check fires on a file whose body holds undecodable bytes, and
    the finding still points at the offending line.

    ``config-four-points`` is used because it is an ERROR-level check with an exactly
    derivable answer, so "it fired at the right line" is a fact rather than a heuristic.
    """
    root = tmp_path / "scanned"
    loader = root / "runtime" / "gideon" / "core" / "config" / "loader.py"
    loader.parent.mkdir(parents=True)
    source = textwrap.dedent("""
        from dataclasses import dataclass, field
        def _meta(label, help, **k): return {"label": label}
        @dataclass
        class WidgetConfig:
            mapped_field: bool = field(default=True, metadata=_meta("A", "a"))
            forgotten_field: bool = field(default=False, metadata=_meta("B", "b"))
        @dataclass
        class AppConfig:
            @classmethod
            def load(cls, data):
                w = data.get("widget", {})
                return cls(widget=WidgetConfig(mapped_field=bool(w.get("mapped_field", True))))
    """)
    loader.write_bytes(b"# \xff\xfe undecodable header\n" + source.encode("utf-8"))

    findings = [
        f for f in scanner.scan([loader], root) if f.check == "config-four-points"
    ]
    offenders = [f for f in findings if "forgotten_field" in f.what]
    assert offenders, f"the scan produced nothing on an odd-byte file: {findings}"
    assert not any("mapped_field" in f.what for f in findings)

    lines = loader.read_text(encoding="utf-8", errors="replace").splitlines()
    assert "forgotten_field" in lines[offenders[0].line - 1], (
        "the finding's line does not hold the field it is about — attribution drifted "
        f"(line {offenders[0].line}: {lines[offenders[0].line - 1]!r})"
    )
