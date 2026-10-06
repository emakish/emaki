#!/usr/bin/env python3
"""Welcome input, renders, persistence and real IPC in private offscreen profiles."""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from PIL import Image, ImageOps, ImageStat
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / '.cache/welcome-shots'
SHOTS.mkdir(parents=True, exist_ok=True)


def content_contract():
    script = (ROOT / 'shell/WelcomeContent.js').read_text().replace('.pragma library', '')
    pages = json.loads(subprocess.check_output(
        ['node', '-e', script + '\nconsole.log(JSON.stringify(pages));'], text=True))
    config = (ROOT / 'niri/default.kdl').read_text()
    binds = {match[1]: match[2].strip() for match in re.finditer(
        r'^[ \t]*(Mod\+\S+)[ \t]+[^\n{]*\{[ \t]*([^}]+)\}', config, re.M)}
    for page in pages:
        for action in page['actions']:
            for binding, command in action['binds'].items():
                assert binds[binding].startswith(command), (binding, command, binds.get(binding))
    assert '"emaki-shell" "call" "launcher" "toggle"' in binds['Mod+D']
    assert binds['Mod+Shift+Slash'] == 'show-hotkey-overlay;'
    assert 'mod-key "Super"' in config
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(ROOT / 'shell/emaki-welcome.desktop')
    assert desktop['Desktop Entry']['Name'] == 'Welcome to Emaki'
    assert desktop['Desktop Entry']['Exec'] == 'emaki-shell call welcome present'
    main = (ROOT / 'shell/shell.qml').read_text()
    assert 'enabled: root.valid && !root.headless' in main
    assert 'ready: !startup.coverActive' in main
    assert 'onOpening: scene.closeAll()' in main
    print('PASS: displayed shortcuts match shipped bindings; launcher and startup hooks')


def prepare(base, scale):
    shutil.copytree(ROOT / 'shell', base / 'shell')
    for fixture in ('WelcomeTest.qml', 'WelcomeIpcHarness.qml'):
        text = (ROOT / 'tests/fixtures' / fixture).read_text()
        text = text.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
        (base / 'shell' / fixture).write_text(text)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'bin'):
        (base / name).mkdir(mode=0o700)
    (base / 'data/applications').mkdir()
    shutil.copyfile(ROOT / 'shell/emaki-welcome.desktop', base / 'data/applications/emaki-welcome.desktop')
    fake = base / 'bin/niri'
    fake.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                    'p=Path(os.environ["WELCOME_PROFILE"])\n'
                    '(p/"niri-args.json").write_text(json.dumps(sys.argv[1:]))\n'
                    'sys.exit(1 if (p/"fail-shortcuts").exists() else 0)\n')
    fake.chmod(0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QT_SCALE_FACTOR=str(scale),
               QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
               WELCOME_SHOTS=str(SHOTS), WELCOME_PROFILE=str(base),
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                'QT_SCREEN_SCALE_FACTORS', 'QT_AUTO_SCREEN_SCALE_FACTOR'):
        env.pop(key, None)
    return env


def clean_log(output):
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                  'Cannot assign', 'failed to load', 'FAIL!', 'ERROR'):
        assert error not in output, output


def wait_for(test):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = test()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('Welcome condition timed out')


def ipc_cases(base, env, *, expected_seen, expected_opened, save_failed=False, queued=False):
    entry = base / 'shell/WelcomeIpcHarness.qml'
    with (base / 'ipc.log').open('w+') as log:
        proc = subprocess.Popen(['qs', '-p', str(entry), '--no-color'], env=env, stdout=log, stderr=log)
        def call(target, method):
            result = subprocess.run(['qs', '-p', str(entry), 'ipc', 'call', target, method],
                                    env=env, text=True, capture_output=True, timeout=4)
            return result.stdout.strip() if result.returncode == 0 else ''
        def status():
            value = call('welcomeTest', 'status')
            return json.loads(value) if value.startswith('{') else {}
        try:
            state = wait_for(lambda: (s if (s := status()).get('restored') else False))
            wait_for(lambda: status().get('opened') == expected_opened)
            assert status()['seen'] == expected_seen, status()
            if save_failed:
                wait_for(lambda: status()['saveFailed'])
            reply = call('welcome', 'present')
            assert reply == ('queued' if queued else 'presented'), reply
            if queued:
                assert not status()['opened']
                call('welcomeTest', 'ready')
            wait_for(lambda: status()['opened'])
            assert call('welcome', 'present') == 'presented'
            call('welcomeTest', 'dismiss')
            wait_for(lambda: not status()['opened'])
            time.sleep(.15)
            assert not status()['opened']
            assert call('welcome', 'present') == 'presented'
            (base / 'fail-shortcuts').touch()
            call('welcomeTest', 'shortcuts')
            wait_for(lambda: status()['error'])
            assert status()['opened']
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            log.seek(0)
            clean_log(log.read())
            (base / 'fail-shortcuts').unlink(missing_ok=True)


