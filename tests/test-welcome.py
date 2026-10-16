#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Welcome input, renders, persistence and real IPC in private offscreen profiles."""
from runtime_fixture import runtime_path
import configparser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

from PIL import Image, ImageOps
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / '.cache/evidence/welcome-shots'
SHOTS.mkdir(parents=True, exist_ok=True)


# The old orange demo "E": the flat mark (art/logo/mark.svg) and the same slanted E in any colour.
DEMO_MARK = ('M3 22L7.161 22L12.896 2L8.735 2Z', '#e2733f', 'mark.svg', 'mark-large.svg', 'kind: "logo"')


def content_contract():
    script = (ROOT / 'shell/WelcomeContent.js').read_text().replace('.pragma library', '')
    pages = json.loads(subprocess.check_output(
        ['node', '-e', script + '\nconsole.log(JSON.stringify(pages.map(page => Object.assign({}, page, {allBinds: binds(page)}))));'], text=True))
    # A short tour: a few pages, one idea each, a big title and a line or two.
    assert [page['id'] for page in pages] == ['welcome', 'super', 'move', 'system', 'keys'], pages
    for page in pages:
        assert page['title'] and len(page['title']) <= 30, page['title']
        assert page['text'] and len(page['text']) <= 120 and page['text'].count('. ') <= 1, page['text']
    assert pages[0]['title'] == 'Welcome to Emaki' and pages[0]['text'].startswith('Emaki is ')
    super_page = pages[1]
    assert 'Windows key' in super_page['text'] and 'Command on a Mac' in super_page['text'], super_page
    words = json.dumps(pages)
    for needed in ('Programs & Updates', 'Settings', 'Report a problem', 'Super + /', 'Super + Ctrl + H'):
        assert needed in words, needed
    config = (ROOT / 'niri/default.kdl').read_text()
    matches = list(re.finditer(
        r'^[ \t]*(Mod\+\S+)[ \t]+[^\n{]*\{[ \t]*([^}]+)\}', config, re.M))
    binds = {match[1]: match[2].strip() for match in matches}
    assert len(binds) == len(matches), 'duplicate shipped Welcome bindings'
    shown = {}
    for page in pages:
        for binding, command in page['allBinds'].items():
            assert binding in binds, ('Welcome shows a key niri does not bind', binding)
            assert binds[binding].startswith(command), (binding, command, binds.get(binding))
            shown[binding] = page['id']
    # The keys the tour must teach, the 0.9 ones among them.
    for binding in ('Mod+Left', 'Mod+Right', 'Mod+Up', 'Mod+Down', 'Mod+O', 'Mod+D', 'Mod+Return', 'Mod+Q',
                    'Mod+E', 'Mod+Slash', 'Mod+Ctrl+H'):
        assert binding in shown, binding
    assert '"emaki-shell" "call" "launcher" "toggle"' in binds['Mod+D']
    assert 'mod-key "Super"' in config
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(ROOT / 'shell/emaki-welcome.desktop')
    assert desktop['Desktop Entry']['Name'] == 'Welcome to Emaki'
    assert desktop['Desktop Entry']['Exec'] == 'emaki-shell call welcome present'
    assert desktop['Desktop Entry']['Icon'] == 'emaki-welcome'
    main = (ROOT / 'shell/shell.qml').read_text()
    assert 'enabled: root.valid && !root.headless' in main
    assert 'ready: !startup.coverActive' in main
    assert 'onOpening: outputs.closeAll()' in main
    # All shortcuts opens the list Super + / opens, not niri's overlay.
    assert 'openShortcutList: () => root.scene?.openKeyboard("shortcuts") ?? false' in main
    assert binds['Mod+Slash'] == 'spawn "emaki-shell" "call" "keyboard" "open" "shortcuts";'
    assert 'show-hotkey-overlay' not in (ROOT / 'shell/WelcomeController.qml').read_text()
    print('PASS: a five-page tour; every key it shows is a shipped binding; launcher and startup hooks')


def no_demo_logo():
    """No demo "E" anywhere in Welcome: its window, its texts, its launcher icon."""
    for path in sorted((ROOT / 'shell').glob('Welcome*')) + [ROOT / 'shell/emaki-welcome.desktop']:
        text = path.read_text()
        for mark in DEMO_MARK:
            assert mark not in text, (path.name, mark)
    welcome = (ROOT / 'shell/Welcome.qml').read_text()
    assert 'import "LockWordmarkData.js" as Data' in welcome and 'objectName: "welcome-wordmark"' in welcome
    makefile = (ROOT / 'Makefile').read_text()
    assert 'install -Dm644 art/icons/emaki-welcome.svg $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg' in makefile
    assert 'art/logo/mark.svg $(ICONS)/hicolor/scalable/apps/emaki-welcome.svg' not in makefile
    icon = (ROOT / 'art/icons/emaki-welcome.svg').read_text()
    for mark in DEMO_MARK:
        assert mark not in icon, mark
    # The installed icon is the shell's live drawing in the default accent.
    tokens = tomllib.loads((ROOT / 'tokens.toml').read_text())
    accent = tokens['accents'][tokens['defaults']['accent']]
    for key in ('base', 'strong', 'soft'):
        assert 'fill="#' + accent[key] + '"' in icon, key
    print('PASS: no demo "E" in the Welcome window, its texts or its launcher icon')


