#!/usr/bin/env python3
"""Static wpaperd mapping, bounds, private sharp cache; synthetic images only."""
import base64
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from PIL import Image
from unittest.mock import patch
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / 'shell/helpers/wallpaper.py'


def fixture(profile):
    image = Image.new('RGB', (768, 480))
    # Stripes plus gradients in both axes make displacement distinguishable from
    # tinting, and RGB divergence detectable. Not a design choice for wallpapers.
    image.putdata([((x * 2 + y // 3) % 210 + 20,
                    (y * 2 + x // 3) % 170 + 25,
                    190 if (x // 30 + y // 25) % 2 else 55)
                   for y in range(480) for x in range(768)])
    path = profile / 'PRIVATE_WALLPAPER.png'
    image.save(path)
    directory = profile / 'config/wpaperd'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.toml').write_text('[default]\npath = ' + json.dumps(str(path)) + '\nmode = "center"\n')
    return path


def run_tests():
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='wallpaper-test-', dir=cache))
    image_path = fixture(profile)
    config = profile / 'config/wpaperd/config.toml'
    env = dict(os.environ, XDG_CONFIG_HOME=str(profile / 'config'), XDG_CACHE_HOME=str(profile / 'cache'), PYTHONDONTWRITEBYTECODE='1')
    def run(w=320, h=200, scale=1, variant="glass"):
        result = subprocess.run(['python', '-B', str(HELPER), str(w), str(h), str(scale), 'Fixture', variant],
                                env=env, capture_output=True, text=True, timeout=10, check=True)
        assert not result.stderr and 'PRIVATE' not in result.stdout
        if variant != 'sharp':
            assert str(profile) not in result.stdout
        return json.loads(result.stdout)
    before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    textures = []
    for scale in (1, 1.25, 2):
        value = run(scale=scale)
        assert value['state'] == 'ready', value['state']
        im = Image.open(io.BytesIO(base64.b64decode(value['texture'].split(',')[1])))
        assert im.size == (320, 200)
        textures.append(im)
    # Scale changes sampling resolution, not placement. At this smooth pixel
    # the integer rounding difference must remain <=2 RGB levels.
    for im in textures[1:]:
        assert max(abs(a-b) for a,b in zip(im.getpixel((100,100)), textures[0].getpixel((100,100)))) <= 2
    assert before == {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    sharp = run(variant='sharp')
    cached = Path(unquote(urlparse(sharp['texture']).path))
    assert cached.parent == profile / 'cache/emaki/wallpaper-sharp'
    assert cached.stat().st_mode & 0o777 == 0o600
    assert cached.parent.stat().st_mode & 0o777 == 0o700
    assert Image.open(cached).size == (320, 200)
    timestamp = cached.stat().st_mtime_ns
    spec = importlib.util.spec_from_file_location('wallpaper_helper', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with patch.dict(os.environ, env), patch.object(helper, 'bounded_read', wraps=helper.bounded_read) as reads:
        assert helper.texture(320, 200, 1, 'Fixture', 'sharp') == sharp
        assert image_path not in [call.args[0] for call in reads.call_args_list], 'cache hit must not remap the source wallpaper'
    assert run(variant='sharp') == sharp and cached.stat().st_mtime_ns == timestamp
    cached.write_bytes(b'corrupt cache with a valid owner, mode and plausible length')
    assert run(variant='sharp') == sharp, 'corrupt cache is healed under the unchanged key'
    with Image.open(cached) as repaired:
        repaired.load()
        assert repaired.size == (320, 200)
    cached.write_bytes(cached.read_bytes()[:100])
    assert run(variant='sharp') == sharp, 'truncated PNG is also healed'
    with Image.open(cached) as repaired:
        repaired.load()
    stale_temp = cached.parent / '.sharp-abandoned'
    live_temp = cached.parent / '.sharp-still-writing'
    stale_temp.write_bytes(b'old crop interrupted by SIGKILL')
    live_temp.write_bytes(b'concurrent writer')
    old_time = time.time() - helper.STALE_TEMP_SECONDS - 5
    os.utime(stale_temp, (old_time, old_time))
    assert run(variant='sharp') == sharp
    assert not stale_temp.exists() and live_temp.exists(), 'warm hits reap stale atomic writes and preserve concurrent ones'
    assert run(scale=1.25, variant='sharp')['texture'] != sharp['texture']
    os.utime(image_path, ns=(image_path.stat().st_atime_ns, image_path.stat().st_mtime_ns + 1))
    assert run(variant='sharp')['texture'] != sharp['texture'], 'wallpaper mtime invalidates cache'
    for width in range(321, 330):
        run(w=width, variant='sharp')
    assert len(list(cached.parent.glob('*.png'))) == 8
    assert run(8192, 8192)['state'] == 'output_limit'
    large = run(3840, 2400, variant='sharp')
    assert large['state'] == 'ready', 'target 3840x2400 display must fit the texture guard'
    assert Image.open(Path(unquote(urlparse(large['texture']).path))).size == (3840, 2400)
    assert run(4000, 3001, variant='sharp')['state'] == 'output_limit'
    original = config.read_text()
    for mode in ('stretch', 'fit'):
        config.write_text(original.replace('center', mode))
        assert run()['state'] == 'ready'
    bands = Image.new('RGB', (80, 20), (0, 180, 0))
    bands.paste((180, 0, 0), (0, 0, 20, 20))
    bands.paste((0, 0, 180), (60, 0, 80, 20))
    bands.save(image_path)
    def mapped(mode, offset=.5):
        config.write_text(original.replace('center', mode) + f'offset={offset}\n')
        result = run(100, 100)
        return Image.open(io.BytesIO(base64.b64decode(result['texture'].split(',')[1])))
    for offset, channel in ((0, 0), (.5, 1), (1, 2)):
        color = mapped('center', offset).getpixel((50, 50))
        assert color[channel] > 170 and sum(color) - color[channel] < 20, ('cover/offset', color)
    assert max(mapped('fit').getpixel((50, 0))) <= 1, 'fit must leave black borders'
    color = mapped('stretch').getpixel((5, 50))
    assert color[0] > color[1] + 100 and color[0] > color[2] + 100, 'stretch must retain left band'
    for mode in ('tile', 'fit-border-color'):
        config.write_text(original.replace('center', mode))
        assert run()['state'] == 'mode_unsupported'
    config.write_text(original + '\n["Unknown description"]\nmode="center"\n')
    assert run()['state'] == 'output_selection_unsupported'
    config.write_text('not [toml')
    assert run()['state'] == 'config_invalid'
    config.write_text('default = 42')
    assert run()['state'] == 'config_invalid'
    config.write_text(original.replace(str(image_path), str(profile)))
    assert run()['state'] == 'slideshow_unsupported'
    config.write_text(original)
    image_path.write_bytes(b'not an image')
    assert run()['state'] == 'image_invalid'
    image_path.unlink()
    assert run()['state'] == 'image_missing'
    config.unlink()
    assert run()['state'] == 'config_missing'
    os.mkfifo(config)
    assert run()['state'] == 'config_invalid'
    print('Wallpaper: mapping scale 1/1.25/2, static modes, bounds, corrupt/missing/FIFO/config fallback, private source paths, sharp cache hit/self-heal/invalidation/permissions/8-file bound, stale atomic-write cleanup: PASS')


if __name__ == '__main__':
    run_tests()
