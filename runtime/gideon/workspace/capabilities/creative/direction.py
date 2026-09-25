"""Creative direction projects with pinned sources and resumable production plans."""
import hashlib,json
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from gideon.core.sqlite_compat import sqlite3
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .series import SeriesStore
from .store import CatalogError,identifier,integer,keys,text
from .works import WorkStore

OPERATIONS={'source.verify','treatment.snapshot'}
TERMINAL={'done','skipped'}
def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class DirectionStore:
    def __init__(self,home=None):
        self.home=Path(home) if home is not None else __import__('gideon.core.config.loader',fromlist=['config_dir']).config_dir();self.path=self.home/'capabilities/creative/direction.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.works=WorkStore(self.home);self.series=SeriesStore(self.home);self.artifacts=NativeArtifactProvider(self.home/'artifacts')
        with self.db() as db:db.executescript('CREATE TABLE IF NOT EXISTS direction_projects(id TEXT PRIMARY KEY,record TEXT);CREATE TABLE IF NOT EXISTS direction_requests(id TEXT PRIMARY KEY,digest TEXT,record TEXT);PRAGMA user_version=1;')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def _load(self,db,identity):
        row=db.execute('SELECT record FROM direction_projects WHERE id=?',(identifier(identity),)).fetchone()
        if not row:raise CatalogError('Creative direction project not found',404)
        return json.loads(row[0])
    def _save(self,db,record):db.execute('INSERT OR REPLACE INTO direction_projects VALUES(?,?)',(record['id'],json.dumps(record,sort_keys=True)));return record
    def _pin_work(self,work_id,revision,chapter_id=None,title=None):
        work=self.works.export(identifier(work_id),integer(revision));draft_id=work.get('active_draft_id')
        if not draft_id:raise CatalogError('Selected work revision has no active manuscript draft',409)
        draft=self.works.read_draft(work['id'],draft_id)
        if draft['missing']:raise CatalogError('Selected manuscript artifact is missing',404)
        return {'chapter_id':chapter_id,'title':title or work['title'],'work_id':work['id'],'work_revision':work['revision'],'draft_id':draft_id,'artifact_id':draft['artifact_id'],'artifact_version':draft['artifact_version'],'content_hash':digest(draft['text'])}
    def pin(self,source):
        keys(source,{'kind','id','revision'});kind=source.get('kind');identity=identifier(source.get('id'));revision=integer(source.get('revision'))
        if kind=='work':
            row=self._pin_work(identity,revision);return {'kind':kind,'id':identity,'revision':revision,'title':row['title'],'chapters':[row]}
        if kind!='series':raise CatalogError('Direction sources must be work or series')
        series=self.series.export(identity,revision);pending=[]
        with self.series.connection() as db:
            for volume in series['volumes']:
                for chapter in volume['chapters']:
                    link=db.execute('SELECT record FROM series_chapters WHERE series_id=? AND chapter_id=?',(identity,chapter['id'])).fetchone()
                    if not link:raise CatalogError('Series chapter is not prepared: '+chapter['title'],409)
                    work=self.works._work(db,json.loads(link[0])['work_id']);pending.append((chapter,work['id'],work['revision']))
        chapters=[self._pin_work(work_id,work_revision,chapter['id'],chapter['title']) for chapter,work_id,work_revision in pending]
        if not chapters:raise CatalogError('Series has no production chapters',409)
        return {'kind':kind,'id':identity,'revision':revision,'title':series['title'],'chapters':chapters}
    def _steps(self,values,prior=()):
        if not isinstance(values,list) or not 1<=len(values)<=50:raise CatalogError('Production plan requires 1 to 50 steps')
        old={row['id']:row for row in prior};output=[];seen=set()
        for value in values:
            keys(value,{'id','title','operation','depends_on'});identity=identifier(value.get('id'));operation=value.get('operation');dependencies=value.get('depends_on',[])
            if identity in seen or operation not in OPERATIONS or not isinstance(dependencies,list) or any(identifier(item) not in seen for item in dependencies):raise CatalogError('Plan steps must be unique, supported and depend only on earlier steps')
            normalized={'id':identity,'title':text(value.get('title'),200,True),'operation':operation,'depends_on':list(dict.fromkeys(dependencies))};existing=old.get(identity)
            if existing and existing['status']=='done':
                if any(existing[key]!=normalized[key] for key in normalized):raise CatalogError('Completed plan steps cannot be changed',409)
                normalized=existing
            else:normalized={**normalized,'status':'pending','result':None,'attempts':0}
            seen.add(identity);output.append(normalized)
        missing=[row for row in prior if row['status']=='done' and row['id'] not in seen]
        if missing:raise CatalogError('Completed plan steps cannot be removed',409)
        return output
    def create(self,payload):
        keys(payload,{'request_id','name','treatment','sources','steps'});request=identifier(payload.get('request_id'));name=text(payload.get('name'),200,True);treatment=text(payload.get('treatment'),20000,True);sources=payload.get('sources',[])
        if not isinstance(sources,list) or not 1<=len(sources)<=20:raise CatalogError('Direction project requires 1 to 20 canonical sources')
        encoded=json.dumps(payload,sort_keys=True);fingerprint=digest(encoded)
        with self.db() as db:
            prior=db.execute('SELECT digest,record FROM direction_requests WHERE id=?',(request,)).fetchone()
            if prior:
                if prior[0]!=fingerprint:raise CatalogError('Direction request already used with different values',409)
                return json.loads(prior[1])
        pinned=[self.pin(source) for source in sources];created=now();record={'id':str(uuid4()),'revision':1,'name':name,'treatment':treatment,'sources':pinned,'steps':self._steps(payload.get('steps')),'status':'draft','created_at':created,'updated_at':created}
        with self.db() as db:
            prior=db.execute('SELECT digest,record FROM direction_requests WHERE id=?',(request,)).fetchone()
            if prior:
                if prior[0]!=fingerprint:raise CatalogError('Direction request already used with different values',409)
                return json.loads(prior[1])
            self._save(db,record);db.execute('INSERT INTO direction_requests VALUES(?,?,?)',(request,fingerprint,json.dumps(record,sort_keys=True)))
        return record
    def get(self,identity):
        with self.db() as db:return self._load(db,identity)
    def list(self):
        with self.db() as db:return [json.loads(row[0]) for row in db.execute('SELECT record FROM direction_projects ORDER BY rowid DESC LIMIT 100')]
    def _mutate(self,identity,revision,operation):
        with self.db() as db:
            record=self._load(db,identity)
            if integer(revision)!=record['revision']:raise CatalogError('Direction project changed; reload',409)
            changed=operation(record);changed={**changed,'revision':record['revision']+1,'updated_at':now()};return self._save(db,changed)
    def control(self,identity,payload):
        keys(payload,{'revision','action'});action=payload.get('action')
        def apply(record):
            if action=='pause' and record['status']=='running':return {**record,'status':'paused'}
            if action in {'start','resume'} and record['status'] in {'draft','paused'}:return {**record,'status':'running'}
            raise CatalogError('Project control is invalid for current state',409)
        return self._mutate(identity,payload.get('revision'),apply)
    def replace_plan(self,identity,payload):
        keys(payload,{'revision','steps'})
        def apply(record):
            if record['status'] not in {'draft','paused'}:raise CatalogError('Pause production before editing the plan',409)
            return {**record,'steps':self._steps(payload.get('steps'),record['steps'])}
        return self._mutate(identity,payload.get('revision'),apply)
    def _execute(self,project,step):
        if step['operation']=='source.verify':
            current=[self.pin({'kind':row['kind'],'id':row['id'],'revision':row['revision']}) for row in project['sources']]
            if current!=project['sources']:raise CatalogError('Canonical source changed since direction approval',409)
            return {'verified_sources':[{'kind':row['kind'],'id':row['id'],'revision':row['revision']} for row in current],'verified_at':now()}
        body='# '+project['name']+'\n\n'+project['treatment']+'\n\n## Canonical sources\n'+''.join(f"- {row['kind']} {row['id']} revision {row['revision']}\n" for row in project['sources'])
        slug='creative-direction-'+project['id']+'-'+step['id'];artifact=self.artifacts.get(slug,version=1) or self.artifacts.create(name=project['name']+' treatment',slug=slug,kind='markdown',content=body,description=digest(body),readonly=True)
        saved=self.artifacts.get(slug,version=1)
        if not saved or saved.content!=body or not saved.readonly:raise CatalogError('Treatment artifact persistence could not be confirmed',500)
        return {'artifact_id':slug,'artifact_version':1,'content_hash':digest(body),'path':f'/api/artifacts/{slug}?version=1'}
    def advance(self,identity,payload):
        keys(payload,{'revision'})
        with self.db() as db:
            project=self._load(db,identity)
            if integer(payload.get('revision'))!=project['revision']:raise CatalogError('Direction project changed; reload',409)
            if project['status']!='running':raise CatalogError('Start or resume production before advancing',409)
            ready=next((row for row in project['steps'] if row['status']=='pending' and all(next(item for item in project['steps'] if item['id']==dep)['status'] in TERMINAL for dep in row['depends_on'])),None)
            if not ready:raise CatalogError('Production plan has no ready step',409)
        result=self._execute(project,ready)
        def apply(current):
            step=next((row for row in current['steps'] if row['id']==ready['id']),None)
            if not step or step['status']!='pending':raise CatalogError('Production step changed; reload',409)
            steps=[{**row,'status':'done','result':result,'attempts':row['attempts']+1} if row['id']==ready['id'] else row for row in current['steps']];complete=all(row['status'] in TERMINAL for row in steps)
            return {**current,'steps':steps,'status':'completed' if complete else 'running'}
        return self._mutate(identity,project['revision'],apply)
