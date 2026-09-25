import base64
import io
import math
import re

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from gideon.integrations.image_gen.provider import ImageResult

from .sketches import SketchError, fields, integer


def factor(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise SketchError('Transform parameter is outside its supported range')


def solid_background(image, color, tolerance):
    difference = ImageChops.difference(image.convert('RGB'), Image.new('RGB', image.size, color))
    red, green, blue = difference.split()
    eligible = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value <= tolerance else 0)
    remaining = eligible.copy()
    width, height = image.size
    for point in [(x, y) for x in range(width) for y in (0, height-1)] + [(x, y) for y in range(height) for x in (0, width-1)]:
        if remaining.getpixel(point) == 255:
            ImageDraw.floodfill(remaining, point, 0, border=0)
    image.putalpha(ImageChops.subtract(image.getchannel('A'), ImageChops.subtract(eligible, remaining)))
    return image


class CleanupService:
    def __init__(self, images):
        self.images = images

    def source(self, artifact_id, version):
        self.images.source(artifact_id, version)
        data = self.images.artifacts.raw_bytes(artifact_id, version=version)[0]
        with Image.open(io.BytesIO(data)) as original:
            return ImageOps.exif_transpose(original).convert('RGBA')

    def prepare(self, body):
        fields(body, ('source_artifact_id', 'source_version', 'operations'), ('source_artifact_id', 'source_version', 'operations'))
        if not isinstance(body['source_artifact_id'], str) or not 1 <= len(body['source_artifact_id']) <= 200:
            raise SketchError('Source artifact ID must be a nonempty string')
        image = self.source(body['source_artifact_id'], body['source_version'])
        operations = body['operations']
        if not isinstance(operations, list) or not 1 <= len(operations) <= 10:
            raise SketchError('Cleanup requires 1–10 ordered transforms')
        width, height = image.size
        for operation in operations:
            if not isinstance(operation, dict):
                raise SketchError('Each transform must be an object')
            name = operation.get('op')
            if name == 'crop':
                fields(operation, ('op', 'x', 'y', 'width', 'height'), ('op', 'x', 'y', 'width', 'height'))
                for key in ('x', 'y'):
                    integer(operation[key], 0, 4095)
                for key in ('width', 'height'):
                    integer(operation[key], 1, 4096)
                if operation['x']+operation['width'] > width or operation['y']+operation['height'] > height:
                    raise SketchError('Crop must fit the current transformed image')
                width, height = operation['width'], operation['height']
            elif name == 'resize':
                fields(operation, ('op', 'width', 'height'), ('op', 'width', 'height'))
                width, height = integer(operation['width'], 1, 4096), integer(operation['height'], 1, 4096)
            elif name == 'rotate':
                fields(operation, ('op', 'degrees'), ('op', 'degrees'))
                if operation['degrees'] not in (90, 180, 270):
                    raise SketchError('Rotation must be 90, 180 or 270 degrees counterclockwise')
                if operation['degrees'] != 180:
                    width, height = height, width
            elif name == 'flip':
                fields(operation, ('op', 'axis'), ('op', 'axis'))
                if operation['axis'] not in ('horizontal', 'vertical'):
                    raise SketchError('Invalid flip axis')
            elif name in ('brightness', 'contrast'):
                fields(operation, ('op', 'factor'), ('op', 'factor'))
                factor(operation['factor'], 0, 3)
            elif name == 'sharpen':
                fields(operation, ('op', 'radius', 'percent', 'threshold'), ('op', 'radius', 'percent', 'threshold'))
                factor(operation['radius'], 0, 5)
                integer(operation['percent'], 0, 300)
                integer(operation['threshold'], 0, 255)
            elif name == 'solid_background':
                fields(operation, ('op', 'color', 'tolerance'), ('op', 'color', 'tolerance'))
                if not isinstance(operation['color'], str) or not re.fullmatch('#[0-9a-fA-F]{6}', operation['color']):
                    raise SketchError('Background color must be #RRGGBB')
                integer(operation['tolerance'], 0, 100)
                if width * height > 4 * 1024 * 1024:
                    raise SketchError('Solid background removal is limited to 4 megapixels')
            else:
                raise SketchError('Unsupported cleanup transform')
        return dict(body, prompt='Image cleanup', selection='', engine='pillow', output_width=width, output_height=height)

    def execute(self, request, job_id):
        validated = self.prepare({key: request[key] for key in ('source_artifact_id', 'source_version', 'operations')})
        if request != validated:
            raise SketchError('Cleanup request no longer matches its pinned source', 409)
        image = self.source(request['source_artifact_id'], request['source_version'])
        for operation in request['operations']:
            name = operation['op']
            if name == 'crop':
                x, y = operation['x'], operation['y']
                image = image.crop((x, y, x+operation['width'], y+operation['height']))
            elif name == 'resize':
                image = image.resize((operation['width'], operation['height']), Image.Resampling.LANCZOS)
            elif name == 'rotate':
                image = image.rotate(operation['degrees'], expand=True)
            elif name == 'flip':
                image = ImageOps.mirror(image) if operation['axis'] == 'horizontal' else ImageOps.flip(image)
            elif name in ('brightness', 'contrast'):
                alpha = image.getchannel('A')
                image = (ImageEnhance.Brightness if name == 'brightness' else ImageEnhance.Contrast)(image).enhance(operation['factor'])
                image.putalpha(alpha)
            elif name == 'sharpen':
                alpha = image.getchannel('A')
                image = image.filter(ImageFilter.UnsharpMask(operation['radius'], operation['percent'], operation['threshold']))
                image.putalpha(alpha)
            else:
                image = solid_background(image, operation['color'], operation['tolerance'])
        output = io.BytesIO()
        image.save(output, 'PNG')
        return self.images.materialize(ImageResult(b64=base64.b64encode(output.getvalue()).decode()), request, job_id)
