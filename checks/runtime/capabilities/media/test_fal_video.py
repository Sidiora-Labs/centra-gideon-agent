import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from gideon.integrations.video_gen.fal_provider import FalVideoProvider, create_provider
from gideon.sdk.video import VideoGenError, VideoGenProvider


def test_native_manifest_uses_real_registry_factory():
    path = Path('runtime/gideon/extensions/apps/native/gideon-fal-video/app.json')
    manifest = json.loads(path.read_text())
    assert manifest['native'] is True
    assert manifest['provider']['type'] == 'model'
    assert manifest['provider']['capabilities'] == ['video_gen']
    assert manifest['provider']['implementation'] == 'gideon.integrations.video_gen.fal_provider:create_provider'
    provider = create_provider({'api_key': ''})
    assert isinstance(provider, VideoGenProvider)
    assert provider.name == 'gideon-fal-video'
    assert provider.display_name == 'FAL Veo 3.1'


@pytest.mark.asyncio
async def test_real_fal_catalog_advertises_only_documented_inputs():
    provider = FalVideoProvider()
    models = await provider.list_models()
    assert len(models) == 1
    model = models[0]
    assert model.name == 'fal-ai/veo3.1'
    assert model.durations == [4, 6, 8]
    assert model.aspect_ratios == ['16:9', '9:16']
    assert model.max_duration_s == 8
    assert model.supports_first_frame
    assert model.supports_continuation
    assert not model.supports_last_frame
    assert set(model.supported_controls) == {'seed'}
    assert model.supported_controls['seed'].integer
    assert 'last frame only' in model.description


def test_real_fal_payload_keeps_exact_prompt_and_supported_duration():
    provider = FalVideoProvider()
    endpoint, body = provider.payload('A scene', 'fal-ai/veo3.1', 6, '9:16', {'seed': 42})
    assert endpoint == 'fal-ai/veo3.1'
    assert body['prompt'] == 'A scene'
    assert body['duration'] == '6s'
    assert body['aspect_ratio'] == '9:16'
    assert body['seed'] == 42
    assert body['resolution'] == '720p'
    assert body['generate_audio'] is True
    assert body['auto_fix'] is False
    assert 'image_url' not in body
    assert 'api_key' not in body
    assert 'provider' not in body


def test_real_png_conditioning_becomes_documented_data_uri(tmp_path):
    path = tmp_path / 'frame.png'
    Image.new('RGBA', (32, 24), (10, 90, 200, 255)).save(path)
    provider = FalVideoProvider()
    endpoint, body = provider.payload('Move', '', 4, '', {'first_frame': str(path)})
    assert endpoint == 'fal-ai/veo3.1/image-to-video'
    assert body['image_url'].startswith('data:image/png;base64,')
    raw = base64.b64decode(body['image_url'].split(',', 1)[1])
    assert raw == path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        assert image.getpixel((0, 0)) == (10, 90, 200, 255)
        assert image.size == (32, 24)
    continuation_endpoint, continuation = provider.payload('Continue', '', 8, '16:9', {'continuation_video': 'internal-only', 'continuation_frame': str(path)})
    assert continuation_endpoint == endpoint
    assert continuation['image_url'] == body['image_url']
    assert 'continuation_video' not in continuation
    assert 'internal-only' not in json.dumps(continuation)


@pytest.mark.parametrize('model,duration,aspect,opts', [
    ('other', 4, '', {}), ('', 5, '', {}), ('', True, '', {}),
    ('', 4, '1:1', {}), ('', 4, '', {'guidance': 5}), ('', 4, '', {'motion': 2}),
    ('', 4, '', {'last_frame': 'path'}), ('', 4, '', {'seed': True}),
    ('', 4, '', {'seed': -1}), ('', 4, '', {'seed': 1.5}),
    ('', 4, '', {'continuation_video': 'path'}),
    ('', 4, '', {'first_frame': 'a', 'continuation_frame': 'b'}),
])
def test_unsupported_fal_inputs_never_reach_network(model, duration, aspect, opts):
    with pytest.raises(VideoGenError):
        FalVideoProvider().payload('scene', model, duration, aspect, opts)


@pytest.mark.asyncio
async def test_missing_real_credential_is_not_success():
    provider = FalVideoProvider()
    if provider.api_key:
        pytest.skip('Configured provider is not invoked by this local-only gate')
    assert await provider.is_available() is False
    with pytest.raises(VideoGenError, match='credential'):
        await provider.generate('scene', duration_seconds=4)


def test_non_png_conditioning_is_rejected_before_request(tmp_path):
    path = tmp_path / 'image.jpg'
    Image.new('RGB', (8, 8), 'blue').save(path)
    with pytest.raises(VideoGenError):
        FalVideoProvider().payload('scene', '', 4, '', {'first_frame': str(path)})
    path.write_bytes(b'')
    with pytest.raises(VideoGenError):
        FalVideoProvider().payload('scene', '', 4, '', {'first_frame': str(path)})
