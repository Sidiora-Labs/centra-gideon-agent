from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gideon.operations.durability import inventory, shards


ENTRY_ID = "agent_prompt_override"
PROMPT = b"You are terse, and you answer in one line.\n"
TOKEN = b"ghp_fixture_hourly_backup_0123456789abcd"
SESSION_KEY = b"fixture-session-key-5e4d3c2b1a09"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    (root / "prompt.md").write_bytes(PROMPT)
    (root / ".env").write_bytes(b"FIXTURE_TOKEN=" + TOKEN + b"\n")
    (root / ".local_secret").write_bytes(SESSION_KEY)
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "marker.sqlite3").write_bytes(b"fixture database bytes")
    entry = inventory.StateEntry(
        id=ENTRY_ID,
        kind=inventory.KIND_TREE,
        path="prompt.md",
        domain=inventory.DOMAIN_CONFIG,
        merge=inventory.MERGE_REPLACE_ONLY,
    )
    monkeypatch.setattr(inventory, "INVENTORY", (entry,))
    monkeypatch.setenv("GIDEON_HOME", str(root))
    return root


def _entry_blobs(out: Path) -> list[bytes]:
    blob_root = out / ENTRY_ID / "blobs"
    return [path.read_bytes() for path in blob_root.rglob("*") if path.is_file()]


def _assert_only_prompt(out: Path) -> None:
    all_blobs = [
        path.read_bytes()
        for path in (out / ENTRY_ID / "blobs").rglob("*")
        if path.is_file()
    ]
    assert all_blobs == [PROMPT]
    assert TOKEN not in b"".join(all_blobs)
    assert SESSION_KEY not in b"".join(all_blobs)


def test_manual_export_contains_only_the_named_prompt_file(home: Path, tmp_path: Path):
    out = tmp_path / "shards"

    shards.export_shards(home, out)

    _assert_only_prompt(out)


def test_export_removes_stale_entry_blobs_only(home: Path, tmp_path: Path):
    out = tmp_path / "shards"
    leaked = (home / ".env").read_bytes()
    digest = hashlib.sha256(leaked).hexdigest()
    stale = out / ENTRY_ID / "blobs" / digest[:2] / digest
    stale.parent.mkdir(parents=True)
    stale.write_bytes(leaked)
    other = out / "other_entry" / "blobs" / "preserved"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"unrelated entry")

    shards.export_shards(home, out)

    assert not stale.exists()
    assert other.read_bytes() == b"unrelated entry"
    _assert_only_prompt(out)


def test_incremental_export_contains_only_the_named_prompt_file(home: Path):
    from gideon.operations.durability.service import run_incremental_export

    result = run_incremental_export()

    assert result.ok, result.detail
    _assert_only_prompt(home / "shards")


def test_symlink_file_entry_is_skipped(home: Path, tmp_path: Path):
    prompt = home / "prompt.md"
    prompt.unlink()
    prompt.symlink_to(home / ".env")
    out = tmp_path / "shards"

    shards.export_shards(home, out)

    assert _entry_blobs(out) == []


def test_symlink_directory_entry_is_skipped(home: Path, tmp_path: Path):
    prompt = home / "prompt.md"
    prompt.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"directory target sentinel")
    prompt.symlink_to(outside, target_is_directory=True)
    out = tmp_path / "shards"

    shards.export_shards(home, out)

    assert _entry_blobs(out) == []
    assert sentinel.read_bytes() == b"directory target sentinel"


def test_symlinked_blob_root_cannot_clean_outside_files(home: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep-me"
    sentinel.write_bytes(b"outside sentinel")
    entry_root = tmp_path / "shards" / ENTRY_ID
    entry_root.mkdir(parents=True)
    (entry_root / "blobs").symlink_to(outside, target_is_directory=True)

    shards.export_shards(home, tmp_path / "shards")

    assert sentinel.read_bytes() == b"outside sentinel"


def test_symlinked_entry_root_cannot_clean_or_write_outside(home: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep-me"
    sentinel.write_bytes(b"outside sentinel")
    entries_root = tmp_path / "shards"
    entries_root.mkdir()
    (entries_root / ENTRY_ID).symlink_to(outside, target_is_directory=True)

    shards.export_shards(home, entries_root)

    assert sentinel.read_bytes() == b"outside sentinel"
    assert not (outside / "blobs").exists()


def test_symlinked_digest_prefix_cannot_clean_or_write_outside(home: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep-me"
    sentinel.write_bytes(b"outside sentinel")
    digest = hashlib.sha256(PROMPT).hexdigest()
    blob_root = tmp_path / "shards" / ENTRY_ID / "blobs"
    blob_root.mkdir(parents=True)
    (blob_root / digest[:2]).symlink_to(outside, target_is_directory=True)

    shards.export_shards(home, tmp_path / "shards")

    assert sentinel.read_bytes() == b"outside sentinel"
    assert not (outside / digest).exists()


def test_directory_tree_export_still_exports_each_file(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tree = home / "attachments"
    tree.mkdir()
    (tree / "one.bin").write_bytes(b"first attachment")
    (tree / "nested").mkdir()
    (tree / "nested" / "two.bin").write_bytes(b"second attachment")
    entry = inventory.StateEntry(
        id="attachments",
        kind=inventory.KIND_TREE,
        path="attachments",
        domain=inventory.DOMAIN_WORK,
        merge=inventory.MERGE_REPLACE_ONLY,
    )
    monkeypatch.setattr(inventory, "INVENTORY", (entry,))
    out = tmp_path / "shards"

    shards.export_shards(home, out)

    blobs = [
        path.read_bytes()
        for path in (out / "attachments" / "blobs").rglob("*")
        if path.is_file()
    ]
    assert sorted(blobs) == [b"first attachment", b"second attachment"]
