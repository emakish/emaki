#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository inventory, installation previews and privileged transaction boundaries."""
from datetime import datetime, timezone
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
    def setUp(self):
        self.enterContext(patch.object(arch, 'private_database',
                                      side_effect=lambda *args, **kwargs: contextlib.nullcontext(Path('/private-db'))))

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

    def test_optional_package_names_strip_versions_and_descriptions(self):
        row = arch.parse_desc(DESC + '\n%OPTDEPENDS%\npaint>=2: Optional editor\n'
                              'viewer: View documents\ninvalid name: Bad\n../escape\n', 'extra')
        self.assertEqual(row['optionalPackages'], ['paint', 'viewer'])
        self.assertEqual(arch.parse_desc(DESC, 'extra')['optionalPackages'], [])

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
            self.assertEqual(command.call_args.args[0], ['pacman', '-Sup', '--needed', '--noconfirm', '--print-format', '%r|%n|%v|%s', *arch.database_arguments('/private-db'), 'extra/hello'])
            pkg['version'] = '3-1'
            self.assertNotEqual(first['token'], arch.install_plan('extra/hello')['token'])

    def test_nvidia_preview_uses_installer_selection_and_binds_targets(self):
        graphics = arch.graphics_policy()
        devices = ({'vendor': graphics.NVIDIA, 'device': 0x2200},)
        names = graphics.package_selection(devices)
        packages = {'extra/' + name: dict(package=name, version='1') for name in names}
        with patch.object(arch, 'inventory', return_value=(packages, {})), patch.object(arch, 'run', return_value=''), patch.object(arch, 'graphics_devices', return_value=devices):
            plan = arch.install_plan('extra/emaki-nvidia')
            self.assertEqual(plan['targets'], ['extra/' + name for name in names])
            original = plan['token']
            del packages['extra/nvidia-open']
            for name in ('nvidia-open-dkms', 'dkms', 'linux-headers', 'linux-lts-headers'):
                packages['extra/' + name] = dict(package=name, version='1')
            plan = arch.install_plan('extra/emaki-nvidia')
            self.assertEqual(plan['targets'], ['extra/' + name for name in graphics.package_selection(devices, ())])
            self.assertNotEqual(original, plan['token'])

    def test_nvidia_old_or_absent_gpu_refuses_before_preview(self):
        for devices in ((), ({'vendor': 0x10de, 'device': 0x1000},),
                        ({'vendor': 0x10de, 'device': 0x2200}, {'vendor': 0x10de, 'device': 0x1000})):
            with self.subTest(devices=devices), patch.object(arch, 'inventory', return_value=({'extra/emaki-nvidia': {}}, {})), patch.object(arch, 'graphics_devices', return_value=devices), patch.object(arch, 'run') as run:
                self.assertFalse(arch.install_plan('extra/emaki-nvidia')['ok'])
                run.assert_not_called()

    def test_nvidia_missing_stack_refuses_before_preview(self):
        devices = ({'vendor': 0x10de, 'device': 0x2200},)
        with patch.object(arch, 'inventory', return_value=({'extra/emaki-nvidia': {}}, {})), patch.object(arch, 'graphics_devices', return_value=devices), patch.object(arch, 'run') as run:
            self.assertFalse(arch.install_plan('extra/emaki-nvidia')['ok'])
            run.assert_not_called()

    def test_graphics_detection_ignores_nvidia_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / 'bus/pci/devices/0000:01:00.0'
            device.mkdir(parents=True)
            (device / 'vendor').write_text('0x10de')
            (device / 'device').write_text('0x2200')
            (device / 'class').write_text('0x040300')
            self.assertEqual(arch.graphics_policy().detect(Path(directory)), ())
            (device / 'class').write_text('0x030000')
            self.assertEqual(arch.graphics_policy().detect(Path(directory))[0]['device'], 0x2200)

    def test_unavailable_target_does_not_preview(self):
        with patch.object(arch, 'inventory', return_value=({}, {})), patch.object(arch, 'run') as command:
            self.assertEqual(arch.install_plan('extra/hello')['code'], 'unavailable')
            command.assert_not_called()


