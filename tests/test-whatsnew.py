#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real QML show-once cases and translated pages in private offscreen profiles."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / '.cache/evidence/whatsnew'


def release_contract():
    script = (ROOT / 'tests/qml-translation.js').read_text() + '\n' + (ROOT / 'shell/WhatsNewContent.js').read_text().replace('.pragma library', '')
    versions = ['1.0', '1.0.0', '1.0.19', '0.12', '0.12.9', '1.1.0', '', '1.00.0', '10.0.0', '1.0.0-beta']
    script += '\nconsole.log(JSON.stringify({title: title(), pages: pages(), available: ' + json.dumps(versions) + '.map(available)}));'
    content = json.loads(subprocess.check_output(['node', '-e', script], text=True))
    assert content['title'] == "What's new in Emaki 1.0"
    assert content['available'] == [True, True, True, False, False, False, False, False, False, False]
    pages = content['pages']
    assert len(pages) == 3 and all(page['id'] == 'release' for page in pages)
    assert [page.get('action') for page in pages] == [None, None, None]
    assert [page['title'] for page in pages] == ['Emaki 1.0, the first beta', 'A lighter launcher', 'Fixes']
    assert pages[-1]['title'] == 'Fixes' and len(pages[-1]['text'].splitlines()) == 2
    assert not any('wallpaper' in page['title'] + page['text'] for page in pages)
    assert pages[1]['text'].startswith('The launcher keeps nothing loaded while it is closed and opens where you left it.')
    print('PASS: three release pages, no page action and 1.0 series boundary', flush=True)


def prepare(base):
    shutil.copytree(ROOT / 'shell', base / 'shell')
    fixture = (ROOT / 'tests/fixtures/WhatsNewTest.qml').read_text()
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    (base / 'shell/WhatsNewTest.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    (base / 'state/emaki').mkdir()
    (base / 'release').write_text('VERSION=1.0.0\n')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
               QT_SCALE_FACTOR='1', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'),
               EMAKI_LIVE_SESSION='0', EMAKI_LANGUAGE='en', LC_ALL='C.UTF-8',
               EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
               QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               WHATSNEW_RELEASE=str(base / 'release'), WHATSNEW_SHOTS=str(SHOTS))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                'QT_SCREEN_SCALE_FACTORS', 'QT_AUTO_SCREEN_SCALE_FACTOR', 'EMAKI_SETTINGS_PROFILE'):
        env.pop(key, None)
    return env


