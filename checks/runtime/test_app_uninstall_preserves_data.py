"""The middle removal rung: uninstall removes the app and KEEPS its ``data/`` (#2541).

Before this, ``app_manager``'s removal ladder had two rungs and a hole between them:
``uninstall()`` is DEACTIVATE (nothing leaves disk) and ``force_uninstall()`` removes
everything including ``data/``. A user who wanted the app gone but the notes they wrote
with it kept had no operation at all, and the force-uninstall confirm dialog told them to
"use Uninstall instead" — a control that did not exist.

These tests drive the REAL cycle the roadmap gate (`PEP-16`) asks about rather than
asserting on a stub: a fixture app is installed into an isolated home, data is written
**through the app's own tool** (a real subprocess that writes markdown and makes real
``git`` commits into ``data/``), the new uninstall runs, and the assertions are that the
app is gone AND every note plus its git history survived — then it is reinstalled and the
notes are read back.

Both directions are proven, because a preservation test that only shows preservation
cannot tell a working preserve from a broken wipe:

* the preserving rung PRESERVES — notes, git history, an empty ``data/``, absence;
* the wiping rung STILL WIPES — ``force_uninstall`` takes ``data/`` with it, takes a
  parked copy from an earlier keep-data uninstall with it, and a reinstall after it
  comes back empty.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from gideon.extensions.apps import app_manager, manager

NOTE_TOOL = """\
import subprocess, sys
from pathlib import Path

book = Path(__file__).resolve().parent / "data" / "notebook"
book.mkdir(parents=True, exist_ok=True)
if not (book / ".git").is_dir():
    subprocess.run(["git", "init", "-q"], cwd=book, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=book, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=book, check=True)

title, body = sys.argv[1], sys.argv[2]
(book / f"{title}.md").write_text(body + "\\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=book, check=True)
subprocess.run(["git", "commit", "-q", "-m", f"note: {title}"], cwd=book, check=True)
print("wrote", title)
"""


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    """Point config_dir at a tmp dir so the whole cycle runs in a throwaway home."""
    import gideon.core.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _bundle(
    tmp_path: Path, *, name: str = "notes-fixture", ships_data: bool = False
) -> Path:
    """A minimal, real app bundle carrying the note tool."""
    src = tmp_path / "src" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Notes Fixture",
                "description": "A git-backed notebook fixture",
            }
        ),
        encoding="utf-8",
    )
    (src / "note.py").write_text(NOTE_TOOL, encoding="utf-8")
    if ships_data:
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / "welcome.md").write_text(
            "shipped by the bundle\n", encoding="utf-8"
        )
    return src


def _write_note(name: str, title: str, body: str) -> None:
    """Run the app's own tool, in the installed app's own dir, as the app would."""
    app = manager.app_dir(name)
    proc = subprocess.run(
        ["python3", str(app / "note.py"), title, body],
        cwd=str(app),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        proc.returncode == 0
    ), f"the app's note tool failed: {proc.stderr or proc.stdout}"


def _notebook(name: str) -> Path:
    return manager.app_dir(name) / "data" / "notebook"


def _staged(name: str) -> Path:
    """Where the keep-data uninstall stages ``data/`` before parking it."""
    return app_manager._quarantine_dir() / f"{name}{app_manager._DATA_STAGE_SUFFIX}"


def _notes_at(book: Path) -> dict[str, str]:
    """The notes in a notebook dir wherever it lives — the app's, or a copy of it.

    Path-based so a surviving copy can be read back as NOTES, not merely counted as a
    directory that exists: "the dir is there" is not evidence the user's work is in it.
    """
    if not book.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(book.glob("*.md"))}


def _notes(name: str) -> dict[str, str]:
    return _notes_at(_notebook(name))


def _git_log_at(book: Path) -> list[str]:
    if not (book / ".git").is_dir():
        return []
    proc = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=str(book),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _git_log(name: str) -> list[str]:
    return _git_log_at(_notebook(name))


def test_real_cycle_notes_and_git_history_survive_uninstall_and_reinstall(tmp_path):
    """install → write notes through the tool → uninstall → reinstall → notes readable.

    This is the transcript of the clause `PEP-16` measured FAILING on 2026-09-06, run in
    a temp home with the real app tool and real git, and it now has to pass end to end.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok

    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before_notes = _notes(name)
    before_log = _git_log(name)
    assert set(before_notes) == {"alpha", "beta"}, before_notes
    assert before_log == ["note: beta", "note: alpha"], before_log
    assert (
        _notebook(name) / ".git"
    ).is_dir(), "the fixture must be writing REAL git history"

    assert app_manager.uninstall_keep_data(name) is True

    assert not manager.app_dir(name).exists(), "the app's tree survived a removal"
    assert manager._read_installed(name) is None
    assert name not in {a["name"] for a in manager.list_apps()}, (
        "a parked data copy is being reported as an installed app; the app must be gone "
        "from discovery, not lingering as a ghost"
    )

    parked = app_manager._preserved_data_dir(name)
    assert parked.is_dir(), "uninstall did not keep the app's data/"
    assert (parked / "notebook" / "alpha.md").is_file()
    assert (parked / "notebook" / ".git").is_dir(), "the git history was not preserved"

    assert not _staged(
        name
    ).exists(), "the keep-data uninstall left its staging copy behind"

    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == before_notes, "notes did not survive uninstall → reinstall"
    assert (
        _git_log(name) == before_log
    ), "git history did not survive uninstall → reinstall"

    assert (
        not parked.exists()
    ), "the parked copy outlived the reinstall that consumed it"


def test_parked_user_data_wins_over_data_the_bundle_ships(tmp_path):
    """A reinstall restores the USER's ``data/`` OVER the bundle's shipped seed content.

    Same precedence the update path already applies: the incoming tree's ``data/`` is
    dropped and the preserved one replaces it wholesale. Proven where it is observable
    — on a file the bundle ships AND the user has since edited. The other way round,
    every reinstall would silently reset the user's edits to the shipped defaults.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path, ships_data=True)
    assert app_manager.install(src, confirm=True).ok
    shipped = manager.app_dir(name) / "data" / "welcome.md"
    assert shipped.read_text(encoding="utf-8") == "shipped by the bundle\n"
    shipped.write_text("edited by the user\n", encoding="utf-8")
    _write_note(name, "mine", "my own note")

    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager.install(src, confirm=True).ok

    assert "mine" in _notes(name), "the user's note lost to the bundle's shipped data/"
    assert shipped.read_text(encoding="utf-8") == "edited by the user\n", (
        "the bundle's shipped data/ overwrote the user's restored copy — a reinstall "
        "just reset their edits to the defaults"
    )


def test_force_uninstall_still_takes_the_data_with_it(tmp_path):
    """The negative half: nothing that deleted has stopped deleting.

    ``force_uninstall`` is deliberately unchanged. If this ever preserves, the two
    rungs have collapsed into one and the destructive control is lying to the user.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "doomed", "this is meant to be destroyed")
    assert (_notebook(name) / "doomed.md").is_file()

    assert app_manager.force_uninstall(name) is True

    assert not manager.app_dir(name).exists()
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "force_uninstall parked a copy of the data it was asked to destroy"
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, "force_uninstall's data came back on reinstall"


