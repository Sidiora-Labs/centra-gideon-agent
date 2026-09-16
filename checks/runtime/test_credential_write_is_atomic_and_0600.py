"""The dotenv credential write has no creation window and no truncation window.

`_dotenv_save_credential` used to do `ep.write_text(...)` and then `ep.chmod(0o600)`. Two defects
in that pair, and the final mode is identical either way — which is why a mode-only assertion
cannot tell the fixed code from the broken code:

* **A CREATION WINDOW.** `write_text` creates the file at the umask default (0644 under the common
  022) and the `chmod` narrowed it only *after* the secret was already on disk. On first creation
  the credential was world-readable for that window.
* **NO ATOMICITY.** A crash or a full disk mid-write left the file TRUNCATED — every other key in
  it lost — because the target was rewritten in place.

So the tests below assert the two things that DO distinguish: the call site (an atomic write asked
for 0600 up front) and the behaviour under a failed write (the previous credentials survive).

`apps/app_secret.py::_write_0600` fixes the umask half with `os.open(..., 0o600)` + `fchmod`; it
does not need atomicity because it writes one value to its own file. A credential file holding
every key needs both, which is what `atomic_write(mode=0o600, fsync=True)` gives.
"""

from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. This test writes CREDENTIALS — it must never see the real one."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


def test_a_freshly_created_credential_file_is_0600(home, monkeypatch):
    """End-to-end, under a deliberately loose umask so a umask-inherited mode would show."""
    monkeypatch.setattr(os, "umask", lambda _mask: 0o022, raising=False)
    from gideon.core.config.credentials import _dotenv_save_credential

    _dotenv_save_credential("OPENAI_API_KEY", "sk-secret-1")
    ep = home / ".env"
    assert ep.exists(), "the credential file was not written"
    assert (
        stat.S_IMODE(ep.stat().st_mode) == 0o600
    ), f"credential file is {oct(stat.S_IMODE(ep.stat().st_mode))}, not 0600"


def test_the_write_asks_for_0600_UP_FRONT_and_fsyncs(home, monkeypatch):
    """The call site — the only place the creation window is visible.

    A `write_text` + `chmod` pair ends at the same mode, so this is what separates "narrow from
    the first byte" from "narrow a moment later".
    """
    seen: dict[str, object] = {}
    import gideon.core.atomic_write as aw

    real = aw.atomic_write

    def spy(path, content, **kw):
        seen.update(
            {"path": str(path), "mode": kw.get("mode"), "fsync": kw.get("fsync")}
        )
        return real(path, content, **kw)

    monkeypatch.setattr(aw, "atomic_write", spy)
    from gideon.core.config.credentials import _dotenv_save_credential

    _dotenv_save_credential("KEY_A", "v1")
    assert seen, "the credential write did not go through atomic_write at all"
    assert (
        seen["mode"] == 0o600
    ), f"atomic_write was asked for {seen['mode']!r}, not 0o600"
    assert (
        seen["fsync"] is True
    ), "a credential write that is not fsynced can vanish on a crash"


def test_a_failed_write_leaves_THE_PREVIOUS_credentials_intact(home, monkeypatch):
    """The truncation half. In-place rewriting loses every other key on a mid-write failure."""
    from gideon.core.config.credentials import _dotenv_save_credential

    _dotenv_save_credential("KEEP_ME", "original")
    ep = home / ".env"
    before = ep.read_text(encoding="utf-8")
    assert "KEEP_ME=original" in before

    import gideon.core.atomic_write as aw

    monkeypatch.setattr(
        aw.os,
        "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")),
    )
    with pytest.raises(OSError):
        _dotenv_save_credential("NEW_KEY", "v2")

    after = ep.read_text(encoding="utf-8")
    assert after == before, f"a failed write damaged the credential file:\n{after!r}"
    assert "KEEP_ME=original" in after, "the pre-existing credential was lost"


def test_an_upsert_preserves_other_keys_and_comments(home):
    """The behaviour the function exists for, pinned so the rewrite cannot have changed it."""
    ep = home / ".env"
    ep.write_text("# a comment\nOTHER=untouched\nTARGET=old\n", encoding="utf-8")
    from gideon.core.config.credentials import _dotenv_save_credential

    _dotenv_save_credential("TARGET", "new")
    text = ep.read_text(encoding="utf-8")
    assert "# a comment" in text and "OTHER=untouched" in text
    assert "TARGET=new" in text and "TARGET=old" not in text


def test_the_mode_argument_is_load_bearing(home, monkeypatch):
    """Vacuity: prove `atomic_write` really applies the mode it is given.

    Without this, the call-site assertion above could be satisfied by a helper that accepts `mode`
    and ignores it — which is the same class of defect as the one being fixed.
    """
    monkeypatch.delenv("GIDEON_HOME", raising=False)
    from gideon.core.atomic_write import atomic_write

    target = home / "probe.txt"
    atomic_write(target, "x", mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    atomic_write(target, "x", mode=0o644)
    assert (
        stat.S_IMODE(target.stat().st_mode) == 0o644
    ), "atomic_write ignores its mode argument"


def test_atomic_binary_writes_preserve_bytes_and_permissions(tmp_path):
    from gideon.core.atomic_write import atomic_write_bytes

    destination = tmp_path / "binary" / "body.bin"
    content = bytes(range(256)) * 8
    atomic_write_bytes(destination, content, fsync=True, mode=0o600)
    assert destination.read_bytes() == content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert sorted(item.name for item in destination.parent.iterdir()) == ["body.bin"]


def test_concurrent_replacements_publish_only_complete_content(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from gideon.core.atomic_write import atomic_write

    destination = tmp_path / "shared.txt"
    versions = [f"revision-{number}\n" * 200 for number in range(48)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda value: atomic_write(destination, value, mode=0o600), versions
            )
        )
    assert destination.read_text() in versions
    assert list(tmp_path.iterdir()) == [destination]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_commit_notifications_are_isolated_and_do_not_recurse(tmp_path):
    from gideon.core.atomic_write import (
        atomic_write,
        post_write_hooks,
        register_post_write_hook,
        unregister_post_write_hook,
    )

    delivered = []
    nested = tmp_path / "nested.txt"

    def failing_listener(path):
        raise RuntimeError("notification failed after commit")

    def nested_writer(path):
        delivered.append(path)
        atomic_write(nested, "nested state")

    register_post_write_hook(failing_listener)
    register_post_write_hook(nested_writer)
    register_post_write_hook(nested_writer)
    try:
        assert post_write_hooks().count(nested_writer) == 1
        destination = tmp_path / "state.txt"
        atomic_write(destination, "committed")
        assert destination.read_text() == "committed"
        assert nested.read_text() == "nested state"
        assert delivered == [destination]
    finally:
        unregister_post_write_hook(failing_listener)
        unregister_post_write_hook(nested_writer)
        unregister_post_write_hook(nested_writer)
    assert nested_writer not in post_write_hooks()
