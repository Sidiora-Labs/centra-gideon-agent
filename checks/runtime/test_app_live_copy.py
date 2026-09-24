import json
import os
import sys
from contextlib import contextmanager
from queue import Queue
from threading import Event, Thread

import pytest

from gideon.extensions.apps import app_manager, manager


@contextmanager
def live_writer(path, *, continuous):
    requests = Queue()
    active = True
    copies = []
    errors = []

    def write():
        while (request := requests.get()) is not None:
            done, generation = request
            try:
                path.write_text(f"generation {generation}")
                (path.parent / "deleted.txt").unlink(missing_ok=True)
                (path.parent / "added.txt").write_text("new entry")
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

    worker = Thread(target=write)
    worker.start()

    def during_copy(event, args):
        if not active or event != "shutil.copyfile" or os.fspath(args[0]) != str(path):
            return
        copies.append(len(copies) + 1)
        if continuous or len(copies) == 1:
            done = Event()
            requests.put((done, len(copies)))
            assert done.wait(5), "live writer did not run"
            assert not errors, errors

    sys.addaudithook(during_copy)
    try:
        yield copies
    finally:
        active = False
        requests.put(None)
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize("continuous", [False, True])
def test_live_copy_retries_real_concurrent_changes(tmp_path, continuous):
    source, destination = tmp_path / "data", tmp_path / "copy"
    source.mkdir()
    (source / "value.txt").write_text("original")
    (source / "deleted.txt").write_text("old entry")
    with live_writer(source / "value.txt", continuous=continuous) as copies:
        if continuous:
            with pytest.raises(OSError, match="did not settle after 3 attempts"):
                app_manager._copy_live_tree(source, destination)
            assert copies == [1, 2, 3]
            assert not destination.exists()
            assert (source / "value.txt").read_text() == "generation 3"
        else:
            app_manager._copy_live_tree(source, destination)
            assert copies == [1, 2]
            assert (destination / "value.txt").read_text() == "generation 1"
            assert (destination / "added.txt").read_text() == "new entry"
            assert not (destination / "deleted.txt").exists()


def test_existing_destination_is_not_overwritten(tmp_path):
    source, destination = tmp_path / "data", tmp_path / "copy"
    source.mkdir()
    destination.mkdir()
    (destination / "valuable.txt").write_text("earlier copy")
    with pytest.raises(FileExistsError):
        app_manager._copy_live_tree(source, destination)
    assert (destination / "valuable.txt").read_text() == "earlier copy"


def test_copy_preserves_empty_directories_and_symlinks(tmp_path):
    source, destination = tmp_path / "data", tmp_path / "copy"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "value.txt").write_text("content")
    (source / "link").symlink_to("value.txt")
    (source / "dangling").symlink_to("missing")
    app_manager._copy_live_tree(source, destination)
    assert (destination / "empty").is_dir()
    assert (destination / "link").is_symlink()
    assert (destination / "link").read_text() == "content"
    assert (destination / "dangling").is_symlink()


@pytest.mark.parametrize("continuous", [False, True])
def test_keep_data_uninstall_uses_settled_copy(monkeypatch, tmp_path, continuous):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.json").write_text(
        json.dumps(
            {
                "name": "live-notes",
                "version": "1.0.0",
                "displayName": "Live Notes",
                "description": "Notes",
            }
        )
    )
    installed = app_manager.install(bundle, confirm=True)
    assert installed.ok, installed.error
    live = manager.app_dir("live-notes")
    data = live / "data"
    data.mkdir(exist_ok=True)
    (data / "value.txt").write_text("original")
    with live_writer(data / "value.txt", continuous=continuous) as copies:
        result = app_manager.uninstall_keep_data("live-notes")
    if continuous:
        assert result is False
        assert live.is_dir()
        assert (data / "value.txt").read_text() == "generation 3"
        assert not app_manager._data_stage_dir("live-notes").exists()
    else:
        assert result is True
        assert not live.exists()
        parked = app_manager._preserved_data_dir("live-notes")
        assert (parked / "value.txt").read_text() == "generation 1"
    assert len(copies) == (3 if continuous else 2)


@pytest.mark.parametrize("persistent", [False, True])
def test_retry_requires_every_failed_source_to_have_vanished(tmp_path, persistent):
    source, destination = tmp_path / "data", tmp_path / "copy"
    source.mkdir()
    disappearing = source / "disappearing.txt"
    disappearing.write_text("temporary")
    if persistent:
        os.mkfifo(source / "pipe")
    active = True
    attempts = []

    def mutate(event, args):
        if not active:
            return
        if event == "shutil.copytree" and os.fspath(args[0]) == str(source):
            attempts.append(1)
        if event == "shutil.copyfile" and os.fspath(args[0]) == str(disappearing):
            disappearing.unlink(missing_ok=True)

    sys.addaudithook(mutate)
    try:
        if persistent:
            with pytest.raises(app_manager.shutil.Error):
                app_manager._copy_live_tree(source, destination)
            assert len(attempts) == 1
            assert (source / "pipe").exists()
            assert not destination.exists()
        else:
            app_manager._copy_live_tree(source, destination)
            assert len(attempts) == 2
            assert list(destination.iterdir()) == []
    finally:
        active = False