def test_a_successful_reinstall_consumes_the_park_so_none_can_go_stale(tmp_path):
    """The ordinary route leaves nothing behind: the reinstall consumes the parked copy.

    install → keep-data uninstall (parked) → install (restores AND consumes) → force
    uninstall. This is the common path, and it must not depend on the stale-park sweep
    below: a park that outlived the install that used it is a second copy of the user's
    data nobody asked for.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager._preserved_data_dir(name).is_dir()

    assert app_manager.install(src, confirm=True).ok
    assert "first" in _notes(name)
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "the park outlived its use"

    assert app_manager.force_uninstall(name) is True
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, "data came back after a force uninstall"


def test_force_uninstall_drops_a_park_a_failed_restore_left_behind(
    tmp_path, monkeypatch
):
    """ "Removes everything" has to include a park the install could not consume.

    A park is normally consumed by the install that restores it. The one way it can
    coexist with an installed app is a restore that FAILED — the park is deliberately
    left on disk then, so the data is recoverable rather than lost. That leaves a live
    app with a parked copy beside it, and a force uninstall that skipped the park would
    let the NEXT install resurrect the very data the destructive button was pressed to
    destroy.

    Driven through the real failure path rather than by planting a directory, because a
    planted one would prove the sweep works on a state the product cannot reach.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)
    assert parked.is_dir()

    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("simulated restore failure")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok

    assert _notes(name) == {}, "the restore was supposed to fail"
    assert parked.is_dir(), (
        "a failed restore discarded the user's only copy; it must be LEFT so the data "
        "is recoverable"
    )

    assert app_manager.force_uninstall(name) is True
    assert (
        not parked.exists()
    ), "force uninstall left a parked copy of the data it was asked to destroy"
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, (
        "data survived a force uninstall via a stale parked copy — the destructive rung "
        "has a hole in it"
    )