def prepare(base, scale):
    shutil.copytree(ROOT / 'shell', base / 'shell')
    for fixture in ('WelcomeTest.qml', 'WelcomeIpcHarness.qml'):
        text = (ROOT / 'tests/fixtures' / fixture).read_text()
        text = text.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
        text = text.replace('../../shell/WelcomeContent.js', 'WelcomeContent.js')
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
    env = dict(os.environ, EMAKI_LIVE_SESSION='0', HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
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


def ipc_cases(base, env, *, expected_seen, expected_opened, save_failed=False, queued=False, live=False):
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
            if live:
                assert reply == 'unavailable', reply
                assert not status()['opened']
                assert not (base / 'state/emaki/welcome.json').exists()
                return
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
            call('welcomeTest', 'shortcuts')
            wait_for(lambda: status().get('listOpened') == 1)
            assert status()['error'] == '', status()
            call('welcomeTest', 'failShortcuts')
            call('welcomeTest', 'shortcuts')
            wait_for(lambda: status()['error'])
            assert status()['error'] == 'Could not open the shortcut list. Try Super + /.', status()
            assert status()['opened']
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            log.seek(0)
            clean_log(log.read())


def content_regions(output):
    return {record['name']: record['regions'] for record in
            (json.loads(line.split('WELCOME_CONTENT ', 1)[1])
             for line in output.splitlines() if 'WELCOME_CONTENT ' in line)}


def rendered_copy(path, dimensions, scale, regions):
    """Require text ink in every semantically checked, unclipped text region: pixels that stand
    clearly apart from the region's own background, light text on dark or dark on light."""
    assert regions, (path, 'No visible copy regions')
    with Image.open(path) as frame:
        assert frame.size == tuple(value * scale for value in dimensions), (path, frame.size)
        for region in regions:
            box = tuple(round(value * scale) for value in (
                region['x'], region['y'], region['x'] + region['width'], region['y'] + region['height']))
            histogram = frame.crop(box).convert('L').histogram()
            background = max(range(256), key=lambda value: histogram[value])
            ink = sum(count for value, count in enumerate(histogram) if abs(value - background) > 60)
            assert ink >= max(4, len(region['text'].strip())) * scale * scale, (path, region['text'], 'Missing rendered text ink')


def content_mutations(base, env):
    """Real fixture runs must reject missing, invisible and clipped page content."""
    view = base / 'shell/Welcome.qml'
    original = view.read_text()
    mutations = (
        ('missing title', 'text: welcome.current.title', 'text: ""', 'Expected welcome copy:'),
        ('hidden body', 'id: body', 'id: body\n        opacity: 0', 'Welcome copy is opaque:'),
        ('clipped title', 'text: welcome.current.title', 'x: -100\n                text: welcome.current.title', 'Welcome copy stays inside'),
        ('demo logo', 'id: band', 'id: band\n        Icon {\n            kind: "logo"\n        }', 'Welcome draws no letter mark'),
        ('arrows do not turn', 'turn(event.key === Qt.Key_Left ? -1 : 1);', '', 'Welcome comparison'),
        ('card text overflows', 'height: places.stacked ? Math.max(74, words.implicitHeight + 24) : 16 + 48 + 12 + places.tallest + 18', 'height: places.stacked ? 40 : 120', 'Welcome copy stays inside the picture'),
    )
    try:
        for name, old, new, failure in mutations:
            assert old in original
            view.write_text(original.replace(old, new, 1))
            result = subprocess.run(['qs', '-p', str(base / 'shell/WelcomeTest.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
            assert failure in output and 'WELCOME_TEST_RESULT 1 1' in output, (name, output)
            (SHOTS / ('mutation-' + name.replace(' ', '-') + '.log')).write_text(output)
            # Each launch must begin with an unseen welcome profile.
            (base / 'state/emaki/welcome.json').unlink(missing_ok=True)
    finally:
        view.write_text(original)
    print('PASS: missing, invisible and clipped welcome content fail the real fixture')


def glass_renders(cache, regions_by_scale):
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
        for page in range(5):
            target = SHOTS / f'glass-{page + 1}@{scale}x.png'
            output = renderer.render(
                ROOT / 'shell/Welcome.qml', png=target,
                stats=SHOTS / f'glass-{page + 1}@{scale}x.json',
                width=800, height=660, scale=scale, warmup=100, milliseconds=100,
                properties={'page': page, 'wallpaperTexture': texture.as_uri(), 'dpr': scale},
                shader_dir=ROOT / '.cache/shell-shaders', ready_property='glassReady')
            clean_log(output)
            rendered_copy(target, (800, 660), scale, regions_by_scale[scale][f'page-{page + 1}'])
            (SHOTS / f'glass-{page + 1}@{scale}x.log').write_text(output)
        print(f'PASS: real Qt RHI wallpaper glass on every page at {scale}x (surfaceless llvmpipe)')



def content_only():
    """Exercise page content without using the blocked Unix IPC transport."""
    content_contract()
    no_demo_logo()
    for scale in (1, 2):
        with tempfile.TemporaryDirectory(prefix='ewf-', dir='/tmp') as temporary:
            base = Path(temporary)
            env = prepare(base, scale)
            result = subprocess.run(['qs', '-p', str(base / 'shell/WelcomeTest.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
            (SHOTS / f'content@{scale}x.log').write_text(output)
            assert result.returncode == 0 and 'WELCOME_TEST_RESULT 2 0' in output, output
            regions = content_regions(output)
            assert len(regions) == 20, regions.keys()
            for name, copy in regions.items():
                dimensions = (560, 420) if name.startswith('small-') else (800, 648) if name.startswith('1366x768-') else (800, 660)
                rendered_copy(SHOTS / f'{name}@{scale}x.png', dimensions, scale, copy)
            # A nonuniform background with all text removed used to pass.
            blank = SHOTS / f'no-copy@{scale}x.png'
            Image.linear_gradient('L').resize((800 * scale, 660 * scale)).point(lambda value: 110 + value // 2).save(blank)
            try:
                rendered_copy(blank, (800, 660), scale, regions['page-1'])
            except AssertionError as error:
                assert 'Missing rendered text ink' in str(error), error
            else:
                raise AssertionError('A background without welcome copy passed')
        print(f'PASS: welcome content, clipping and text ink at {scale}x, including 1366x768 geometry')
    with tempfile.TemporaryDirectory(prefix='ewm-', dir='/tmp') as temporary:
        base = Path(temporary)
        content_mutations(base, prepare(base, 1))


def main():
    content_contract()
    no_demo_logo()
    regions_by_scale = {}
    for scale in (1, 2):
        with tempfile.TemporaryDirectory(prefix='ew-', dir='/tmp') as temporary:
            base = Path(temporary)
            env = prepare(base, scale)
            ipc_cases(base, dict(env, EMAKI_LIVE_SESSION="1"), expected_seen=False, expected_opened=False, live=True)
            print(f"PASS: {scale}x live session neither opens welcome nor writes progress, including explicit IPC")
            result = subprocess.run(['qs', '-p', str(base / 'shell/WelcomeTest.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=45)
            output = result.stdout + result.stderr
            (SHOTS / f'interactions@{scale}x.log').write_text(output)
            assert result.returncode == 0 and 'WELCOME_TEST_RESULT 2 0' in output, output
            clean_log(output)
            state_file = base / 'state/emaki/welcome.json'
            assert json.loads(state_file.read_text()) == {'version': 1, 'seen': True}
            assert not (base / 'niri-args.json').exists(), 'All shortcuts must not start niri\'s overlay'
            regions_by_scale[scale] = content_regions(output)
            for size, dimensions in (('page', (800, 660)), ('1366x768', (800, 648)), ('small', (560, 420))):
                for page in range(1, 6):
                    for suffix in ('-top', '-bottom') if size == 'small' else ('',):
                        path = SHOTS / f'{size}-{page}{suffix}@{scale}x.png'
                        rendered_copy(path, dimensions, scale, regions_by_scale[scale][f'{size}-{page}{suffix}'])
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
    with tempfile.TemporaryDirectory(prefix='ewm-', dir='/tmp') as temporary:
        base = Path(temporary)
        content_mutations(base, prepare(base, 1))
    with tempfile.TemporaryDirectory(prefix='ewg-', dir='/tmp') as temporary:
        glass_renders(Path(temporary), regions_by_scale)
    print('Welcome screenshots:', SHOTS)


if __name__ == "__main__":
    if sys.argv[1:] == ["--content-only"]:
        content_only()
    else:
        assert not sys.argv[1:], sys.argv[1:]
        main()
