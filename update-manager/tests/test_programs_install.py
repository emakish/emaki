#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository inventory, installation previews and privileged transaction boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'update-manager'))
import programs_arch as arch
import programs_install as helper
import emaki_update_space as space

DESC = '%NAME%\nhello\n\n%VERSION%\n2-1\n\n%DESC%\nA greeting\n\n%LICENSE%\nGPL\n\n%CSIZE%\n42\n\n%ISIZE%\n90\n'


class Inventory(unittest.TestCase):
    def test_source_qualified_validation(self):
        self.assertEqual(arch.validate_target('extra/hello+world'), ['extra', 'hello+world'])
        for value in ('hello', '-S/hello', 'extra/--root', 'extra/../root', 'extra/a b', 'extra/a\n', 'extra/a/b', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                arch.validate_target(value)

    def test_description_sizes_and_missing(self):
        row = arch.parse_desc(DESC, 'extra')
        self.assertEqual((row['downloadSize'], row['installedSize']), (42, 90))
        self.assertEqual(row['licence'], 'GPL')
        self.assertIsNone(arch.parse_desc(DESC.replace('42', '-1'), 'extra')['downloadSize'])

    def test_inventory_reads_only_configured_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)
            (database / 'sync').mkdir()
            with tarfile.open(database / 'sync/extra.db', 'w:gz') as archive:
                record = tarfile.TarInfo('hello-2-1/desc')
                data = DESC.encode()
                record.size = len(data)
                archive.addfile(record, io.BytesIO(data))
            with patch.object(arch, 'run', side_effect=['hello 1-1\n', str(database), 'extra\n']) as run:
                packages, installed = arch.inventory()
            self.assertEqual(installed, {'hello': '1-1'})
            self.assertEqual(packages['extra/hello']['version'], '2-1')
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             [['pacman', '-Q'], ['pacman-conf', 'DBPath'], ['pacman-conf', '--repo-list']])

    def test_plan_records_unknown_size(self):
        rows = arch.parse_plan(':: Starting full system upgrade...\nextra/ignored\nextra|hello|2-1|unknown\n')
        self.assertEqual(rows[0]['downloadSize'], None)
        with self.assertRaises(ValueError):
            arch.parse_plan('extra|hello||42')

    def test_plan_token_covers_inventory_and_full_upgrade(self):
        pkg = arch.parse_desc(DESC, 'extra')
        with patch.object(arch, 'inventory', return_value=({'extra/hello': pkg}, {'base': '1'})), patch.object(arch, 'run', return_value='extra|hello|2-1|42\n') as command:
            first = arch.install_plan('extra/hello')
            self.assertTrue(first['fullUpgrade'])
            self.assertEqual(command.call_args.args[0], ['pacman', '-Sup', '--needed', '--noconfirm', '--print-format', '%r|%n|%v|%s', 'extra/hello'])
            pkg['version'] = '3-1'
            self.assertNotEqual(first['token'], arch.install_plan('extra/hello')['token'])

    def test_unavailable_target_does_not_preview(self):
        with patch.object(arch, 'inventory', return_value=({}, {})), patch.object(arch, 'run') as command:
            self.assertEqual(arch.install_plan('extra/hello')['code'], 'unavailable')
            command.assert_not_called()


