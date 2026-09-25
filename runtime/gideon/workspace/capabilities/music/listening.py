"""Canonical privacy-export ingestion and immutable listening evidence."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from .store import DomainError,integer,text


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def instant(value,legacy=False):
    try:
        result=datetime.strptime(value,'%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc) if legacy else datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError('Timestamp needs UTC offset')
        return result.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,AttributeError) as exc:
        raise DomainError('Invalid listening timestamp') from exc


def normalize_history(records,format,account):
    if not isinstance(records,list) or len(records)>10000:raise DomainError('History must contain at most10000 records')
    result=[];skipped=0
    for row in records:
        if not isinstance(row,dict):raise DomainError('History record must be an object')
        if format=='spotify_extended':
            title=row.get('master_metadata_track_name');artist=row.get('master_metadata_album_artist_name');album=row.get('master_metadata_album_album_name') or '';uri=row.get('spotify_track_uri') or ''
            if not title or not artist:skipped+=1;continue
            ended=instant(row.get('ts'));played=integer(row.get('ms_played'),'ms_played',0,86400000)
        else:
            title=row.get('trackName');artist=row.get('artistName');album='';uri=''
            if not title or not artist:skipped+=1;continue
            ended=instant(row.get('endTime'),True);played=integer(row.get('msPlayed'),'msPlayed',0,86400000)
        for name,value in (('track',title),('artist',artist),('album',album),('uri',uri)):text(value,name,1000,name in ('track','artist'))
        event={'account_label':account,'ended_at':ended,'title':title,'artist':artist,'album':album,'uri':uri,'ms_played':played,'played_at':None,'timestamp_semantics':'ended_at'}
        event['id']=digest([account,ended,title,artist,played]);result.append(event)
    return result,skipped


def normalize_playlists(document,account):
    if not isinstance(document,dict) or not isinstance(document.get('playlists'),list) or len(document['playlists'])>1000:
        raise DomainError('Expected Spotify playlists privacy export')
    result=[]
    for playlist in document['playlists']:
        if not isinstance(playlist,dict):raise DomainError('Invalid playlist')
        name=text(playlist.get('name'),'playlist name',500,True);items=playlist.get('items');tracks=[];skipped=0
        if not isinstance(items,list) or len(items)>10000:raise DomainError('Playlist item limit exceeded')
        for row in items:
            if not isinstance(row,dict):raise DomainError('Invalid playlist item')
            track=row.get('track')
            if track is None:skipped+=1;continue
            if not isinstance(track,dict):raise DomainError('Invalid playlist track')
            tracks.append({'title':text(track.get('trackName'),'track name',1000,True),'artist':text(track.get('artistName'),'artist',1000,True),
                           'album':text(track.get('albumName') or '','album',1000),'uri':text(track.get('trackUri') or '','URI',1000)})
        identity=digest([account,playlist.get('uri') or name])
        snapshot={'playlist_id':identity,'account_label':account,'name':name,'modified_at':text(playlist.get('lastModifiedDate') or '','modified date',100),'tracks':tracks,'skipped':skipped}
        snapshot['id']=digest(snapshot);result.append(snapshot)
    return result


def _normalize_api(document,account):
    if not isinstance(document,dict) or not isinstance(document.get('recent'),list) or not isinstance(document.get('playlists'),list):raise DomainError('Invalid Spotify API snapshot')
    events=[];playlists=[];skipped=0
    for row in document['recent']:
        track=row.get('track') if isinstance(row,dict) else None
        if not isinstance(track,dict) or track.get('type','track')!='track':skipped+=1;continue
        played=instant(row.get('played_at'));artists=track.get('artists')
        if not isinstance(artists,list) or not artists:raise DomainError('API track artist missing')
        title=text(track.get('name'),'title',1000,True);artist=', '.join(text(value.get('name'),'artist',500,True) for value in artists)
        event={'id':digest([account,'played_at',played,track.get('uri'),title,artist]),'account_label':account,'played_at':played,'ended_at':None,'timestamp_semantics':'played_at',
               'title':title,'artist':artist,'album':text((track.get('album') or {}).get('name') or '','album',1000),'uri':text(track.get('uri') or '','URI',1000),'ms_played':None}
        events.append(event)
    for playlist in document['playlists']:
        items=[]
        for row in playlist['items']:
            track=row.get('item',row.get('track'))
            if not track or track.get('type','track')!='track':skipped+=1;continue
            items.append({'track':{'trackName':track['name'],'artistName':', '.join(value['name'] for value in track['artists']),'albumName':(track.get('album') or {}).get('name',''),'trackUri':track.get('uri','')}})
        playlists.append({'name':playlist['name'],'uri':'spotify:playlist:'+playlist['id'],'lastModifiedDate':playlist.get('snapshot_id',''),'items':items})
    return events,normalize_playlists({'playlists':playlists},account),skipped


def normalize_api(document,account):
    try:return _normalize_api(document,account)
    except DomainError:raise
    except (AttributeError,KeyError,TypeError,ValueError) as exc:raise DomainError('Malformed Spotify API snapshot') from exc


class ListeningStore:
    def __init__(self,home,artifacts):
        self.home,self.artifacts=Path(home),artifacts
        self.root=self.home/'capabilities'/'music';self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'listening.sqlite3'
        with self._db() as db:
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,playlist_id TEXT,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def import_data(self,data):
        if not isinstance(data,dict) or set(data)!={'request_id','account_label','format','artifact_ref'}:raise DomainError('Import requires request_id,account_label,format,artifact_ref')
        text(data['request_id'],'request_id',200,True);text(data['account_label'],'account label',200,True)
        if data['format'] not in ('spotify_extended','spotify_legacy','spotify_playlists','spotify_api'):raise DomainError('Unknown listening export format')
        ref=data['artifact_ref']
        if not isinstance(ref,dict) or set(ref)!={'slug','version'}:raise DomainError('Pinned JSON artifact required')
        text(ref['slug'],'slug',200,True);integer(ref['version'],'version',1,1000000)
        fingerprint=digest(data)
        with self._db() as db:
            prior=db.execute('SELECT fingerprint,payload FROM imports WHERE id=?',(data['request_id'],)).fetchone()
            if prior:
                if prior[0]!=fingerprint:raise DomainError('Import request ID conflict',409,'request_conflict')
                return {**json.loads(prior[1]),'replayed':True}
            artifact=self.artifacts.get(ref['slug'],version=ref['version'])
            if not artifact or artifact.kind!='json':raise DomainError('JSON import artifact unavailable',404,'artifact_not_found')
            try:document=json.loads(artifact.content)
            except (ValueError,TypeError) as exc:raise DomainError('Import artifact is not valid JSON') from exc
            events=[];snapshots=[];skipped=0
            if data['format']=='spotify_api':events,snapshots,skipped=normalize_api(document,data['account_label'])
            elif data['format']=='spotify_playlists':snapshots=normalize_playlists(document,data['account_label']);skipped=sum(row['skipped'] for row in snapshots)
            else:events,skipped=normalize_history(document,data['format'],data['account_label'])
            added=duplicates=0
            for event in events:
                event['artifact_ref']=ref
                inserted=db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',(event['id'],json.dumps(event))).rowcount
                added+=inserted;duplicates+=1-inserted
            for snapshot in snapshots:
                snapshot['artifact_ref']=ref
                inserted=db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)',(snapshot['id'],snapshot['playlist_id'],json.dumps(snapshot))).rowcount
                added+=inserted;duplicates+=1-inserted
            result={'id':data['request_id'],'format':data['format'],'account_label':data['account_label'],'artifact_ref':ref,'added':added,'duplicates':duplicates,'skipped':skipped,'playlist_snapshots':[row['id'] for row in snapshots],'replayed':False}
            db.execute('INSERT INTO imports VALUES (?,?,?)',(data['request_id'],fingerprint,json.dumps(result)))
            return result

    def history(self,query=None):
        query=query or {}
        if not isinstance(query,dict) or set(query)-{'q','account_label','offset','limit'}:raise DomainError('Unknown listening query fields')
        q=text(query.get('q',''),'query',200);account=text(query.get('account_label',''),'account label',200)
        offset=integer(query.get('offset',0),'offset',0,1000000);limit=integer(query.get('limit',50),'limit',1,100)
        where="WHERE (?='' OR json_extract(payload,'$.account_label')=?) AND instr(lower(json_extract(payload,'$.title')||' '||json_extract(payload,'$.artist')||' '||json_extract(payload,'$.album')),lower(?))>0"
        params=(account,account,q)
        with self._db() as db:
            total=db.execute('SELECT count(*) FROM events '+where,params).fetchone()[0]
            rows=db.execute("SELECT payload FROM events "+where+" ORDER BY coalesce(json_extract(payload,'$.ended_at'),json_extract(payload,'$.played_at')) DESC,id LIMIT ? OFFSET ?",(*params,limit,offset))
            return {'items':[json.loads(row[0]) for row in rows],'total':total}

    def playlists(self):
        with self._db() as db:return [json.loads(row[0]) for row in db.execute('SELECT payload FROM snapshots WHERE rowid IN (SELECT max(rowid) FROM snapshots GROUP BY playlist_id) ORDER BY rowid DESC LIMIT 100')]

    def playlist(self,item_id):
        with self._db() as db:
            rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM snapshots WHERE playlist_id=? ORDER BY rowid DESC',(text(item_id,'playlist ID',100,True),))]
            if not rows:raise DomainError('Playlist not found',404,'not_found')
            return {'current':rows[0],'snapshots':rows}

    def imports(self):
        with self._db() as db:return [json.loads(row[0]) for row in db.execute('SELECT payload FROM imports ORDER BY rowid DESC LIMIT 100')]

    def stats(self):
        with self._db() as db:
            count,played,unknown=db.execute("SELECT count(*),coalesce(sum(json_extract(payload,'$.ms_played')),0),sum(json_extract(payload,'$.ms_played') IS NULL) FROM events").fetchone()
            artists=db.execute("SELECT json_extract(payload,'$.artist'),count(*),sum(json_extract(payload,'$.ms_played')) FROM events GROUP BY json_extract(payload,'$.artist') ORDER BY 3 DESC,1 LIMIT 20")
            return {'event_count':count,'ms_played':played,'unknown_duration_count':unknown or 0,'top_artists':[{'artist':row[0],'event_count':row[1],'ms_played':row[2]} for row in artists]}
