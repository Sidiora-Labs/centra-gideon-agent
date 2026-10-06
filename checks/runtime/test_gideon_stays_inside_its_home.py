from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def test_active_home_and_default_workspace_follow_environment_after_import(
    tmp_path, monkeypatch
):
    from gideon.core.config import loader

    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("GIDEON_HOME", str(first))
    assert loader.resolve_config_dir() == first.resolve()
    assert not first.exists()
    assert loader.config_path() == first / "config.json"
    assert loader.workspace_root() == first / "workspace" / "gideon-workspace"
    monkeypatch.setenv("GIDEON_HOME", str(second))
    assert loader.resolve_config_dir() == second.resolve()
    assert loader.config_path() == second / "config.json"
    assert loader.workspace_root() == second / "workspace" / "gideon-workspace"


def test_configuration_home_allows_var_tree_without_creating_it(tmp_path):
    import logging

    from gideon.core.config.locations import configuration_home

    candidate = Path("/var/lib/gideon")
    existed_before = candidate.exists()
    default = tmp_path / "default-home"

    assert (
        configuration_home(str(candidate), default, logging.getLogger(__name__))
        == candidate.resolve()
    )
    assert candidate.exists() is existed_before
    assert not default.exists()


def test_files_rejects_parent_and_symlink_escape_from_an_admitted_workspace(
    tmp_path, monkeypatch
):
    from gideon.interfaces.dashboard.handlers.files import _validate_dashboard_path

    home = tmp_path / "gideon"
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    for path in (home, workspace, outside):
        path.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    link = workspace / "escape"
    link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))

    assert _validate_dashboard_path(str(workspace / "note.md")) == str(
        workspace / "note.md"
    )
    assert (
        _validate_dashboard_path(str(workspace / ".." / "outside" / "secret.txt"))
        is None
    )
    assert _validate_dashboard_path(str(link / "secret.txt")) is None
    assert _validate_dashboard_path(str(home / "config.json")) is None


def test_outside_home_is_named_and_denied_until_config_allows_read(
    tmp_path, monkeypatch
):
    from gideon.core.config.loader import AppConfig
    from gideon.core.outside_home import allowed_paths
    from gideon.engine.agent import _all_skill_paths

    home = tmp_path / "gideon"
    shared = tmp_path / ".agents" / "skills"
    home.mkdir()
    shared.mkdir(parents=True)
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert allowed_paths() == []
    assert str(shared.resolve()) not in _all_skill_paths()
    (home / "config.json").write_text(
        '{"security":{"outside_home":["agent-skills"]}}', encoding="utf-8"
    )
    assert AppConfig.load().security.outside_home == ["agent-skills"]
    assert allowed_paths() == [("Shared agent skills", str(shared.resolve()))]
    assert str(shared.resolve()) in _all_skill_paths()


