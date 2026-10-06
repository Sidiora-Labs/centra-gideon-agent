"""Real app code registration follows native app unload and reload."""
import json
from pathlib import Path
import pytest
from gideon.extensions.apps import app_manager,manager,code_provenance
from gideon.extensions.apps.native_contract import load_bundle_module
from gideon.extensions.skills.marketplace import get_default_skills_registry

_SOURCE='''
import os,sys,subprocess
from pathlib import Path
from gideon.extensions.skills.marketplace import SkillsMarketplace,SkillEntry,SkillDetail,get_default_skills_registry
class Catalog(SkillsMarketplace):
    def touch(self,kind):
        subprocess.run([sys.executable,"-c","from pathlib import Path; import sys; p=Path(sys.argv[1]); p.open('a').write(sys.argv[2]+'\\\\n')",os.environ["MARKETPLACE_TRACE"],kind],check=True)
    def search(self,query,limit=20):
        self.touch('search')
        return [SkillEntry('helper','Helper','fixture', 'fixture-catalog')]
    def fetch(self,skill_id):
        self.touch('fetch')
        return SkillDetail(skill_id,'Helper',files=[{'path':'SKILL.md','contents':'---\\nname: helper\\ndescription: A fixture\\n---\\n\\nBenign helper.'}])
marketplace=Catalog()
get_default_skills_registry().register('fixture-catalog',marketplace)
def register_again():get_default_skills_registry().register('fixture-later',marketplace)
def create_provider(config=None):return None
'''

@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    monkeypatch.setenv('MARKETPLACE_TRACE',str(tmp_path/'trace'))
    name='marketplace-fixture'
    root=tmp_path/'source';root.mkdir()
    (root/'app.json').write_text(json.dumps({'name':name,'version':'1.0.0','displayName':'Fixture','description':'Catalog fixture','provider':{'type':'skills','implementation':'provider:create_provider'}}))
    (root/'provider.py').write_text(_SOURCE)
    registry=get_default_skills_registry()
    yield name,root,registry,tmp_path/'trace'
    if manager._read_installed(name) is not None:app_manager.force_uninstall(name)
    registry.unregister('fixture-catalog');registry.unregister('fixture-later')
    code_provenance.release(name)


def assert_withdrawn(registry,trace,target):
    before=trace.read_bytes() if trace.exists() else b''
    assert 'fixture-catalog' not in registry.list()
    assert all(row['name']!='fixture-catalog' for row in registry.info())
    for operation in (lambda:registry.get('fixture-catalog').search('helper'),lambda:registry.get('fixture-catalog').fetch('helper'),lambda:registry.install_guarded('fixture-catalog','helper',target)):
        with pytest.raises(KeyError):operation()
    assert (trace.read_bytes() if trace.exists() else b'')==before
    assert {'native','installed'}<=set(registry.list())


def test_disable_reenable_and_late_registration_follow_unload(app):
    name,root,registry,trace=app
    assert app_manager.install(root,confirm=True).ok
    old=registry.get('fixture-catalog')
    assert old.search('helper')[0].name=='Helper'
    assert old.fetch('helper').name=='Helper'
    module=load_bundle_module(manager.app_dir(name),name,'provider')
    module.register_again();assert 'fixture-later' in registry.list()
    assert app_manager.disable(name)
    assert 'fixture-later' not in registry.list()
    assert_withdrawn(registry,trace,root/'installed')
    assert app_manager.enable(name)
    assert registry.get('fixture-catalog') is not old
    assert registry.get('fixture-catalog').search('helper')

@pytest.mark.parametrize('remove',['uninstall','uninstall_keep_data','force_uninstall'])
def test_all_uninstall_rungs_withdraw_catalog(app,remove):
    name,root,registry,trace=app
    assert app_manager.install(root,confirm=True).ok
    assert getattr(app_manager,remove)(name)
    assert_withdrawn(registry,trace,root/'installed')


def test_update_unloads_old_catalog_then_registers_current_code(app):
    name,root,registry,trace=app
    assert app_manager.install(root,confirm=True).ok
    old=registry.get('fixture-catalog')
    manifest=json.loads((root/'app.json').read_text());manifest['version']='1.1.0';(root/'app.json').write_text(json.dumps(manifest))
    (root/'provider.py').write_text(_SOURCE.replace("'Helper'","'Updated'"))
    result=app_manager.update(root,name=name,confirm=True)
    assert result.ok,result.error
    assert registry.get('fixture-catalog') is not old
    assert registry.get('fixture-catalog').search('helper')[0].name=='Updated'


def test_stale_cleanup_preserves_other_apps_replacement(tmp_path):
    registry=get_default_skills_registry()
    for name in ('catalog-one','catalog-two'):
        root=tmp_path/name;root.mkdir();(root/'provider.py').write_text(_SOURCE)
        load_bundle_module(root,name,'provider')
    replacement=registry.get('fixture-catalog')
    code_provenance.release('catalog-one')
    assert registry.get('fixture-catalog') is replacement
    code_provenance.release('catalog-two')
    assert 'fixture-catalog' not in registry.list()


def test_failed_module_import_rolls_back_only_its_callbacks(tmp_path):
    registry=get_default_skills_registry()
    root=tmp_path/'failed';root.mkdir();(root/'provider.py').write_text(_SOURCE+"\nraise ValueError('failed import')\n")
    with pytest.raises(ValueError,match='failed import'):
        load_bundle_module(root,'catalog-failed','provider')
    assert 'fixture-catalog' not in registry.list()
    assert code_provenance.loaded_app(str(root/'provider.py')) is None
    assert {'native','installed'}<=set(registry.list())


def test_withdrawal_failure_is_visible_and_retryable(tmp_path):
    root=tmp_path/'broken';root.mkdir()
    (root/'provider.py').write_text(_SOURCE+'''
from gideon.extensions.apps.code_provenance import keep
attempts=[]
def cleanup():
    attempts.append(1)
    if len(attempts)==1:raise ValueError('cleanup failed')
keep(cleanup)
''')
    module=load_bundle_module(root,'catalog-broken','provider')
    with pytest.raises(RuntimeError,match='retained registrations'):
        code_provenance.release('catalog-broken')
    assert 'fixture-catalog' not in get_default_skills_registry().list()
    assert code_provenance.loaded_app(str(root/'provider.py'))=='catalog-broken'
    code_provenance.release('catalog-broken')
    assert len(module.attempts)==2
    assert code_provenance.loaded_app(str(root/'provider.py')) is None
