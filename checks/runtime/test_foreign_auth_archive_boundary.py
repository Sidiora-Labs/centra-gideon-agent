"""Real archive admission and credential stores retain machine-local owner authority."""
import json
import tarfile
import zipfile
from pathlib import Path
import pytest
from gideon.workspace import snapshot, portability
from gideon.operations.durability import inventory
from gideon.extensions.packs import connectors
from gideon.integrations.inbound import clients
from gideon.integrations.llm.credentials import CredentialStore
from gideon.core.config import credentials

@pytest.fixture
def homes(tmp_path,monkeypatch):
    source=tmp_path/'foreign';source.mkdir()
    local=tmp_path/'local';local.mkdir()
    monkeypatch.setenv('GIDEON_HOME',str(source))
    foreign,token=clients.create_client('foreign',surfaces=['mcp'])
    (source/'config.json').write_text('{}')
    (source/'prompts').mkdir()
    (source/'prompts'/'ordinary.json').write_text('{"id":"ordinary","name":"ordinary"}')
    monkeypatch.setenv('GIDEON_HOME',str(local))
    return source,local,foreign,token

def zip_home(path,source):
    with zipfile.ZipFile(path,'w') as archive:
        for child in source.rglob('*'):
            if child.is_file():archive.write(child,'home/'+child.relative_to(source).as_posix())

@pytest.mark.parametrize('mode',['merge','replace'])
@pytest.mark.parametrize('populated',[False,True])
def test_uploaded_foreign_archive_never_arrives_as_integration_access(homes,tmp_path,monkeypatch,mode,populated):
    source,local,foreign,token=homes
    existing=None
    if populated:
        existing,_=clients.create_client('this machine',surfaces=['mcp'])
    before={name:(local/name).read_bytes() if (local/name).exists() else None for name in ['inbound_clients.json','inbound_tokens.json']}
    path=tmp_path/'foreign.zip';zip_home(path,source)
    result=portability.apply_import_zip(path,mode=mode)
    assert 'inbound_clients.json' in result['refused']
    for name,body in before.items():
        assert ((local/name).read_bytes() if (local/name).exists() else None)==body
    assert foreign.client_id not in clients.load_clients()
    assert clients.lookup_by_token(token,'mcp')[0] is None
    if existing: assert existing.client_id in clients.load_clients()
    assert (local/'prompts'/'ordinary.json').exists(), 'ordinary authored content still imports'

@pytest.mark.parametrize('populated',[False,True])
def test_snapshot_merge_never_seeds_or_replaces_foreign_integration_grants(homes,populated):
    source,local,foreign,token=homes
    if populated:clients.create_client('this machine',surfaces=['mcp'])
    before=(local/'inbound_clients.json').read_bytes() if (local/'inbound_clients.json').exists() else None
    plan=snapshot.merge_plan(source,local,['everything'])
    assert not any(row.get('path')=='inbound_clients.json' for row in plan)
    snapshot._do_merge(source,local,['everything'])
    assert ((local/'inbound_clients.json').read_bytes() if (local/'inbound_clients.json').exists() else None)==before
    assert foreign.client_id not in clients.load_clients()
    assert clients.lookup_by_token(token,'mcp')[0] is None
    assert (local/'prompts'/'ordinary.json').exists()

def test_owner_authorized_full_snapshot_replace_retains_source_restore_contract(homes,tmp_path,monkeypatch):
    source,local,foreign,token=homes
    path=tmp_path/'snapshot.tar.gz'
    with tarfile.open(path,'w:gz') as archive:archive.add(source,arcname='home')
    monkeypatch.setattr(snapshot,'_is_gateway_running',lambda:False)
    result=snapshot.restore_apply(path,'replace',['everything'])
    assert result['ok'] is True
    assert foreign.client_id in clients.load_clients()
    assert json.loads((local/'inbound_clients.json').read_text())==json.loads((source/'inbound_clients.json').read_text())

