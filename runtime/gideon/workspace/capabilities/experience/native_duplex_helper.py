"""Authenticated paired-device executor for fixed AVFoundation duplex operations."""
import asyncio
import base64
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from aiohttp import web

from gideon.core.cancellation import terminate_and_reap
from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.security.sandbox import PROFILE_TOOL, build_child_env, create_subprocess_limited

MAX_AUDIO = 2 * 1024 * 1024


class DuplexExecutorError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class NativeDuplexExecutor:
    def __init__(self, *, root=None, platform=None, xcrun=None):
        self.platform = platform or sys.platform
        self.xcrun = xcrun or shutil.which('xcrun')
        self.source = Path(__file__).parent/'assets/native_duplex.swift'
        self.root = Path(root or config_dir()).resolve()/'capabilities/experience/native-duplex-executor'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root/'operations.sqlite3'
        if self.db.is_symlink() or (self.root/'operations.sqlite3-journal').is_symlink(): raise ValueError('Duplex executor storage symlink refused')
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,kind TEXT,input_hash TEXT,status TEXT,result TEXT)')
            db.execute("UPDATE operations SET status='uncertain',result='Device operation interrupted; actual microphone or speaker state is unknown' WHERE status='running'")
        self.db.chmod(0o600)
        self.lock = asyncio.Lock()

    def readiness(self):
        errors=[]
        if self.platform!='darwin': errors.append('Paired duplex execution requires macOS')
        if not self.xcrun: errors.append('Apple xcrun is unavailable')
        if not self.source.is_file(): errors.append('Bundled AVFoundation helper source is missing')
        return {'available':not errors,'errors':errors,'capture':'16 kHz mono PCM','playback':'AVFoundation','permission':'microphone requested by AVFoundation','device_qualification':'unverified'}

    @staticmethod
    def fields(body, kind):
        expected={'session_id','operation_id','capture_seconds'} if kind=='capture' else {'session_id','operation_id','mime','audio','sha256'}
        if not isinstance(body,dict) or set(body)!=expected: raise DuplexExecutorError('Exact paired duplex fields required',400)
        for name in ('session_id','operation_id'):
            if not isinstance(body[name],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',body[name]): raise DuplexExecutorError('Invalid paired duplex identity',400)
        if kind=='capture':
            if type(body['capture_seconds']) is not int or not 1<=body['capture_seconds']<=30: raise DuplexExecutorError('Capture duration must be 1 to 30 seconds',400)
            return None
        if body['mime']!='audio/wav' or not isinstance(body['audio'],str) or len(body['audio'])>2796204 or not isinstance(body['sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',body['sha256']): raise DuplexExecutorError('Invalid paired playback audio',400)
        try: audio=base64.b64decode(body['audio'],validate=True)
        except ValueError: raise DuplexExecutorError('Invalid paired playback encoding',400) from None
        if not 1<=len(audio)<=MAX_AUDIO or hashlib.sha256(audio).hexdigest()!=body['sha256']: raise DuplexExecutorError('Invalid paired playback digest',400)
        return audio

    def reserve(self, body, kind):
        reduced={key:value for key,value in body.items() if key!='audio'}
        digest=hashlib.sha256(json.dumps(reduced,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with sqlite3.connect(self.db) as db:
            row=db.execute('SELECT kind,input_hash,status,result FROM operations WHERE id=?',(body['operation_id'],)).fetchone()
            if row:
                if row[0]!=kind or row[1]!=digest: raise DuplexExecutorError('Duplex operation identifier conflicts',409)
                if kind=='play' and row[2]=='completed': return json.loads(row[3])
                raise DuplexExecutorError('Duplex operation cannot be replayed safely',409)
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(body['operation_id'],kind,digest,'running',''))
        return None

    def finish(self, operation_id, status, result):
        with sqlite3.connect(self.db) as db: db.execute('UPDATE operations SET status=?,result=? WHERE id=?',(status,json.dumps(result) if isinstance(result,dict) else str(result)[:1000],operation_id))

    async def run(self, operation, path, seconds=None):
        report=self.readiness()
        if not report['available']: raise DuplexExecutorError('; '.join(report['errors']))
        argv=[self.xcrun,'swift',str(self.source),operation,str(path)]
        if seconds is not None: argv.append(str(seconds))
        process=await create_subprocess_limited(*argv,profile=PROFILE_TOOL,cwd=str(path.parent),env=build_child_env(site='native-duplex'),stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,limit=4096)
        try: stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=(seconds or 165)+15)
        except asyncio.TimeoutError:
            await terminate_and_reap(process);raise DuplexExecutorError('Native duplex helper timed out; device state is unknown')
        if process.returncode or stdout.decode(errors='replace').strip()!='ok': raise DuplexExecutorError((stderr.decode(errors='replace') or 'Native duplex helper failed')[:1000])

    async def capture(self, body):
        self.fields(body,'capture')
        async with self.lock:
            replay=self.reserve(body,'capture')
            if replay is not None:return replay
            try:
                with tempfile.TemporaryDirectory(prefix='gideon-native-duplex-') as directory:
                    path=Path(directory)/'capture.wav';await self.run('capture',path,body['capture_seconds'])
                    if path.is_symlink() or not path.is_file(): raise DuplexExecutorError('Native capture produced no audio')
                    audio=path.read_bytes()
                if not 44<=len(audio)<=MAX_AUDIO or audio[:4]!=b'RIFF' or audio[8:12]!=b'WAVE': raise DuplexExecutorError('Native capture produced invalid PCM')
                result={'audio':base64.b64encode(audio).decode(),'mime':'audio/wav','sha256':hashlib.sha256(audio).hexdigest()}
                self.finish(body['operation_id'],'completed_no_replay',{'sha256':result['sha256']});return result
            except BaseException as error:
                self.finish(body['operation_id'],'uncertain' if not isinstance(error,DuplexExecutorError) else 'failed',str(error));raise

    async def play(self, body):
        audio=self.fields(body,'play')
        async with self.lock:
            replay=self.reserve(body,'play')
            if replay is not None:return replay
            try:
                with tempfile.TemporaryDirectory(prefix='gideon-native-duplex-') as directory:
                    path=Path(directory)/'reply.wav';path.write_bytes(audio);path.chmod(0o600);await self.run('play',path)
                result={'status':'played','sha256':body['sha256']};self.finish(body['operation_id'],'completed',result);return result
            except BaseException as error:
                self.finish(body['operation_id'],'uncertain' if not isinstance(error,DuplexExecutorError) else 'failed',str(error));raise


def register_native_duplex_executor(app, executor=None):
    service=executor or NativeDuplexExecutor()
    async def handle(request):
        try:
            body=await read_json_body(request)
            result=await (service.capture(body) if request.match_info['operation']=='capture' else service.play(body))
            return web.json_response(result,headers={'Cache-Control':'no-store'})
        except DuplexExecutorError as error:return web.json_response({'error':str(error)},status=error.status,headers={'Cache-Control':'no-store'})
        except (ValueError,TypeError,KeyError):return web.json_response({'error':'Invalid native duplex request'},status=400)
    app.router.add_post('/v1/voice/duplex/{operation:capture|play}',handle)
    return service

