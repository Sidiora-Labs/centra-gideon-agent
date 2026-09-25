"""Reviewable model proposals grounded in pinned images and universe revisions."""
import asyncio
import json
import os
from uuid import uuid4
from gideon.workspace.capabilities.creative.universes import UniverseStore
from gideon.workspace.capabilities.media.jobs import identity
from .deck_model import DeckModel
from .listening import digest
from .store import DomainError,text,integer


def parse_result(kind,content,keys,canon_ids):
    if not isinstance(content,str) or len(content)>65536:raise DomainError('Invalid model response size',422,'model_output')
    try:value=json.loads(content)
    except ValueError as exc:raise DomainError('Model did not return JSON',422,'model_output') from exc
    if not isinstance(value,dict):raise DomainError('Model result must be an object',422,'model_output')
    if kind=='analyze':
        if set(value)!={'style_notes','layout_prompt','negative_prompt'}:raise DomainError('Invalid sample analysis fields',422,'model_output')
        for key,limit in (('style_notes',2000),('layout_prompt',1000),('negative_prompt',1000)):text(value[key],key,limit,key=='style_notes')
    else:
        if set(value)!={'cards'} or not isinstance(value['cards'],list) or len(value['cards'])!=len(keys):raise DomainError('Model card count does not match selection',422,'model_output')
        seen=set()
        for card in value['cards']:
            if not isinstance(card,dict) or set(card)!={'key','prompt','negative_prompt','canon_id'}:raise DomainError('Invalid generated card fields',422,'model_output')
            if not isinstance(card['key'],str) or card['key'] not in keys or card['key'] in seen:raise DomainError('Unknown or duplicate model card',422,'model_output')
            seen.add(card['key']);text(card['prompt'],'prompt',2000,True);text(card['negative_prompt'],'negative prompt',1000)
            if card['canon_id'] is not None and (not isinstance(card['canon_id'],str) or card['canon_id'] not in canon_ids):raise DomainError('Model selected an unknown canon entry',422,'model_output')
    return value


def apply_values(item,proposal):
    result=proposal['result']
    if proposal['kind']=='analyze':
        item.update(result);ref=proposal['sample_ref']
        if ref not in item['sample_refs']:
            if len(item['sample_refs'])>=12:raise DomainError('Style reference limit reached')
            item['sample_refs'].append(ref)
    else:
        for value in result['cards']:
            card=next(card for card in item['cards'] if card['key']==value['key'])
            card.update(prompt=value['prompt'],negative_prompt=value['negative_prompt'])
            card['canon_ref']={'universe_id':proposal['universe_ref']['id'],'revision':proposal['universe_ref']['revision'],'canon_id':value['canon_id']} if value['canon_id'] is not None else None
    return item