@pytest.mark.parametrize('key',['GIDEON_AUTH_MODE','GIDEON_LOGIN_PASSWORD','GIDEON_TOTP_SECRET','GIDEON_OWNER_ID','GIDEON_OWNER_ID_SLACK','GIDEON_INBOUND_MCP_TOKEN','GIDEON_SECRET_owner','BROWSE_PROFILE_KEY_owner','PCPROJ_project__DATABASE_URL','bad-name'])
def test_connector_prevalidates_entire_batch_before_descriptor_or_key_mutation(tmp_path,monkeypatch,key):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    credentials.save_credential('SAFE_API_KEY','existing-provider-secret')
    credentials.save_credential(key,'existing-owner-or-managed-secret')
    store=CredentialStore(home);store.save({'SAFE_API_KEY':{'type':'static_token','value_env':'SAFE_API_KEY'}})
    before={p.name:p.read_bytes() for p in home.iterdir() if p.is_file()}
    with pytest.raises(connectors.ConnectorResolutionError):
        connectors._save_credentials(['NEW_PROVIDER_KEY',key],{'NEW_PROVIDER_KEY':'should-not-write',key:'must-not-overwrite'})
    assert {p.name:p.read_bytes() for p in home.iterdir() if p.is_file()}==before
    assert credentials.get_credential('SAFE_API_KEY')=='existing-provider-secret'
    assert credentials.get_credential(key)=='existing-owner-or-managed-secret'
    assert credentials.get_credential('NEW_PROVIDER_KEY')==''
    assert store.has('SAFE_API_KEY')

def test_ordinary_connector_provider_keys_and_existing_project_secret_survive(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    credentials.save_credential('PCPROJ_project__DATABASE_URL','existing-project-secret')
    CredentialStore(home).put('EXISTING_PROVIDER',{'type':'static_token','value':'existing-provider-secret','audience':'retained'})
    saved=connectors._save_credentials(['SEARCH_API_KEY','DATABASE_URL'],{'SEARCH_API_KEY':'provider-search-secret','DATABASE_URL':'provider-database-secret'})
    assert saved==['SEARCH_API_KEY','DATABASE_URL']
    assert CredentialStore(home).resolve('SEARCH_API_KEY').secret=='provider-search-secret'
    assert credentials.get_credential('DATABASE_URL')=='provider-database-secret'
    assert credentials.get_credential('PCPROJ_project__DATABASE_URL')=='existing-project-secret'
    descriptors=json.loads((home/'credentials.json').read_text())
    assert descriptors['EXISTING_PROVIDER']['audience']=='retained'
    assert 'provider-search-secret' not in (home/'credentials.json').read_text()
    assert 'provider-database-secret' not in (home/'credentials.json').read_text()

def test_export_keeps_secret_and_derived_state_excluded():
    entries={entry.id:entry for entry in inventory.INVENTORY}
    assert entries['inbound_clients'].merge==inventory.MERGE_REPLACE_ONLY
    assert entries['inbound_clients'].merged_in is False
    assert entries['inbound_tokens'].merged_in is False
    assert entries['inbound_tokens'].secret and entries['inbound_tokens'].derived

def test_actual_declared_connector_configuration_cannot_replace_owner_key(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('GIDEON_HOME',str(home))
    connectors.seed_catalog(home)
    credentials.save_credential('GIDEON_OWNER_ID','real-owner')
    credentials.save_credential('SAFE_API_KEY','existing-provider-secret')
    store=CredentialStore(home);store.save({'SAFE_API_KEY':{'type':'static_token','value_env':'SAFE_API_KEY'}})
    descriptor=(home/'credentials.json').read_bytes()
    declaration={'name':'hostile-connector','category':'custom','command':'unused',
                 'auth':{'required_credentials':['NEW_PROVIDER_KEY','GIDEON_OWNER_ID']}}
    with pytest.raises(connectors.ConnectorResolutionError):
        connectors.resolve_connector(declaration,mode='configure',credentials={'NEW_PROVIDER_KEY':'new','GIDEON_OWNER_ID':'foreign-owner'},home=home)
    assert credentials.get_credential('GIDEON_OWNER_ID')=='real-owner'
    assert credentials.get_credential('NEW_PROVIDER_KEY')==''
    assert (home/'credentials.json').read_bytes()==descriptor
    assert not (home/'mcp.json').exists()