class Transaction(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_strict_arguments(self):
        self.assertEqual(helper.validate(['extra/hello', 'a' * 64]), (False, 'extra/hello', 'a' * 64))
        for args in (['extra/hello'], ['extra/hello', 'A' * 64], ['--run', '--root/a', 'a' * 64]):
            with self.assertRaises(ValueError):
                helper.validate(args)

    def test_request_revalidates_before_launch(self):
        with patch.object(helper, 'update_backend') as backend, patch.object(arch, 'install_plan', return_value={'ok': True, 'token': 'b' * 64}), patch.object(helper.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                helper.request('extra/hello', 'a' * 64)
            backend.return_value.require_update_root.assert_called_once()
            run.assert_not_called()

    def test_rollback_refuses_before_launch(self):
        with patch.object(helper, 'update_backend') as backend, patch.object(helper, 'revalidate') as check, patch.object(helper.subprocess, 'run') as run:
            backend.return_value.require_update_root.side_effect = RuntimeError('pending')
            with self.assertRaises(RuntimeError):
                helper.request('extra/hello', 'a' * 64)
            check.assert_not_called()
            run.assert_not_called()

    def test_fixed_service_journal_result(self):
        invocation = 'f' * 32
        finished = {'type': 'finished', 'action': 'install', 'ok': True, 'code': 'success'}
        launch = Mock(returncode=0, stdout='', stderr='Running as unit: emaki-programs-install.service; invocation ID: ' + invocation)
        journal = Mock(returncode=0, stdout=json.dumps({'MESSAGE': json.dumps(finished)}))
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate'), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]) as run:
            self.assertEqual(helper.request('extra/hello', 'a' * 64), 0)
            command = run.call_args_list[0].args[0]
            self.assertIn('--wait', command)
            self.assertIn('--collect', command)
            self.assertIn('--property=StandardOutput=journal', command)
            self.assertNotIn('--pipe', command)
            self.assertEqual(command[-3:], ['--run', 'extra/hello', 'a' * 64])
            marker = next(item.removeprefix('--property=LogExtraFields=') for item in command
                          if item.startswith('--property=LogExtraFields='))
            self.assertEqual(run.call_args_list[1].args[0][-1], marker)
            self.assertNotIn('_SYSTEMD_INVOCATION_ID=' + invocation, run.call_args_list[1].args[0])

    def test_finished_result_without_invocation_status_line(self):
        finished = dict(type='finished', action='install', ok=True, code='success',
                        restart=True, signOut=True)
        launch = Mock(returncode=0, stdout='', stderr='Running as unit: emaki-programs-install.service')
        journal = Mock(returncode=0, stdout=json.dumps({'MESSAGE': json.dumps(finished)}))
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate'), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]) as run:
            self.assertEqual(helper.request('extra/hello', 'a' * 64), 0)
        self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1]), finished)
        command = run.call_args_list[0].args[0]
        marker = next(item for item in command if item.startswith('--property=LogExtraFields='))
        self.assertRegex(marker, r'^--property=LogExtraFields=EMAKI_INSTALL_REQUEST=[0-9a-f]{32}$')
        self.assertEqual(run.call_args_list[1].args[0], [
            'journalctl', '--quiet', '--no-pager', '-o', 'json',
            '_SYSTEMD_UNIT=emaki-programs-install.service', '_UID=0',
            marker.removeprefix('--property=LogExtraFields=')])

    def test_each_request_ignores_other_service_results(self):
        old_id = 'e' * 32
        old = dict(type='finished', action='install', ok=True, code='success', restart=True)
        records = [{'_SYSTEMD_INVOCATION_ID': old_id,
                    '_SYSTEMD_UNIT': 'emaki-programs-install.service', '_UID': '0',
                    'EMAKI_INSTALL_REQUEST': '0' * 32, 'MESSAGE': json.dumps(old)}]
        markers = []

        def command(argv, **kwargs):
            if argv[0] == 'systemd-run':
                self.assertIn('--unit=emaki-programs-install', argv)
                marker = next((item.removeprefix('--property=LogExtraFields=')
                               for item in argv if item.startswith('--property=LogExtraFields=')), '')
                markers.append(marker)
                # Extra fields are untrusted without the journal's service credentials.
                if marker:
                    for credentials in ({'_UID': '1000'}, {'_SYSTEMD_UNIT': 'other.service'}):
                        records.append(dict(records[0], EMAKI_INSTALL_REQUEST=marker.split('=', 1)[1],
                                            **credentials))
                # A failed start must not claim the active or previous unit's result.
                return Mock(returncode=1, stdout='', stderr='Unit already exists. Invocation ID: ' + old_id)
            self.assertEqual(argv[0], 'journalctl')
            matches = [item.split('=', 1) for item in argv if '=' in item]
            selected = [record for record in records if all(record.get(key) == value for key, value in matches)]
            return Mock(returncode=0, stdout='\n'.join(json.dumps(record) for record in selected))

        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate'), patch.object(helper.subprocess, 'run', side_effect=command):
            for _ in range(2):
                self.assertEqual(helper.request('extra/hello', 'a' * 64), 1)
                self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1])['code'], 'unknown')
        self.assertNotEqual(markers[0], markers[1])
        for marker in markers:
            self.assertRegex(marker, r'^EMAKI_INSTALL_REQUEST=[0-9a-f]{32}$')

    def test_failed_install_retains_invocation_diagnostic(self):
        finished = dict(type='finished', action='install', ok=False, message='Installation failed.')
        cause = 'Not enough free space: needs 1.5 GB, 257.5 MB is free.'
        launch = Mock(returncode=1, stdout='', stderr='Running as unit: emaki-programs-install.service')
        journal = Mock(returncode=0, stdout='\n'.join(json.dumps({'MESSAGE': text}) for text in
                       ('x' * 5000, '\x1b[31m' + cause + '\x1b[0m', json.dumps(finished))))
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate'), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]):
            self.assertEqual(helper.request('extra/hello', 'a' * 64), 1)
        result = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertIn(cause, result['message'])
        self.assertNotIn('\x1b', result['message'])
        self.assertLessEqual(len(result['message']), len(finished['message']) + 4097)

    def test_busy_worker_never_runs_update(self):
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock', side_effect=BlockingIOError), patch.object(helper.subprocess, 'run') as run:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
            run.assert_not_called()
            self.assertEqual(json.loads(self.output.getvalue())['code'], 'busy')

    def test_worker_plan_refusal_never_runs_update(self):
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate', side_effect=ValueError('changed')), patch.object(helper.subprocess, 'run') as run:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
            run.assert_not_called()
            self.assertTrue(json.loads(self.output.getvalue())['notStarted'])

    def test_worker_full_upgrade_and_partial_failure(self):
        backend = Mock()
        backend.versions.side_effect = [{'base': '1'}, {'base': '2'}]
        backend.changed.return_value = (False, True)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate'), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=1)) as run:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
            self.assertEqual(run.call_args.args[0][-3:], ['--noninteractive', '--install', 'extra/hello'])
            self.assertIn('--what=sleep:handle-lid-switch', run.call_args.args[0])
            self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1])['code'], 'partial')

    def test_install_space_probe_keeps_target_and_private_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database, cache = Path(directory) / 'db', Path(directory) / 'cache'
            (database / 'local').mkdir(parents=True)
            cache.mkdir()
            calls = []
            def command(arguments, probe=False):
                calls.append((arguments, probe))
                if arguments[-1] == 'DBPath':
                    return str(database)
                if arguments[-1] == 'CacheDir':
                    return str(cache)
                self.assertNotEqual(Path(arguments[arguments.index('--dbpath') + 1]), database)
                return 'there is nothing to do'
            with patch.object(space, 'command', side_effect=command):
                space.preflight(targets=['extra/hello'])
            self.assertEqual(calls[-1][0][-2:], ['--needed', 'extra/hello'])
            self.assertEqual(calls[-1][0][1:3], ['-Su', '--confirm'])
            self.assertTrue(calls[-1][1])

    def test_wrapper_passes_install_to_space_and_full_upgrade(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        preflight, run = Mock(), Mock(return_value=0)
        with patch.dict(module['main'].__globals__, preflight=preflight, run=run,
                        upgrades=Mock(return_value={}), held=Mock(return_value={})), patch.object(os, 'geteuid', return_value=0), patch.object(sys, 'argv', ['emaki-update', '--noninteractive', '--install', 'extra/hello']):
            self.assertEqual(module['main'](), 0)
            preflight.assert_called_once_with(targets=['extra/hello'])
            self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                             [['-Sy'], ['-Su', '--noconfirm', '--needed', 'extra/hello']])


if __name__ == '__main__':
    unittest.main()
