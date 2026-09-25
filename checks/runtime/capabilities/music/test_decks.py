import copy
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pdfplumber
import pytest
from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.decks import DeckStore
from gideon.workspace.capabilities.music.deck_layout import roster,compose,MAJORS
from gideon.workspace.capabilities.music.deck_tools import DeckTools
from gideon.workspace.capabilities.music.store import DomainError
from gideon.workspace.capabilities.media.jobs import MediaWorker
from gideon.interfaces.dashboard.handlers.capabilities_music_decks import register


def store_at(home):return DeckStore(home,NativeArtifactProvider(root=home/'artifacts'))

def picture(store,color='red'):
    data=io.BytesIO();Image.new('RGB',(80,120),color).save(data,'PNG')
    artifact=store.artifacts.create_binary(name='Authored color art',data=data.getvalue(),mime='image/png',kind='image',source='import')
    return {'slug':artifact.slug,'version':artifact.version}

def card_data(item,key='spades-A',**changes):
    card=next(row for row in item['cards'] if row['key']==key)
    return {'revision':item['revision'],'name':card['name'],'prompt':card['prompt'],'negative_prompt':card['negative_prompt'],'artifact_ref':card['artifact_ref'],**changes}


def test_complete_rosters_and_defaults(tmp_path):
    store=store_at(tmp_path)
    playing=store.create({'name':'Playing','kind':'playing'})
    tarot=store.create({'name':'Tarot','kind':'tarot'})
    assert len(playing['cards'])==55 and len(tarot['cards'])==79
    assert len({card['key'] for card in playing['cards']})==55
    assert len({card['key'] for card in tarot['cards']})==79
    assert sum(row['group']=='major' for row in tarot['cards'])==22
    assert sum(row['group']=='jokers' for row in playing['cards'])==2
    assert tarot['cards'][0]['name']=='The Fool'
    assert tarot['cards'][21]['name']=='The World'
    assert all(item['cards'][-1]['key']=='back' for item in (playing,tarot))
    assert playing['width_mm']==63.5 and playing['height_mm']==88.9
    assert tarot['width_mm']==69.85 and tarot['height_mm']==120.65
    assert playing['orientation']=='two_way' and tarot['orientation']=='one_way'
    assert playing['completion']=={'total':55,'artwork':0,'prompted':0}
    assert store_at(tmp_path).get(tarot['id'])==tarot
    with sqlite3.connect(store.path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==1


def test_revision_history_styles_and_archive(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'First','kind':'playing'})
    sample=picture(store)
    context=store.artifacts.create(name='Authored setting',content='{"setting":"Garden"}',kind='json',source='import')
    changed=store.update(item['id'],{'revision':1,'name':'Garden','style_notes':'Botanical ink','negative_prompt':'Blur','sample_refs':[sample],'context_ref':{'slug':context.slug,'version':context.version},'orientation':'one_way','archived':True})
    assert changed['revision']==2 and changed['archived']
    assert changed['sample_refs']==[sample]
    assert changed['context_ref']['slug']==context.slug
    history=store.history(item['id'])
    assert [row['revision'] for row in history]==[2,1]
    assert history[1]['name']=='First'
    with pytest.raises(DomainError) as error:store.update(item['id'],{'revision':1,'name':'Lost update'})
    assert error.value.status==409
    assert store.get(item['id'])['name']=='Garden'
    assert store.list()[0]['archived']
    assert store_at(tmp_path).history(item['id'])==history


