import asyncio
import hashlib
import json
import io
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from .sketches import SketchError, fields, integer


def selected_image():
    from gideon.integrations.image_gen.registry import active_image_gen
    return active_image_gen()


class ImageService:
    def __init__(self, artifacts, selector=None):
        self.artifacts = artifacts
        self.selector = selector or selected_image

    def source(self, artifact_id, version):
        integer(version, 1, 1000000)
        artifact = self.artifacts.get(artifact_id)
        raw = self.artifacts.raw_bytes(artifact_id, version=version) if artifact and artifact.kind == 'image' else None
        if not raw:
            raise SketchError('Pinned conditioning image is unavailable', 404)
        try:
            image = Image.open(io.BytesIO(raw[0]))
            if image.width > 4096 or image.height > 4096 or getattr(image, 'n_frames', 1) != 1:
                raise SketchError('Conditioning image exceeds supported dimensions or frames')
            image.load()
            return image.convert('RGBA')
        except (OSError, ValueError, SyntaxError) as exc:
            raise SketchError('Conditioning image cannot be decoded') from exc

    def prepare(self, body):
        fields(body, ('prompt', 'size', 'source_artifact_id', 'source_version', 'mask_artifact_id', 'mask_version', 'controls'), ('prompt',))
        prompt = body['prompt']
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise SketchError('Prompt must contain 1–4000 characters')
        size = body.get('size', '')
        if not isinstance(size, str) or len(size) > 40:
            raise SketchError('Invalid image size')
        controls = body.get('controls', {})
        fields(controls, ('seed', 'steps', 'guidance', 'strength'))
        for key, value in controls.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise SketchError('Controls must contain finite numeric values')
            if key in ('seed', 'steps') and not isinstance(value, int):
                raise SketchError('Seed and steps must be integers')
            if not 0 <= value <= 4294967295:
                raise SketchError('Control value is outside the supported input range')
        for prefix in ('source', 'mask'):
            if (prefix+'_artifact_id' in body) != (prefix+'_version' in body):
                raise SketchError('Conditioning references require an artifact and version')
        if 'mask_artifact_id' in body and 'source_artifact_id' not in body:
            raise SketchError('A mask requires a source image')
        if 'source_artifact_id' in body:
            source = self.source(body['source_artifact_id'], body['source_version'])
            if 'mask_artifact_id' in body:
                mask = self.source(body['mask_artifact_id'], body['mask_version'])
                if mask.size != source.size:
                    raise SketchError('Mask dimensions must match the source image')
        selected = self.selector()
        return dict(body, prompt=prompt.strip(), controls=dict(controls), size=size,
                    selection=f'{selected[0].name}:{selected[1]}' if selected else '')

    async def capabilities(self):
        selected = self.selector()
        if selected is None:
            return {'selection': '', 'models': [], 'available': False}
        provider, model_id = selected
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
        return dict(selection=f'{provider.name}:{model_id}', available=bool(await asyncio.wait_for(provider.is_available(), timeout=5)),
                    models=[dict(name=model.name, sizes=model.sizes, supports_edit=model.supports_edit,
                                 supports_mask=model.supports_mask, controls={key: dict(minimum=value.minimum, maximum=value.maximum, integer=value.integer) for key, value in model.supported_controls.items()}) for model in models if model.name == model_id])

    def validate_model(self, request, model):
        if request['size'] and model.sizes and request['size'] not in model.sizes:
            raise SketchError('Selected model does not support this size')
        if request.get('source_artifact_id') and not model.supports_edit:
            raise SketchError('Selected model does not support image conditioning')
        if request.get('mask_artifact_id') and not model.supports_mask:
            raise SketchError('Selected model does not advertise mask conditioning')
        for key, value in request['controls'].items():
            control = model.supported_controls.get(key)
            if control is None:
                raise SketchError(f'Selected model does not advertise {key}')
            if not control.minimum <= value <= control.maximum or (control.integer and not isinstance(value, int)):
                raise SketchError(f'{key} is outside the model control bounds')

    async def execute(self, request, job_id):
        selected = self.selector()
        if selected is None:
            raise SketchError('No image generation provider is configured', 503)
        provider, model_id = selected
        if request['selection'] != f'{provider.name}:{model_id}':
            raise SketchError('Image selection changed; submit a new request', 409)
        if provider.name == 'stub' or not await asyncio.wait_for(provider.is_available(), timeout=5):
            raise SketchError('Image generation provider is unavailable', 503)
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
        model = next((item for item in models if item.name == model_id), None)
        if model is None:
            raise SketchError('Selected image model is absent from the catalog', 409)
        self.validate_model(request, model)
        with TemporaryDirectory(prefix='gideon-image-') as directory:
            parameters = dict(model=model_id, size=request['size'], n=1, **request['controls'])
            if request.get('source_artifact_id'):
                source = self.source(request['source_artifact_id'], request['source_version'])
                source_path = Path(directory) / 'source.png'
                source.save(source_path, 'PNG')
                parameters['source_image'] = str(source_path)
                if request.get('mask_artifact_id'):
                    mask = self.source(request['mask_artifact_id'], request['mask_version'])
                    mask_path = Path(directory) / 'mask.png'
                    mask.save(mask_path, 'PNG')
                    parameters['mask'] = str(mask_path)
                results = await self.invoke(provider.edit(request['prompt'], **parameters))
            else:
                results = await self.invoke(provider.generate(request['prompt'], **parameters))
        if not results:
            raise SketchError('Image provider returned no output')
        return await asyncio.to_thread(self.materialize, results[0], request, job_id)

    async def invoke(self, operation):
        try:
            return await asyncio.wait_for(operation, timeout=300)
        except Exception as exc:
            raise SketchError("Image provider execution failed; inspect provider diagnostics", 503) from exc

    def materialize(self, result, request, job_id):
        from gideon.integrations.mcp_artifacts import _materialize_image
        raw = _materialize_image(result)
        if raw is None or len(raw[0]) > 16 * 1024 * 1024:
            raise SketchError('Image output is absent or too large')
        try:
            image = Image.open(io.BytesIO(raw[0]))
            if image.width > 4096 or image.height > 4096 or getattr(image, 'n_frames', 1) != 1:
                raise SketchError('Generated image exceeds supported dimensions or frames')
            image.load()
            output = io.BytesIO()
            image.convert('RGBA').save(output, 'PNG')
        except (OSError, ValueError, SyntaxError) as exc:
            raise SketchError('Generated image cannot be decoded') from exc
        slug = f'image-job-{job_id}'
        metadata = dict(media_job_id=job_id, generation_request_sha256=hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(), model_selection=request['selection'])
        metadata.update({key: request[key] for key in ('source_artifact_id', 'source_version', 'mask_artifact_id', 'mask_version') if key in request})
        existing = self.artifacts.get(slug)
        if existing:
            if not existing.events or existing.events[0].metadata != metadata:
                raise SketchError('Image result identity conflicts', 409)
            return {'artifact_id': slug, 'version': existing.version}
        artifact = self.artifacts.create_binary(name=request['prompt'][:80], data=output.getvalue(), mime='image/png', source='chat', event_metadata=metadata, slug=slug)
        return {'artifact_id': artifact.slug, 'version': artifact.version}
