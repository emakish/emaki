#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop suite transport tests; no guest or graphical session is started."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/vm'))
spec = importlib.util.spec_from_file_location('glass_target', ROOT / 'tests/vm/glass-target.py')
glass = importlib.util.module_from_spec(spec)
spec.loader.exec_module(glass)


class GlassTargetTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='glass-target-')
        self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.fixture = self.base / 'plan.json'
        self.fixture.write_text(json.dumps({'config': {'user': {
            'login': 'acceptance', 'password': 'testing $quoted; secret'}},
            'transport': {'kind': 'ssh', 'dir': str(self.base / 'vm'),
                          'ssh_port': 2297, 'key': str(self.base / 'key'),
                          'known_hosts': str(self.base / 'hosts')}}))
        self.guest = glass.target(['--fixture', str(self.fixture)])
        self.calls = []

    def transport(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout='kvm\n', stderr='')

    def test_login_uses_fixture_transport_and_separate_stdin_secrets(self):
        with patch.object(glass.subprocess, 'run', side_effect=self.transport):
            self.assertEqual(glass.login(self.guest, 'niri-emaki-session'), 0)
        argv, options = self.calls[0]
        self.assertIn('2297', argv)
        self.assertIn('acceptance@127.0.0.1', argv)
        self.assertIn('UserKnownHostsFile=' + str(self.base / 'hosts'), argv)
        self.assertIn('guest-login.py acceptance niri-emaki-session', argv[-1])
        self.assertNotIn(self.guest.password, ' '.join(argv))
        self.assertEqual(options['input'], (self.guest.password + '\n' + self.guest.password).encode())

    def test_installed_wallpaper_comes_only_from_guest_package(self):
        with patch.object(glass.subprocess, 'run', side_effect=self.transport):
            self.assertEqual(glass.wallpaper(self.guest), 0)
        argv, options = self.calls[0]
        self.assertIn('/usr/share/emaki/wallpaper/ring.png', argv[-1])
        self.assertNotIn('input', options)

    def test_helper_sends_only_test_instrumentation_and_password_stdin(self):
        with patch.object(glass.subprocess, 'run', side_effect=self.transport):
            self.assertEqual(glass.helper(self.guest, 'guest-strip-click.sh', ['glass']), 0)
        self.assertEqual(len(self.calls), 2)
        argv, options = self.calls[1]
        self.assertIn('EMAKI_VM_PASSWORD_STDIN=1', argv[-1])
        self.assertNotIn(self.guest.password, ' '.join(argv))
        self.assertEqual(options['input'], (self.guest.password + '\n').encode())
        self.assertEqual(self.calls[0][1]['input'],
                         (ROOT / 'tests/vm/guest-strip-click.sh').read_bytes())

    def test_upload_failure_does_not_run_helper(self):
        with patch.object(glass.subprocess, 'run', return_value=subprocess.CompletedProcess([], 23)):
            self.assertEqual(glass.helper(self.guest, 'guest-strip-click.sh', ['glass']), 23)

    def test_development_defaults_keep_account_and_passwordless_sudo(self):
        guest = glass.target([])
        with patch.object(glass.subprocess, 'run', side_effect=self.transport):
            glass.login(guest, 'niri-session')
        argv, options = self.calls[0]
        self.assertIn('arch@127.0.0.1', argv)
        self.assertTrue(argv[-1].startswith('sudo -n -- '))
        self.assertEqual(options['input'], b'arch')

    def test_capture_uses_fixture_vm_and_overrides(self):
        with patch.dict(os.environ, {glass.ARGUMENTS: json.dumps(['--fixture', str(self.fixture)])}), \
                patch.object(glass.subprocess, 'run', side_effect=self.transport):
            glass.main(['shot', str(self.base / 'frame.png'), '--dir', str(self.base / 'other')])
        argv, _ = self.calls[0]
        self.assertIn(str(self.base / 'other'), argv)
        self.assertNotIn(self.guest.password, ' '.join(argv))

    def test_launch_inherits_only_options_and_probes_vm_before_session(self):
        with patch.object(glass.subprocess, 'run', side_effect=self.transport):
            glass.main(['launch', str(self.base / 'evidence'), '--fixture', str(self.fixture)])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0][-1], 'systemd-detect-virt --vm')
        self.assertNotIn(self.guest.password, json.dumps(self.calls[1][1]['env']))
        self.assertEqual(json.loads(self.calls[1][1]['env'][glass.ARGUMENTS]),
                         ['--fixture', str(self.fixture)])

    def test_guest_capture_preserves_discovered_environment(self):
        helper = (ROOT / 'tests/vm/guest-frames.sh').read_text()
        self.assertNotIn('/run/user/1000', helper)
        for name in ('XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            self.assertIn('${' + name + ':-', helper)


if __name__ == '__main__':
    unittest.main(verbosity=2)
