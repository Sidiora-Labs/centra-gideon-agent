"""Check the supplied artwork, packaged copies, rendered launchers, and theme wiring."""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dimensions(data: bytes) -> tuple[int, int]:
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'Expected a PNG image'
    return struct.unpack('>II', data[16:24])


def main() -> None:
    sources = json.loads((ROOT / 'assets/identity/sources.json').read_text())
    consumers = json.loads((ROOT / 'assets/identity/consumers.json').read_text())
    for relative, expected in sources['archives'].items():
        assert digest((ROOT / relative).read_bytes()) == expected, relative
        with zipfile.ZipFile(ROOT / relative) as archive:
            assert len(archive.namelist()) == 112
    for relative, record in sources['files'].items():
        data = (ROOT / relative).read_bytes()
        with zipfile.ZipFile(ROOT / record['archive']) as archive:
            assert data == archive.read(record['member']), relative
        assert digest(data) == record['sha256'], relative
        size = int(Path(relative).stem.split('-')[-1])
        assert dimensions(data) == (size, size), relative
    for target, source in consumers['copies'].items():
        assert (ROOT / target).read_bytes() == (ROOT / source).read_bytes(), target
    for mode, body in (('dark', (255, 255, 255)), ('light', (0, 0, 0))):
        image = Image.open(ROOT / f'assets/identity/{mode}/logo-512.png').convert('RGBA')
        assert image.getchannel('A').getextrema() == (0, 255)
        colors = dict((color, count) for count, color in image.getcolors(512 * 512))
        assert colors.get((*body, 255), 0) > 80000, mode
        assert colors.get((31, 72, 255, 255), 0) > 25000, mode
    allowed_images = {(ROOT / f'assets/identity/{mode}/logo-512.png').read_bytes() for mode in ('dark', 'light')}
    svg_files = [ROOT / 'apps/desktop/assets/gideon.svg', ROOT / 'apps/console/public/gideon.svg',
                 *list((ROOT / 'apps/console/public/icons').glob('*gideon*.svg')), ROOT / 'apps/console/public/icons/icon.svg',
                 *list((ROOT / 'apps/mobile/assets').glob('*.svg'))]
    embedded_count = 0
    for path in svg_files:
        document = ET.fromstring(path.read_text())
        assert not list(document.iter('{http://www.w3.org/2000/svg}path')), path
        for image in document.iter('{http://www.w3.org/2000/svg}image'):
            href = image.get('{http://www.w3.org/1999/xlink}href') or image.get('href')
            assert href and href.startswith('data:image/png;base64,'), path
            assert base64.b64decode(href.split(',', 1)[1]) in allowed_images, path
            embedded_count += 1
    for name in ('icon-only', 'icon-foreground', 'icon-background', 'splash', 'splash-dark'):
        size = 2732 if name.startswith('splash') else 1024
        assert dimensions((ROOT / f'apps/mobile/assets/{name}.png').read_bytes()) == (size, size)
    package = json.loads((ROOT / 'apps/desktop/package.json').read_text())['build']
    for icon in ('gideon-dark.png', 'gideon-light.png', 'tray-dark.png', 'tray-light.png'):
        assert f'assets/{icon}' in package['files'], icon
    for platform in ('mac', 'linux'):
        assert (ROOT / 'apps/desktop' / package[platform]['icon']).is_file()
    loading = (ROOT / 'apps/desktop/views/loading.html').read_text()
    assert 'color-scheme: dark' in loading and '../assets/gideon-dark.png' in loading
    mobile = (ROOT / 'apps/mobile/www/index.html').read_text()
    assert 'media="(prefers-color-scheme: dark)" srcset="assets/gideon-dark.png"' in mobile
    assert 'src="assets/gideon-light.png"' in mobile
    policy = (ROOT / 'apps/console/src/app/shell/swPolicy.ts').read_text()
    for mode in ('dark', 'light'):
        for suffix in ('', '-32'):
            assert f'/icons/gideon-{mode}{suffix}.png' in policy
    desktop = (ROOT / 'apps/desktop/src/application/desktop-application.js').read_text()
    assert 'electron.nativeTheme?.shouldUseDarkColors ? "dark" : "light"' in desktop
    tray = (ROOT / 'apps/desktop/src/native/tray-presence.js').read_text()
    assert 'nativeTheme?.on?.("updated", this.onAppearance)' in tray
    assert 'nativeTheme?.removeListener?.("updated", this.onAppearance)' in tray
    print(f'PASS: 2 unchanged archives; {len(sources["files"])} exact source assets; {len(consumers["copies"])} exact packaged copies; '
          f'{embedded_count} preserved embedded images; 5 rendered mobile assets; desktop/mobile/offline theme mapping')


if __name__ == '__main__':
    main()