def run(base, env, case, label):
    result = subprocess.run(['qs', '-p', str(base / 'shell/WhatsNewTest.qml'), '--no-color'],
                            env=dict(env, WHATSNEW_CASE=case), capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    (SHOTS / (label + '.log')).write_text(output)
    assert result.returncode == 0 and 'WHATSNEW_TEST_RESULT 2 0' in output, output
    for error in ('ReferenceError:', 'TypeError:', 'Binding loop', 'Unable to assign', 'Cannot assign', 'FAIL!'):
        assert error not in output, output
    for line in output.splitlines():
        if 'WHATSNEW_CONTENT ' not in line:
            continue
        record = json.loads(line.split('WHATSNEW_CONTENT ', 1)[1])
        (SHOTS / (record['name'] + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        with Image.open(SHOTS / (record['name'] + '.png')) as frame:
            assert frame.size == (int(env.get('WHATSNEW_WIDTH', '800')), int(env.get('WHATSNEW_HEIGHT', '660'))), frame.size
            for region in record['regions']:
                box = tuple(round(value) for value in (region['x'], region['y'], region['x'] + region['width'], region['y'] + region['height']))
                histogram = frame.crop(box).convert('L').histogram()
                background = max(range(256), key=lambda value: histogram[value])
                ink = sum(count for value, count in enumerate(histogram) if abs(value - background) > 60)
                assert ink >= max(4, len(region['text'].strip())), (record['name'], region['text'], 'Missing rendered ink')
    print('PASS:', label, flush=True)


def state(base):
    return json.loads((base / 'state/emaki/whatsnew.json').read_text())


def main():
    release_contract()
    SHOTS.mkdir(parents=True, exist_ok=True)
    assert (ROOT / '.cache/i18n-native/qml/Emaki/I18n/qmldir').exists(), 'Run make build-i18n first'
    for language in ('es', 'pt_BR', 'de', 'fr', 'ru'):
        assert (ROOT / 'i18n/locale' / ('shell_' + language + '.qm')).exists(), 'Compile translation catalogues first'
    with tempfile.TemporaryDirectory(prefix='ewn-', dir='/tmp') as temporary:
        base = Path(temporary)
        env = prepare(base)
        run(base, env, 'fresh', 'fresh-install')
        assert state(base)['seenVersion'] == '1.0.0'
        run(base, env, 'second', 'fresh-second-login')
    with tempfile.TemporaryDirectory(prefix='ewn-', dir='/tmp') as temporary:
        base = Path(temporary)
        env = prepare(base)
        (base / 'state/emaki/welcome.json').write_text('{"version":1,"seen":true}\n')
        (base / 'state/emaki/whatsnew.json').write_text('{"version":1,"seenVersion":"0.12.0"}\n')
        run(base, env, 'update', 'first-update-escape')
        assert state(base)['seenVersion'] == '1.0.0'
        run(base, env, 'second', 'second-login-after-escape')
        (base / 'release').write_text('VERSION=1.0.1\n')
        run(base, env, 'patch', 'patch-release')
        # The same series was already seen: quiet, and the record stays on the series.
        assert state(base)['seenVersion'] == '1.0.0'
        run(base, env, 'patch', 'patch-second-login')

    # Someone who skips 1.0.0 and lands on 1.0.1 from 0.12 still sees the 1.0 pages once.
    with tempfile.TemporaryDirectory(prefix='ewn-', dir='/tmp') as temporary:
        base = Path(temporary)
        env = prepare(base)
        (base / 'state/emaki/welcome.json').write_text('{"version":1,"seen":true}\n')
        (base / 'release').write_text('VERSION=1.0.1\n')
        run(base, env, 'update', 'skipped-1.0.0-update')
        assert state(base)['seenVersion'] == '1.0.1'
    for label, saved, release in (
            ('future-schema', {'version': 2, 'seenVersion': '1.0.0', 'future': True}, '1.0.0'),
            ('rollback', {'version': 1, 'seenVersion': '1.1.0'}, '1.0.0'),
            ('missing-release', None, None)):
        with tempfile.TemporaryDirectory(prefix='ewn-', dir='/tmp') as temporary:
            base = Path(temporary)
            env = prepare(base)
            (base / 'state/emaki/welcome.json').write_text('{"version":1,"seen":true}\n')
            path = base / 'state/emaki/whatsnew.json'
            if saved:
                path.write_text(json.dumps(saved) + '\n')
            if release is None:
                (base / 'release').unlink()
            original = path.read_bytes() if path.exists() else None
            run(base, env, 'suppressed', label)
            assert (path.read_bytes() if path.exists() else None) == original
    for language in ('en', 'es', 'pt_BR', 'de', 'fr', 'ru'):
        for theme in ('light', 'dark'):
            for size, width, height in (('normal', 800, 660), ('small', 560, 420)):
                with tempfile.TemporaryDirectory(prefix='ewn-', dir='/tmp') as temporary:
                    base = Path(temporary)
                    env = prepare(base)
                    env.update(EMAKI_LANGUAGE=language, WHATSNEW_THEME=theme,
                               WHATSNEW_SIZE=size, WHATSNEW_WIDTH=str(width), WHATSNEW_HEIGHT=str(height))
                    (base / 'state/emaki/welcome.json').write_text('{"version":1,"seen":true}\n')
                    label = language + '-' + theme + '-' + size
                    run(base, env, 'render', label)
                    assert state(base)['seenVersion'] == '1.0.0'
                    run(base, env, 'second', label + '-after-done')
    print('Screenshots and clipping diagnostics:', SHOTS)


if __name__ == '__main__':
    main()