class DeckAssist:
    def __init__(self,decks,universes=None,model=None):
        self.decks=decks;self.universes=universes or UniverseStore(decks.home);self.model=model or DeckModel(decks)
        with decks._db() as db:db.execute('CREATE TABLE IF NOT EXISTS deck_assists(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,fingerprint TEXT,payload TEXT)')

    def _public(self,item):return {key:value for key,value in item.items() if key not in ('owner_pid','owner_identity','prompt')}

    def providers(self):return self.model.providers()

    def prepare(self,deck_id,kind,data):
        expected={'revision','request_id','provider_name','sample_ref'} if kind=='analyze' else {'revision','request_id','provider_name','card_keys','universe_ref'}
        if kind not in ('analyze','prompts') or not isinstance(data,dict) or set(data)!=expected:raise DomainError('Invalid deck assistance fields')
        text(data['request_id'],'request ID',100,True);text(data['provider_name'],'provider name',200,True)
        item=self.decks.get(deck_id);self.decks._guard(item,data)
        universe=None;sample=None;cards=[]
        if kind=='analyze':
            sample=data['sample_ref'];self.decks.image(sample)
            instruction='Analyze the attached card artwork. Return JSON {"style_notes":string,"layout_prompt":string,"negative_prompt":string}. Describe visible style, colors, composition and lettering; avoid guessing provenance.'
        else:
            keys=data['card_keys']
            if not isinstance(keys,list) or not 1<=len(keys)<=12 or any(not isinstance(key,str) for key in keys) or len(set(keys))!=len(keys):raise DomainError('Choose one to twelve unique cards')
            cards=[self.decks._card(item,key) for key in keys]
            ref=data['universe_ref']
            if ref is not None:
                if not isinstance(ref,dict) or set(ref)!={'id','revision'}:raise DomainError('Pinned universe reference required')
                integer(ref['revision'],'universe revision',1,1000000);universe=self.universes.export(ref['id'],ref['revision'])
            instruction='Write one visual art prompt per selected card, retaining recognizable rank or tarot archetype. Return JSON {"cards":[{"key":string,"prompt":string,"negative_prompt":string,"canon_id":string|null}]}. Use only supplied canon IDs for casting characters, places or objects; use null if none fits. Never invent canon IDs.'
        source={'deck':{key:item[key] for key in ('name','kind','style_notes','layout_prompt','orientation','negative_prompt')},'cards':cards,'universe':universe}
        prompt=instruction+'\nREFERENCE DATA:\n'+json.dumps(source,ensure_ascii=False)
        if len(prompt)>60000:raise DomainError('Selected universe exceeds model input bound; reduce canonical context')
        return {'id':str(uuid4()),'deck_id':deck_id,'kind':kind,'status':'running','snapshot_revision':item['revision'],'sample_ref':sample,'universe_ref':data.get('universe_ref'),'universe_snapshot':universe,'card_keys':[card['key'] for card in cards],'provider':data['provider_name'],'model':'','result':None,'error':None,'applied_revision':None,'prompt':prompt,'owner_pid':os.getpid(),'owner_identity':identity(os.getpid())}

    def get(self,deck_id,proposal_id):
        with self.decks._db() as db:
            row=db.execute('SELECT payload FROM deck_assists WHERE id=?',(proposal_id,)).fetchone()
            if not row:raise DomainError('Proposal not found',404,'not_found')
            proposal=json.loads(row[0])
            if proposal['deck_id']!=deck_id:raise DomainError('Proposal not found',404,'not_found')
            if proposal['status']=='running' and identity(proposal['owner_pid'])!=proposal['owner_identity']:
                proposal.update(status='interrupted',error='Process exited before recording a model result')
                db.execute('UPDATE deck_assists SET payload=? WHERE id=?',(json.dumps(proposal),proposal_id))
            return self._public(proposal)

    def list(self,deck_id):
        self.decks.get(deck_id)
        with self.decks._db() as db:ids=[row[0] for row in db.execute("SELECT id FROM deck_assists WHERE json_extract(payload,'$.deck_id')=? ORDER BY rowid DESC LIMIT 100",(deck_id,))]
        results=[]
        for proposal_id in ids:
            try:results.append(self.get(deck_id,proposal_id))
            except DomainError:continue
        return results

    async def propose(self,deck_id,kind,data):
        fingerprint=digest([deck_id,kind,data])
        if not isinstance(data,dict):raise DomainError('Expected assistance request')
        with self.decks._db() as db:
            prior=db.execute('SELECT id,fingerprint FROM deck_assists WHERE request_id=?',(data.get('request_id'),)).fetchone()
        if prior:
            if prior[1]!=fingerprint:raise DomainError('Assistance request ID conflict',409,'request_conflict')
            return self.get(deck_id,prior[0])
        proposal=self.prepare(deck_id,kind,data)
        with self.decks._db() as db:
            try:db.execute('INSERT INTO deck_assists VALUES (?,?,?,?)',(proposal['id'],data['request_id'],fingerprint,json.dumps(proposal)))
            except Exception as exc:raise DomainError('Assistance request already exists; retry receipt read',409,'request_conflict') from exc
        try:
            response=await asyncio.wait_for(self.model(proposal['provider'],proposal['prompt'],proposal['sample_ref']),timeout=130)
            canon_ids={row['id'] for row in (proposal['universe_snapshot'] or {}).get('canon',[])}
            result=parse_result(kind,response['content'],proposal['card_keys'],canon_ids)
            artifact=self.decks.artifacts.create(name='Card design proposal',content=json.dumps({'result':result,'provider':response['provider'],'model':response['model'],'universe_ref':proposal['universe_ref'],'sample_ref':proposal['sample_ref']}),kind='json',source='chat')
            proposal.update(status='succeeded',result=result,provider=response['provider'],model=response['model'],artifact_ref={'slug':artifact.slug,'version':artifact.version})
        except asyncio.CancelledError:
            proposal.update(status='interrupted',error='Request cancelled before model completion');raise
        except Exception as exc:
            proposal.update(status='failed',error=str(exc) if isinstance(exc,DomainError) else 'Model execution failed; inspect provider diagnostics')
        finally:
            with self.decks._db() as db:db.execute('UPDATE deck_assists SET payload=? WHERE id=?',(json.dumps(proposal),proposal['id']))
        return self._public(proposal)

    def apply(self,deck_id,proposal_id,data):
        if not isinstance(data,dict) or set(data)!={'revision'}:raise DomainError('Apply requires deck revision')
        with self.decks._db() as db:
            row=db.execute('SELECT payload FROM deck_assists WHERE id=?',(proposal_id,)).fetchone()
            if not row:raise DomainError('Proposal not found',404)
            proposal=json.loads(row[0]);item=self.decks._get(db,deck_id)
            if proposal['deck_id']!=deck_id:raise DomainError('Proposal not found',404)
            if proposal['applied_revision'] is not None:return {'item':item,'applied_revision':proposal['applied_revision'],'replayed':True}
            self.decks._guard(item,data)
            if proposal['snapshot_revision']!=item['revision']:raise DomainError('Deck changed after proposal; generate a new proposal',409,'revision_conflict')
            if proposal['status']!='succeeded':raise DomainError('Only successful proposals can be applied',409,'proposal_incomplete')
            apply_values(item,proposal);item['revision']+=1;self.decks._save(db,item)
            proposal['applied_revision']=item['revision'];db.execute('UPDATE deck_assists SET payload=? WHERE id=?',(json.dumps(proposal),proposal_id))
            return {'item':item,'applied_revision':item['revision'],'replayed':False}
