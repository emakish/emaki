#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the settings surface through its real Qt shader path in isolation."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.environ.get('EMAKI_SETTINGS_VISUAL_OUTPUT', ROOT / '.cache/evidence/settings-visual'))
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
OUTPUT.mkdir(parents=True, exist_ok=True)
account = json.loads(subprocess.check_output(
    [sys.executable, '-B', str(ROOT / 'shell/helpers/system-tools.py')],
    input=json.dumps({'op': 'account-read'}), text=True))
assert account['state'] == 'ready', account
with tempfile.TemporaryDirectory(prefix='settings-visual-') as temporary:
    profile = Path(temporary)
    config = profile / 'config' / 'wpaperd'
    config.mkdir(parents=True)
    (config / 'config.toml').write_text('[default]\npath = ' + json.dumps(str(ROOT / 'art/wallpaper/ring.png')) + '\n')
    wallpaper = json.loads(subprocess.check_output(
        [sys.executable, '-B', str(ROOT / 'shell/helpers/wallpaper.py'), '1240', '810', '1', '', 'sharp'],
        env=dict(os.environ, XDG_CONFIG_HOME=str(profile / 'config'), XDG_CACHE_HOME=str(profile / 'cache')), text=True))
    assert wallpaper['state'] == 'ready', wallpaper
    qml = profile / 'shell'
    shutil.copytree(ROOT / 'shell', qml)
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsVisualTest.qml', qml / 'SettingsVisualTest.qml')
    # Exercise the real page's asynchronous helper protocol with stable details.
    details = dict(version='0.5.0', label='Preview', channel='preview',
                   computer='Emaki Test Computer', processor='Eight-core processor',
                   graphics='Integrated graphics', memory='16 GiB',
                   disk='512 GiB total, 320 GiB free', firmware='UEFI 1.2',
                   kernel='6.12.0', window_manager='niri 25.05')
    binaries = profile / 'bin'
    binaries.mkdir()
    helper = binaries / 'emaki-settings-about'
    helper.write_text('#!' + sys.executable + '\nprint(' + repr(json.dumps(
        dict(schema_version=1, status='ready', details=details))) + ')\n')
    helper.chmod(0o755)
    # The isolated renderer has no installed icon theme; use the shipped asset.
    about = qml / 'SettingsAboutPage.qml'
    about.write_text(about.read_text().replace(
        'Quickshell.iconPath("emaki-welcome")',
        json.dumps((ROOT / 'art/logo/mark.svg').as_uri())))
    platform = qml / 'Platform.qml'
    platform.write_text(platform.read_text().replace(
        'readonly property string dataDir: "/usr/share/emaki"',
        'readonly property string dataDir: ' + json.dumps(str(ROOT))))
    for page in ('appearance', 'displays', 'keyboard', 'wifi', 'about', 'wallpaper', 'panel', 'windows'):
        stats = OUTPUT / (page + '.json')
        log = renderer.render(qml / 'SettingsVisualTest.qml',
                              png=OUTPUT / (page + '.png'), stats=stats,
                              properties={'requestedPage': page}, width=1240, height=810,
                              warmup=700, milliseconds=100, ready_property='ready',
                              shader_dir=ROOT / '.cache/shell-shaders',
                              extra_env={'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                                         'EMAKI_FIXTURE_WALLPAPER': wallpaper['texture'],
                                         'EMAKI_FIXTURE_ACCOUNT': json.dumps(account),
                                         'EMAKI_FIXTURE_PICTURE': str(ROOT / 'art/wallpaper/fallback.png'),
                                         'QT_QUICK_CONTROLS_STYLE': 'Basic'})
        (OUTPUT / (page + '.log')).write_text(log)
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                      'Cannot assign', 'Cannot create delegate', 'Required property', 'Failed to deserialize', 'Failed to find shader'):
            assert error not in log, log
        result = json.loads(stats.read_text())
        assert result['readiness_reached'] and 'llvmpipe' in result['renderer'], result
        state = result['fixture_end']
        assert state['graphics_api'] == 3, state
        expected = page if page in ('displays', 'keyboard', 'wifi', 'wallpaper', 'panel', 'windows', 'about') else 'panel'
        assert state['actual_page'] == expected, state
        if page == 'about':
            assert state['about_details'] == details, state
            assert 'Could not load icon' not in log, log
        assert state['keyboard_connected'], state
        assert (OUTPUT / (page + '.png')).stat().st_size > 1000
        print(f'PASS: {page}: Qt shader capture; visible page {expected}')