def test_a_failed_restore_is_recorded_not_silent(tmp_path, monkeypatch):
    """A restore that fails says so in the audit; the install still completes.

    The user asked for the app, so the install proceeds — but "your data came back" and
    "your data is still parked and did not come back" must not be the same log line.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append(
            (op, outcome, kw.get("detail", ""))
        ),
    )
    real_copytree = app_manager.shutil.copytree
    monkeypatch.setattr(
        app_manager.shutil,
        "copytree",
        lambda s, d, *a, **k: (
            (_ for _ in ()).throw(OSError("nope"))
            if Path(s) == parked
            else real_copytree(s, d, *a, **k)
        ),
    )
    assert app_manager.install(src, confirm=True).ok

    detail = [d for op, outcome, d in records if op == "install" and outcome == "ok"][
        -1
    ]
    assert "preserved_data=restore_failed" in detail, detail


def test_an_empty_data_dir_is_preserved_as_an_empty_data_dir(tmp_path):
    """An EMPTY ``data/`` is parked (as an empty dir), and reported as ``empty``.

    "the app had no data dir" and "the app had a data dir and it held nothing" are
    different facts about the user's state. install() creates ``data/`` for every app,
    so this is the common case for an app the user never wrote through — and it must
    still take the preserving path, not fall through to "nothing to keep".
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    data = manager.app_dir(name) / "data"
    assert data.is_dir() and not any(
        data.iterdir()
    ), "install should mint an empty data/"

    facts = app_manager.describe_app_data(name)
    assert facts["present"] is True and facts["entries"] == 0, facts
    assert app_manager._data_fact("data", data) == "data=empty"

    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)
    assert (
        parked.is_dir()
    ), "an empty data/ was treated as 'no data/' and was not parked"
    assert not any(parked.iterdir())
    assert not _staged(
        name
    ).exists(), "the empty-data/ park left its staging copy behind"


def test_an_absent_data_dir_parks_nothing_and_reports_absent(tmp_path):
    """No ``data/`` at all ⇒ no parked dir is minted, and the fact says ``absent``.

    The other side of the same distinction: a name with nothing parked must not grow an
    empty parked dir, or the next install would "restore" a directory that never
    existed and the two facts would be indistinguishable afterwards.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    import shutil

    shutil.rmtree(manager.app_dir(name) / "data")
    assert not (manager.app_dir(name) / "data").exists()

    facts = app_manager.describe_app_data(name)
    assert facts["present"] is False and facts["entries"] == 0, facts
    assert app_manager._data_fact("data", None) == "data=absent"
    assert (
        app_manager._data_fact("data", manager.app_dir(name) / "data") == "data=absent"
    )

    assert app_manager.uninstall_keep_data(name) is True
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), (
        "an app with NO data/ grew a parked dir; absent and empty have collapsed"
    )


def test_describe_app_data_separates_present_from_entries(tmp_path):
    """``present`` is about existence, ``entries`` about content — never one truthiness.

    The removal dialogs make a different promise in each case, so a single "has data"
    boolean would make the wrong promise for an app with an empty data dir.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "one", "content")
    facts = app_manager.describe_app_data(name)
    assert facts["present"] is True
    assert facts["entries"] == 1, facts
    assert facts["path"].endswith(f".{name}.data"), facts["path"]
    ghost = app_manager.describe_app_data("ghost")
    assert ghost["present"] is False and ghost["entries"] == 0, ghost
    assert app_manager.describe_app_data("Not A Kebab Name") == {
        "present": False,
        "entries": 0,
        "path": "",
        "unconsumed": [],
    }


def test_unknown_app_is_false_and_parks_nothing(tmp_path):
    assert app_manager.uninstall_keep_data("ghost") is False
    assert not (manager.apps_dir() / ".ghost.data").exists()


def test_a_name_that_cannot_hold_a_parked_copy_is_refused_not_guessed(tmp_path):
    """An on-disk app whose dir name is not a mintable app id refuses this rung.

    ``list_apps`` iterates REAL directory names, so such an app is reachable. Both paths
    this rung derives (the quarantine stage and the parked dir) embed the name as a path
    segment and are rmtree/move targets, so the choice is refuse or guess a directory for
    the user's data — and the refusal has to come BEFORE anything is removed. The app
    stays intact and force_uninstall remains available for it.
    """
    weird = "Not Kebab"
    d = manager.apps_dir() / weird
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": weird, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )
    (d / "data").mkdir()
    (d / "data" / "note.md").write_text("still here\n", encoding="utf-8")
    assert manager._read_installed(weird) is not None, "the fixture must be reachable"

    assert app_manager.uninstall_keep_data(weird) is False
    assert (d / "data" / "note.md").is_file(), "the refusal happened after a removal"
    assert d.is_dir()


