#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restart evidence and once-per-boot dismissal without host state changes."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reminder

BOOT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
OTHER_BOOT = '11111111-2222-3333-4444-555555555555'
TIME = int(datetime.fromisoformat('2026-10-10T10:00:00+00:00').timestamp())


def event(package, old='1', new='2', date='2026-10-10T10:01:00+0000', action='upgraded'):
    return f'[{date}] [ALPM] {action} {package} ({old} -> {new})\n'


class Evidence(unittest.TestCase):
    def test_actual_changes_only_since_boot(self):
        lines = [event('linux'), event('linux-lts'), event('glibc'), event('systemd', action='downgraded'),
                 event('intel-ucode'), event('amd-ucode'), event('linux-firmware'),
                 event('linux', action='reinstalled'), event('systemd', old='2', new='2'),
                 event('linux', date='invalid')]
        self.assertEqual(reminder.changed_packages(lines, TIME, 'linux', 'GenuineIntel'),
                         {'linux', 'glibc', 'systemd', 'intel-ucode'})
        self.assertEqual(reminder.changed_packages([event('linux', date='2026-10-10T09:59:59+0000')],
                                                  TIME, 'linux', ''), set())
        self.assertEqual(reminder.changed_packages([event('linux')], TIME, 'linux-lts', ''), set())
        self.assertEqual(reminder.changed_packages([event('linux')], TIME, '', ''), set())
        self.assertEqual(reminder.changed_packages([event('amd-ucode')], TIME, '', 'AuthenticAMD'), {'amd-ucode'})

    def test_module_identity_and_strict_stock_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for release, package in [('6.16.8-arch3-1', 'linux'), ('6.12.55-1-lts', 'linux-lts'),
                                     ('6.17.1-zen1-1-zen', 'linux-zen'), ('6.16.8-hardened1-1-hardened', 'linux-hardened'),
                                     ('6.16.8-rt7-1-rt', 'linux-rt'), ('6.12.55-1-rt-lts', 'linux-rt-lts'),
                                     ('6.16.8-custom-lts', ''), ('../outside', ''), ('6.16.8-custom', '')]:
                self.assertEqual(reminder.running_package(release, root), package, release)
            (root / '6.16.8-custom').mkdir()
            (root / '6.16.8-custom/pkgbase').write_text('personal-kernel\n')
            self.assertEqual(reminder.running_package('6.16.8-custom', root), 'personal-kernel')


class Persistence(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc = self.root / 'proc'
        (self.proc / 'sys/kernel/random').mkdir(parents=True)
        (self.proc / 'sys/kernel/random/boot_id').write_text(BOOT)
        (self.proc / 'stat').write_text(f'cpu 1 2 3\nbtime {TIME}\n')
        (self.proc / 'cpuinfo').write_text('vendor_id\t: GenuineIntel\n')
        self.cache = self.root / 'cache'
        self.log = self.root / 'packages.log'
        self.log.write_text('')

    def status(self):
        return reminder.status(self.cache, proc=self.proc, logs=[self.log],
                               release='6.16.8-arch3-1', modules=self.root / 'modules')

    def test_terminal_change_dismissal_reopen_rotation_and_reboot(self):
        self.assertFalse(self.status()['restart'])
        self.log.write_text(event('linux'))
        self.assertEqual(self.status(), dict(restart=True, reminder=True, restartPackages=['linux']))
        self.assertTrue(self.status()['reminder'])
        self.assertTrue(reminder.acknowledge(self.cache, proc=self.proc))
        self.assertFalse(self.status()['reminder'])
        self.assertTrue(self.status()['restart'])
        self.log.write_text(event('systemd'))
        self.assertEqual(self.status(), dict(restart=True, reminder=False, restartPackages=['linux', 'systemd']))
        self.log.unlink()
        self.assertTrue(self.status()['restart'])
        (self.proc / 'sys/kernel/random/boot_id').write_text(OTHER_BOOT)
        self.assertEqual(self.status(), dict(restart=False, reminder=False, restartPackages=[]))

    def test_alternate_kernel_and_preboot_changes_do_not_trigger(self):
        self.log.write_text(event('linux-lts') + event('linux', date='2026-10-10T09:59:00+0000'))
        self.assertFalse(self.status()['restart'])

    def test_corrupt_cache_and_unwritable_cache_keep_detected_evidence(self):
        self.cache.mkdir()
        path = self.cache / 'restart.json'
        self.log.write_text(event('glibc'))
        for data in ('[]', 'invalid', json.dumps(dict(bootId=BOOT, packages=[None])),
                     json.dumps(dict(bootId=BOOT, packages='linux'))):
            path.write_text(data)
            self.assertTrue(self.status()['reminder'])
        with patch.object(reminder.tempfile, 'NamedTemporaryFile', side_effect=OSError):
            path.unlink()
            self.assertTrue(self.status()['reminder'])
            self.assertFalse(reminder.acknowledge(self.cache, proc=self.proc))

    def test_missing_boot_identity_does_not_reuse_previous_boot(self):
        self.log.write_text(event('linux'))
        self.assertTrue(self.status()['restart'])
        (self.proc / 'sys/kernel/random/boot_id').unlink()
        self.assertFalse(self.status()['restart'])
        self.assertFalse(reminder.acknowledge(self.cache, proc=self.proc))


if __name__ == '__main__':
    unittest.main()
