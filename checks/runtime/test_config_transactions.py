import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


_WORKER = r"""
import os, sys, time
from pathlib import Path
from gideon.core.config.transactions import mutate_config
home = Path(os.environ['GIDEON_HOME'])
name = sys.argv[1]
(home / ('ready-' + name)).touch()
deadline = time.monotonic() + 30
while not (home / 'go').exists():
    if time.monotonic() > deadline:
        raise SystemExit('barrier timeout')
    time.sleep(0.002)
for _ in range(int(sys.argv[2])):
    mutate_config(lambda doc: doc.update(counter=int(doc.get('counter', 0)) + 1))
"""

_HOLDING_WORKER = r"""
import os, time
from pathlib import Path
from gideon.core.config.transactions import mutate_config
home = Path(os.environ['GIDEON_HOME'])
def hold(document):
    (home / 'holding').touch()
    deadline = time.monotonic() + 30
    while not (home / 'release').exists():
        if time.monotonic() > deadline:
            raise SystemExit('release timeout')
        time.sleep(0.002)
    document['winner'] = 'holder'
mutate_config(hold)
"""

_RESTORE_CONFIG_WORKER = r"""
import os
from pathlib import Path
from gideon.workspace.snapshot import _copy_config_if_missing
home = Path(os.environ['GIDEON_HOME'])
(home / 'restore-started').touch()
print(_copy_config_if_missing(home / 'snapshot' / 'config.json', home / 'config.json'))
"""

