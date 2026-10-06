"""CSV formula safety at real native mutation, tool and HTTP export paths."""
import csv
import io
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.readers import FileReader, delimited_rows
from gideon.interfaces.dashboard import token_auth
from gideon.integrations.mcp_artifacts import _call_tool_inner
from gideon.workspace.artifacts import registry, source_files
from gideon.workspace.artifacts.handlers import register_artifact_routes
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.documents.model import SheetModel, Sheet, SheetCell
from gideon.workspace.documents.writers.csv_writer import render_csv, render_csv_text


def cells(text):
    return list(csv.reader(io.StringIO(text, newline='')))


@pytest.mark.parametrize('value', ['=1+1','+SUM(A1)','-cmd|x','@SUM(A1)','\t=1','\r=1','\ufeff=1','-12%evil','-$45evil'])
def test_formula_leading_cells_are_literal_and_idempotent(value):
    model=SheetModel.from_rows({'Only':[[value]]})
    output=render_csv(model).decode()
    assert cells(output) == [["'"+value]]
    assert render_csv_text(output) == output


@pytest.mark.parametrize('value', [-20,1.5,None,'-20','-1,234.56','-$45.20','-12.5%','+1.5e3','-45€','---','2026-10-06',"'=1",'normal'])
def test_numbers_dates_and_benign_text_keep_native_values(value):
    output=render_csv(SheetModel.from_rows({'Only':[[value]]})).decode()
    assert cells(output) == [[str(value) if value is not None else '']]


def test_quotes_multiline_bom_and_declared_formulas():
    text='\ufeffname,value\r\n"a,b","=SUM(A1)"\r\n"two\nlines",ok\r\n'
    safe=render_csv_text(text)
    assert safe.startswith('\ufeff')
    assert cells(safe.lstrip('\ufeff')) == [['name','value'],['a,b',"'=SUM(A1)"],['two\nlines','ok']]
    benign='name,value\r\n"a,b",-20\r\n'
    assert render_csv_text(benign) == benign
    sheet=Sheet(name='Only',cells=[[SheetCell(formula='=SUM(A1)')]])
    assert cells(render_csv(SheetModel(sheets=[sheet])).decode()) == [["'=SUM(A1)"]]
    assert delimited_rows('a, "b,c"\n"multi\nline", "a""b"\n\n') == [['a','b,c'],['multi\nline','a"b']]


