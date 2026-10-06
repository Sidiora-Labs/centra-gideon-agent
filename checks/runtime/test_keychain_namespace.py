"""Home keychain identity uses real filesystem state and fails closed."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gideon.core.config import credentials, loader
from gideon.operations.durability.inventory import IGNORED
from gideon.sdk.testing import keychain_off


def test_namespace_identity_is_lazy_private_stable_and_distinct(tmp_path, monkeypatch):
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setattr(loader, "default_config_dir", lambda: tmp_path / "default")
    assert credentials.keychain_service(first) == ""
    assert not first.exists()
    with ThreadPoolExecutor(max_workers=8) as executor:
        services = list(executor.map(lambda _: credentials.keychain_service(first, mint=True), range(16)))
    assert len(set(services)) == 1
    assert services[0].startswith("gideon-")
    assert credentials.keychain_service(second, mint=True) != services[0]
    identity = first / credentials.KEYCHAIN_NAMESPACE_FILE
    assert identity.stat().st_mode & 0o777 == 0o600
    moved = tmp_path / "moved"
    first.rename(moved)
    assert credentials.keychain_service(moved) == services[0]
    assert credentials.KEYCHAIN_NAMESPACE_FILE in IGNORED
    from gideon.workspace.portability import _is_excluded
    from pathlib import PurePosixPath

    assert _is_excluded(PurePosixPath(credentials.KEYCHAIN_NAMESPACE_FILE))


def test_default_keeps_legacy_service_and_invalid_identity_fails_closed(tmp_path, monkeypatch):
    default = tmp_path / "default"
    monkeypatch.setattr(loader, "default_config_dir", lambda: default)
    assert credentials.keychain_service(default) == "gideon"
    assert not default.exists()
    home = tmp_path / "other"
    home.mkdir()
    identity = home / credentials.KEYCHAIN_NAMESPACE_FILE
    identity.write_bytes(b"invalid\xff")
    assert credentials.keychain_namespace(home, mint=True).scope == "unreadable"
    assert credentials.keychain_service(home, mint=True) == ""
    assert identity.read_bytes() == b"invalid\xff"
    store = credentials.KeyringStore(None, home)
    assert store.index() == []
    assert store.get("TOKEN") == ""
    assert store.put("TOKEN", "value") is False
    assert store.remove("TOKEN") is False


def test_sdk_keychain_off_is_nested_and_reversible():
    previous = credentials._keychain_disabled
    restore = keychain_off()
    nested = keychain_off()
    assert credentials._usable_keyring() is None
    nested()
    assert credentials._usable_keyring() is None
    restore()
    assert credentials._keychain_disabled is previous
