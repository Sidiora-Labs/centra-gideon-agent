import os
import stat

import pytest

from gideon.extensions.packs.build import build_pack
from gideon.extensions.packs.import_ import import_pack, PackImportRefused
from gideon.extensions.packs.installed import installed_ledger, InstalledPackLedgerError
from gideon.security.supply_chain import TrustTier


def actual_pack(tmp_path, monkeypatch):
    author = tmp_path / 'author'
    (author / 'prompts').mkdir(parents=True)
    (author / 'prompts/sample.yaml').write_text('name: sample\nkind: user\ncontent: Hello\n')
    monkeypatch.setenv('GIDEON_HOME', str(author))
    path = tmp_path / 'sample.gideon'
    build_pack(['prompt:sample'], name='sample', version='1.0.0', out_path=path)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    return path, home


@pytest.mark.parametrize('relative', ['prompts', 'packs/.installing', 'packs/staged'])
def test_public_pack_import_preflight_linked_ancestor(tmp_path, monkeypatch, relative):
    path, home = actual_pack(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.mkdir()
    linked = home / relative
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(outside, target_is_directory=True)
    before = set(item.relative_to(home) for item in home.rglob('*'))
    with pytest.raises(PackImportRefused) as error:
        import_pack(path, tier=TrustTier.BUILTIN)
    assert error.value.reason == 'link'
    assert list(outside.iterdir()) == []
    assert set(item.relative_to(home) for item in home.rglob('*')) == before


@pytest.mark.parametrize('relative', ['packs/installed.json', 'packs/installed.json.lock'])
def test_public_pack_import_preflight_hardlinked_publication(tmp_path, monkeypatch, relative):
    path, home = actual_pack(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.write_text('{}')
    outside.chmod(0o644)
    target = home / relative
    target.parent.mkdir()
    os.link(outside, target)
    with pytest.raises(PackImportRefused) as error:
        import_pack(path, tier=TrustTier.BUILTIN)
    assert error.value.reason == 'link'
    assert outside.read_text() == '{}'
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert not (home / 'prompts').exists()
    assert not (home / 'packs/.installing').exists()


def test_pack_fresh_id_keeps_existing_link_and_lands_beside(tmp_path, monkeypatch):
    path, home = actual_pack(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.write_text('keep')
    (home / 'prompts').mkdir()
    (home / 'prompts/sample.yaml').symlink_to(outside)
    import_pack(path, tier=TrustTier.BUILTIN)
    assert outside.read_text() == 'keep'
    assert (home / 'prompts/sample.yaml').is_symlink()
    assert (home / 'prompts/sample-imported-1.yaml').is_file()


def test_direct_ledger_hardlinked_lock_refused_before_mode_changes(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (home / 'packs').mkdir(parents=True)
    monkeypatch.setenv('GIDEON_HOME', str(home))
    outside = tmp_path / 'outside'
    outside.write_text('keep')
    outside.chmod(0o644)
    os.link(outside, home / 'packs/installed.json.lock')
    with pytest.raises(InstalledPackLedgerError):
        with installed_ledger(home):
            pytest.fail('must not enter ledger transaction')
    assert outside.read_text() == 'keep'
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
