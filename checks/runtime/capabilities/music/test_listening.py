import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.listening import ListeningStore,normalize_api,normalize_history,normalize_playlists,instant
from gideon.workspace.capabilities.music.listening_tools import ListeningTools
from gideon.workspace.capabilities.music.spotify import SpotifyBridge
from gideon.workspace.capabilities.music.store import DomainError
from gideon.interfaces.dashboard.handlers.capabilities_music_listening import register


def store_at(home):return ListeningStore(home,NativeArtifactProvider(root=home/'artifacts'))

def extended(**changes):
    return {'ts':'2026-01-02T03:04:05Z','ms_played':12500,'master_metadata_track_name':'Measured song','master_metadata_album_artist_name':'Actual artist','master_metadata_album_album_name':'Album','spotify_track_uri':'spotify:track:abc',**changes}

def legacy(**changes):return {'endTime':'2026-01-02 03:04','msPlayed':12500,'trackName':'Measured song','artistName':'Actual artist',**changes}

def api_track(**changes):return {'name':'API song','artists':[{'name':'API artist'}],'album':{'name':'Album'},'uri':'spotify:track:def','duration_ms':123456,**changes}

def playlist_doc(name='Practice',**changes):
    return {'playlists':[{'name':name,'lastModifiedDate':'2026-01-01','items':[{'track':{'trackName':'One','artistName':'First','albumName':'Album','trackUri':'spotify:track:a'}},{'track':{'trackName':'One','artistName':'First','albumName':'Album','trackUri':'spotify:track:a'}},{'track':None}],**changes}]}

def payload(store,document,format='spotify_extended',request_id='first',account='owner'):
    art=store.artifacts.create(name='Actual export',content=json.dumps(document),kind='json',source='import')
    return {'request_id':request_id,'account_label':account,'format':format,'artifact_ref':{'slug':art.slug,'version':art.version}}


