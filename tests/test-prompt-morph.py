#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render real clock/prompt frames and exercise production output ownership offscreen."""
import json
import os
from pathlib import Path
import resource
import re
import shutil
import subprocess

from PIL import Image, ImageChops
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / '.cache/evidence/n81p/prompt'


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    qs = shutil.which('qs')
    assert qs, 'qs is required'
    source = (ROOT / 'shell/PolkitPromptSurface.qml').read_text()
    assert 'PanelWindow {' in source and 'emaki-auth-prompt' in source, \
        'Authentication must retain the reserved compositor surface and its input isolation'
    if DEST.exists():
        shutil.rmtree(DEST)
    for part in ('cache', 'config', 'state', 'data', 'tmp', 'shots', 'runtime'):
        (DEST / part).mkdir(parents=True, mode=0o700)
    shutil.copytree(ROOT / 'shell', DEST / 'qml')
    baseline = os.environ.get('EMAKI_PROMPT_BASELINE_FILES', '')
    for name in filter(None, baseline.split(',')):
        assert name in ('PolkitPromptSurface.qml', 'PolkitPromptBody.qml',
                        'PolkitPromptCard.qml', 'ShellScene.qml')
        previous = subprocess.run(['git', 'show', '0847049:shell/' + name],
                                  cwd=ROOT, capture_output=True, check=True)
        (DEST / 'qml' / name).write_bytes(previous.stdout)
    # Load the production protected surface in a second native offscreen Window.
    # Only the Wayland role/anchors/screen adapter is replaced; its real backdrop,
    # frameSwapped connection and body bindings remain under test.
    source = (DEST / 'qml/PolkitPromptSurface.qml').read_text()
    source = source.replace('PanelWindow {', 'Window {\n'
                            '    property bool holdFirstFrame: false\n'
                            '    width: scene?.viewportWidth ?? 1280\n'
                            '    height: scene?.viewportHeight ?? 800')
    source = re.sub(r'^    (screen:|exclusionMode:|WlrLayershell\.).*\n', '',
                    source, flags=re.MULTILINE)
    source = re.sub(r'    anchors \{[^}]*\}\n', '', source)
    source = source.replace('screen: surface.screen', 'screen: null')
    source = source.replace('presented: surface.presented',
                            'presented: surface.presented && !surface.holdFirstFrame')
    (DEST / 'qml/PromptWindow.qml').write_text(source)
    with (DEST / 'qml/qmldir').open('a') as module:
        module.write('\nPromptWindow 1.0 PromptWindow.qml\n')
    shutil.copyfile(ROOT / 'tests/fixtures/PromptMorphTest.qml', DEST / 'qml/shell.qml')
    env = dict(os.environ, HOME=str(DEST), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR='1',
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
               EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_GLASS_RENDERER='canvas',
               EMAKI_FIXTURE_WALLPAPER='', EMAKI_SESSION_START='', EMAKI_SESSION_SKIP_INTRO='',
               EMAKI_REDUCED_MOTION='0', EMAKI_LOCK_REDUCED_MOTION='0',
               PROMPT_MORPH_SHOTS=str(DEST / 'shots'), XDG_RUNTIME_DIR=str(DEST / 'runtime'),
               TMPDIR=str(ROOT / '.cache/tmp'), NIRI_SOCKET='', QT_LOGGING_RULES='*.debug=false',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(DEST / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(DEST / 'no-system'))
    Path(env['TMPDIR']).mkdir(parents=True, exist_ok=True)
    for kind in ('CACHE', 'CONFIG', 'STATE', 'DATA'):
        env['XDG_' + kind + '_HOME'] = str(DEST / kind.lower())
    env['XDG_DATA_DIRS'] = str(DEST / 'data')
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
        env.pop(name, None)
    result = subprocess.run([qs, '-p', str(DEST / 'qml'), '--no-color'], env=env,
                            text=True, capture_output=True, timeout=25, preexec_fn=no_core)
    output = result.stdout + result.stderr
    (DEST / 'qs.log').write_text(output)
    assert result.returncode == 0 and 'PROMPT_MORPH_OK' in output, output
    errors = [line for line in output.splitlines()
              if any(word in line for word in ('ReferenceError', 'TypeError', 'Binding loop', 'ERROR',
                                               'Cannot use same item on different windows',
                                               'can only be queried on the rendering thread',
                                               'not assigned a valid texture provider'))
              and 'quickshell.ipc: Failed to start IPC server' not in line
              and 'quickshell.wayland.buffer.dmabuf: Failed to query render formats: No GL context.' not in line]
    assert not errors, '\n'.join(errors)
    frames = [json.loads(line.split('PROMPT_MORPH_FRAME ', 1)[1])
              for line in output.splitlines() if 'PROMPT_MORPH_FRAME ' in line]
    (DEST / 'frames.json').write_text(json.dumps(frames, indent=2) + '\n')
    assert len(frames) >= 13, frames
    for frame in frames:
        with Image.open(DEST / 'shots' / (frame['name'] + '.png')) as image:
            assert image.size == ((1280, 800) if frame['output'] == 'First' else (1440, 900))
            assert image.convert('RGBA').getchannel('A').getbbox(), frame
    with Image.open(DEST / 'shots/00-bubble.png') as bubble, \
            Image.open(DEST / 'shots/02-prompt.png') as prompt:
        assert ImageChops.difference(bubble.convert('RGB'), prompt.convert('RGB')).getbbox(), \
            'Prompt render must differ from the clock bubble'
    print('PASS: separate-window textures, first frame, clock → prompt → clock, deferred cancel/timeout notices, queued providers, lock and output migration')
    print(f'Real offscreen frames and geometry: {DEST}')
    print('Not proven offscreen: native authentication, Wayland exclusive focus, fullscreen stacking or GPU glass.')


if __name__ == '__main__':
    main()