def test_shared_skills_are_readable_but_never_updated_or_deleted(tmp_path, monkeypatch):
    from gideon.extensions.skills.loader import ProcedureLibrary

    home = tmp_path / "gideon"
    shared = tmp_path / ".agents" / "skills"
    skill = shared / "read-only-skill" / "SKILL.md"
    home.mkdir()
    skill.parent.mkdir(parents=True)
    content = "---\nname: Read only skill\ndescription: Shared\n---\n\nBody\n"
    skill.write_text(content, encoding="utf-8")
    (home / "config.json").write_text(
        '{"security":{"outside_home":["agent-skills"]}}', encoding="utf-8"
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    loader = ProcedureLibrary(install_builtins=False)

    assert loader.load_skill("read-only-skill") == content
    assert loader.delete_skill("read-only-skill") is False
    assert loader.update_skill("read-only-skill", "---\nname: Changed\n---\n") is False
    assert skill.is_file()


def test_generated_audio_and_sandbox_files_stay_in_home_and_clean_up(
    tmp_path, monkeypatch
):
    from gideon.integrations.tts.audio_output import AudioDestination
    from gideon.integrations.voice_reply import _concat_manifest, _StitchTarget
    from gideon.security.sandbox import _sandbox_temp_file, namespace_argv

    home = tmp_path / "gideon-home"
    user_home = tmp_path / "user-home"
    system_temp = tmp_path / "system-temp"
    home.mkdir()
    user_home.mkdir()
    system_temp.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))

    audio = AudioDestination.allocate("")
    audio_path = Path(audio.path)
    assert audio_path.parent == home / "tmp"
    try:
        audio.discard()
    finally:
        audio.discard()
    assert not audio_path.exists()

    stitch = _StitchTarget.create(None)
    stitch_path = Path(stitch.path)
    assert stitch_path.parent == home / "tmp"
    try:
        assert stitch.finish(1) is None
    finally:
        stitch.close()
    assert not stitch_path.exists()

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    with _concat_manifest([str(clip)]) as manifest:
        manifest_path = Path(manifest)
        assert manifest_path.parent == home / "tmp"
        assert "clip.wav" in manifest_path.read_text(encoding="utf-8")
    assert not manifest_path.exists()

    profile_path = None
    launcher_path = None
    try:
        profile_fd, profile_path = _sandbox_temp_file(
            suffix=".sb", prefix="storage-test-"
        )
        os.close(profile_fd)
        script_argv = namespace_argv(["/bin/true"])
        launcher_path = Path(script_argv[1])
        assert Path(profile_path).parent == home / "tmp"
        assert launcher_path.parent == home / "tmp"
        assert launcher_path.is_file()
    finally:
        if profile_path is not None:
            Path(profile_path).unlink(missing_ok=True)
        if launcher_path is not None:
            launcher_path.unlink(missing_ok=True)

    assert list((home / "tmp").iterdir()) == []
    assert list(user_home.iterdir()) == []
    assert list(system_temp.iterdir()) == []


@pytest.mark.parametrize("exit_code", [0, 7])
def test_namespace_launcher_cleans_scratch_after_real_child_exit(
    tmp_path, monkeypatch, exit_code
):
    if sys.platform != "linux":
        pytest.skip("Linux user and mount namespaces are required")

    from gideon.security.sandbox import _probe_unshare, namespace_argv

    if not _probe_unshare():
        pytest.skip("Kernel or execution environment denies user/mount namespaces")

    home = tmp_path / "home"
    scratch_temp = home / "tmp"
    scratch_temp.mkdir(parents=True)
    credential = home / ".aws" / "credentials"
    credential.parent.mkdir()
    marker = b"namespace-cleanup-fixture"
    credential.write_bytes(marker)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(scratch_temp))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_temp))

    scratch_roots = [Path(f"/run/user/{os.getuid()}"), Path("/dev/shm"), scratch_temp]

    def owned_scratch():
        paths = set()
        for root in scratch_roots:
            if not root.is_dir():
                continue
            for path in root.glob("gideon_sb_*"):
                try:
                    if path.is_dir() and path.stat().st_uid == os.getuid():
                        paths.add(path)
                except FileNotFoundError:
                    continue
        return paths

    before = owned_scratch()
    child = (
        "import json,os,sys; from pathlib import Path; "
        "print(json.dumps({'uid':os.getuid(),"
        "'user_ns':os.readlink('/proc/self/ns/user'),"
        "'mount_ns':os.readlink('/proc/self/ns/mnt'),"
        "'credential_visible':Path(sys.argv[1]).exists()}),flush=True); "
        "sys.exit(int(sys.argv[2]))"
    )
    argv = namespace_argv(
        [sys.executable, "-c", child, str(credential), str(exit_code)]
    )
    launcher = Path(argv[1])
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        assert result.returncode == exit_code, result.stderr
        observed = json.loads(result.stdout)
        assert observed["uid"] == os.getuid()
        assert observed["user_ns"] != os.readlink("/proc/self/ns/user")
        assert observed["mount_ns"] != os.readlink("/proc/self/ns/mnt")
        assert observed["credential_visible"] is False
        assert credential.read_bytes() == marker
        assert owned_scratch() - before == set()
    finally:
        launcher.unlink(missing_ok=True)
