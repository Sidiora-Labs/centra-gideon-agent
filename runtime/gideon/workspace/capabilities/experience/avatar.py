"""Published avatar variants bind existing GLB artifacts and actual clip coverage."""
import hashlib
import json
import struct
from uuid import uuid4
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .graph import identifier,revision,text
from .assets.build_robot import build_robot
from .store import Conflict,NotFound

STATES=('idle','working','needs_input','waiting_approval','error','speaking')


def avatar_info(data):
    from gideon.workspace.capabilities.music.image3d import validate_glb
    try:
        info=validate_glb(data)
    except (TypeError,AttributeError,KeyError,struct.error) as exc:
        raise ValueError('Invalid avatar model structure') from exc
    offset,document=12,None
    while offset<len(data):
        length,kind=struct.unpack_from('<II',data,offset)
        offset+=8
        if kind==0x4e4f534a:
            document=json.loads(data[offset:offset+length])
        offset+=length
    nodes=document.get('nodes',[])
    accessors=document.get('accessors',[])
    animations=document.get('animations',[])
    if not all(isinstance(value,list) for value in (nodes,accessors,animations)):
        raise ValueError('Avatar nodes, accessors and animations must be lists')
    names=[]
    for animation in animations:
        if not isinstance(animation,dict):
            raise ValueError('Invalid avatar animation')
        name=text(animation.get('name'),80)
        if name in names or not isinstance(animation.get('channels'),list) or not isinstance(animation.get('samplers'),list) or not animation['channels'] or not animation['samplers']:
            raise ValueError('Avatar animation names must be unique and contain channels')
        names.append(name)
        for channel in animation['channels']:
            if not isinstance(channel,dict) or not isinstance(channel.get('target'),dict):
                raise ValueError('Invalid avatar animation channel')
            index=channel.get('sampler')
            node=channel.get('target',{}).get('node')
            path=channel.get('target',{}).get('path')
            if type(index) is not int or not 0<=index<len(animation['samplers']) or type(node) is not int or not 0<=node<len(nodes) or path not in ('translation','rotation','scale','weights'):
                raise ValueError('Avatar animation targets are invalid')
            sampler=animation['samplers'][index]
            if not isinstance(sampler,dict):
                raise ValueError('Invalid avatar animation sampler')
            if any(type(sampler.get(key)) is not int or not 0<=sampler[key]<len(accessors) for key in ('input','output')):
                raise ValueError('Avatar animation accessor is missing')
    if not names:
        raise ValueError('Avatar requires actual named animation clips')
    return {**info,'animations':names}


class AvatarStore:
    def __init__(self,store):
        self.store=store
        self.artifacts=NativeArtifactProvider(store.path.parent.parent/'artifacts')
        with store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS avatars(id TEXT PRIMARY KEY,body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS avatar_selection(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT)')
            db.execute('INSERT OR IGNORE INTO avatar_selection VALUES(1,?)',(json.dumps({'revision':1,'avatar_id':None,'entity_id':''}),))

    def _asset(self,slug,version):
        artifact=self.artifacts.get(slug,version=version)
        raw=self.artifacts.raw_bytes(slug,version=version)
        if artifact is None or artifact.kind!='model' or raw is None or raw[1]!='model/gltf-binary':
            raise NotFound('Published binary model artifact not found')
        return raw[0]

    def list(self):
        with self.store.connection() as db:
            rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM avatars ORDER BY rowid DESC')]
        for row in rows:
            try:
                data=self._asset(row['artifact_slug'],row['artifact_version'])
                row['availability']='ready' if hashlib.sha256(data).hexdigest()==row['source_hash'] else 'missing'
            except (NotFound,ValueError):
                row['availability']='missing'
        return rows

    def models(self):
        result=[]
        for artifact in self.artifacts.list(kind='model'):
            try:
                info=avatar_info(self._asset(artifact.slug,artifact.version))
                result.append({'slug':artifact.slug,'version':artifact.version,'name':artifact.name,'clips':info['animations']})
            except (ValueError,NotFound):
                continue
        return result

    def publish(self,body):
        if not isinstance(body,dict) or set(body)!={'title','artifact_slug','artifact_version','clips'}:
            raise ValueError('Avatar requires title, artifact_slug, artifact_version and clips')
        title=text(body['title'],200)
        slug=text(body['artifact_slug'],200)
        version=revision(body['artifact_version'])
        data=self._asset(slug,version)
        info=avatar_info(data)
        clips=body['clips']
        if not isinstance(clips,dict) or 'idle' not in clips or set(clips)-set(STATES) or any(value not in info['animations'] for value in clips.values()):
            raise ValueError('Avatar clip mapping requires idle and only actual supported clips')
        record={'id':uuid4().hex,'title':title,'artifact_slug':slug,'artifact_version':version,'source_hash':hashlib.sha256(data).hexdigest(),'available_clips':info['animations'],'clips':clips}
        with self.store.connection() as db:
            for row in db.execute('SELECT body FROM avatars'):
                previous=json.loads(row[0])
                if all(previous[key]==record[key] for key in record if key!='id'):
                    return {**previous,'availability':'ready'}
            db.execute('INSERT INTO avatars VALUES(?,?)',(record['id'],json.dumps(record)))
        return {**record,'availability':'ready'}

    def bundled(self):
        data=build_robot()
        source_hash=hashlib.sha256(data).hexdigest()
        for record in self.list():
            if record['source_hash']==source_hash and record['clips']=={name:name for name in STATES} and record['availability']=='ready':
                return record
        artifact=self.artifacts.create_binary(name='Gideon robot',data=data,mime='model/gltf-binary',kind='model',source='manual',tags=['avatar'],description='Original articulated robot with authored motion clips.')
        return self.publish({'title':'Gideon robot','artifact_slug':artifact.slug,'artifact_version':artifact.version,'clips':{name:name for name in STATES}})

    def selection(self):
        with self.store.connection() as db:
            return json.loads(db.execute('SELECT body FROM avatar_selection WHERE id=1').fetchone()[0])

    def select(self,body):
        if not isinstance(body,dict) or set(body)!={'revision','avatar_id','entity_id'}:
            raise ValueError('Selection requires revision, avatar_id and entity_id')
        expected=revision(body['revision'])
        if not isinstance(body['entity_id'],str) or len(body['entity_id'])>300:
            raise ValueError('Invalid activity entity identity')
        if body['avatar_id'] is not None:
            key=identifier(body['avatar_id'])
            if not any(row['id']==key and row['availability']=='ready' for row in self.list()):
                raise NotFound('Ready avatar not found')
        with self.store.connection() as db:
            old=json.loads(db.execute('SELECT body FROM avatar_selection WHERE id=1').fetchone()[0])
            if old['revision']!=expected:
                raise Conflict('Avatar selection changed; reload before saving')
            saved={**body,'revision':expected+1}
            db.execute('UPDATE avatar_selection SET body=? WHERE id=1',(json.dumps(saved),))
            return saved

    def raw(self,slug,version):
        data=self._asset(slug,revision(version))
        if not any(row['artifact_slug']==slug and row['artifact_version']==version and row['source_hash']==hashlib.sha256(data).hexdigest() for row in self.list()):
            raise NotFound('Registered avatar source is missing or changed')
        return data
