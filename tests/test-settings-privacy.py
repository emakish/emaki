#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Privacy reads remain conservative and never implicitly change system state."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def device(kind, number, children=None, **fields):
    return dict(type=kind, **{'maj:min': number}, children=children or [], **fields)


class Privacy(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader('settings_privacy', str(ROOT / 'scripts/emaki-settings-privacy'))
        self.helper = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        self.portals = types.ModuleType('emaki_privacy_portals')
        self.portals.status = lambda: {'state': 'unavailable', 'rows': [], 'unavailable': []}
        self.portals.revoke = lambda *args: False
        paths = types.ModuleType('emaki_paths')
        paths.LIBEXECDIR = '/test-prefix/libexec/emaki'
        with patch.dict(sys.modules, emaki_paths=paths, emaki_privacy_portals=self.portals):
            loader.exec_module(self.helper)

    def test_encrypted_plain_and_unknown_roots(self):
        mount = {'maj:min': '253:0', 'fstype': 'ext4'}
        crypt = device('disk', '8:0', [device('part', '8:1', [device('crypt', '253:0')])])
        self.assertEqual(self.helper.mount_encryption(mount, [crypt]), 'encrypted')
        plain = device('disk', '8:0', [device('part', '253:0')])
        self.assertEqual(self.helper.mount_encryption(mount, [plain]), 'unencrypted')
        for nodes in ([], None, [device('loop', '253:0')], [device('lvm', '253:0')], [42]):
            self.assertEqual(self.helper.mount_encryption(mount, nodes), 'unavailable')
        self.assertEqual(self.helper.mount_encryption(dict(mount, fstype='overlay'), [crypt]), 'unavailable')
        self.assertEqual(self.helper.mount_encryption(None, [crypt]), 'unavailable')

    def test_lvm_with_mixed_backing_branches_is_not_fully_encrypted(self):
        mount = {'maj:min': '253:1', 'fstype': 'ext4'}
        encrypted = device('disk', '8:0', [device('crypt', '253:0', [device('lvm', '253:1')])])
        plain = device('disk', '8:1', [device('lvm', '253:1')])
        self.assertEqual(self.helper.mount_encryption(mount, [encrypted, plain]), 'mixed')

    def test_incomplete_btrfs_identity_is_not_assumed_single_device(self):
        mount = {'maj:min': '253:0', 'fstype': 'btrfs'}
        encrypted = device('disk', '8:0', [device('crypt', '253:0')])
        self.assertEqual(self.helper.mount_encryption(mount, [encrypted]), 'unavailable')

    def test_btrfs_checks_other_members_of_filesystem(self):
        mount = {'maj:min': '253:0', 'fstype': 'btrfs', 'uuid': 'filesystem'}
        encrypted = device('disk', '8:0', [device('crypt', '253:0', uuid='filesystem')])
        plain = device('disk', '8:1', uuid='filesystem')
        self.assertEqual(self.helper.mount_encryption(mount, [encrypted, plain]), 'mixed')

    def test_read_only_commands_and_home_are_checked_independently(self):
        calls = []
        def command(args):
            calls.append(args)
            if args[0] == 'lsblk':
                return {'blockdevices': [device('disk', '8:0', [device('crypt', '253:0')]), device('disk', '8:1')]}
            return {'filesystems': [{'maj:min': '253:0' if args[3] == '/' else '8:1', 'fstype': 'ext4'}]}
        with patch.object(self.helper, 'command', command):
            result = self.helper.encryption()
        self.assertEqual(result['state'], 'mixed')
        self.assertEqual(result['mounts'], [{'mount': '/', 'state': 'encrypted'}, {'mount': '/home', 'state': 'unencrypted'}])
        self.assertEqual([args[0] for args in calls], ['lsblk', 'findmnt', 'findmnt'])
        self.assertTrue(all('--json' in args for args in calls))

    def test_missing_or_bad_helpers_are_unavailable(self):
        for response in ({}, {'schema_version': 2}, {'schema_version': 1, 'status': 'ready', 'snapshot': []}):
            with patch.object(self.helper, 'command', return_value=response):
                self.assertEqual(self.helper.recovery()['snapshot']['state'], 'unavailable')
                self.assertEqual(self.helper.lock_status()['state'], 'unavailable')

    def test_recovery_uses_only_fixed_noninteractive_privileged_status(self):
        response = {'schema_version': 1, 'status': 'ready',
                    'snapshot': {'state': 'ready', 'number': '42', 'date': '2026-10-11'},
                    'rollback': {'mode': 'normal'},
                    'menu': {'state': 'checked', 'latest_snapshot_present': True}}
        with patch.object(self.helper, 'command', return_value=response) as command:
            self.assertEqual(self.helper.recovery(),
                             {key: response[key] for key in ('snapshot', 'rollback', 'menu')})
        command.assert_called_once_with(['pkexec', '--disable-internal-agent',
                                        '/test-prefix/libexec/emaki/emaki-privacy-recovery',
                                        'status', '--json'], timeout=60)

    def test_policy_denial_or_missing_pkexec_does_not_retry_with_more_privilege(self):
        for result in (subprocess.CompletedProcess([], 126, '', ''),
                       subprocess.CompletedProcess([], 127, '', 'denied')):
            with patch.object(self.helper.subprocess, 'run', return_value=result) as run:
                self.assertEqual(self.helper.recovery()['snapshot']['state'], 'unavailable')
            self.assertEqual(run.call_count, 1)

    def test_policy_authorizes_only_active_local_status_reader(self):
        policy = ET.parse(ROOT / 'update-manager/org.emaki.privacy.recovery.policy').getroot()
        actions = policy.findall('action')
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.attrib, {'id': 'org.emaki.privacy.recovery'})
        self.assertEqual({child.tag: child.text for child in action.find('defaults')},
                         {'allow_any': 'no', 'allow_inactive': 'no', 'allow_active': 'yes'})
        self.assertEqual({child.get('key'): child.text for child in action.findall('annotate')},
                         {'org.freedesktop.policykit.exec.path': '@EMAKI_LIBEXECDIR@/emaki-privacy-recovery',
                          'org.freedesktop.policykit.exec.argv1': 'status'})

    def test_lock_delay_needs_available_idle_backend(self):
        for available, delay, expected in [(True, 0, 'ready'), (True, 300, 'ready'), (False, 300, 'unavailable'),
                                           (True, -1, 'unavailable'), (True, True, 'unavailable'), (True, '300', 'unavailable')]:
            with patch.object(self.helper, 'command', return_value={'state': 'ready', 'idle_available': available, 'values': {'lock_delay': delay}}):
                self.assertEqual(self.helper.lock_status()['state'], expected)

    def test_report_paths_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.dict(os.environ, XDG_STATE_HOME=str(directory / 'state'), XDG_RUNTIME_DIR=str(directory / 'run')):
                self.assertEqual(self.helper.report_locations(), {'saved': str(directory / 'state/emaki/reports'),
                                                                  'runtime': str(directory / 'run/emaki-shell-health')})
            self.assertEqual(list(directory.iterdir()), [])
            with patch.dict(os.environ, XDG_STATE_HOME='relative', XDG_RUNTIME_DIR='relative'), patch.object(Path, 'home', return_value=directory):
                self.assertEqual(self.helper.report_locations(), {'saved': str(directory / '.local/state/emaki/reports'), 'runtime': None})

    def test_fixed_protocol_does_not_revoke_on_read_or_invalid_command(self):
        with patch.object(self.helper, 'status', return_value={'schema_version': 1, 'status': 'ready'}), patch.object(self.portals, 'revoke') as revoke:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.helper.main(['status', '--json']), 0)
                self.assertEqual(self.helper.main(['revoke-portal', 'devices', 'camera', 'app']), 2)
                self.assertEqual(self.helper.main(['grant', '--json']), 2)
            revoke.assert_not_called()
            output = io.StringIO()
            revoke.return_value = False
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.helper.main(['revoke-portal', 'devices', 'camera', 'app', '--json']), 0)
            revoke.assert_called_once_with('devices', 'camera', 'app')
            self.assertEqual(json.loads(output.getvalue())['operation'], 'failed')

    def test_command_failure_timeout_and_malformed_json_are_bounded(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('fixture', 20), UnicodeError()):
            with patch.object(self.helper.subprocess, 'run', side_effect=error):
                self.assertEqual(self.helper.command(['fixture']), {})
        for code, data in [(1, '{}'), (0, '[]'), (0, '{'), (0, ' ' * (2**20 + 1))]:
            with patch.object(self.helper.subprocess, 'run', return_value=types.SimpleNamespace(returncode=code, stdout=data)):
                self.assertEqual(self.helper.command(['fixture']), {})


if __name__ == '__main__':
    unittest.main()
