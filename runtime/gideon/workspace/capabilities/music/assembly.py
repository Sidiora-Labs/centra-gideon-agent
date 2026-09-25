"""Versioned authored geometry, deterministic refinement and canonical exports."""
import copy
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from .assembly_geometry import compile_model,source_module
from .store import DomainError,integer,text


def number(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise DomainError('Finite number outside supported geometry range')
    return value


def validate(data):
    if not isinstance(data,dict) or set(data)!={'title','parts','clips'}:
        raise DomainError('Assembly requires title,parts,clips')
    text(data['title'],'title',200,True)
    parts,clips=data['parts'],data['clips']
    if not isinstance(parts,list) or not 1<=len(parts)<=64 or not isinstance(clips,list) or len(clips)>16:
        raise DomainError('Use one to64 parts and up to16 clips')
    ids=set()
    for part in parts:
        if not isinstance(part,dict) or set(part)!={'id','name','shape','size','position','rotation','color','parent_id','joint'}:
            raise DomainError('Invalid part fields')
        if not isinstance(part['id'],str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',part['id']) or part['id'] in ids:
            raise DomainError('Part IDs must be unique identifier names')
        ids.add(part['id']);text(part['name'],'part name',200,True)
        if part['shape'] not in ('box','sphere','cylinder') or not isinstance(part['color'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',part['color']):
            raise DomainError('Invalid primitive shape or color')
        for key,low,high in (('size',.001,1000),('position',-1000,1000),('rotation',-math.pi*2,math.pi*2)):
            if not isinstance(part[key],list) or len(part[key])!=3:
                raise DomainError('Geometry vector must have three values')
            for value in part[key]:number(value,low,high)
        joint=part['joint']
        if joint is not None:
            if not isinstance(joint,dict) or set(joint)!={'axis','min_degrees','max_degrees'} or joint['axis'] not in ('x','y','z'):
                raise DomainError('Invalid rigid joint declaration')
            number(joint['min_degrees'],-360,360);number(joint['max_degrees'],-360,360)
            if joint['min_degrees']>=joint['max_degrees']:raise DomainError('Joint range must increase')
    lookup={part['id']:part for part in parts}
    for part in parts:
        parent=part['parent_id'];seen={part['id']}
        while parent is not None:
            if not isinstance(parent,str) or parent not in ids or parent in seen:raise DomainError('Unknown parent or cyclic hierarchy')
            seen.add(parent);parent=lookup[parent]['parent_id']
    names=set()
    for clip in clips:
        if not isinstance(clip,dict) or set(clip)!={'name','part_id','axis','from_degrees','to_degrees','duration_seconds'}:
            raise DomainError('Invalid clip fields')
        name=text(clip['name'],'clip name',100,True)
        if name in names or clip['part_id'] not in lookup:raise DomainError('Duplicate clip name or unknown target')
        names.add(name);joint=lookup[clip['part_id']]['joint']
        if not joint or joint['axis']!=clip['axis']:raise DomainError('Clip axis must match a declared joint')
        number(clip['from_degrees'],-360,360);number(clip['to_degrees'],-360,360);number(clip['duration_seconds'],.1,60)
    return copy.deepcopy(data)


class AssemblyStore:
    def __init__(self,home,artifacts):
        self.home,self.artifacts=Path(home),artifacts
        self.root=self.home/'capabilities'/'music';self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'assemblies.sqlite3'
        with self._db() as db:
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE IF NOT EXISTS assemblies(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS versions(id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))')
            db.execute('CREATE TABLE IF NOT EXISTS exports(id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def _get(self,db,item_id):
        row=db.execute('SELECT payload FROM assemblies WHERE id=?',(text(item_id,'id',100,True),)).fetchone()
        if not row:raise DomainError('Assembly not found',404,'not_found')
        item=json.loads(row[0]);item['exports']=[json.loads(row[0]) for row in db.execute('SELECT payload FROM exports WHERE id=? ORDER BY revision',(item_id,))]
        return item

    def _save(self,db,item):
        payload=json.dumps(item);db.execute('INSERT OR REPLACE INTO assemblies VALUES (?,?)',(item['id'],payload))
        db.execute('INSERT INTO versions VALUES (?,?,?)',(item['id'],item['revision'],payload))
        return item

    def create(self,data):
        spec=validate(data);_,diagnostics=compile_model(spec)
        with self._db() as db:return self._save(db,{**spec,'id':str(uuid4()),'revision':1,'diagnostics':diagnostics,'exports':[]})

    def get(self,item_id):
        with self._db() as db:return self._get(db,item_id)

    def list(self,offset=0,limit=50):
        integer(offset,'offset',0,1000000);integer(limit,'limit',1,100)
        with self._db() as db:return [self._get(db,row[0]) for row in db.execute('SELECT id FROM assemblies ORDER BY rowid LIMIT ? OFFSET ?',(limit,offset)).fetchall()]

    def history(self,item_id):
        with self._db() as db:
            self._get(db,item_id)
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM versions WHERE id=? ORDER BY revision',(item_id,))]

    def _revision(self,item,value):
        integer(value,'revision',1,1000000)
        if item['revision']!=value:raise DomainError('Assembly changed; reload before editing',409,'revision_conflict')

    def update(self,item_id,data):
        if not isinstance(data,dict) or 'revision' not in data:raise DomainError('Saved revision required')
        spec=validate({key:value for key,value in data.items() if key!='revision'});_,diagnostics=compile_model(spec)
        with self._db() as db:
            item=self._get(db,item_id);self._revision(item,data['revision'])
            return self._save(db,{**item,**spec,'diagnostics':diagnostics,'revision':item['revision']+1})

    def refine(self,item_id,data):
        if not isinstance(data,dict) or set(data)!={'revision','operations'} or not isinstance(data['operations'],list) or not data['operations'] or any(value not in ('ground','clamp_clips') for value in data['operations']):
            raise DomainError('Choose explicit ground or clamp_clips refinements')
        with self._db() as db:
            item=self._get(db,item_id);self._revision(item,data['revision']);spec={key:copy.deepcopy(item[key]) for key in ('title','parts','clips')}
            if 'ground'in data['operations']:
                ground=item['diagnostics']['bounds']['min'][1]
                for part in spec['parts']:
                    if part['parent_id'] is None:part['position'][1]-=ground
            if 'clamp_clips'in data['operations']:
                lookup={part['id']:part for part in spec['parts']}
                for clip in spec['clips']:
                    joint=lookup[clip['part_id']]['joint']
                    for key in ('from_degrees','to_degrees'):clip[key]=max(joint['min_degrees'],min(joint['max_degrees'],clip[key]))
            spec=validate(spec);_,diagnostics=compile_model(spec)
            return self._save(db,{**item,**spec,'diagnostics':diagnostics,'revision':item['revision']+1})

    def export(self,item_id,data):
        if not isinstance(data,dict) or set(data)!={'revision'}:raise DomainError('Export requires saved revision')
        with self._db() as db:
            item=self._get(db,item_id);self._revision(item,data['revision'])
            prior=next((row for row in item['exports'] if row['revision']==item['revision']),None)
            if prior:
                if not self.artifacts.get(prior['model_ref']['slug'],version=prior['model_ref']['version']) or not self.artifacts.get(prior['source_ref']['slug'],version=prior['source_ref']['version']):raise DomainError('Export artifacts missing',404,'artifact_not_found')
                return prior
            spec={key:item[key] for key in ('title','parts','clips')};raw,_=compile_model(spec)
            model=self.artifacts.create_binary(name=item['title'],data=raw,kind='model',mime='model/gltf-binary',source='manual')
            source=self.artifacts.create(name=item['title']+' source',content=source_module(spec),kind='text',source='manual')
            result={'revision':item['revision'],'model_ref':{'slug':model.slug,'version':model.version},'source_ref':{'slug':source.slug,'version':source.version}}
            db.execute('INSERT INTO exports VALUES (?,?,?)',(item_id,item['revision'],json.dumps(result)))
            return result
