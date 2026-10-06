import os

import pytest

from gideon.extensions.packs.build import build_pack
from gideon.extensions.packs.import_ import import_pack
from gideon.extensions.packs.installed import load_installed, InstalledPackLedgerError
from gideon.extensions.packs.uninstall import plan_uninstall, apply_uninstall
from gideon.security.supply_chain import TrustTier


def install_actual_pack(tmp_path, monkeypatch, *, skill=False):
    author = tmp_path / 'author'
    (author / 'prompts').mkdir(parents=True)
    for name in ['blocked', 'safe', 'edited']:
        (author / 'prompts' / f'{name}.yaml').write_text(f'name: {name}\nkind: user\ncontent: hello\n')
    refs = ['prompt:blocked', 'prompt:safe', 'prompt:edited']
    if skill:
        folder = author / 'skills/sample'
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('---\nname: sample\ndescription: a sample\n---\n# Sample\nRead a note.\n')
        refs.append('skill:sample')
    monkeypatch.setenv('GIDEON_HOME', str(author))
    path = tmp_path / 'sample.gideon'
    build_pack(refs, name='sample', version='1.0.0', out_path=path)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    import_pack(path, tier=TrustTier.BUILTIN)
    return home


@pytest.mark.parametrize('kind', ['hard', 'symbolic'])
def test_public_uninstall_keeps_link_and_owner_edit_removes_safe_sibling(tmp_path, monkeypatch, kind):
    home = install_actual_pack(tmp_path, monkeypatch)
    blocked = home / 'prompts/blocked.yaml'
    outside = tmp_path / 'outside'
    outside.write_bytes(blocked.read_bytes())
    blocked.unlink()
    if kind == 'hard':
        os.link(outside, blocked)
    else:
        blocked.symlink_to(outside)
    edited = home / 'prompts/edited.yaml'
    edited.write_text('owner edited content')
    before = outside.read_bytes()
    plan = plan_uninstall('sample')
    assert any(item.ref == 'prompt:blocked' and 'linked' in item.reason for item in plan.kept)
    apply_uninstall('sample', plan.confirmation_token)
    assert outside.read_bytes() == before
    assert blocked.exists()
    assert edited.read_text() == 'owner edited content'
    assert not (home / 'prompts/safe.yaml').exists()


def test_public_uninstall_keeps_skill_with_linked_descendant(tmp_path, monkeypatch):
    home = install_actual_pack(tmp_path, monkeypatch, skill=True)
    outside = tmp_path / 'outside'
    outside.write_text('external private content')
    os.link(outside, home / 'skills/sample/note.txt')
    plan = plan_uninstall('sample')
    assert any(item.ref == 'skill:sample' and 'linked' in item.reason for item in plan.kept)
    apply_uninstall('sample', plan.confirmation_token)
    assert (home / 'skills/sample/SKILL.md').exists()
    assert outside.read_text() == 'external private content'
    assert not (home / 'prompts/safe.yaml').exists()


def test_lockless_installed_reader_refuses_hardlink(tmp_path, monkeypatch, caplog):
    home = tmp_path / 'home'
    (home / 'packs').mkdir(parents=True)
    monkeypatch.setenv('GIDEON_HOME', str(home))
    outside = tmp_path / 'outside'
    outside.write_text('{}')
    os.link(outside, home / 'packs/installed.json')
    assert load_installed(home) == []
    assert "ledger unreadable" in caplog.text
    assert outside.read_text() == '{}'