def test_native_mutations_revert_and_invalid_text(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    p=NativeArtifactProvider(tmp_path/'artifacts')
    art=p.create(name='CSV',kind='csv',content='a,=1\n')
    assert cells(p.get(art.slug).content) == [['a',"'=1"]]
    p.update(art.slug,content='b,@SUM(A1)\n')
    assert cells(p.get(art.slug).content) == [['b',"'@SUM(A1)"]]
    historical=p.root/art.slug/'versions'/'v1.html'
    historical.write_text('a,=legacy\n')
    before=historical.read_bytes()
    restored=p.revert(art.slug,1)
    assert restored is not None and cells(restored.content) == [['a',"'=legacy"]]
    assert historical.read_bytes() == before
    fingerprint=p.state_fingerprint(art.slug)
    oversized='x'* (csv.field_size_limit()+1)
    with pytest.raises(ValueError,match='could not read the csv'):
        p.update(art.slug,content=oversized)
    assert p.state_fingerprint(art.slug) == fingerprint
    with pytest.raises(ValueError,match='could not read the csv'):
        p.create(name='Invalid',kind='csv',slug='invalid',content=oversized)
    assert p.get('invalid') is None
    ordinary=p.create(name='Text',kind='text',content='=not CSV')
    assert ordinary.content == '=not CSV'


def test_real_sheet_tool_and_knowledge_share_quoted_parser(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    p=NativeArtifactProvider(tmp_path/'artifacts')
    previous=registry._providers.get('native')
    registry.register_provider(p)
    try:
        reply=_call_tool_inner('sheet_create',{'name':'Quoted','format':'csv','csv':'name, value\n"a,b", "=danger"\n"multi\nline", -20'})
        assert '/#/artifacts/' in reply and '/raw' not in reply
        art=p.get(p.list()[0].slug)
        assert cells(art.content) == [['name','value'],['a,b',"'=danger"],['multi\nline','-20']]
        reply=_call_tool_inner('sheet_create',{'name':'Quoted','format':'csv','csv':'name, value\n"a,b", "=danger"\n"multi\nline", -20'})
        assert reply.startswith('Updated')
        assert "''=danger" not in p.get(art.slug).content
        source=tmp_path/'quoted.csv'
        source.write_text('name, value\n"a,b", "multi\nline"\n')
        text,metadata=FileReader()._read_csv(str(source))
        assert metadata['row_count'] == 2 and 'a,b' in text and metadata['format'] == 'csv'
        count=len(p.list())
        reply=_call_tool_inner('sheet_create',{'name':'Invalid','format':'csv','csv':'x'*(csv.field_size_limit()+1)})
        assert 'could not read the csv text' in reply and len(p.list()) == count
    finally:
        registry.unregister_provider('native')
        if previous is not None: registry.register_provider(previous)


@pytest.mark.asyncio
async def test_real_authenticated_csv_export_keeps_source_and_history_immutable(tmp_path,monkeypatch):
    monkeypatch.setenv('GIDEON_HOME',str(tmp_path/'home'))
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS',raising=False)
    workspace=tmp_path/'workspace';workspace.mkdir()
    monkeypatch.setenv('GIDEON_WORKSPACE',str(workspace))
    p=NativeArtifactProvider(tmp_path/'artifacts')
    previous=registry._providers.get('native')
    registry.register_provider(p)
    token_auth.use_ephemeral_secret(b'csv-export-test')
    token_auth.revoke_all_sessions()
    owner={'Authorization':'Bearer '+token_auth.generate_token('csv-owner',kind='desktop')}
    app=web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
    app['state']=None
    app['port']=0
    app['allowed_origins']=set()
    register_artifact_routes(app)
    client=TestClient(TestServer(app));await client.start_server()
    try:
        art=p.create(name='Report / cash',kind='csv',content='name,=1\n')
        historical=p.root/art.slug/'versions'/'v1.html';historical.write_text('name,@legacy\n')
        old=historical.read_bytes()
        route=f'/api/artifacts/{art.slug}/export.csv'
        assert (await client.get(route)).status == 403
        response=await client.get(route+'?version=1',headers=owner)
        assert response.status == 200 and response.headers['Content-Type'].startswith('text/csv')
        assert '.csv' in response.headers['Content-Disposition'] and '-v1' in response.headers['Content-Disposition']
        assert cells(await response.text()) == [['name',"'@legacy"]]
        assert historical.read_bytes() == old
        assert (await client.get(route+'?version=-1',headers=owner)).status == 400
        source=workspace/'source.csv';source.write_text('name,=external\n')
        content,revision=source_files.read(str(source))
        backed=p.create(name='Source',kind='csv',source_path=str(source),source_revision=revision)
        original=source.read_bytes()
        response=await client.get(f'/api/artifacts/{backed.slug}/export.csv',headers=owner)
        assert response.status == 200 and cells(await response.text()) == [['name',"'=external"]]
        assert source.read_bytes() == original
        with pytest.raises(ValueError,match='source file changed'):
            p.update(backed.slug,content='name,=new\n',source_revision='stale')
        assert source.read_bytes() == original
        response=await client.patch(f'/api/artifacts/{art.slug}',json={'content':'name,=editor\n'},headers=owner)
        assert response.status == 200
        assert cells(p.get(art.slug).content) == [['name',"'=editor"]]
        response=await client.get(route,headers=owner)
        assert cells(await response.text()) == [['name',"'=editor"]]
    finally:
        await client.close();token_auth.revoke_all_sessions();token_auth.use_persistent_secret()
        registry.unregister_provider('native')
        if previous is not None:registry.register_provider(previous)
