"""Ability-specific commission admission through canonical durable job stores."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .store import CatalogError, identifier, integer, keys, text


FORBIDDEN = {'provider', 'model', 'credential', 'credential_name', 'home', 'runtime', 'api_key', 'token'}


def _contains_override(value):
    if isinstance(value, dict):
        return any(str(key).lower() in FORBIDDEN or _contains_override(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_override(item) for item in value)
    return False


def normalize_dispatch(ability, value, sources):
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or _contains_override(value):
        raise CatalogError('Commission dispatch must be provider-neutral')
    if ability in ('image', 'video'):
        keys(value, {'input'})
        if not isinstance(value.get('input'), dict):
            raise CatalogError('Media dispatch input must be an object')
        return {'input': value['input']}
    if ability == 'music':
        allowed = {'track_id', 'track_revision', 'prompt', 'music_length_ms', 'force_instrumental', 'license'}
        keys(value, allowed)
        if set(value) != allowed:
            raise CatalogError('Music dispatch requires a pinned track, prompt, duration, mode and license')
        return {'track_id': identifier(value['track_id']), 'track_revision': integer(value['track_revision']),
                'prompt': text(value['prompt'], 4100, True), 'music_length_ms': value['music_length_ms'],
                'force_instrumental': value['force_instrumental'], 'license': text(value['license'], 500, True)}
    if ability == 'music-video':
        keys(value, {'project_id', 'revision'})
        if set(value) != {'project_id', 'revision'}:
            raise CatalogError('Music video dispatch requires project_id and revision')
        return {'project_id': identifier(value['project_id']), 'revision': integer(value['revision'])}
    keys(value, {'series_id', 'series_revision', 'mode', 'max_attempts'})
    if set(value) != {'series_id', 'series_revision', 'mode', 'max_attempts'}:
        raise CatalogError('Series dispatch requires series identity, revision, mode and attempt bound')
    series_id, revision = identifier(value['series_id']), integer(value['series_revision'])
    if value['mode'] not in ('model', 'authored'):
        raise CatalogError('Series dispatch mode must be model or authored')
    if not any(row.get('kind') == 'series' and row.get('id') == series_id and row.get('revision') == revision for row in sources):
        raise CatalogError('Series dispatch must match a canonical commission source')
    return {'series_id': series_id, 'series_revision': revision, 'mode': value['mode'],
            'max_attempts': integer(value['max_attempts'], 1, 3)}


class CommissionDispatcher:
    def __init__(self, home, media_jobs=None, music_generation=None, music_video=None, production=None):
        self.home = Path(home)
        self._media_jobs = media_jobs
        self._music_generation = music_generation
        self._music_video = music_video
        self._production = production

    def media_jobs(self):
        if self._media_jobs is None:
            try:
                from gideon.workspace.artifacts.native import NativeArtifactProvider
                from gideon.workspace.capabilities.media.jobs import MediaJobs
                from gideon.workspace.capabilities.media.sketches import SketchStore
            except ImportError as exc:
                raise CatalogError('Canonical media jobs are unavailable', 503) from exc
            artifacts = NativeArtifactProvider(self.home / 'artifacts')
            sketches = SketchStore(self.home / 'capabilities/media/sketches.sqlite3', artifacts)
            self._media_jobs = MediaJobs(self.home / 'capabilities/media/jobs.sqlite3', sketches)
        return self._media_jobs

    def music_catalog(self):
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.capabilities.music.catalog import MusicCatalog
        return MusicCatalog(self.home / 'capabilities/music', NativeArtifactProvider(root=self.home / 'artifacts'))

    def music_generation(self):
        if self._music_generation is None:
            try:
                from gideon.workspace.capabilities.music.generation import MusicGeneration
                self._music_generation = MusicGeneration(self.home, self.music_catalog())
            except ImportError as exc:
                raise CatalogError('Canonical music generation is unavailable', 503) from exc
        return self._music_generation

    def music_video(self):
        if self._music_video is None:
            try:
                from gideon.workspace.capabilities.music.video import VideoStore
                self._music_video = VideoStore(self.home / 'capabilities/music', self.music_catalog())
            except ImportError as exc:
                raise CatalogError('Canonical music video rendering is unavailable', 503) from exc
        return self._music_video

    def production(self):
        if self._production is None:
            from .production import SeriesProductionStore
            self._production = SeriesProductionStore(self.home)
        return self._production

    @staticmethod
    def _artifact_refs(value):
        if not isinstance(value, dict):
            return []
        ref = value.get('artifact_ref') or value.get('result')
        if isinstance(ref, dict):
            artifact_id = ref.get('artifact_id') or ref.get('slug')
            version = ref.get('artifact_version') or ref.get('version')
            if artifact_id and version:
                return [{'artifact_id': artifact_id, 'artifact_version': version}]
        return []

    @staticmethod
    def _receipt(backend, operation, request_id, resource_id, status, upstream, attempt, error_code='', artifacts=None):
        return {'backend': backend, 'operation': operation, 'request_id': request_id, 'resource_id': resource_id,
                'status': status, 'upstream_status': upstream, 'attempt': attempt,
                'submitted_at': datetime.now(timezone.utc).isoformat(), 'error_code': error_code,
                'artifact_refs': artifacts or []}

    async def submit(self, ability, config, run_id, attempt):
        request_id = 'commission-' + run_id[:48] + '-' + str(attempt)
        try:
            if ability in ('image', 'video'):
                operation = ability + '_generate'
                jobs = self.media_jobs()
                service = jobs.images if ability == 'image' else jobs.videos
                if service.selector() is None:
                    return self._receipt('media_jobs', operation, request_id, '', 'external_unavailable', '', attempt,
                                         error_code=ability + '_provider_unavailable')
                job = jobs.submit({'operation': operation, 'request_id': request_id, 'input': config['input']})
                status = 'completed' if job['status'] == 'succeeded' else 'failed' if job['status'] in ('failed', 'cancelled') else 'submitted'
                return self._receipt('media_jobs', operation, request_id, job['id'], status, job['status'], attempt,
                                     artifacts=self._artifact_refs(job))
            if ability == 'music':
                job = await self.music_generation().submit({'request_id': request_id, **config})
                status = 'completed' if job['status'] == 'completed' else 'failed' if job['status'] in ('failed', 'cancelled', 'interrupted') else 'submitted'
                return self._receipt('music_generation', 'compose', request_id, job['id'], status, job['status'], attempt,
                                     artifacts=self._artifact_refs(job))
            if ability == 'music-video':
                job = await self.music_video().submit({'request_id': request_id, **config})
                status = 'completed' if job['status'] == 'completed' else 'failed' if job['status'] in ('failed', 'cancelled', 'interrupted') else 'submitted'
                return self._receipt('music_video', 'render', request_id, job['id'], status, job['status'], attempt,
                                     artifacts=self._artifact_refs(job))
            store = self.production()
            run = store.start(config['series_id'], {'request_id': request_id, 'series_revision': config['series_revision'],
                'mode': config['mode'], 'max_attempts': config['max_attempts']})
            if run['status'] == 'running':
                run = await store.advance(config['series_id'], run['id'])
            unavailable = run['status'] == 'exhausted' or run.get('pause_reason') == 'configured_model_failed'
            status = 'external_unavailable' if unavailable else 'completed' if run['status'] == 'done' else 'submitted'
            artifacts = [{'artifact_id': row['artifact_id'], 'artifact_version': row['artifact_version'],
                          'content_hash': row.get('content_hash')} for row in run.get('source_pins', []) if row.get('artifact_id')]
            return self._receipt('series_production', 'advance', request_id, run['id'], status, run['status'], attempt,
                                 error_code=run.get('pause_reason', '') if unavailable else '', artifacts=artifacts)
        except Exception as exc:
            code = str(getattr(exc, 'code', '') or '')
            unavailable = getattr(exc, 'status', 0) == 503 or 'unavailable' in code
            if unavailable and not code:
                code = {'image': 'image_backend_unavailable', 'video': 'video_backend_unavailable',
                        'music': 'music_backend_unavailable', 'music-video': 'music_video_backend_unavailable',
                        'series': 'series_backend_unavailable'}[ability]
            return self._receipt({'image': 'media_jobs', 'video': 'media_jobs', 'music': 'music_generation',
                                  'music-video': 'music_video', 'series': 'series_production'}[ability],
                                 {'image': 'image_generate', 'video': 'video_generate', 'music': 'compose',
                                  'music-video': 'render', 'series': 'advance'}[ability], request_id, '',
                                 'external_unavailable' if unavailable else 'failed', '', attempt,
                                 error_code=code or type(exc).__name__)
