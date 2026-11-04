#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise overview event lifecycle and real decode/fallback offscreen in isolation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='emaki-overview-video-') as temporary:
    base = Path(temporary)
    for name in ('home', 'config', 'data', 'cache', 'state'):
        (base / name).mkdir(mode=0o700)
    for name in ('OverviewVideo', 'OverviewVideoContent', 'OverviewVideoPlayer'):
        shutil.copyfile(ROOT / 'shell' / (name + '.qml'), base / (name + '.qml'))
    (base / 'NiriService.qml').write_text('import QtQuick\nQtObject { property bool overviewOpen: false }\n')
    (base / 'SettingsBridge.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { property var values: []; property int surfaceCount: 0; property int playingCount: 0 }\n')
    # Count production manager surfaces without requiring a compositor in this unit test.
    (base / 'OverviewVideoSurface.qml').write_text('''import QtQuick
QtObject {
    property var screen
    property string videoPath
    property string stillPath
    property bool playing: false
    onPlayingChanged: SettingsBridge.playingCount += playing ? 1 : -1
    Component.onCompleted: SettingsBridge.surfaceCount++
    Component.onDestruction: {
        SettingsBridge.surfaceCount--;
        if (playing) SettingsBridge.playingCount--;
    }
}
''')
    (base / 'qmldir').write_text('singleton SettingsBridge 1.0 SettingsBridge.qml\n' + ''.join(f'{name} 1.0 {name}.qml\n' for name in ('NiriService', 'OverviewVideo', 'OverviewVideoContent', 'OverviewVideoPlayer', 'OverviewVideoSurface')))
    shutil.copyfile(ROOT / 'tests/fixtures/OverviewVideoTest.qml', base / 'test.qml')
    from PIL import Image
    still = base / 'still.png'
    Image.new('RGB', (64, 36), '#77aacc').save(still)
    video = base / 'tiny.mp4'
    subprocess.run(['nice', '-n', '19', 'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=64x36:r=5:d=0.4', '-threads', '1', '-c:v', 'mpeg4',
                    str(video)], check=True, timeout=20)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               QT_FFMPEG_DECODING_HW_DEVICE_TYPES=',', EMAKI_TEST_VIDEO=str(video), EMAKI_TEST_STILL=str(still),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['nice', '-n', '19', 'qs', '-p', str(base / 'test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=40)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'OVERVIEW_VIDEO_RESULT 0' in output, output
    assert not any(marker in output for marker in ('ReferenceError', 'TypeError', 'Binding loop')), output
    print('PASS: still survives overview close while decoder unloads; real video frames, missing-file fallback and recovery')
