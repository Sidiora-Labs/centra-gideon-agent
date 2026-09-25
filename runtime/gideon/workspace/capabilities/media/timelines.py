import hashlib
import json
import math
import os
import signal
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from gideon.integrations.video_gen.provider import VideoResult
from .cleanup import CleanupService
from .sketches import SketchError, fields, integer
from .videos import media_tools, probe_video


def number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise SketchError('Timeline number is outside supported bounds')
    return value


def render_process(command, log, stopped, attached):
    with log.open('ab') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            attached(process.pid)
            deadline = time.monotonic()+600
            while process.poll() is None:
                if stopped() or time.monotonic() > deadline:
                    raise SketchError('Timeline render interrupted', 409)
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise SketchError('FFmpeg timeline rendering failed', 503)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)


class TimelineStore:
    def __init__(self, path, videos):
        self.path, self.videos = Path(path), videos
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS timelines (id TEXT, revision INTEGER, body TEXT, PRIMARY KEY(id,revision))')
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, body TEXT)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, timeline_id, revision=None):
        if not isinstance(timeline_id, str):
            raise SketchError('Invalid timeline ID')
        with self.db() as db:
            if revision is None:
                row = db.execute('SELECT body FROM timelines WHERE id=? ORDER BY revision DESC LIMIT 1', (timeline_id,)).fetchone()
            else:
                integer(revision, 1, 1000000)
                row = db.execute('SELECT body FROM timelines WHERE id=? AND revision=?', (timeline_id, revision)).fetchone()
        if not row:
            raise SketchError('Timeline revision not found', 404)
        return json.loads(row[0])

    def list(self):
        with self.db() as db:
            rows = db.execute('SELECT body FROM timelines t WHERE revision=(SELECT MAX(revision) FROM timelines WHERE id=t.id) ORDER BY rowid DESC LIMIT 100').fetchall()
        return {'items': [json.loads(row[0]) for row in rows]}

    def history(self, timeline_id):
        self.get(timeline_id)
        with self.db() as db:
            rows = db.execute('SELECT body FROM timelines WHERE id=? ORDER BY revision DESC', (timeline_id,)).fetchall()
        return {'items': [json.loads(row[0]) for row in rows]}

    def source(self, entry, kind, directory, index):
        artifact_id, version = entry['artifact_id'], entry['version']
        if not isinstance(artifact_id, str) or not artifact_id:
            raise SketchError('Invalid timeline artifact reference')
        integer(version, 1, 1000000)
        artifact = self.videos.artifacts.get(artifact_id)
        raw = self.videos.artifacts.raw_bytes(artifact_id, version=version) if artifact and artifact.kind == kind else None
        if not raw:
            raise SketchError('Pinned timeline source is unavailable', 404)
        if len(raw[0]) > 16*1024*1024:
            raise SketchError('Timeline source exceeds 16 MiB')
        path = directory / f'source-{index}'
        if kind == 'image':
            CleanupService(self.videos.images).source(artifact_id, version).save(path, 'PNG')
            return path, None
        path.write_bytes(raw[0])
        if kind == 'video':
            duration = probe_video(path)['duration_seconds']
        else:
            header = raw[0][:16]
            if not (header.startswith((b'RIFF', b'ID3', b'fLaC', b'OggS')) or header[4:8] == b'ftyp' or len(header) > 1 and header[0] == 255 and header[1] & 224 == 224):
                raise SketchError('Unsupported audio container')
            try:
                probe = subprocess.run(['ffprobe', '-protocol_whitelist', 'file,pipe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name:format=duration', '-of', 'json', str(path)], capture_output=True, check=True, timeout=20)
                data = json.loads(probe.stdout)
                if not data['streams']:
                    raise ValueError()
                duration = number(float(data['format']['duration']), .01, 3600)
            except (ValueError, KeyError, subprocess.SubprocessError) as exc:
                raise SketchError('Audio source cannot be decoded') from exc
        return path, duration

    def validate(self, body):
        fields(body, ('title', 'width', 'height', 'fps', 'segments', 'overlays', 'audio', 'request_id', 'revision'), ('title', 'width', 'height', 'fps', 'segments', 'overlays', 'audio', 'request_id'))
        if not isinstance(body['title'], str) or not 1 <= len(body['title'].strip()) <= 120:
            raise SketchError('Timeline title requires 1–120 characters')
        for key in ('width', 'height'):
            integer(body[key], 2, 1920)
            if body[key] % 2:
                raise SketchError('Timeline output dimensions must be even')
        integer(body['fps'], 1, 60)
        for key in ('segments', 'overlays', 'audio'):
            if not isinstance(body[key], list) or not (1 if key == 'segments' else 0) <= len(body[key]) <= 20:
                raise SketchError('Timeline track item count is invalid')
        total = 0
        media_tools()
        with TemporaryDirectory(prefix='gideon-timeline-validation-') as temporary:
            directory = Path(temporary)
            for index, entry in enumerate(body['segments']):
                fields(entry, ('artifact_id', 'version', 'kind', 'start', 'duration'), ('artifact_id', 'version', 'kind', 'start', 'duration'))
                if entry['kind'] not in ('image', 'video'):
                    raise SketchError('Timeline segments require image or video artifacts')
                number(entry['start'], 0, 3600)
                total += number(entry['duration'], .05, 300)
                _, duration = self.source(entry, entry['kind'], directory, index)
                if entry['kind'] == 'image' and entry['start'] != 0 or duration is not None and entry['start']+entry['duration'] > duration+.001:
                    raise SketchError('Segment trim exceeds source duration')
            number(total, .05, 300)
            for index, overlay in enumerate(body['overlays']):
                fields(overlay, ('artifact_id', 'version', 'start', 'duration', 'x', 'y', 'width', 'height'), ('artifact_id', 'version', 'start', 'duration', 'x', 'y', 'width', 'height'))
                number(overlay['start'], 0, total)
                number(overlay['duration'], .05, total-overlay['start'])
                for key in ('x', 'y'):
                    integer(overlay[key], 0, body['width' if key == 'x' else 'height']-1)
                integer(overlay['width'], 1, body['width']-overlay['x'])
                integer(overlay['height'], 1, body['height']-overlay['y'])
                self.source(overlay, 'image', directory, 'overlay'+str(index))
            for index, audio in enumerate(body['audio']):
                fields(audio, ('artifact_id', 'version', 'start', 'trim', 'duration', 'volume', 'fade_in', 'fade_out'), ('artifact_id', 'version', 'start', 'trim', 'duration', 'volume', 'fade_in', 'fade_out'))
                number(audio['start'], 0, total)
                number(audio['duration'], .05, total-audio['start'])
                number(audio['trim'], 0, 3600)
                number(audio['volume'], 0, 2)
                for key in ('fade_in', 'fade_out'):
                    number(audio[key], 0, audio['duration'])
                _, duration = self.source(audio, 'audio', directory, 'audio'+str(index))
                if audio['trim']+audio['duration'] > duration+.001:
                    raise SketchError('Audio trim exceeds source duration')
        return total

    def save(self, body, timeline_id=None):
        total = self.validate(body)
        revision = integer(body.get('revision', 0), 0, 1000000)
        if (timeline_id is None) != (revision == 0):
            raise SketchError('Existing timeline requires its current revision')
        request_id = body['request_id']
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise SketchError('Invalid timeline request ID')
        fingerprint = hashlib.sha256(json.dumps([timeline_id, body], sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            replay = db.execute('SELECT fingerprint,body FROM requests WHERE id=?', (request_id,)).fetchone()
            if replay:
                if replay[0] != fingerprint:
                    raise SketchError('Timeline request ID conflict', 409)
                return json.loads(replay[1])
            if timeline_id and self.get(timeline_id)['revision'] != revision:
                raise SketchError('Timeline revision changed', 409)
            value = {key: body[key] for key in ('title', 'width', 'height', 'fps', 'segments', 'overlays', 'audio')}
            value.update(id=timeline_id or str(uuid4()), revision=revision+1, duration=total, updated_at=datetime.now(timezone.utc).isoformat())
            encoded = json.dumps(value)
            db.execute('INSERT INTO timelines VALUES (?,?,?)', (value['id'], value['revision'], encoded))
            db.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, fingerprint, encoded))
        return value

    def prepare(self, body):
        fields(body, ('timeline_id', 'revision'), ('timeline_id', 'revision'))
        self.get(body['timeline_id'], body['revision'])
        return dict(body)

    def execute(self, request, job_id, stopped, attached, progress=lambda value: None):
        document = self.get(request['timeline_id'], request['revision'])
        with TemporaryDirectory(prefix='gideon-timeline-render-') as temporary:
            directory = Path(temporary)
            log = directory / 'ffmpeg.log'
            outputs = []
            for index, entry in enumerate(document['segments']):
                if stopped():
                    raise SketchError('Timeline render cancelled', 409)
                source, _ = self.source(entry, entry['kind'], directory, index)
                output = directory / f'clip-{index}.mp4'
                command = ['ffmpeg', '-v', 'error', '-y', '-threads', '1']
                command += ['-loop', '1'] if entry['kind'] == 'image' else ['-ss', str(entry['start'])]
                command += ['-protocol_whitelist', 'file,pipe', '-i', str(source), '-t', str(entry['duration']), '-an', '-vf', f"scale={document['width']}:{document['height']}:force_original_aspect_ratio=decrease,pad={document['width']}:{document['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={document['fps']}", '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1', str(output)]
                render_process(command, log, stopped, attached)
                outputs.append(output)
                progress((index+1)/(len(document['segments'])+1))
            listing = directory / 'concat.txt'
            listing.write_text(''.join(f"file '{path.name}'\n" for path in outputs))
            command = ['ffmpeg', '-v', 'error', '-y', '-filter_complex_threads', '1', '-f', 'concat', '-safe', '1', '-i', str(listing)]
            filters, video_label = [], '0:v'
            for index, overlay in enumerate(document['overlays']):
                path, _ = self.source(overlay, 'image', directory, 'overlay'+str(index))
                command += ['-loop', '1', '-protocol_whitelist', 'file,pipe', '-i', str(path)]
                filters += [f"[{index+1}:v]scale={overlay['width']}:{overlay['height']}[o{index}]", f"[{video_label}][o{index}]overlay={overlay['x']}:{overlay['y']}:enable='gte(t,{overlay['start']})*lt(t,{overlay['start']+overlay['duration']})'[v{index}]"]
                video_label = f'v{index}'
            silent = 1+len(document['overlays'])
            command += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            audio_labels = [f'[{silent}:a]']
            for index, audio in enumerate(document['audio']):
                path, _ = self.source(audio, 'audio', directory, 'audio'+str(index))
                command += ['-protocol_whitelist', 'file,pipe', '-i', str(path)]
                delay = round(audio['start']*1000)
                filters.append(f"[{silent+1+index}:a]atrim=start={audio['trim']}:duration={audio['duration']},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={audio['volume']},afade=t=in:d={audio['fade_in']},afade=t=out:st={audio['duration']-audio['fade_out']}:d={audio['fade_out']},adelay={delay}|{delay}[a{index}]")
                audio_labels.append(f'[a{index}]')
            filters.append(''.join(audio_labels)+f'amix=inputs={len(audio_labels)}:normalize=0:duration=first[aout]')
            output = directory / 'timeline.mp4'
            command += ['-filter_complex', ';'.join(filters), '-map', f'[{video_label}]' if filters and video_label != '0:v' else '0:v', '-map', '[aout]', '-t', str(document['duration']), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-threads', '1', '-movflags', '+faststart', str(output)]
            render_process(command, log, stopped, attached)
            prepared = dict(request, prompt=document['title'], selection='', engine='ffmpeg')
            result = self.videos.materialize(VideoResult(local_path=str(output)), prepared, job_id)
            progress(1)
            return result
