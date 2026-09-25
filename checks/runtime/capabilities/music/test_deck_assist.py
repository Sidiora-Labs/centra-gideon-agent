import base64
import copy
import io
import json
import sqlite3
import pytest
from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from gideon.integrations.llm.registry import ProviderRegistry
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.music.deck_assist import DeckAssist,parse_result,apply_values
from gideon.workspace.capabilities.music.deck_model import DeckModel
from gideon.workspace.capabilities.music.deck_tools import DeckTools
from gideon.workspace.capabilities.music.store import DomainError
from gideon.interfaces.dashboard.handlers.capabilities_music_decks import register
from test_decks import store_at,picture


def assist_at(home):
    decks=store_at(home)
    return DeckAssist(decks,model=DeckModel(decks,ProviderRegistry()))


def universe_at(assist):
    return assist.universes.create({'request_id':'garden','title':'Garden','canon':[{'id':'hero','title':'Willow','body':'A patient gardener who protects the river.'},{'id':'tower','title':'Stone tower','body':'An abandoned lookout above the orchard.'}],'visual_identity':{'colors':['#445566'],'style_notes':'Botanical copper engravings'},'ingredient_ids':[],'board_refs':[]})


def prompt_request(item,universe=None,**changes):
    return {'revision':item['revision'],'request_id':'prompts','provider_name':'not-configured','card_keys':['spades-A'],'universe_ref':{'id':universe['id'],'revision':universe['revision']} if universe else None,**changes}


def output(**changes):return {'cards':[{'key':'spades-A','prompt':'Willow tending an oak sapling beside the river.','negative_prompt':'Blur','canon_id':'hero',**changes}]}