class Transaction(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(arch, 'private_database',
                                      side_effect=lambda *args, **kwargs: contextlib.nullcontext(Path('/private-db'))))
        self.snapshot_patch = patch.object(helper, 'preinstall_snapshot', return_value=('', ''))
        self.snapshot = self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
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
        with patch.object(helper, 'update_backend') as backend, patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}) as check, patch.object(helper.subprocess, 'run') as run:
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
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]) as run:
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
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]) as run:
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

        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=command):
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
        with patch.object(helper, 'update_backend'), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[launch, journal]):
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
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=1)) as run:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
            self.assertEqual(run.call_args.args[0][-1:], ['--noninteractive'])
            self.assertEqual(run.call_count, 1)
            self.assertIn('--what=sleep:handle-lid-switch', run.call_args.args[0])
            self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1])['code'], 'partial')

    def test_successful_install_records_what_it_added(self):
        self.snapshot.return_value = ('5', '5:' + 'a' * 64)
        backend = Mock()
        backend.versions.side_effect = [{'base': '1'}, {'base': '2'}, {'base': '2', 'hello': '1-1', 'libhello': '2'}]
        backend.changed.return_value = (False, False)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)) as run, patch.object(helper.receipts, 'snapshot_numbers', return_value={'3'}), patch.object(helper.receipts, 'first_snapshot', return_value='5') as snapshot, patch.object(helper.receipts, 'record', return_value={'id': 'r1'}) as record:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 0)
        self.assertEqual(record.call_args.args, ('arch', '', 'hello', 'hello', {'base': '2'},
                                                 {'base': '2', 'hello': '1-1', 'libhello': '2'}, '5'))
        self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1])['receipt'], 'r1')
        self.assertEqual(run.call_args_list[0].args[0][-2:], [helper.UPDATE, '--noninteractive'])
        self.assertEqual(run.call_args_list[1].args[0][-3:], ['--noninteractive', '--install-only', 'extra/hello'])
        self.snapshot.assert_called_once_with(required=False)
        backend.changed.assert_called_once_with({'base': '1'}, {'base': '2', 'hello': '1-1', 'libhello': '2'})

    def test_nvidia_worker_reconstructs_and_installs_the_full_stack(self):
        devices = ({'vendor': 0x10de, 'device': 0x2200},)
        names = arch.graphics_policy().package_selection(devices)
        targets = ['extra/' + name for name in names]
        packages = {target: dict(package=target.split('/')[1], version='1') for target in targets}
        backend = Mock()
        backend.versions.side_effect = [{}, {}, dict.fromkeys(names, '1')]
        backend.changed.return_value = (True, False)
        with patch.object(arch, 'inventory', return_value=(packages, {})), patch.object(arch, 'graphics_devices', return_value=devices), patch.object(arch, 'run', return_value=''):
            token = arch.install_plan('extra/emaki-nvidia')['token']
            with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)) as run, patch.object(helper.receipts, 'snapshot_numbers', return_value=set()), patch.object(helper.receipts, 'first_snapshot', return_value=''), patch.object(helper.receipts, 'record', return_value={'id': 'stack'}) as record:
                self.assertEqual(helper.worker('extra/emaki-nvidia', token), 0)
            self.assertEqual(run.call_args_list[1].args[0][-len(targets):], targets)
            self.assertEqual(record.call_args.args[5], dict.fromkeys(names, '1'))
            packages['extra/nvidia-utils']['version'] = '2'
            with patch.object(helper, 'update_backend'), patch.object(helper.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    helper.request('extra/emaki-nvidia', token)
                run.assert_not_called()

    def test_replacement_receipt_keeps_exact_install_snapshot_token(self, failed=False, database_changed=True, target="extra/hello"):
        backend = Mock()
        backend.versions.side_effect = [{'base': '1', 'old': '1'},
                                       {'base': '2', 'old': '1'},
                                       {'base': '2', 'hello': '1'} if database_changed else {'base': '2', 'old': '1'}]
        backend.changed.return_value = (False, False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / 'snapshots'
            snapshots.mkdir()
            def snapshot(number, description):
                point = snapshots / number
                (point / 'snapshot').mkdir(parents=True)
                stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                (point / 'info.xml').write_text(
                    f'<snapshot><num>{number}</num><type>pre</type><date>{stamp}</date>'
                    f'<description>{description}</description></snapshot>')
            snapshot('3', 'pacman -Syu')
            self.snapshot_patch.stop()
            def command(argv, **kwargs):
                if 'create' in argv:
                    snapshot('5', argv[-1])
                    return Mock(returncode=0, stdout='5\n')
                if '--install-only' in argv:
                    snapshot('6', 'pacman -S --noconfirm --needed extra/hello')
                    return Mock(returncode=int(failed))
                return Mock(returncode=0, stdout='ro=true\n')
            original_open = os.open
            def open_file(path, flags, *args, **kwargs):
                if path == '/run/emaki-update-manager.lock':
                    path = root / 'lock'
                return original_open(path, flags, *args, **kwargs)
            with patch.object(helper.os, 'open', side_effect=open_file), patch.object(helper, 'SNAPSHOTS', snapshots), patch.object(helper.receipts, 'DIRECTORY', root / 'receipts'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': [target]}), patch.object(helper.subprocess, 'run', side_effect=command):
                self.assertEqual(helper.worker(target, 'a' * 64), int(failed))
            if failed:
                result = json.loads(self.output.getvalue().splitlines()[-1])
                restore = helper.receipts.restore
                with patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), patch.object(restore, 'run', return_value=Mock(returncode=0, stdout='', stderr='')) as rollback:
                    self.assertTrue(restore.restore_point(result['restoreToken'], snapshots)['ok'])
                rollback.assert_called_once_with([restore.ROLLBACK, 'snapshot', '5'], timeout=None)
                return
            rows = helper.receipts.receipts(root / 'receipts')
            self.assertEqual(len(rows), 1)
            receipt = rows[0]
            token = '5:' + helper.receipts.restore.receipt(snapshots / '5')['digest']
            self.assertEqual(receipt['snapshotToken'], token)
            restore = helper.receipts.restore
            with patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), patch.object(restore, 'run', return_value=Mock(returncode=0, stdout='restored', stderr='')) as rollback:
                self.assertTrue(restore.restore_point(token, snapshots)['ok'])
            rollback.assert_called_once_with([restore.ROLLBACK, 'snapshot', '5'], timeout=None)
            self.assertEqual(receipt['upgraded'], {})
            self.assertEqual(receipt['removed'], {'old': '1'})
            with patch.object(helper.receipts.restore, 'list_points', return_value=[]) as points:
                plan = helper.receipts.snapshot_plan(receipt)
            self.assertEqual(plan['restoreToken'], token)
            points.assert_not_called()

    def test_nvidia_install_point_is_listed_and_restorable(self):
        self.test_replacement_receipt_keeps_exact_install_snapshot_token(target='extra/emaki-nvidia')

    def test_unverified_snapshot_is_deleted_and_refused(self):
        self.snapshot_patch.stop()
        for readonly in ('ro=false', ''):
            with self.subTest(readonly=readonly), tempfile.TemporaryDirectory() as directory, patch.object(helper, 'SNAPSHOTS', Path(directory)), patch.object(helper.receipts.restore, 'receipt', return_value={'kind': 'pre', 'description': 'Program install', 'digest': 'a' * 64}), patch.object(helper.subprocess, 'run', side_effect=[Mock(returncode=0, stdout='5'), Mock(returncode=0), Mock(returncode=0, stdout=readonly), Mock(returncode=0)]) as run:
                with self.assertRaisesRegex(RuntimeError, 'snapshot could not be created'):
                    helper.preinstall_snapshot()
                self.assertEqual(run.call_args.args[0], ['snapper', '--no-dbus', '-c', 'root', 'delete', '5'])

    def test_repository_install_without_snapshot_support_has_no_recovery_token(self):
        self.snapshot_patch.stop()
        with tempfile.TemporaryDirectory() as directory, patch.object(helper, 'SNAPSHOTS', Path(directory) / 'absent'), patch.object(helper.subprocess, 'run') as run:
            self.assertEqual(helper.preinstall_snapshot(), ('', ''))
        run.assert_not_called()

    def test_failed_repository_restore_uses_the_real_restore_path(self):
        self.test_replacement_receipt_keeps_exact_install_snapshot_token(failed=True)

    def test_failed_install_without_database_change_still_restores_files(self):
        self.test_replacement_receipt_keeps_exact_install_snapshot_token(failed=True, database_changed=False)

    def test_snapshot_failure_prevents_install_transaction(self):
        self.snapshot.side_effect = RuntimeError('Snapshot failed')
        backend = Mock()
        backend.versions.side_effect = [{'base': '1'}, {'base': '2'}]
        backend.changed.return_value = (True, False)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/emaki-nvidia']}), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(helper.worker('extra/emaki-nvidia', 'a' * 64), 1)
        self.snapshot.assert_called_once_with(required=True)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('--install-only', run.call_args.args[0])
        result = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertEqual(result['code'], 'refused')
        self.assertIn('snapshot could not be created', result['message'])
        self.assertTrue(result['restart'])
        self.assertFalse(result.get('restoreToken'))

    def test_committed_install_with_failed_hook_still_has_undo(self):
        backend = Mock()
        baseline = {'base': '2'}
        after = dict(baseline, hello='1-1', libhello='2')
        backend.versions.side_effect = [{'base': '1'}, baseline, after]
        backend.changed.return_value = (True, False)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=1)]), patch.object(helper.receipts, 'first_snapshot', return_value=''), patch.object(helper.receipts, 'record', return_value={'id': 'committed'}) as record:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
        record.assert_called_once_with('arch', '', 'hello', 'hello', baseline, after, '', snapshot_token='')
        result = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'partial')
        self.assertEqual(result['receipt'], 'committed')
        self.assertTrue(result['restart'])

    def test_failed_repository_install_offers_its_snapshot(self):
        self.snapshot.return_value = ('42', '42:' + 'a' * 64)
        backend = Mock()
        backend.versions.side_effect = [{'base': '1'}, {'base': '2'}, {'base': '2'}]
        backend.changed.return_value = (True, False)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=1)]), patch.object(helper.receipts, 'first_snapshot', return_value='42'), patch.object(helper.receipts.restore, 'receipt', return_value={'kind': 'pre', 'digest': 'a' * 64}), patch.object(helper.receipts, 'record') as record:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
        record.assert_not_called()
        result = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertEqual(result.get('restoreToken'), '42:' + 'a' * 64)

    def test_failed_install_keeps_completed_update_without_install_receipt(self):
        backend = Mock()
        backend.versions.side_effect = [{'base': '1'}, {'base': '2'}, {'base': '2'}]
        backend.changed.return_value = (True, False)
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/hello']}), patch.object(helper.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=1)]) as run, patch.object(helper.receipts, 'record') as record:
            self.assertEqual(helper.worker('extra/hello', 'a' * 64), 1)
        self.assertEqual(run.call_count, 2)
        record.assert_not_called()
        result = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertEqual(result['code'], 'partial')
        self.assertTrue(result['restart'])


    def test_install_only_wrapper_does_not_update_again(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        preflight, run, update = Mock(), Mock(return_value=0), Mock()
        with patch.dict(module['main'].__globals__, preflight=preflight, run=run, update=update), patch.object(os, 'geteuid', return_value=0), patch.object(sys, 'argv', ['emaki-update', '--noninteractive', '--install-only', 'extra/hello', 'extra/library']):
            self.assertEqual(module['main'](), 0)
        preflight.assert_called_once_with(targets=['extra/hello', 'extra/library'])
        update.assert_not_called()
        self.assertEqual(run.call_args.args[0][1:], ['-S', '--noconfirm', '--needed', 'extra/hello', 'extra/library'])
        self.assertTrue(run.call_args.kwargs['fail_on_hook_error'])

    def test_install_only_rejects_invalid_targets_before_any_transaction(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        preflight, run = Mock(), Mock()
        with patch.dict(module['main'].__globals__, preflight=preflight, run=run):
            for target in ('', '--root/x', 'extra/../x'):
                with self.subTest(target=target), patch.object(sys, 'argv', ['emaki-update', '--install-only=' + target]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    module['main']()
        preflight.assert_not_called()
        run.assert_not_called()

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

    def test_wrapper_passes_install_to_full_upgrade_with_preflight(self):
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
