#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise installed-session health checks without a VM or guest access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('boot_health', ROOT / 'tests/vm/iso-boot-check.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)


class HealthTests(unittest.TestCase):
    def run_boot(self, broken=None):
        calls = []
        logged_in = False
        clock = 0

        def tick():
            nonlocal clock
            clock += 1
            return clock

        def run(argv, **kwargs):
            nonlocal logged_in
            command = argv[-1]
            calls.append(command)
            output, code = '', 0
            if 'iso-guest-login.py ' in command:
                logged_in = True
            elif '--failed' in command:
                manager = 'user' if '--user' in command else 'system'
                if logged_in and broken == manager:
                    output = 'broken.service loaded failed failed\n'
                if logged_in and broken == manager + '-query':
                    code = 1
            elif 'journalctl -p err' in command:
                if logged_in and broken == 'journal':
                    output = 'Desktop component could not start\n'
            elif 'pacman -Qi' in command:
                output = 'Version : ' + (ROOT / 'iso/VERSION').read_text().strip() + '-1\n'
            elif 'pacman -Qo' in command:
                output = 'owned one\nowned two\n'
            elif 'nmcli' in command:
                output = 'connected:full\n'
            elif 'pacman-conf' in command:
                output = 'https://example.invalid/packages\n'
            elif 'readlink /etc/os-release' in command:
                output = '../usr/lib/emaki/os-release\nPRETTY_NAME="Emaki"\nID=arch\n'
            elif 'findmnt' in command:
                output = 'ext4\n'
            elif 'grep -c Emaki' in command:
                output = '2\n'
            return subprocess.CompletedProcess(argv, code, output.encode(), b'')

        with tempfile.TemporaryDirectory(dir=EVIDENCE) as directory, \
                patch.object(boot.sys, 'argv', ['iso-boot-check.py', 'erase-ext4', '--dir', directory]), \
                patch.object(boot.subprocess, 'run', side_effect=run), \
                patch.object(boot.monitor, 'command', return_value=''), \
                patch.object(boot, 'valid_boot_frame', return_value=True), \
                patch.object(boot.time, 'monotonic', side_effect=tick), \
                patch.object(boot.time, 'sleep'), \
                contextlib.redirect_stdout(io.StringIO()):
            result = boot.main()
            summary = next(Path(directory).glob('runs/*/summary.txt')).read_text()
        return result, calls, summary

    def test_both_managers_and_journal_are_observed_after_login(self):
        result, calls, _ = self.run_boot()
        self.assertEqual(result, 0)
        login = next(i for i, command in enumerate(calls) if 'iso-guest-login.py ' in command)
        probes = [(i, command) for i, command in enumerate(calls)
                  if '--failed' in command or 'journalctl -p err' in command]
        self.assertEqual(len(probes), 3)
        self.assertTrue(all(i > login for i, _ in probes))
        self.assertTrue(any('--user --failed' in command for _, command in probes))

    def test_post_login_failures_and_unavailable_managers_fail(self):
        for broken, label in [('system', 'failed-units'), ('user', 'failed-user-units'),
                              ('system-query', 'failed-units'), ('user-query', 'failed-user-units'),
                              ('journal', 'journal-errors')]:
            with self.subTest(broken=broken):
                result, _, summary = self.run_boot(broken)
                self.assertEqual(result, 1)
                self.assertIn('BAD: ' + label, summary)


if __name__ == '__main__':
    unittest.main()