def test_extended_pinned_facts_and_reopen(tmp_path):
    store=store_at(tmp_path)
    data=payload(store,[extended(),extended(ts='2026-01-02T04:04:05+01:00'),extended(master_metadata_track_name=None)])
    result=store.import_data(data)
    assert (result['added'],result['duplicates'],result['skipped'])==(1,1,1)
    assert result['artifact_ref']==data['artifact_ref']
    row=store.history()['items'][0]
    assert row['ended_at']=='2026-01-02T03:04:05+00:00'
    assert row['ms_played']==12500 and row['played_at'] is None
    assert row['timestamp_semantics']=='ended_at'
    assert row['album']=='Album' and row['uri']=='spotify:track:abc'
    assert row['artifact_ref']==data['artifact_ref']
    again=store_at(tmp_path)
    assert again.history()==store.history()
    assert again.import_data(data)=={**result,'replayed':True}
    assert again.stats()['event_count']==1
    assert again.stats()['ms_played']==12500
    assert again.stats()['unknown_duration_count']==0
    with sqlite3.connect(store.path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==1


def test_legacy_utc_zero_listen_and_overlap(tmp_path):
    store=store_at(tmp_path)
    store.import_data(payload(store,[legacy(msPlayed=0)],'spotify_legacy'))
    row=store.history()['items'][0]
    assert row['ended_at']=='2026-01-02T03:04:00+00:00'
    assert row['album']=='' and row['uri']=='' and row['ms_played']==0
    overlap=extended(ts='2026-01-02T03:04:00Z',ms_played=0)
    result=store.import_data(payload(store,[overlap],request_id='overlap'))
    assert result['duplicates']==1 and result['added']==0
    assert store.stats()['unknown_duration_count']==0


def test_api_duration_unknown_and_time_semantics_separate(tmp_path):
    store=store_at(tmp_path)
    document={'recent':[{'played_at':'2026-01-02T03:04:05Z','track':api_track(name='Measured song',artists=[{'name':'Actual artist'}])}],'playlists':[]}
    result=store.import_data(payload(store,document,'spotify_api'))
    assert result['added']==1
    row=store.history()['items'][0]
    assert row['ms_played'] is None and row['ended_at'] is None
    assert row['played_at']=='2026-01-02T03:04:05+00:00'
    assert row['timestamp_semantics']=='played_at'
    store.import_data(payload(store,[extended()],request_id='privacy'))
    assert store.stats()['event_count']==2
    assert store.stats()['unknown_duration_count']==1
    assert store.stats()['ms_played']==12500
    assert len(store.history()['items'])==2


def test_playlist_order_duplicates_snapshots_and_source(tmp_path):
    store=store_at(tmp_path)
    first=payload(store,playlist_doc(),'spotify_playlists')
    receipt=store.import_data(first)
    assert receipt['skipped']==1
    current=store.playlists()[0]
    assert [row['title'] for row in current['tracks']]==['One','One']
    assert current['artifact_ref']==first['artifact_ref']
    second=playlist_doc(lastModifiedDate='2026-01-02')
    second['playlists'][0]['items'].insert(0,{'track':{'trackName':'Two','artistName':'Second'}})
    store.import_data(payload(store,second,'spotify_playlists','second'))
    detail=store.playlist(current['playlist_id'])
    assert len(detail['snapshots'])==2
    assert [row['title'] for row in detail['current']['tracks']]==['Two','One','One']
    assert detail['snapshots'][1]==current
    assert store_at(tmp_path).playlist(current['playlist_id'])==detail
    with pytest.raises(DomainError):store.playlist('missing')


def test_import_identity_conflict_and_account_isolation(tmp_path):
    store=store_at(tmp_path)
    first=payload(store,[extended()])
    store.import_data(first)
    with pytest.raises(DomainError) as error:store.import_data({**first,'account_label':'different'})
    assert error.value.status==409
    other=payload(store,[extended()],request_id='other',account='other')
    assert store.import_data(other)['added']==1
    assert store.history({'account_label':'owner'})['total']==1
    assert store.history({'account_label':'other'})['total']==1
    assert store_at(tmp_path/'separate').history()['total']==0
    assert len(store.imports())==2


def test_search_sort_pagination_stats(tmp_path):
    store=store_at(tmp_path)
    records=[extended(ts=f'2026-01-02T03:{i:02}:00Z',master_metadata_track_name=f'Song {i}',ms_played=i) for i in range(30)]
    store.import_data(payload(store,records))
    page=store.history({'limit':10,'offset':10})
    assert page['total']==30
    assert [row['title'] for row in page['items']]==[f'Song {i}' for i in range(19,9,-1)]
    assert store.history({'q':'ACTUAL'})['total']==30
    assert store.history({'q':'Song 29'})['total']==1
    assert store.history({'q':'nothing'})=={'items':[],'total':0}
    summary=store.stats()
    assert summary['ms_played']==435
    assert summary['top_artists']==[{'artist':'Actual artist','event_count':30,'ms_played':435}]


@pytest.mark.parametrize('query',[{'limit':0},{'limit':101},{'offset':-1},{'limit':True},{'home':'/tmp'},{'q':None}])
def test_invalid_queries(tmp_path,query):
    with pytest.raises(DomainError):store_at(tmp_path).history(query)


@pytest.mark.parametrize('record',[extended(ts='invalid'),extended(ts='2026-01-02T00:00:00'),extended(ms_played=-1),extended(ms_played=True),extended(ms_played=86400001),extended(master_metadata_track_name=123)])
def test_invalid_late_record_rolls_back_all(tmp_path,record):
    store=store_at(tmp_path)
    with pytest.raises(DomainError):store.import_data(payload(store,[extended(),record]))
    assert store.history()['total']==0
    assert store.imports()==[]


@pytest.mark.parametrize('document',[
 {'recent':[{'played_at':'2026-01-02T00:00:00Z','track':api_track(artists=[None])}],'playlists':[]},
 {'recent':[],'playlists':[{}]},
 {'recent':[],'playlists':[{'items':[None]}]},
 {'recent':[],'playlists':[{'items':[{'item':17}]}]},
 {'recent':[{'played_at':'2026-01-02T00:00:00Z','track':api_track(album=7)}],'playlists':[]},
 {'recent':[],'playlists':[{'items':[{'item':api_track(artists=[None])}]}]},
 {'recent':None,'playlists':[]},
])
def test_malformed_api_snapshot_domain_error(tmp_path,document):
    store=store_at(tmp_path)
    with pytest.raises(DomainError):store.import_data(payload(store,document,'spotify_api'))
    assert store.stats()['event_count']==0
    assert store.imports()==[]


def test_api_playlist_new_item_field_and_null_skips(tmp_path):
    store=store_at(tmp_path)
    document={'recent':[{'track':None}],'playlists':[{'id':'abc','name':'API list','snapshot_id':'one','items':[{'item':api_track()},{'item':None},{'item':api_track()}]}]}
    result=store.import_data(payload(store,document,'spotify_api'))
    assert result['skipped']==2
    current=store.playlists()[0]
    assert [row['title'] for row in current['tracks']]==['API song','API song']
    assert current['modified_at']=='one'


def test_concurrent_same_request_exactly_once(tmp_path):
    store=store_at(tmp_path)
    data=payload(store,[extended()])
    stores=[store_at(tmp_path) for _ in range(6)]
    with ThreadPoolExecutor(6) as pool:results=list(pool.map(lambda service:service.import_data(data),stores))
    assert sum(not row['replayed'] for row in results)==1
    assert store.history()['total']==1 and len(store.imports())==1


@pytest.mark.asyncio
async def test_actual_http_and_native_consumers(tmp_path):
    store=store_at(tmp_path);bridge=SpotifyBridge(tmp_path,store)
    app=web.Application();register(app,store,bridge)
    async with TestClient(TestServer(app)) as client:
        prefix='/api/capabilities/music/listening'
        data=payload(store,[extended()])
        response=await client.post(prefix+'/imports',json=data)
        assert response.status==200 and (await response.json())['added']==1
        assert (await(await client.get(prefix+'/history?limit=1')).json())['total']==1
        assert (await(await client.get(prefix+'/stats')).json())['ms_played']==12500
        assert len((await(await client.get(prefix+'/imports')).json())['items'])==1
        malformed=payload(store,{'recent':[],'playlists':[{}]},'spotify_api','malformed')
        assert (await client.post(prefix+'/imports',json=malformed)).status==400
        assert (await client.get(prefix+'/history?limit=not-number')).status==400
        assert (await client.get(prefix+'/playlists/missing')).status==404
        assert (await client.post(prefix+'/spotify/sync',json={'request_id':'sync','playlist_ids':[]})).status==503
    tools=ListeningTools(store,bridge)
    definitions=await tools.list_tools()
    assert len(definitions)==10
    assert (await tools.invoke('music_listening_history',{'data':{}})).success
    assert (await tools.invoke('music_listening_import',{'data':data})).success
    for name in ('stats','imports','playlists','spotify_config','spotify_readiness'):
        result=await tools.invoke('music_listening_'+name,{})
        assert result.success
        assert json.loads(result.output) is not None
    assert not (await tools.invoke('music_listening_stats',{'home':'/tmp'})).success
    assert not (await tools.invoke('music_listening_spotify_sync',{'data':{'request_id':'native','playlist_ids':[]}})).success
    assert not (await tools.invoke('missing',{})).success