def glass_renders(cache):
    spec = importlib.util.spec_from_file_location('welcome_render', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    # Quickshell's Unix IPC socket also includes the profile path (107-byte limit).
    renderer.CACHE = cache
    # A local crop of the shipped wallpaper, with no dependency on the session wallpaper.
    texture = SHOTS / 'wallpaper.png'
    with Image.open(ROOT / 'art/wallpaper/ring.png') as wallpaper:
        ImageOps.fit(wallpaper, (800, 660)).save(texture)
    for scale in (1, 2):
        for page in range(3):
            target = SHOTS / f'glass-{page + 1}@{scale}x.png'
            output = renderer.render(
                ROOT / 'shell/Welcome.qml', png=target,
                stats=SHOTS / f'glass-{page + 1}@{scale}x.json',
                width=800, height=660, scale=scale, warmup=100, milliseconds=100,
                properties={'page': page, 'wallpaperTexture': texture.as_uri(), 'dpr': scale},
                shader_dir=ROOT / '.cache/shell-shaders', ready_property='glassReady')
            clean_log(output)
            with Image.open(target) as frame:
                assert frame.size == (800 * scale, 660 * scale)
                assert max(ImageStat.Stat(frame.convert('RGB')).stddev) > 15
            (SHOTS / f'glass-{page + 1}@{scale}x.log').write_text(output)
        print(f'PASS: real Qt RHI wallpaper glass on every page at {scale}x (surfaceless llvmpipe)')


content_contract()
for scale in (1, 2):
    with tempfile.TemporaryDirectory(prefix='ew-', dir='/tmp') as temporary:
        base = Path(temporary)
        env = prepare(base, scale)
        result = subprocess.run(['qs', '-p', str(base / 'shell/WelcomeTest.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=45)
        output = result.stdout + result.stderr
        (SHOTS / f'interactions@{scale}x.log').write_text(output)
        assert result.returncode == 0 and 'WELCOME_TEST_RESULT 2 0' in output, output
        clean_log(output)
        state_file = base / 'state/emaki/welcome.json'
        assert json.loads(state_file.read_text()) == {'version': 1, 'seen': True}
        assert json.loads((base / 'niri-args.json').read_text()) == ['msg', 'action', 'show-hotkey-overlay']
        for size, dimensions in (('page', (800, 660)), ('small', (560, 420))):
            for page in range(1, 4):
                for suffix in ('',) if size == 'page' else ('-top', '-bottom'):
                    path = SHOTS / f'{size}-{page}{suffix}@{scale}x.png'
                    with Image.open(path) as frame:
                        assert frame.size == tuple(v * scale for v in dimensions), (path, frame.size)
                        assert max(ImageStat.Stat(frame.convert('RGB')).stddev) > 15, path
        print(f'PASS: every page at {scale}x, small-window scroll, Tab/arrows/Enter/Esc, mouse, close and reopen')
        original = state_file.read_bytes()
        stamp = state_file.stat().st_mtime_ns
        ipc_cases(base, env, expected_seen=True, expected_opened=False)
        assert state_file.read_bytes() == original and state_file.stat().st_mtime_ns == stamp
        print(f'PASS: {scale}x restart stays closed; real welcome present IPC reopens; help failure is visible')
        state_file.write_text('{broken')
        ipc_cases(base, env, expected_seen=True, expected_opened=True)
        assert json.loads(state_file.read_text())['seen'] is True
        ipc_cases(base, dict(env, WELCOME_READY='0'), expected_seen=True, expected_opened=False, queued=True)
        # A file written by a newer shell (kept in /home across a system rollback) counts as
        # seen, whatever it holds, and is never rewritten.
        state_file.write_text(json.dumps({'version': 2, 'tour': 'future'}) + '\n')
        newer = state_file.read_bytes()
        ipc_cases(base, env, expected_seen=True, expected_opened=False)
        assert state_file.read_bytes() == newer
        if scale == 1:
            state_file.unlink()
            state_file.parent.rmdir()
            state_file.parent.write_text('blocked parent')
            ipc_cases(base, env, expected_seen=True, expected_opened=True, save_failed=True)
            state_file.parent.unlink()
            fallback = dict(env)
            fallback.pop('XDG_STATE_HOME')
            ipc_cases(base, fallback, expected_seen=True, expected_opened=True)
            assert json.loads((base / 'home/.local/state/emaki/welcome.json').read_text())['seen']
        assert not list((base / 'config').iterdir()), 'Welcome wrote configuration'
        print(f'PASS: {scale}x damaged state recovery and startup gate; no configuration writes')
with tempfile.TemporaryDirectory(prefix='ewg-', dir='/tmp') as temporary:
    glass_renders(Path(temporary))
print('Welcome screenshots:', SHOTS)