def test_pinned_universe_grounding_survives_canon_edits(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Garden deck','kind':'playing'});universe=universe_at(assist)
    data=prompt_request(item,universe)
    proposal=assist.prepare(item['id'],'prompts',data)
    assert proposal['universe_ref']=={'id':universe['id'],'revision':1}
    assert proposal['universe_snapshot']['canon'][0]['title']=='Willow'
    assert proposal['universe_snapshot']['visual_identity']['colors']==['#445566']
    assert proposal['card_keys']==['spades-A']
    assert proposal['snapshot_revision']==1
    assert 'Never invent canon IDs' in proposal['prompt']
    assert 'A patient gardener' in proposal['prompt']
    assist.universes.update(universe['id'],{'revision':1,'title':'Updated setting','canon':[{'id':'other','title':'Someone else','body':'New canon'}]})
    reopened=assist_at(tmp_path)
    repeated=reopened.prepare(item['id'],'prompts',data)
    assert repeated['universe_snapshot']==proposal['universe_snapshot']
    assert repeated['prompt']==proposal['prompt']
    assert reopened.universes.get(universe['id'])['revision']==2
    assert repeated['universe_snapshot']['revision']==1


def test_actual_image_message_bytes_are_pinned_and_bounded(tmp_path):
    assist=assist_at(tmp_path);ref=picture(assist.decks,'blue')
    messages=assist.model.messages('Describe this actual image.',ref)
    assert messages[0]['role']=='system'
    assert 'reference data' in messages[0]['content']
    assert messages[1]['role']=='user'
    assert messages[1]['content'][0]=={'type':'text','text':'Describe this actual image.'}
    url=messages[1]['content'][1]['image_url']['url']
    assert url.startswith('data:image/png;base64,')
    image=Image.open(io.BytesIO(base64.b64decode(url.split(',',1)[1])))
    assert image.size==(80,120)
    assert image.getpixel((0,0))==(0,0,255,255)
    assert assist.model.messages('Only text.')[1]['content']==[{'type':'text','text':'Only text.'}]
    with pytest.raises(DomainError):assist.model.messages('x'*60001)
    with pytest.raises(DomainError):assist.model.messages('',ref)
    with pytest.raises(DomainError):assist.model.messages('Missing',{'slug':'unknown','version':1})


def test_analysis_preparation_uses_canonical_sample(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Sample','kind':'tarot'});ref=picture(assist.decks)
    data={'revision':1,'request_id':'sample','provider_name':'not-configured','sample_ref':ref}
    proposal=assist.prepare(item['id'],'analyze',data)
    assert proposal['sample_ref']==ref
    assert proposal['card_keys']==[] and proposal['universe_snapshot'] is None
    assert proposal['kind']=='analyze'
    assert 'Describe visible style' in proposal['prompt']
    assert proposal['result'] is None
    assert proposal['status']=='running'
    assert 'owner_pid' not in assist._public(proposal)
    assert 'prompt' not in assist._public(proposal)


def test_strict_prompt_parser_and_real_canon_mapping(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Garden','kind':'playing'});universe=universe_at(assist)
    parsed=parse_result('prompts',json.dumps(output()),['spades-A'],{'hero','tower'})
    assert parsed['cards'][0]['canon_id']=='hero'
    proposal=assist.prepare(item['id'],'prompts',prompt_request(item,universe))
    proposal['result']=parsed
    changed=apply_values(copy.deepcopy(item),proposal)
    assert changed['cards'][0]['prompt'].startswith('Willow tending')
    assert changed['cards'][0]['negative_prompt']=='Blur'
    assert changed['cards'][0]['canon_ref']=={'universe_id':universe['id'],'revision':1,'canon_id':'hero'}
    assert changed['cards'][0]['name']==item['cards'][0]['name']
    assert changed['cards'][1:]==item['cards'][1:]
    assert item['cards'][0]['prompt']==''
    without_cast=parse_result('prompts',json.dumps(output(canon_id=None)),['spades-A'],set())
    proposal['result']=without_cast;proposal['universe_ref']=None
    assert apply_values(copy.deepcopy(item),proposal)['cards'][0]['canon_ref'] is None


@pytest.mark.parametrize('document',[
 output(canon_id='invented'),output(key='back'),output(prompt=''),output(prompt=17),output(negative_prompt=[]),output(canon_id={}),
 {'cards':[]},{'cards':[output()['cards'][0],output()['cards'][0]]},{'cards':[{'key':'spades-A','prompt':'Tree','negative_prompt':''}]},{'cards':[output()['cards'][0]],'extra':True},[],
])
def test_rejects_bad_generated_casting_without_mutation(tmp_path,document):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Deck','kind':'playing'})
    with pytest.raises(DomainError):parse_result('prompts',json.dumps(document),['spades-A'],{'hero'})
    assert assist.decks.get(item['id'])==item


def test_analysis_validation_and_reference_limit(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Deck','kind':'playing'});ref=picture(assist.decks)
    analysis={'style_notes':'Blue ink on cream paper','layout_prompt':'Framed portrait','negative_prompt':'Heavy blur'}
    parsed=parse_result('analyze',json.dumps(analysis),[],set())
    proposal={'kind':'analyze','result':parsed,'sample_ref':ref}
    changed=apply_values(copy.deepcopy(item),proposal)
    assert changed['style_notes']=='Blue ink on cream paper'
    assert changed['layout_prompt']=='Framed portrait'
    assert changed['sample_refs']==[ref]
    assert apply_values(changed,proposal)['sample_refs']==[ref]
    changed['sample_refs']=[{'slug':str(index),'version':1} for index in range(12)]
    with pytest.raises(DomainError):apply_values(changed,proposal)
    for value in ('broken','[]','{}',json.dumps({**analysis,'extra':1}),json.dumps({**analysis,'style_notes':''})):
        with pytest.raises(DomainError):parse_result('analyze',value,[],set())
    with pytest.raises(DomainError):parse_result('analyze','x'*65537,[],set())


@pytest.mark.asyncio
async def test_absent_actual_provider_persists_failed_receipt_and_idempotent_retry(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Deck','kind':'playing'});data=prompt_request(item)
    result=await assist.propose(item['id'],'prompts',data)
    assert result['status']=='failed'
    assert result['error']=='Configured chat model unavailable'
    assert result['result'] is None and 'artifact_ref' not in result
    assert result['model']==''
    assert result['applied_revision'] is None
    assert await assist.propose(item['id'],'prompts',data)==result
    assert assist_at(tmp_path).get(item['id'],result['id'])==result
    assert assist.list(item['id'])==[result]
    with pytest.raises(DomainError) as error:await assist.propose(item['id'],'prompts',{**data,'card_keys':['back']})
    assert error.value.status==409
    with pytest.raises(DomainError) as error:assist.apply(item['id'],result['id'],{'revision':1})
    assert error.value.code=='proposal_incomplete'
    assert assist.decks.get(item['id'])==item
    assert len(assist.decks.history(item['id']))==1
    assert assist.providers()==[]


@pytest.mark.asyncio
async def test_missing_sample_or_foreign_universe_never_calls_model(tmp_path):
    assist=assist_at(tmp_path/'one');item=assist.decks.create({'name':'Deck','kind':'playing'})
    other=assist_at(tmp_path/'other');foreign=universe_at(other)
    with pytest.raises(ValueError):await assist.propose(item['id'],'prompts',prompt_request(item,foreign))
    with pytest.raises(DomainError):await assist.propose(item['id'],'analyze',{'revision':1,'request_id':'sample','provider_name':'absent','sample_ref':picture(other.decks)})
    assert assist.list(item['id'])==[]
    assert assist.decks.get(item['id'])==item


@pytest.mark.parametrize('changes',[{'card_keys':[]},{'card_keys':['back','back']},{'card_keys':['unknown']},{'card_keys':['back']*13},{'revision':2},{'universe_ref':{'id':'x'}},{'provider_name':''},{'endpoint':'https://example.com'}])
def test_request_validation(tmp_path,changes):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Deck','kind':'playing'})
    with pytest.raises(ValueError):assist.prepare(item['id'],'prompts',prompt_request(item,**changes))
    assert assist.list(item['id'])==[]


@pytest.mark.asyncio
async def test_real_http_and_native_refusal_isolation_and_persisted_receipts(tmp_path):
    assist=assist_at(tmp_path);item=assist.decks.create({'name':'Deck','kind':'playing'})
    second=assist.decks.create({'name':'Separate deck','kind':'tarot'})
    app=web.Application();register(app,assist.decks,assist)
    base='/api/capabilities/music/decks'
    async with TestClient(TestServer(app)) as client:
        assert (await(await client.get(base+'/assist/providers')).json())=={'items':[]}
        response=await client.post(base+'/'+item['id']+'/assist/prompts',json=prompt_request(item))
        assert response.status==200
        proposal=(await response.json())['item']
        assert proposal['status']=='failed'
        assert (await client.get(base+'/'+second['id']+'/assist/'+proposal['id'])).status==404
        rows=await(await client.get(base+'/'+item['id']+'/assist')).json()
        assert len(rows['items'])==1
        assert (await client.post(base+'/'+item['id']+'/assist/'+proposal['id']+'/apply',json={'revision':1})).status==409
        assert (await client.post(base+'/'+item['id']+'/assist/prompts',json={**prompt_request(item),'model':'override'})).status==409
    tools=DeckTools(assist.decks,assist)
    definitions=await tools.list_tools()
    assert len(definitions)==15
    assert (await tools.invoke('music_decks_assist_providers',{})).success
    assert (await tools.invoke('music_decks_assist_list',{'id':item['id']})).success
    receipt=await tools.invoke('music_decks_assist_get',{'id':item['id'],'proposal_id':proposal['id']})
    assert json.loads(receipt.output)['status']=='failed'
    assert not (await tools.invoke('music_decks_assist_apply',{'id':item['id'],'proposal_id':proposal['id'],'data':{'revision':1}})).success
    assert not (await tools.invoke('music_decks_assist_providers',{'home':'foreign'})).success
    assert assist.decks.get(item['id'])['revision']==1
