# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise hardware transcripts through the production offscreen window."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


UI = Path(__file__).resolve().parents[1]


class HardwareNotices(unittest.TestCase):
    def test_hardware_transcripts(self):
        fixture = json.loads((UI / 'tests/fixtures/hardware.json').read_text())
        fixture['inventory'] = json.loads((UI / 'tests/transcripts/render.json').read_text())['inventory']
        with tempfile.TemporaryDirectory(prefix='ih-') as temporary:
            root = Path(temporary)
            shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
            entry = (UI / 'tests/HardwareTest.qml').read_text().replace(
                '({}) // HARDWARE_FIXTURE', '(' + json.dumps(fixture) + ')')
            entry = entry.replace('".." as UI', '"." as UI').replace(
                '"../Protocol.js" as Protocol', '"Protocol.js" as Protocol')
            (root / 'ui/hardware-test.qml').write_text(entry)
            for filename in (root / 'ui').glob('*.qml'):
                filename.write_text(filename.read_text().replace(
                    '"file:///usr/share/emaki/shell"', '"file://' + str(UI.parents[1] / 'shell') + '"'))
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1')
            for name in ['runtime', 'cache', 'config', 'state', 'data']:
                (root / name).mkdir(mode=0o700)
                env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(root / name)
            for name in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
                env.pop(name, None)
            result = subprocess.run(['qs', '-p', str(root / 'ui/hardware-test.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=20)
            log = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, log)
            self.assertIn('HARDWARE_OK', log)
            for diagnostic in ['ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign']:
                self.assertNotIn(diagnostic, log)


if __name__ == '__main__':
    unittest.main()