def test_native_app_refuses_the_middle_rung_too(tmp_path):
    """A native app is locked on: every removal rung refuses, this one included.

    A new rung that skipped the lock would be a way to delete a Tier-1 app.
    """
    src = _bundle(tmp_path, name="native-fixture")
    mani = json.loads((src / "app.json").read_text(encoding="utf-8"))
    mani["native"] = True
    (src / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    assert app_manager.install(src, confirm=True).ok

    assert app_manager.uninstall_keep_data("native-fixture") is False
    assert (
        manager.app_dir("native-fixture") / "app.json"
    ).is_file(), "files were removed"
    assert manager._read_installed("native-fixture") is not None


def test_preservation_failure_removes_nothing(tmp_path, monkeypatch):
    """FAIL-CLOSED: if ``data/`` cannot be copied out, the app is NOT removed.

    The rung's whole promise is "your data survives this". Proceeding to the delete
    having failed to keep it is the exact bug #2541 reports, arrived at by accident.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "precious", "must not be lost")

    import shutil as _shutil

    def _boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(_shutil, "copytree", _boom)

    assert app_manager.uninstall_keep_data(name) is False
    assert manager.app_dir(
        name
    ).is_dir(), "the app was removed after preservation failed"
    assert (_notebook(name) / "precious.md").is_file(), "the note was destroyed anyway"
    assert manager._read_installed(name) is not None, "the app was deregistered anyway"


def test_a_failed_park_keeps_the_last_copy_and_says_where_it_is(tmp_path, monkeypatch):
    """The SECOND failure point: the park fails AFTER the app tree is already gone (#2574).

    The ``copytree`` above is fail-closed because nothing has been removed yet. The park
    is the other end: ``force_uninstall`` has run, the app's tree (and its ``data/``) are
    off disk, and the staged copy in quarantine is the ONLY copy of the user's work left
    on the machine. So it must be LEFT there — the policy ``_restore_preserved_data``
    already applies to a failed restore, "recoverable rather than lost" — and the audit
    line has to name it, or the record is a diagnosis with no recovery in it.

    Driven through the real failure: one raising rename on the real park. Not by planting
    or deleting a directory, which would assert about a state the product cannot reach,
    and not by checking the return value, which was already ``False`` while the data was
    being destroyed.

    The injection point moved from ``shutil.move`` to ``os.rename`` when the park became a
    single atomic rename (#2585) — the failure it drives is the same one, and it now also
    covers the case that used to send ``shutil.move`` into its ``copytree`` fallback. Left
    pointed at ``shutil.move`` this test would have gone quietly VACUOUS: the patch would
    never fire, the park would succeed, and the assertions below would run against a state
    where nothing was ever at risk.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before_notes = _notes(name)
    before_log = _git_log(name)
    assert set(before_notes) == {"alpha", "beta"}, before_notes
    assert before_log == ["note: beta", "note: alpha"], before_log

    staged = _staged(name)
    records: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append(
            (op, outcome, kw.get("detail", ""), str(kw.get("error", "")))
        ),
    )
    real_rename = os.rename
    fired: list[str] = []

    def _fail_park(s, d, *a, **k):
        if Path(s) == staged:
            fired.append("rename")
            raise OSError(errno.ENOSPC, "injected: could not park data/")
        return real_rename(s, d, *a, **k)

    monkeypatch.setattr(os, "rename", _fail_park)

    assert app_manager.uninstall_keep_data(name) is False
    assert fired == [
        "rename"
    ], "the injected park failure never fired; the test is vacuous"

    assert not manager.app_dir(
        name
    ).exists(), "the fixture no longer drives the park branch"
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "the park was meant to fail"

    assert staged.is_dir(), (
        "the failed park deleted the only remaining copy of the user's data: the app tree "
        "is gone and the stage was GC'd behind it (#2574)"
    )
    assert (
        _notes_at(staged / "notebook") == before_notes
    ), "the surviving copy does not read back as the notes that went in"
    assert (
        _git_log_at(staged / "notebook") == before_log
    ), "the surviving copy lost the git history the app wrote"

    detail, error = next(
        (d, e)
        for op, outcome, d, e in records
        if op == "uninstall_keep_data" and outcome == "error"
    )
    assert "data=park_failed" in detail, f"the fact token changed: {detail!r}"
    assert (
        str(staged) in detail or str(staged) in error
    ), f"the audit line does not say where the surviving copy is: {detail!r} / {error!r}"


def test_every_outcome_reaches_the_audit_channel(tmp_path, monkeypatch):
    """Each rung emits an ``app.*`` SEL record with a REAL outcome, not a bare 'ok'.

    A data-affecting lifecycle operation that leaves no auditable trace of WHAT it did
    to the data is indistinguishable afterwards from one that did the other thing.
    """
    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, name, **kw: records.append(
            (op, outcome, kw.get("detail", ""))
        ),
    )

    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "audited", "body")
    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager.install(src, confirm=True).ok

    ops = [(op, outcome) for op, outcome, _ in records]
    assert ("uninstall_keep_data", "ok") in ops, ops
    assert ("force_uninstall", "ok") in ops, ops

    keep = next(d for op, _, d in records if op == "uninstall_keep_data")
    assert keep == "data=1", f"the record must say what happened to data/, saw {keep!r}"

    reinstall = [
        d for op, outcome, d in records if op == "install" and outcome == "ok"
    ][-1]
    assert "preserved_data=1" in reinstall, reinstall
    first_install = [
        d for op, outcome, d in records if op == "install" and outcome == "ok"
    ][0]
    assert "preserved_data=absent" in first_install, (
        "a first install must record that nothing was parked, distinctly from an empty "
        f"park; saw {first_install!r}"
    )


