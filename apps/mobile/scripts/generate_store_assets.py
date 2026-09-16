"""Prepare product identity assets from the supplied, unmodified Gideon artwork."""
from __future__ import annotations

import ctypes
from ctypes.util import find_library
from pathlib import Path


def render_svg(source: Path, destination: Path, size: int) -> None:
    svg = ctypes.CDLL(find_library('rsvg-2') or 'librsvg-2.so.2')
    cairo = ctypes.CDLL(find_library('cairo') or 'libcairo.so.2')
    objects = ctypes.CDLL(find_library('gobject-2.0') or 'libgobject-2.0.so.0')
    signatures = (
        (svg, 'rsvg_handle_new_from_file', [ctypes.c_char_p, ctypes.c_void_p], ctypes.c_void_p),
        (svg, 'rsvg_handle_render_cairo', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        (cairo, 'cairo_image_surface_create', [ctypes.c_int, ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
        (cairo, 'cairo_create', [ctypes.c_void_p], ctypes.c_void_p),
        (cairo, 'cairo_scale', [ctypes.c_void_p, ctypes.c_double, ctypes.c_double], None),
        (cairo, 'cairo_surface_write_to_png', [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
        (cairo, 'cairo_destroy', [ctypes.c_void_p], None),
        (cairo, 'cairo_surface_destroy', [ctypes.c_void_p], None),
        (objects, 'g_object_unref', [ctypes.c_void_p], None),
    )
    for library, name, arguments, result in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    document = svg.rsvg_handle_new_from_file(bytes(source), None)
    if not document:
        raise ValueError(f'Could not parse SVG: {source}')
    surface = cairo.cairo_image_surface_create(0, size, size)
    context = cairo.cairo_create(surface)
    try:
        cairo.cairo_scale(context, size / 512, size / 512)
        if not svg.rsvg_handle_render_cairo(document, context):
            raise ValueError(f'Could not render SVG: {source}')
        status = cairo.cairo_surface_write_to_png(surface, bytes(destination))
        if status:
            raise OSError(f'Could not write {destination}: Cairo status {status}')
    finally:
        cairo.cairo_destroy(context)
        cairo.cairo_surface_destroy(surface)
        objects.g_object_unref(document)


def image_svg(image: bytes, *, background: str | None = None, inset: int = 0) -> str:
    import base64
    encoded = base64.b64encode(image).decode('ascii')
    fill = f'<rect width="512" height="512" fill="{background}"/>' if background else ''
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="512" height="512" viewBox="0 0 512 512" role="img" aria-label="Gideon">'
            f'{fill}<image x="{inset}" y="{inset}" width="{512 - 2 * inset}" height="{512 - 2 * inset}" '
            f'xlink:href="data:image/png;base64,{encoded}"/></svg>\n')


def themed_svg(dark: bytes, light: bytes) -> str:
    import base64
    images = ''.join(f'<image class="{mode}" width="512" height="512" xlink:href="data:image/png;base64,{base64.b64encode(data).decode()}"/>'
                     for mode, data in (('light', light), ('dark', dark)))
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="512" height="512" viewBox="0 0 512 512" role="img" aria-label="Gideon">'
            '<style>.dark{display:none}@media(prefers-color-scheme:dark){.light{display:none}.dark{display:block}}</style>'
            f'{images}</svg>\n')


def main() -> int:
    import json
    import shutil
    root = Path(__file__).resolve().parents[3]
    identity = root / 'assets/identity'
    sources = {mode: (identity / mode / 'logo-512.png').read_bytes() for mode in ('dark', 'light')}
    copies: dict[str, str] = {}

    def copy(source: str, *targets: str) -> None:
        for target in targets:
            destination = root / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / source, destination)
            copies[target] = source

    for mode in ('dark', 'light'):
        copy(f'assets/identity/{mode}/logo-512.png', f'apps/desktop/assets/gideon-{mode}.png',
             f'apps/console/public/icons/gideon-{mode}.png', f'apps/mobile/www/assets/gideon-{mode}.png')
        copy(f'assets/identity/{mode}/logo-24.png', f'apps/desktop/assets/tray-{mode}.png')
        copy(f'assets/identity/{mode}/logo-32.png', f'apps/console/public/icons/gideon-{mode}-32.png')
    copy('assets/identity/dark/logo-512.png', 'apps/desktop/assets/gideon.png', 'apps/console/public/icons/icon-512.png')
    copy('assets/identity/dark/logo-192.png', 'apps/console/public/icons/icon-192.png')
    adaptive = themed_svg(sources['dark'], sources['light'])
    for target in ('apps/desktop/assets/gideon.svg', 'apps/console/public/gideon.svg', 'apps/console/public/icons/icon.svg'):
        (root / target).write_text(adaptive)
    (root / 'apps/console/public/icons/personality-gideon-arcade.svg').write_text(image_svg(sources['dark'], background='#160f04'))
    assets = root / 'apps/mobile/assets'
    designs = {
        'icon-only': image_svg(sources['dark'], background='#0f1724'),
        'icon-foreground': image_svg(sources['dark'], inset=72),
        'icon-background': '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><rect width="512" height="512" fill="#0f1724"/></svg>\n',
        'splash': image_svg(sources['light'], background='#ffffff', inset=184),
        'splash-dark': image_svg(sources['dark'], background='#0f1724', inset=184),
    }
    for name, design in designs.items():
        source = assets / f'{name}.svg'
        source.write_text(design)
        render_svg(source, assets / f'{name}.png', 2732 if name.startswith('splash') else 1024)
    (identity / 'consumers.json').write_text(json.dumps({'copies': copies, 'mobile_rendering': {
        'source_size': 512, 'launcher_size': 1024, 'splash_size': 2732,
        'reason': 'Supplied iOS exports flatten the mark into the background; Android exports retain all artwork and transparency.',
    }}, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
