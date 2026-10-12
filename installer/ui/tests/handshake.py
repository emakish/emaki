#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Require a real source and packaged window to greet the worker on every connection."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / 'installer/ui'


class WindowHandshakeTests(unittest.TestCase):
    def test_source_and_packaged_window(self):
        for packaged in (False, True):
            with self.subTest(packaged=packaged):
                self.check_window(packaged)

    def check_window(self, packaged):
        with tempfile.TemporaryDirectory(prefix='installer-hello-') as temporary, \
                tempfile.TemporaryDirectory(prefix='eih-', dir='/tmp') as runtime:
            # Only short socket paths live here; all payloads and logs obey TMPDIR.
            root = Path(temporary)
            endpoint = str(Path(runtime) / 'worker.sock')
            if packaged:
                src = root / 'src'
                src.mkdir()
                (src / 'emaki-installer').symlink_to(ROOT, target_is_directory=True)
                pkg = root / 'pkg'
                subprocess.run([
                    'bash', '-euc',
                    'startdir=$1; srcdir=$2; pkgdir=$3; '
                    'source "$startdir/PKGBUILD"; package',
                    'package', str(ROOT / 'packaging/emaki-installer'), str(src), str(pkg),
                ], check=True, capture_output=True, text=True, timeout=30)
                ui = pkg / 'usr/share/emaki-installer/ui'
                controller = ui / 'InstallerController.qml'
                source = controller.read_text()
                self.assertNotIn('EMAKI_INSTALLER_SOCKET', source)
                literal = 'path: "/run/emaki-installer/sock"'
                self.assertEqual(source.count(literal), 1)
                # Relocate the privileged endpoint without changing its constant QML form.
                controller.write_text(source.replace(literal, 'path: ' + json.dumps(endpoint)))
            else:
                ui = root / 'ui'
                shutil.copytree(UI, ui, ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))

            for filename in ui.glob('*.qml'):
                filename.write_text(filename.read_text().replace(
                    '"file:///usr/share/emaki/shell"', json.dumps((ROOT / 'shell').as_uri())))
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                       QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=runtime,
                       EMAKI_INSTALLER_SOCKET=str(Path(runtime) / 'wrong.sock') if packaged else endpoint)
            for key in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME'):
                directory = root / key.lower()
                directory.mkdir(mode=0o700)
                env[key] = str(directory)
            for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
                env.pop(key, None)

            with socket.socket(socket.AF_UNIX) as worker, (root / 'window.log').open('w+') as log:
                worker.bind(endpoint)
                worker.listen()
                worker.settimeout(5)
                process = subprocess.Popen(['qs', '-p', str(ui / 'shell.qml'), '--no-color'],
                                           env=env, stdout=log, stderr=log)
                try:
                    for connection in range(3):
                        try:
                            peer, _ = worker.accept()
                            with peer:
                                peer.settimeout(3)
                                data = b''
                                while b'\n' not in data and len(data) < 4096:
                                    chunk = peer.recv(4096)
                                    self.assertTrue(chunk, 'window closed before hello')
                                    data += chunk
                                self.assertTrue(data.endswith(b'\n'), data)
                                hello = json.loads(data)
                                self.assertEqual(hello['type'], 'hello')
                                self.assertEqual(hello['proto'], 1)
                                self.assertTrue(hello['id'])
                                # No duplicate greeting on this connection. Closing the peer
                                # then forces the production reconnect timer and Loader path.
                                peer.settimeout(0.2)
                                with self.assertRaises(TimeoutError):
                                    peer.recv(4096)
                        except TimeoutError:
                            self.fail(f'connection {connection + 1}: window did not connect and send hello')
                    self.assertIsNone(process.poll(), 'window exited during handshake checks')
                except Exception as error:
                    log.flush()
                    log.seek(0)
                    raise AssertionError(f'{error}\n{log.read()}') from error
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