_REPLACE_REFUSAL_WORKER = r"""
import os, sys
from pathlib import Path
from gideon.core.atomic_write import register_post_write_hook
from gideon.core.config import secret_refs
from gideon.core.config.transactions import ConfigWriteError, mutate_config
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[3]))
path = Path(sys.argv[1])
def published(destination):
    (destination.parent / 'post-write-published').touch()
register_post_write_hook(published)
try:
    mutate_config(lambda document: document.update(replaced=True), path=path)
except ConfigWriteError as exc:
    if str(exc) != 'config replacement failed; nothing was written':
        raise
    print('refused')
else:
    raise SystemExit('replacement unexpectedly succeeded')
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


def test_real_processes_serialize_updates_without_lost_writes(home):
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, name, "35"],
            env={**os.environ, "GIDEON_HOME": str(home)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("one", "two")
    ]
    deadline = time.monotonic() + 30
    while not all((home / f"ready-{name}").exists() for name in ("one", "two")):
        assert time.monotonic() < deadline, "workers did not reach the real-process barrier"
        for proc in workers:
            assert proc.poll() is None, proc.communicate()[1]
        time.sleep(0.01)
    (home / "go").touch()
    for proc in workers:
        _, stderr = proc.communicate(timeout=90)
        assert proc.returncode == 0, stderr
    assert json.loads((home / "config.json").read_text())["counter"] == 70


def test_stale_modeled_saves_preserve_independent_fields(home):
    from gideon.core.config.loader import AppConfig

    AppConfig.load().save()
    first, second = AppConfig.load(), AppConfig.load()
    first.timezone = "UTC"
    second.dashboard.user_name = "Ada"
    first.save()
    second.save()
    final = AppConfig.load()
    assert (final.timezone, final.dashboard.user_name) == ("UTC", "Ada")


def test_nested_and_unreadable_transactions_write_nothing(home):
    from gideon.core.config.transactions import NestedConfigTransaction, mutate_config

    mutate_config(lambda doc: doc.update(kept="yes"))
    before = (home / "config.json").read_bytes()
    with pytest.raises(NestedConfigTransaction):
        mutate_config(lambda doc: mutate_config(lambda nested: nested.update(lost=True)))
    assert (home / "config.json").read_bytes() == before


def test_dangling_config_symlink_refuses_without_changing_target(home):
    from gideon.core.config.transactions import mutate_config

    config = home / "config.json"
    target = home / "missing-config.json"
    config.symlink_to(target.name)
    original_target = os.readlink(config)

    with pytest.raises(RuntimeError, match="nothing was written"):
        mutate_config(lambda document: document.update(replaced=True))

    assert config.is_symlink()
    assert os.readlink(config) == original_target
    assert not target.exists()

    (home / "config.json").write_text("{broken", encoding="utf-8")
    before = (home / "config.json").read_bytes()
    with pytest.raises(RuntimeError, match="nothing was written"):
        mutate_config(lambda doc: doc.update(lost=True))
    assert (home / "config.json").read_bytes() == before


def test_config_replacement_refusal_preserves_existing_bytes_and_skips_hooks(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("requires root to create a root-owned sticky-directory fixture")
    try:
        nobody = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("requires the unprivileged nobody account")

    sticky_home = Path(tempfile.mkdtemp(prefix="gideon-config-sticky-"))
    sticky_home.chmod(0o1777)
    config = sticky_home / "config.json"
    original = b'{"kept":"original"}\n'
    config.write_bytes(original)
    config.chmod(0o666)
    env = {**os.environ, "GIDEON_HOME": str(sticky_home)}
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                _REPLACE_REFUSAL_WORKER,
                str(config),
                str(nobody.pw_gid),
                str(nobody.pw_uid),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "refused"
        assert config.read_bytes() == original
        assert not (sticky_home / "post-write-published").exists()
    finally:
        shutil.rmtree(sticky_home)


def test_lock_timeout_refuses_without_writing(home):
    from gideon.core.config.transactions import ConfigLockTimeout, mutate_config

    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDING_WORKER],
        env={**os.environ, "GIDEON_HOME": str(home)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not (home / "holding").exists():
        assert time.monotonic() < deadline, "holder did not acquire the real config lock"
        assert holder.poll() is None, holder.communicate()[1]
        time.sleep(0.01)
    with pytest.raises(ConfigLockTimeout, match="nothing was written"):
        mutate_config(lambda document: document.update(loser=True), timeout=0.05)
    assert not (home / "config.json").exists()
    (home / "release").touch()
    _, stderr = holder.communicate(timeout=30)
    assert holder.returncode == 0, stderr
    assert json.loads((home / "config.json").read_text()) == {"winner": "holder"}


def test_config_secret_is_stored_as_an_owner_reference(home):
    from gideon.core.config.secret_refs import resolve_config_secrets
    from gideon.core.config.transactions import mutate_config

    secret = "test-only-provider-secret"
    mutate_config(
        lambda document: document.update(
            providers=[
                {"name": "primary", "type": "openai", "options": {"api_key": secret}}
            ]
        )
    )
    stored = json.loads((home / "config.json").read_text(encoding="utf-8"))
    reference = stored["providers"][0]["options"]["api_key"]
    assert reference.startswith("gideon-config-secret:v1:")
    assert secret not in (home / "config.json").read_text(encoding="utf-8")
    assert resolve_config_secrets(stored)["providers"][0]["options"]["api_key"] == secret


def test_replace_snapshot_rejects_malformed_config_without_overwriting(home):
    from gideon.core.config.transactions import mutate_config
    from gideon.workspace.snapshot import _do_replace

    mutate_config(lambda document: document.update(agent={"approval_timeout_minutes": 37}))
    config = home / "config.json"
    before = config.read_bytes()
    snap = home / "snapshot-malformed"
    snap.mkdir()
    (snap / "config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Snapshot config is unreadable"):
        _do_replace(snap, home, ["config"])

    assert config.read_bytes() == before
    backups = [path for path in home.glob("pre-restore-*") if path.is_dir()]
    assert len(backups) == 1
    assert not (backups[0] / "config.json").exists()


def test_replace_snapshot_without_config_keeps_live_config_bytes(home):
    from gideon.core.config.transactions import mutate_config
    from gideon.workspace.snapshot import _do_replace

    mutate_config(lambda document: document.update(unrelated={"keep": True}))
    config = home / "config.json"
    before = config.read_bytes()
    snap = home / "snapshot-missing-config"
    snap.mkdir()

    _do_replace(snap, home, ["config"])

    assert config.read_bytes() == before


def test_empty_snapshot_config_is_created_for_replace_and_merge(home):
    from gideon.workspace.snapshot import (
        _copy_config_if_missing,
        _replace_config_from_snapshot,
    )

    snap = home / "snapshot-empty-config"
    snap.mkdir()
    source = snap / "config.json"
    source.write_text("{}", encoding="utf-8")

    replace_home = home / "replace-empty"
    replace_home.mkdir()
    replace_path = replace_home / "config.json"
    _replace_config_from_snapshot(source, replace_path, replace_home / "backup" / "config.json")
    assert json.loads(replace_path.read_text(encoding="utf-8")) == {}

    merge_home = home / "merge-empty"
    merge_home.mkdir()
    merge_path = merge_home / "config.json"
    assert _copy_config_if_missing(source, merge_path) is True
    assert json.loads(merge_path.read_text(encoding="utf-8")) == {}


def test_merge_snapshot_copy_if_missing_rechecks_after_real_writer(home):
    snap = home / "snapshot"
    snap.mkdir()
    (snap / "config.json").write_text('{"snapshot":"older"}', encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDING_WORKER],
        env={**os.environ, "GIDEON_HOME": str(home)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not (home / "holding").exists():
        assert time.monotonic() < deadline, "holder did not acquire the real config lock"
        assert holder.poll() is None, holder.communicate()[1]
        time.sleep(0.01)

    restore = subprocess.Popen(
        [sys.executable, "-c", _RESTORE_CONFIG_WORKER],
        env={**os.environ, "GIDEON_HOME": str(home)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not (home / "restore-started").exists():
        assert time.monotonic() < deadline, "restore worker did not start"
        assert restore.poll() is None, restore.communicate()[1]
        time.sleep(0.01)
    time.sleep(0.1)
    assert restore.poll() is None, "restore did not wait for the writer's config lock"

    (home / "release").touch()
    _, holder_stderr = holder.communicate(timeout=30)
    restore_stdout, restore_stderr = restore.communicate(timeout=30)
    assert holder.returncode == 0, holder_stderr
    assert restore.returncode == 0, restore_stderr
    assert restore_stdout.strip() == "False"
    assert json.loads((home / "config.json").read_text(encoding="utf-8")) == {
        "winner": "holder"
    }


@pytest.mark.asyncio
async def test_gideon_config_get_does_not_expose_resolved_secret_or_reference(home):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from gideon.core.config.transactions import mutate_config
    from gideon.interfaces.dashboard.handlers.core import api_gideon_config

    secret = "dashboard-response-secret-check"
    mutate_config(
        lambda document: document.update(
            providers={"openai": {"api_key": secret}},
            security={"credential_keychain": True},
        )
    )
    stored = json.loads((home / "config.json").read_text(encoding="utf-8"))
    reference = stored["providers"]["openai"]["api_key"]
    assert reference.startswith("gideon-config-secret:v1:")

    app = web.Application()
    app.router.add_get("/api/config/gideon", api_gideon_config)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/config/gideon")
        assert response.status == 200
        payload = await response.json()

    encoded = json.dumps(payload)
    assert secret not in encoded
    assert reference not in encoded
    assert isinstance(payload.get("agent"), dict)
    assert payload["security"]["credential_keychain"] is True
