#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Own overview media uses one reversible settings transaction."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / '.cache/target/debug/emaki').resolve()
spec = importlib.util.spec_from_file_location('overview_background', ROOT / 'shell/helpers/overview_background.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)
with tempfile.TemporaryDirectory(prefix='emaki-overview-') as temporary:
    base = Path(temporary)
    for part in ('home', 'config', 'state', 'runtime', 'cache', 'data', 'assets'):
        (base / part).mkdir(mode=0o700)
    catalog = json.loads((ROOT / 'themes/catalog.json').read_text())
    catalog['styles'] = catalog['styles'][:2]
    for style in catalog['styles']:
        for key in ('wallpaper', 'overview_still', 'thumbnail'):
            path = base / 'assets' / style[key]
            path.parent.mkdir(exist_ok=True)
            Image.new('RGB', (64, 32), '#7799aa').save(path)
    (base / 'catalog.json').write_text(json.dumps(catalog))
    picture = base / 'own picture.png'
    Image.new('RGB', (64, 32), '#ffaa77').save(picture)
    video = base / 'own video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1', '-loop', '1',
                    '-i', str(picture), '-t', '0.2', '-threads', '1', '-pix_fmt', 'yuv420p', str(video)], check=True)
    broken = base / 'broken.mp4'
    broken.write_bytes(b'not media')
    assert media.inspect(str(picture))['kind'] == 'picture'
    assert media.inspect(str(video))['kind'] == 'video'
    assert media.inspect(str(broken))['state'] == 'invalid_overview_background'
    assert media.inspect(str(base / 'missing.png'))['state'] == 'invalid_overview_background'
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_DATA_HOME=str(base / 'data'),
               EMAKI_THEME_CATALOG=str(base / 'catalog.json'), EMAKI_THEME_ASSET_ROOT=str(base / 'assets'))
    for key in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(key, None)

    def call(*args, rejected=False):
        result = subprocess.run([str(BINARY), 'settings', *args, '--profile-root', str(base), '--json'],
                                env=env, capture_output=True, text=True, timeout=20)
        reply = json.loads(result.stdout)
        assert result.returncode == (1 if rejected else 0), reply
        return reply

    def values():
        return {row['key']: row['value'] for row in call('list')['settings']}

    def select(video_path, still_path):
        return call('set', 'appearance.overview_background', json.dumps(dict(video=video_path, still=still_path)))

    original = values()
    change = select('', str(picture))
    chosen = values()
    assert chosen['appearance.overview_video'] == ''
    assert chosen['appearance.overview_still'] == str(picture)
    assert chosen['appearance.wallpaper'] == original['appearance.wallpaper']
    call('undo', change['change_id'])
    assert values() == original
    select(str(video), str(picture))
    assert values()['appearance.overview_video'] == str(video)
    change = call('reset', 'appearance.overview_background')
    assert values()['appearance.overview_still'] == original['appearance.overview_still']
    call('undo', change['change_id'])
    assert values()['appearance.overview_video'] == str(video)
    second = catalog['styles'][1]
    call('set', 'appearance.style', second['id'])
    select('', str(picture))
    call('reset', 'appearance.overview_background')
    assert values()['appearance.overview_still'] == str(base / 'assets' / second['overview_still'])
    call('set', 'appearance.overview_background', 'catalog:emaki')
    assert values()['appearance.overview_still'] == original['appearance.overview_still']
    assert values()['appearance.style'] == second['id']
    select(str(video), str(picture))
    # The chooser's broken-media fallback preserves the still and disables decoding.
    call('set', 'appearance.overview_video', '')
    assert values()['appearance.overview_still'] == str(picture)
    assert values()['appearance.overview_video'] == ''
    before = values()
    call('set', 'appearance.overview_background', json.dumps(dict(video='', still=str(base / 'absent'))), rejected=True)
    assert values() == before
    call('set', 'appearance.style', 'emaki')
    assert values()['appearance.overview_still'] == original['appearance.overview_still']
print('PASS: picture/video validation, broken file fallback, atomic set/undo/reset, selected-theme reset and replacement')