def test_card_edits_pinned_art_and_clear(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Deck','kind':'playing'});ref=picture(store)
    item=store.card(item['id'],'spades-A',card_data(item,name='Garden Ace',prompt='A flowering tree',negative_prompt='No letters',artifact_ref=ref))
    assert item['revision']==2
    assert item['cards'][0]['artifact_ref']==ref
    assert item['completion']=={'total':55,'artwork':1,'prompted':1}
    assert store.image(ref).getpixel((10,10))==(255,0,0,255)
    item=store.card(item['id'],'spades-A',card_data(item,artifact_ref=None))
    assert item['completion']['artwork']==0
    assert item['completion']['prompted']==1
    assert store.history(item['id'])[1]['cards'][0]['artifact_ref']==ref
    with pytest.raises(DomainError):store.card(item['id'],'unknown',card_data(item))
    with pytest.raises(DomainError):store.card(item['id'],'spades-A',{**card_data(item),'rank':'K'})


@pytest.mark.parametrize('fields',[{'width_mm':float('nan')},{'height_mm':201},{'bleed_mm':-1},{'safe_mm':1},{'width_mm':30,'safe_mm':15},{'orientation':'diagonal'},{'archived':'yes'},{'sample_refs':[None]},{'context_ref':{'slug':'missing','version':1}},{'provider':'private'},{'name':''}])
def test_invalid_update_preserves_revision(tmp_path,fields):
    store=store_at(tmp_path);item=store.create({'name':'Deck','kind':'playing'})
    with pytest.raises(DomainError):store.update(item['id'],{'revision':1,**fields})
    assert store.get(item['id'])==item
    assert len(store.history(item['id']))==1


def test_foreign_missing_wrong_kind_and_corrupt_art(tmp_path):
    store=store_at(tmp_path/'one');other=store_at(tmp_path/'two')
    ref=picture(other);item=store.create({'name':'Deck','kind':'playing'})
    with pytest.raises(DomainError):store.card(item['id'],'spades-A',card_data(item,artifact_ref=ref))
    json_art=store.artifacts.create(name='JSON',content='{}',kind='json',source='import')
    with pytest.raises(DomainError):store.image({'slug':json_art.slug,'version':json_art.version})
    bad=store.artifacts.create_binary(name='Invalid PNG',data=b'not a PNG',mime='image/png',kind='image',source='import')
    with pytest.raises(DomainError):store.image({'slug':bad.slug,'version':bad.version})
    assert store.get(item['id'])['completion']['artwork']==0


def test_pdf_actual_dimensions_pages_text_images_and_cached_receipt(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Print deck','kind':'playing'});ref=picture(store)
    item=store.card(item['id'],'spades-A',card_data(item,artifact_ref=ref))
    receipt=store.export(item['id'],{'revision':item['revision']})
    raw,mime=store.artifacts.raw_bytes(receipt['pdf_ref']['slug'],version=receipt['pdf_ref']['version'])
    assert mime=='application/pdf' and raw.startswith(b'%PDF-')
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        assert len(pdf.pages)==55
        page=pdf.pages[0]
        assert page.width==pytest.approx((63.5+6)*72/25.4,abs=.001)
        assert page.height==pytest.approx((88.9+6)*72/25.4,abs=.001)
        assert 'A of Spades' in page.extract_text()
        assert len(page.images)==1
        assert page.images[0]['srcsize']==(80,120)
        assert len(page.lines)==8
        assert 'Card back' in pdf.pages[-1].extract_text()
    manifest=store.artifacts.get(receipt['manifest_ref']['slug'],version=receipt['manifest_ref']['version'])
    document=json.loads(manifest.content)
    assert document['cards'][0]['artifact_ref']==ref
    assert document['revision']==2 and len(document['cards'])==55
    assert store.export(item['id'],{'revision':2})==receipt
    assert store_at(tmp_path).get(item['id'])['exports']==[receipt]
    next_item=store.update(item['id'],{'revision':2,'name':'Revised deck'})
    next_receipt=store.export(item['id'],{'revision':3})
    assert next_receipt!=receipt
    assert len(store.get(item['id'])['exports'])==2
    with pytest.raises(DomainError):store.export(item['id'],{'revision':1})


def test_tarot_export_and_unicode_refusal(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Tarot','kind':'tarot'})
    receipt=store.export(item['id'],{'revision':1})
    with pdfplumber.open(io.BytesIO(store.artifacts.raw_bytes(receipt['pdf_ref']['slug'])[0])) as pdf:
        assert len(pdf.pages)==79
        assert 'The Fool' in pdf.pages[0].extract_text()
        assert 'The World' in pdf.pages[21].extract_text()
        assert pdf.pages[0].width==pytest.approx((69.85+6)*72/25.4,abs=.001)
    item=store.card(item['id'],'major-0',card_data(item,'major-0',name='世界'))
    with pytest.raises(DomainError) as error:store.export(item['id'],{'revision':2})
    assert error.value.code=='unsupported_pdf_glyph'
    assert store.get(item['id'])['cards'][0]['name']=='世界'
    assert len(store.get(item['id'])['exports'])==1


def test_actual_prompt_composition_and_durable_job_submission(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Deck','kind':'playing'})
    item=store.update(item['id'],{'revision':1,'style_notes':'Watercolor','negative_prompt':'Blur'})
    item=store.card(item['id'],'spades-A',card_data(item,prompt='An oak tree',negative_prompt='Text'))
    composed=compose(item,item['cards'][0])
    assert composed['parts']['style']=='Watercolor'
    assert composed['parts']['subject']=='A of Spades: An oak tree'
    assert '180 degrees' in composed['prompt']
    assert composed['negative_prompt']=='Blur, Text'
    data={'revision':3,'request_id':'batch','card_keys':['spades-A','back'],'size':'','controls':{}}
    receipt=store.generate(item['id'],data)
    assert len(receipt['jobs'])==2
    assert [row['card_key'] for row in receipt['jobs']]==['spades-A','back']
    job=store.jobs.get(receipt['jobs'][0]['job_id'])
    assert job['operation']=='image_generate' and job['status']=='queued'
    assert job['input']['prompt']==composed['prompt']
    assert job['result'] is None
    assert store.generate(item['id'],data)==receipt
    assert store_at(tmp_path).generate(item['id'],data)==receipt
    assert len(store.jobs.list()['items'])==2
    with pytest.raises(DomainError):store.generate(item['id'],{**data,'card_keys':['back']})
    with pytest.raises(DomainError) as error:store.adopt(item['id'],'spades-A',{'revision':3,'job_id':job['id']})
    assert error.value.code=='job_incomplete'
    with pytest.raises(DomainError) as error:store.adopt(item['id'],'back',{'revision':3,'job_id':job['id']})
    assert error.value.code=='job_conflict'
    cancelled=store.jobs.cancel(job['id'],{'state_revision':job['state_revision']})
    assert cancelled['status']=='cancelled'
    assert store.get(item['id'])['completion']['artwork']==0


def test_batch_validation_before_any_job(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Deck','kind':'playing'})
    for keys in ([],['back','back'],['back','missing'],['spades-A']*17):
        with pytest.raises(DomainError):store.generate(item['id'],{'revision':1,'request_id':'invalid','card_keys':keys,'size':'','controls':{}})
    assert store.jobs.list()['items']==[]
    assert store.get(item['id'])['revision']==1


def test_concurrent_revisions_only_one_wins(tmp_path):
    store=store_at(tmp_path);item=store.create({'name':'Deck','kind':'playing'})
    def change(index):
        try:return store_at(tmp_path).update(item['id'],{'revision':1,'name':str(index)})['revision']
        except DomainError:return 'conflict'
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(change,range(4)))
    assert results.count(2)==1 and results.count('conflict')==3
    assert len(store.history(item['id']))==2


@pytest.mark.asyncio
async def test_http_native_actual_print_and_pinned_download(tmp_path):
    store=store_at(tmp_path);app=web.Application();register(app,store)
    base='/api/capabilities/music/decks'
    async with TestClient(TestServer(app)) as client:
        response=await client.post(base,json={'name':'HTTP cards','kind':'playing'})
        assert response.status==200
        item=(await response.json())['item']
        changed=await client.patch(base+'/'+item['id']+'/cards/back',json=card_data(item,'back',prompt='Woven branches'))
        assert changed.status==200
        item=(await changed.json())['item']
        result=await(await client.post(base+'/'+item['id']+'/export',json={'revision':2})).json()
        ref=result['pdf_ref'];response=await client.get(base+f"/artifacts/{ref['slug']}/{ref['version']}/raw")
        assert response.status==200 and response.content_type=='application/pdf'
        assert (await response.read()).startswith(b'%PDF-')
        assert len((await(await client.get(base+'/'+item['id']+'/history')).json())['items'])==2
        assert (await client.patch(base+'/'+item['id'],json={'revision':1,'name':'stale'})).status==409
        assert (await client.get(base+'/missing')).status==404
    tools=DeckTools(store)
    assert len(await tools.list_tools())==9
    for action,args in [('get',{'id':item['id']}),('list',{}),('history',{'id':item['id']}),('export',{'id':item['id'],'data':{'revision':2}})]:
        result=await tools.invoke('music_decks_'+action,args)
        assert result.success and json.loads(result.output) is not None
    assert not (await tools.invoke('music_decks_list',{'home':'elsewhere'})).success
    assert not (await tools.invoke('music_decks_adopt',{'id':item['id'],'key':'back','data':{'revision':2,'job_id':'unknown'}})).success
    assert not (await tools.invoke('unknown',{})).success