def test_deactivate_rung_is_unchanged_and_still_keeps_the_files(tmp_path):
    """``uninstall()`` still DEACTIVATES. The middle rung was added, not swapped in.

    Repointing this name would have converted every existing caller — the unflagged
    ``DELETE /api/apps/{name}`` among them — from "turn it off" to "delete it".
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "kept", "still here")

    assert app_manager.uninstall(name) is True

    assert manager.app_dir(name).is_dir(), "uninstall() started removing files"
    assert (_notebook(name) / "kept.md").is_file()
    meta = manager._read_installed(name)
    assert meta is not None and meta.enabled is False
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), (
        "deactivate parked a copy; nothing left disk, so there is nothing to park"
    )


def _files_at(root: Path) -> int:
    """File count under *root*, walked with pathlib — not asked of the product."""
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


def _bytes_at(root: Path) -> int:
    return (
        sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if root.is_dir()
        else 0
    )


def _shape(root: Path) -> tuple[int, int, dict[str, str], list[str]]:
    """A copy's whole observable shape: files, bytes, notes read back, git history.

    Four independent oracles, none of them ``app_manager``: a directory that merely
    exists, or one whose entry COUNT matches, is not evidence the user's work is in it —
    which is precisely the trap #2585 names ("non-empty is not complete").
    """
    return (
        _files_at(root),
        _bytes_at(root),
        _notes_at(root / "notebook"),
        _git_log_at(root / "notebook"),
    )


def _capture_audit(monkeypatch) -> list[tuple[str, str, str, str]]:
    records: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append(
            (op, outcome, kw.get("detail", ""), str(kw.get("error", "")))
        ),
    )
    return records


def _keep_data_failure(records: list[tuple[str, str, str, str]]) -> tuple[str, str]:
    """The ``(detail, error)`` of the keep-data rung's refusal-or-error record."""
    return next(
        (detail, error)
        for op, outcome, detail, error in records
        if op == "uninstall_keep_data" and outcome in ("error", "refused")
    )


