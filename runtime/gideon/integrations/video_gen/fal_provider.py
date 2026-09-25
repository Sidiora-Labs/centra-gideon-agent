import asyncio
import base64
import json
import os
import re
from pathlib import Path

from gideon.security.net import CONNECTOR, fetch
from .provider import VideoControl, VideoGenError, VideoGenModel, VideoGenProvider, VideoResult


class FalVideoProvider(VideoGenProvider):
    name = 'gideon-fal-video'
    display_name = 'FAL Veo 3.1'

    def __init__(self, api_key=''):
        self.api_key = api_key or os.environ.get('FAL_KEY', '')

    async def is_available(self):
        return bool(self.api_key)

    async def list_models(self):
        return [VideoGenModel('fal-ai/veo3.1', description='Veo 3.1; continuation conditions on the last frame only.', aspect_ratios=['16:9', '9:16'], max_duration_s=8, durations=[4, 6, 8], supports_first_frame=True, supports_continuation=True, supported_controls={'seed': VideoControl(0, 4294967295, True)})]

    def payload(self, prompt, model, duration_seconds, aspect_ratio, opts):
        if model not in ('', 'fal-ai/veo3.1') or duration_seconds not in (4, 6, 8) or isinstance(duration_seconds, bool):
            raise VideoGenError('Unsupported FAL model or duration')
        if aspect_ratio not in ('', '16:9', '9:16') or set(opts) - {'seed', 'first_frame', 'continuation_frame', 'continuation_video'}:
            raise VideoGenError('Unsupported FAL video controls')
        if opts.get('first_frame') and opts.get('continuation_frame'):
            raise VideoGenError('Choose a first frame or continuation')
        if opts.get('continuation_video') and not opts.get('continuation_frame'):
            raise VideoGenError('Continuation requires an extracted final frame')
        body = dict(prompt=prompt, duration=f'{int(duration_seconds)}s', aspect_ratio=aspect_ratio or '16:9', resolution='720p', generate_audio=True, auto_fix=False)
        if 'seed' in opts:
            seed = opts['seed']
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
                raise VideoGenError('Invalid FAL seed')
            body['seed'] = seed
        frame = opts.get('first_frame') or opts.get('continuation_frame')
        endpoint = 'fal-ai/veo3.1'
        if frame:
            raw = Path(frame).read_bytes()
            if not raw or len(raw) > 8 * 1024 * 1024:
                raise VideoGenError('FAL conditioning frame exceeds 8 MiB')
            from PIL import Image
            import io
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG':
                    raise VideoGenError('FAL conditioning requires a prepared PNG')
                image.verify()
            body['image_url'] = 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii')
            endpoint += '/image-to-video'
        return endpoint, body

    async def request(self, path, method='GET', body=None):
        response = await fetch('https://queue.fal.run/'+path, policy=CONNECTOR, method=method, headers={'Authorization': 'Key '+self.api_key, 'Content-Type': 'application/json'}, data=json.dumps(body).encode() if body is not None else None)
        if response.status not in (200, 201, 202) or response.truncated:
            raise VideoGenError('FAL request failed')
        value = json.loads(response.body)
        if not isinstance(value, dict):
            raise VideoGenError('Invalid FAL response')
        return value

    async def generate(self, prompt, *, model='', duration_seconds=8, aspect_ratio='', **opts):
        endpoint, body = self.payload(prompt, model, duration_seconds, aspect_ratio, opts)
        if not self.api_key:
            raise VideoGenError('FAL credential is unavailable')
        try:
            async with asyncio.timeout(1100):
                submitted = await self.request(endpoint, 'POST', body)
                request_id = submitted.get('request_id', '')
                if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
                    raise VideoGenError('Invalid FAL request identity')
                path = 'fal-ai/veo3.1/requests/'+request_id
                while True:
                    state = await self.request(path+'/status')
                    if state.get('status') == 'COMPLETED':
                        result = await self.request(path)
                        url = result['video']['url']
                        if not isinstance(url, str) or not url.startswith('https://'):
                            raise VideoGenError('Invalid FAL video result')
                        return [VideoResult(url=url, mime='video/mp4', duration_s=duration_seconds)]
                    if state.get('status') not in ('IN_QUEUE', 'IN_PROGRESS'):
                        raise VideoGenError('FAL video request did not complete')
                    await asyncio.sleep(2)
        except Exception as exc:
            raise VideoGenError('FAL video execution failed') from exc


def create_provider(config=None):
    return FalVideoProvider((config or {}).get('api_key', ''))
