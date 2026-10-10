#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sound settings with tracked fake devices and service echo/rejection mutants."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'shell/SoundSettingsBackend.qml').read_text()
MUTANTS = {
    'wrong-input-direction': ('backend.act(input ? "mic-volume" : "volume", percent)',
                              'backend.act("volume", percent)'),
    'swallowed-rejection': ('if (!ready || !attempt.check())', 'if (false)'),
    'ended-stream-success': ('Array.from(streams).some(s => s.id === id && s.audio === stream.audio) && check()', 'check()'),
    'mute-always-toggles': ('if (node.audio.muted !== muted)', 'if (true)'),
    'meter-leak': ('meterOwner.settingsMicMeter = active;', 'meterOwner.settingsMicMeter = true;'),
    'stale-device-success': ('(input ? source : sink) === node && check()', 'check()'),
    'disabled-confirmation-timer': ('running: sound.busy', 'running: false'),
}
with tempfile.TemporaryDirectory(prefix='settings-sound-') as temporary:
    profile = Path(temporary)
    qml = profile / 'qml'
    qml.mkdir()
    for name in ('home', 'config', 'data', 'cache', 'state'):
        (profile / name).mkdir(mode=0o700)
    shutil.copyfile(ROOT / 'shell/SystemBackend.qml', qml / 'SystemBackend.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SoundSettingsBackendTest.qml', qml / 'shell.qml')
    env = dict(os.environ, HOME=str(profile / 'home'), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               XDG_RUNTIME_DIR=str(runtime_path(profile)), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_PLUGIN_PATH', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
        env.pop(name, None)

    def run(source):
        (qml / 'SoundSettingsBackend.qml').write_text(source)
        try:
            result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                                    text=True, capture_output=True, timeout=8)
        except subprocess.TimeoutExpired as error:
            raise AssertionError((error.stdout, error.stderr)) from error
        return result.returncode, result.stdout + result.stderr

    code, log = run(SOURCE)
    assert code == 0 and 'SOUND_SETTINGS_RESULT 0' in log, log
    assert not any(word in log for word in ('TypeError', 'ReferenceError', 'Binding loop')), log
    for name, (before, after) in MUTANTS.items():
        assert before in SOURCE, name
        code, log = run(SOURCE.replace(before, after))
        assert 'SOUND_SETTINGS_RESULT' in log and 'SOUND_SETTINGS_FAIL' in log, (name, log)
        assert 'SOUND_SETTINGS_RESULT 0' not in log, name
print('PASS: Sound settings device selection, automatic defaults, independent volumes/mutes, stream loss, meter ownership, rejection and seven mutants')