def test_a_failed_park_leaves_no_partial_copy_at_the_parked_path(tmp_path, monkeypatch):
    """The park is ATOMIC, so a partial parked copy is unreachable rather than detected.

    #2585's first residual: ``shutil.move`` falls back to ``copytree`` + ``rmtree(src)``
    on ANY ``os.rename`` failure, and a ``copytree`` that cannot write one file copies the
    rest and raises at the end — leaving a PARTIAL copy at the parked path beside an
    intact stage. The next ``install`` restored the partial one, because "the parked dir
    exists" was being read as "the parked copy is finished".

    Measured before the fix, with one file failing mid-copy: parked 34 files / 28 044 B,
    stage 35 files / 28 060 B, and the reinstall handed the user the 34.

    The fix is not a completeness check. The destination is provably absent (the rung
    refuses otherwise), both paths are under ``apps/``, so the park is one
    ``Path.rename`` — it happens or it does not. So the assertion is that the parked path
    does not exist AT ALL, and that no ``copytree`` of the stage was ever attempted: the
    fallback that produced the partial copy is not merely survivable now, it is not
    reached.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before = _shape(manager.app_dir(name) / "data")
    assert before[2] and before[3], f"the fixture wrote nothing to measure: {before}"

    staged = _staged(name)
    parked = app_manager._preserved_data_dir(name)
    records = _capture_audit(monkeypatch)
    fired: list[str] = []
    copytree_of_stage: list[str] = []
    real_rename, real_copytree = os.rename, app_manager.shutil.copytree

    def _cross_device(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            fired.append("rename")
            raise OSError(errno.EXDEV, "injected: cross-device link")
        return real_rename(srcp, dstp, *a, **k)

    def _watch_copytree(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            copytree_of_stage.append(str(dstp))
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _cross_device)
        m.setattr(app_manager.shutil, "copytree", _watch_copytree)
        outcome = app_manager.uninstall_keep_data(name)

    assert fired == ["rename"], "the injection never ran, so this test proved nothing"
    assert not manager.app_dir(
        name
    ).exists(), "the fixture no longer drives the park branch"
    assert not copytree_of_stage, (
        "the park fell back to copying the stage — the very path that leaves a partial "
        f"copy behind: {copytree_of_stage}"
    )

    assert not parked.exists(), (
        f"a failed park left {_files_at(parked)} files / {_bytes_at(parked)} bytes at the "
        "parked path; a later install would restore that as if it were the whole thing"
    )
    assert (
        _shape(staged) == before
    ), f"the surviving copy is not what went in: {_shape(staged)} vs {before}"
    assert outcome is False, "a park that did not happen must not report success"
    detail, error = _keep_data_failure(records)
    assert "data=park_failed" in detail, detail
    assert str(staged) in detail or str(staged) in error, (detail, error)


def test_a_second_keep_data_uninstall_refuses_instead_of_deleting_the_survivor(
    tmp_path, monkeypatch
):
    """#2585's second residual, and the worst of the family: it returned ``True``.

    After a failed park the stage IS the user's surviving copy (#2574) and the audit line
    tells them where it is. The next keep-data uninstall of the same app then ``rmtree``'d
    it at the top as leftover garbage. Measured before the fix: survivor 35 files with
    both notes going in, ``0`` files and ``[]`` notes coming out, return ``True``, outcome
    ``ok``. Nothing anywhere said the data was gone.

    Only this function ever writes that path, and it leaves one behind in exactly one
    case — so the single state the sweep could ever find was the one it must not touch.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    staged = _staged(name)

    real_rename = os.rename

    def _fail_park(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            raise OSError(errno.EIO, "injected: could not park data/")
        return real_rename(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _fail_park)
        assert app_manager.uninstall_keep_data(name) is False
    survivor = _shape(staged)
    assert survivor[2] == {
        "alpha": "the first note\n",
        "beta": "the second note\n",
    }, survivor

    assert app_manager.install(src, confirm=True).ok
    assert _shape(staged) == survivor, "the reinstall touched the quarantined survivor"
    _write_note(name, "gamma", "written after the reinstall")
    live_before = _shape(manager.app_dir(name) / "data")

    records = _capture_audit(monkeypatch)
    assert app_manager.uninstall_keep_data(name) is False, (
        "the second keep-data uninstall reported success while deleting the copy the "
        "first one told the user to go and recover"
    )

    assert _shape(staged) == survivor, (
        f"the survivor was destroyed: {_shape(staged)} vs {survivor} — this is the "
        "return-True data loss #2585 reports"
    )
    assert manager._read_installed(name) is not None, "the app was removed by a refusal"
    assert (
        _shape(manager.app_dir(name) / "data") == live_before
    ), "live data/ was touched"
    detail, error = _keep_data_failure(records)
    assert (
        "data=unconsumed_copy" in detail
    ), f"the new fact token is missing: {detail!r}"
    assert (
        str(staged) in detail
    ), f"the refusal does not name the copy it protected: {detail!r}"
    assert "Nothing was removed" in error, error
    assert app_manager.describe_app_data(name)["unconsumed"] == [str(staged)]


def test_a_keep_data_uninstall_refuses_while_an_unconsumed_park_is_still_on_disk(
    tmp_path, monkeypatch
):
    """The two shapes the issue does NOT report, both silent, both ``True``.

    A park coexists with an installed app only when an earlier RESTORE failed, so that
    copy is data the user has never seen. Proceeding destroyed it two ways:

    * ``force_uninstall`` — which this rung calls to do its removal — discards any park
      for the name. Measured before the fix: park holding ``old`` going in, park holding
      only ``new`` coming out, return ``True``, outcome ``ok``.
    * and had it survived that, the park's own ``rmtree(target, ignore_errors=True)``
      swallows a real permissions failure, after which ``shutil.move`` finds a DIRECTORY
      at the destination and moves the stage INSIDE it. Measured: the reinstall restored
      the stale ``old`` copy plus a nested ``notes-fixture.data.staged`` directory, with
      the user's current work buried inside it — and that one returned ``True`` too.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "old", "round one, never restored")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("injected: restore failed")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok
    unconsumed = _shape(parked)
    assert unconsumed[2] == {"old": "round one, never restored\n"}, unconsumed
    assert _notes(name) == {}, "the restore was supposed to fail"

    _write_note(name, "new", "round two")
    live_before = _shape(manager.app_dir(name) / "data")
    records = _capture_audit(monkeypatch)

    assert app_manager.uninstall_keep_data(name) is False

    assert (
        _shape(parked) == unconsumed
    ), f"the unconsumed park was destroyed or overwritten: {_shape(parked)} vs {unconsumed}"
    assert not (parked / f"{name}{app_manager._DATA_STAGE_SUFFIX}").exists(), (
        "the stage was moved INSIDE the older park — shutil.move's "
        "destination-is-a-directory case"
    )
    assert not _staged(
        name
    ).exists(), "the refusal came after staging; it must come first"
    assert manager._read_installed(name) is not None, "the app was removed by a refusal"
    assert (
        _shape(manager.app_dir(name) / "data") == live_before
    ), "live data/ was touched"
    detail, _error = _keep_data_failure(records)
    assert "data=unconsumed_copy" in detail and str(parked) in detail, detail
    assert app_manager.describe_app_data(name)["unconsumed"] == [str(parked)]


def test_the_refusal_clears_by_the_route_it_names_rather_than_wedging_the_app(
    tmp_path, monkeypatch
):
    """Fail-closed has to leave a way forward, or it is just a different way to lose.

    Both leftovers are cleared here by the routes the refusal names, and the rung then
    succeeds — otherwise the fix would have traded silent loss for a permanently stuck app.

    Note which route is NOT available, because the first draft of the refusal advised it:
    "reinstall the app and it will restore the park" is false HERE. This rung only runs
    while the app is installed, and ``install`` refuses an installed app ("use update"), so
    from inside this refusal a reinstall is not reachable. The routes are: move the copy
    aside, remove it, or press force-uninstall, which discards a park on purpose. Both
    hand routes are driven below; the force-uninstall one is
    ``test_force_uninstall_drops_a_park_a_failed_restore_left_behind``.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "old", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("injected: restore failed")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok
    assert app_manager.uninstall_keep_data(name) is False
    _write_note(name, "current", "the live copy")

    kept_by_hand = parked.parent / "kept-by-hand"
    parked.rename(kept_by_hand)
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True, "the rung stayed wedged"
    assert _notes_at(parked / "notebook") == {"current": "the live copy\n"}
    assert _notes_at(kept_by_hand / "notebook") == {
        "old": "round one\n"
    }, "route 1 lost it"

    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {"current": "the live copy\n"}, "the park did not come back"
    _write_note(name, "later", "round two")
    staged = _staged(name)
    real_rename = os.rename

    def _fail_park(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            raise OSError(errno.EIO, "injected")
        return real_rename(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _fail_park)
        assert app_manager.uninstall_keep_data(name) is False
    assert app_manager.install(src, confirm=True).ok
    assert app_manager.uninstall_keep_data(name) is False, "the survivor is still there"
    aside = staged.parent / "recovered-by-hand"
    staged.rename(aside)
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True
    assert _notes_at(aside / "notebook") == {
        "current": "the live copy\n",
        "later": "round two\n",
    }, "moving the survivor aside lost it"


def test_describe_app_data_reports_no_unconsumed_copies_on_the_ordinary_path(tmp_path):
    """The confirm dialog must not cry wolf: the normal cycle reports an empty list.

    Paired with the two assertions above that it reports a NON-empty one, because a key
    that is always ``[]`` would satisfy those by being broken.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    _write_note(name, "one", "body")
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager.describe_app_data(name)["unconsumed"] == [
        str(app_manager._preserved_data_dir(name))
    ]
    assert app_manager.install(src, confirm=True).ok
    assert app_manager.describe_app_data(name)["unconsumed"] == []


def _refusal_logs(caplog) -> str:
    """Everything ``app_manager`` logged at WARNING or worse, joined."""
    return "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("gideon.extensions.apps.app_manager")
        and r.levelno >= logging.WARNING
    )


def test_a_native_refusal_names_the_app_and_that_it_is_locked(tmp_path, caplog):
    """Every refusal has to be diagnosable from the log alone (#2541 follow-up).

    ``False`` reaches the HTTP layer as "not installed", which is the wrong sentence
    for four of the five ways this rung refuses. The log is where the difference
    between "locked", "an older copy is in the way", "the copy failed" and "the
    removal was refused" survives, so each one names the app AND its cause.
    """
    src = _bundle(tmp_path, name="native-fixture")
    mani = json.loads((src / "app.json").read_text(encoding="utf-8"))
    mani["native"] = True
    (src / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    assert app_manager.install(src, confirm=True).ok

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data("native-fixture") is False

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "native-fixture" in logged
    assert "native (locked)" in logged
    assert "keep-data uninstall refused" in logged


def test_an_unmintable_name_refusal_names_the_app_and_the_reason(tmp_path, caplog):
    """The refusal that happens before anything is copied still has to say why."""
    weird = "Not Kebab"
    d = manager.apps_dir() / weird
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": weird, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data(weird) is False

    logged = _refusal_logs(caplog)
    assert weird in logged
    assert "refused" in logged and "nothing was removed" in logged


def test_an_unconsumed_copy_refusal_names_the_app_and_the_copy(tmp_path, caplog):
    """The one refusal the user can act on names the path they have to act on."""
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    parked = app_manager._preserved_data_dir(name)
    parked.mkdir(parents=True)
    (parked / "note.md").write_text("an earlier copy\n", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data(name) is False

    logged = _refusal_logs(caplog)
    assert name in logged
    assert str(parked) in logged
    assert "unconsumed" in logged
    assert manager.app_dir(name).is_dir(), "the refusal happened after a removal"


def test_a_preservation_failure_logs_the_os_error_detail(tmp_path, monkeypatch, caplog):
    """The OS's own words for the failure — errno, strerror, path — reach the log.

    "could not preserve data/" alone cannot be acted on: ENOSPC, EACCES and EROFS are
    three different problems with three different fixes, and only the OSError knows
    which one happened. It is REPORTED, not interpreted — this path makes no claim
    about a filesystem failure it cannot reproduce.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "precious", "must not be lost")

    import shutil as _shutil

    def _boom(*a, **k):
        raise OSError(errno.EACCES, "Permission denied", str(_staged(name)))

    monkeypatch.setattr(_shutil, "copytree", _boom)

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data(name) is False

    logged = _refusal_logs(caplog)
    assert name in logged
    assert "Permission denied" in logged, f"the OS error detail was dropped: {logged}"
    assert str(errno.EACCES) in logged or "Errno" in logged
    assert "nothing was removed" in logged
    assert (_notebook(name) / "precious.md").is_file()


def test_a_refused_removal_is_logged_as_a_refused_removal(
    tmp_path, monkeypatch, caplog
):
    """The removal half can refuse on its own, and that is not a preservation failure.

    Driven through the REAL guard rather than a stubbed return: the app's
    ``installed.json`` is removed concurrently (as a competing force-uninstall would),
    so ``force_uninstall`` refuses through its own "not installed" check after the
    stage has already been written. The stage is then dropped — the live ``data/`` is
    still there, so it is not the last copy — and the log has to say that nothing was
    deleted rather than leaving a bare ``False`` behind.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")

    import shutil as _shutil

    real_copytree = _shutil.copytree
    fired: list[str] = []

    live_data = manager.app_dir(name) / "data"

    def _copy_then_yank(srcp, dstp, *a, **k):
        out = real_copytree(srcp, dstp, *a, **k)
        if Path(srcp) == live_data:
            (manager.app_dir(name) / "installed.json").unlink()
            fired.append("yank")
        return out

    monkeypatch.setattr(_shutil, "copytree", _copy_then_yank)

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data(name) is False

    assert fired == ["yank"], "the injected race never fired; the test is vacuous"
    logged = _refusal_logs(caplog)
    assert name in logged
    assert "removal was refused" in logged
    assert "nothing was deleted" in logged
    assert (_notebook(name) / "alpha.md").is_file(), "data/ was touched anyway"
    assert not _staged(name).exists(), "the stage was left behind as a second copy"


def test_a_failed_park_logs_the_os_error_and_where_the_copy_is(
    tmp_path, monkeypatch, caplog
):
    """The one refusal where the app is already gone says BOTH things it must say.

    The cause (the OSError, verbatim) and the recovery (the path the surviving copy is
    at). The audit record carried both already; the log carried only the path, so an
    operator reading the gateway log saw the recovery without the reason for it.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    staged = _staged(name)
    real_rename = os.rename
    fired: list[str] = []

    def _fail_park(s, d, *a, **k):
        if Path(s) == staged:
            fired.append("rename")
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(s, d, *a, **k)

    monkeypatch.setattr(os, "rename", _fail_park)

    with caplog.at_level(logging.INFO, logger="gideon.extensions.apps.app_manager"):
        assert app_manager.uninstall_keep_data(name) is False

    assert fired == ["rename"], "the injected park failure never fired"
    logged = _refusal_logs(caplog)
    assert name in logged
    assert "Invalid cross-device link" in logged, f"the OS error was dropped: {logged}"
    assert str(staged) in logged, "the log does not say where the surviving copy is"
    assert _notes_at(staged / "notebook") == {"alpha": "the first note\n"}
