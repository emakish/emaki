#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise privileged recovery reporting with isolated receipts and menus."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('privacy_recovery', ROOT / 'update-manager/privacy_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryStatus(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshots = self.root / 'snapshots'
        self.snapshots.mkdir()
        self.grub = self.root / 'grub'
        self.grub.mkdir()
        self.enterContext(patch.object(recovery, 'SNAPSHOTS', self.snapshots))
        self.enterContext(patch.object(recovery, 'GRUB', self.grub))
        self.run = self.enterContext(patch.object(recovery.subprocess, 'run'))
        self.run.return_value = subprocess.CompletedProcess([], 0, '{"mode":"normal"}', '')

    def point(self, number, description='pacman -Syu', kind='pre', stamp='2026-10-10 12:00:00'):
        path = self.snapshots / str(number)
        (path / 'snapshot').mkdir(parents=True)
        (path / 'info.xml').write_text(
            f'<snapshot><num>{number}</num><type>{kind}</type><date>{stamp}</date>'
            f'<description>{escape(description)}</description></snapshot>')
        return path

    def menu(self, number=42, source='source ${prefix}/grub-btrfs.cfg'):
        (self.grub / 'grub.cfg').write_text(source + '\n')
        (self.grub / 'grub-btrfs.cfg').write_text(
            "menuentry 'Recovery' {\n"
            f' linux /@snapshots/{number}/snapshot/boot/vmlinuz-linux '
            f'rootflags=subvol="@snapshots/{number}/snapshot",ro\n' + '}\n')

    def test_latest_full_update_uses_restore_metadata_rules(self):
        self.point(41)
        self.point(42)
        self.point(43, description='pacman -S browser')
        self.point(44, kind='single')
        self.point(45, description='manual snapshot')
        self.point(46, stamp='2026-10-09 12:00:00')
        result = recovery.snapshot_status()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['number'], '42')
        self.assertIn('2026-10-10', result['date'])

    def test_missing_snapshot_directory_is_unavailable(self):
        self.snapshots.rmdir()
        self.assertEqual(recovery.snapshot_status(), {'state': 'unavailable'})

    def test_readable_empty_or_unrelated_snapshots_are_none(self):
        self.assertEqual(recovery.snapshot_status(), {'state': 'none'})
        self.point(42, description='pacman -S browser')
        self.assertEqual(recovery.snapshot_status(), {'state': 'none'})

    def test_inaccessible_directory_is_never_reported_empty(self):
        with patch.object(Path, 'iterdir', side_effect=PermissionError):
            self.assertEqual(recovery.snapshot_status(), {'state': 'unavailable'})

    def test_inaccessible_receipt_does_not_claim_latest(self):
        self.point(42)
        with patch.object(recovery.restore, 'receipt', side_effect=PermissionError):
            self.assertEqual(recovery.snapshot_status(), {'state': 'unavailable'})

    def test_malformed_receipt_does_not_claim_latest(self):
        self.point(41)
        path = self.point(42)
        for malformed in ('broken', '<snapshot><num>42</num></snapshot>',
                          '<!DOCTYPE snapshot><snapshot/>'):
            with self.subTest(malformed=malformed):
                (path / 'info.xml').write_text(malformed)
                self.assertEqual(recovery.snapshot_status(), {'state': 'unavailable'})

    def test_symlinked_snapshot_directory_is_unavailable(self):
        (self.snapshots / '42').symlink_to(self.grub, target_is_directory=True)
        self.assertEqual(recovery.snapshot_status(), {'state': 'unavailable'})

    def test_rollback_modes_and_snapshot_identity(self):
        for mode in ('normal', 'pending', 'unsupported'):
            self.run.return_value.stdout = json.dumps({'mode': mode})
            self.assertEqual(recovery.rollback_status(), {'mode': mode})
        self.run.return_value.stdout = '{"mode":"snapshot","snapshot":"42"}'
        self.assertEqual(recovery.rollback_status(), {'mode': 'snapshot', 'snapshot': '42'})
        self.run.assert_called_with([recovery.restore.ROLLBACK, 'status', '--json'],
                                    capture_output=True, text=True, env=recovery.restore.ENV,
                                    cwd='/', stdin=subprocess.DEVNULL, timeout=30)

    def test_rollback_invalid_or_failed_state_is_unavailable(self):
        for raw in ('null', '[]', '{}', 'bad', '{"mode":"unknown"}',
                    '{"mode":"snapshot"}', '{"mode":"snapshot","snapshot":true}',
                    '{"mode":"snapshot","snapshot":"../42"}'):
            with self.subTest(raw=raw):
                self.run.return_value.stdout = raw
                self.assertEqual(recovery.rollback_status(), {'mode': 'unavailable'})
        self.run.return_value.returncode = 1
        self.assertEqual(recovery.rollback_status(), {'mode': 'unavailable'})
        self.run.side_effect = subprocess.TimeoutExpired('status', 30)
        self.assertEqual(recovery.rollback_status(), {'mode': 'unavailable'})

    def test_menu_syntax_and_current_snapshot_link(self):
        self.point(42)
        self.menu()
        result = recovery.menu_status(recovery.snapshot_status())
        self.assertEqual(result, {'state': 'checked', 'latest_snapshot_present': True})
        self.assertEqual(self.run.call_count, 2)
        for call in self.run.call_args_list:
            self.assertEqual(call.args, (['grub-script-check'],))
            self.assertIn('input', call.kwargs)
            self.assertEqual(call.kwargs['env'], recovery.restore.ENV)

    def test_stale_menu_is_checked_but_latest_snapshot_is_absent(self):
        self.point(42)
        self.menu(420)
        self.assertEqual(recovery.menu_status(recovery.snapshot_status()),
                         {'state': 'checked', 'latest_snapshot_present': False})

    def test_disconnected_or_commented_snapshot_is_absent(self):
        self.point(42)
        for source in ('# source ${prefix}/grub-btrfs.cfg', 'source /another/grub-btrfs.cfg'):
            self.menu(source=source)
            self.assertFalse(recovery.menu_status(recovery.snapshot_status())['latest_snapshot_present'])
        self.menu()
        path = self.grub / 'grub-btrfs.cfg'
        path.write_text('# linux /kernel rootflags=subvol=@snapshots/42/snapshot\n'
                        "menuentry '@snapshots/42/snapshot' { echo unavailable; }\n")
        self.assertFalse(recovery.menu_status(recovery.snapshot_status())['latest_snapshot_present'])

    def test_snapshot_unknown_does_not_claim_present_or_absent(self):
        self.menu()
        for state in ('none', 'unavailable'):
            self.assertEqual(recovery.menu_status({'state': state}),
                             {'state': 'checked', 'latest_snapshot_present': None})

    def test_missing_menu_is_distinct_from_inaccessible(self):
        self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'missing')
        with patch.object(recovery, 'read_menu', side_effect=PermissionError):
            self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'unavailable')

    def test_syntax_failure_and_checker_missing(self):
        self.menu()
        self.run.return_value.returncode = 1
        self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'invalid')
        self.run.side_effect = FileNotFoundError
        self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'unavailable')

    def test_real_grub_syntax_checker_when_available(self):
        if not shutil.which('grub-script-check', path=recovery.restore.ENV['PATH']):
            self.skipTest('grub-script-check is unavailable')
        self.menu()
        with patch.object(recovery.subprocess, 'run', REAL_RUN):
            self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'checked')
            (self.grub / 'grub.cfg').write_text("menuentry 'broken' {\n")
            self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'invalid')

    def test_bounded_and_symlinked_menus_are_unavailable(self):
        self.menu()
        with patch.object(recovery, 'MENU_LIMIT', 4):
            self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'unavailable')
        path = self.grub / 'grub.cfg'
        path.unlink()
        path.symlink_to(self.grub / 'grub-btrfs.cfg')
        self.assertEqual(recovery.menu_status({'state': 'none'})['state'], 'unavailable')

    def test_json_contract_and_no_mutation(self):
        self.point(42)
        self.menu()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.root.rglob('*') if path.is_file()}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(recovery.os, 'geteuid', return_value=0), \
                patch.dict(recovery.os.environ, {'PKEXEC_UID': '1000'}):
            self.assertEqual(recovery.main(['status', '--json']), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['status'], 'ready')
        self.assertEqual(value['snapshot']['number'], '42')
        self.assertEqual(value['rollback']['mode'], 'normal')
        self.assertTrue(value['menu']['latest_snapshot_present'])
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual([call.args[0] for call in self.run.call_args_list],
                         [[recovery.restore.ROLLBACK, 'status', '--json'],
                          ['grub-script-check'], ['grub-script-check']])

    def assert_refused_without_reads(self, arguments, expected):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(recovery, 'status') as status, \
                patch.object(recovery, 'read_menu') as menu, \
                patch.object(recovery.restore, 'receipt') as receipt:
            self.assertEqual(recovery.main(arguments), expected)
        self.assertEqual(output.getvalue(), '')
        status.assert_not_called()
        menu.assert_not_called()
        receipt.assert_not_called()
        self.run.assert_not_called()

    def test_changes_and_unrecognized_arguments_are_refused(self):
        with patch.object(recovery.os, 'geteuid', return_value=0), \
                patch.dict(recovery.os.environ, {'PKEXEC_UID': '1000'}):
            for args in ([], ['status'], ['repair'], ['restore', '42'],
                         ['status', '--json', '--repair'], ['status', '--json', '/tmp'],
                         ['status', '--json', '--root=/tmp'], ['status', '--json', '--command=id']):
                with self.subTest(arguments=args):
                    self.assert_refused_without_reads(args, 2)

    def test_unprivileged_session_cannot_read_status_directly(self):
        with patch.object(recovery.os, 'geteuid', return_value=1000), \
                patch.dict(recovery.os.environ, {'PKEXEC_UID': '1000'}):
            self.assert_refused_without_reads(['status', '--json'], 1)

    def test_root_requires_valid_privileged_session_context(self):
        with patch.object(recovery.os, 'geteuid', return_value=0):
            for context in ({}, {'PKEXEC_UID': ''}, {'PKEXEC_UID': '-1'},
                            {'PKEXEC_UID': '1000\n'}, {'PKEXEC_UID': '١٠٠٠'},
                            {'PKEXEC_UID': 'user'}, {'PKEXEC_UID': '../1000'}):
                with self.subTest(context=context), patch.dict(recovery.os.environ, context, clear=True):
                    self.assert_refused_without_reads(['status', '--json'], 1)

    def test_status_contains_only_display_fields(self):
        self.point(42, description='pacman -Syu private-package-name')
        self.menu()
        with (self.grub / 'grub-btrfs.cfg').open('a') as stream:
            stream.write('# private-menu-content\n')
        self.run.return_value.stdout = json.dumps({
            'mode': 'normal', 'private': '/private/rollback/path'})
        output = io.StringIO()
        with redirect_stdout(output), patch.object(recovery.os, 'geteuid', return_value=0), \
                patch.dict(recovery.os.environ, {'PKEXEC_UID': '1000'}):
            self.assertEqual(recovery.main(['status', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            'schema_version': 1, 'status': 'ready',
            'snapshot': {'state': 'ready', 'number': '42', 'date': recovery.restore.receipt(self.snapshots / '42')['date']},
            'rollback': {'mode': 'normal'},
            'menu': {'state': 'checked', 'latest_snapshot_present': True}})
        for private in ('private-package-name', 'private-menu-content', '/private/',
                        str(self.root), '@snapshots/', 'rootflags=', 'info.xml'):
            self.assertNotIn(private, output.getvalue())

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to use distinct privileged and session users')
    def test_real_restricted_install_modes_require_privileged_reader(self):
        self.point(42)
        self.menu()
        self.root.chmod(0o755)
        self.snapshots.chmod(0o750)
        self.grub.chmod(0o755)
        for path in self.grub.iterdir():
            path.chmod(0o600)
        paths = [self.snapshots, *self.grub.iterdir()]
        before = {path: (path.stat().st_uid, path.stat().st_mode) for path in paths}
        self.assertTrue(all(uid == 0 for uid, mode in before.values()))
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(reader)
            try:
                # A caller's restrictive TMPDIR must not hide the fixture itself.
                os.chdir(self.root)
                recovery.SNAPSHOTS = Path('snapshots')
                recovery.GRUB = Path('grub')
                os.setgroups([])
                os.setgid(65534)
                os.setuid(65534)
                result = [recovery.snapshot_status(), recovery.menu_status({'state': 'none'})]
                os.write(writer, json.dumps(result).encode())
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(writer)
        with os.fdopen(reader) as stream:
            observed = stream.read()
        _, result = os.waitpid(child, 0)
        self.assertEqual(result, 0)
        self.assertEqual([row['state'] for row in json.loads(observed)],
                         ['unavailable', 'unavailable'])
        output = io.StringIO()
        with redirect_stdout(output), patch.dict(recovery.os.environ, {'PKEXEC_UID': '65534'}):
            self.assertEqual(recovery.main(['status', '--json']), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['snapshot']['number'], '42')
        self.assertTrue(value['menu']['latest_snapshot_present'])
        self.assertEqual(before, {path: (path.stat().st_uid, path.stat().st_mode) for path in paths})

    def test_installed_interpreter_ignores_hostile_environment_and_working_directory(self):
        installed = self.root / 'installed'
        installed.mkdir()
        helper = installed / 'emaki-privacy-recovery'
        shutil.copyfile(ROOT / 'update-manager/privacy_recovery.py', helper)
        shutil.copyfile(ROOT / 'update-manager/restore.py', installed / 'emaki-update-restore')
        for name in ('emaki_i18n.py', 'emaki_paths.py'):
            shutil.copyfile(ROOT / 'scripts' / name, installed / name)
        helper.chmod(0o755)
        hostile = self.root / 'hostile'
        hostile.mkdir()
        marker = self.root / 'imported-hostile-module'
        for name in ('json.py', 'sitecustomize.py', 'pathlib.py'):
            (hostile / name).write_text(f'open({str(marker)!r}, "w").write("loaded")\n')
        environment = dict(os.environ, PYTHONPATH=str(hostile), PYTHONHOME=str(hostile))
        result = REAL_RUN([str(helper), 'repair'], cwd=hostile, env=environment,
                          capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse(marker.exists())
        self.assertEqual(sorted(path.name for path in installed.iterdir()),
                         ['emaki-privacy-recovery', 'emaki-update-restore', 'emaki_i18n.py', 'emaki_paths.py'])

    def test_installed_layout_loads_extensionless_restore_without_writes(self):
        installed = self.root / 'installed'
        installed.mkdir()
        shutil.copyfile(ROOT / 'update-manager/privacy_recovery.py', installed / 'emaki-privacy-recovery')
        shutil.copyfile(ROOT / 'update-manager/restore.py', installed / 'emaki-update-restore')
        for name in ('emaki_i18n.py', 'emaki_paths.py'):
            shutil.copyfile(ROOT / 'scripts' / name, installed / name)
        # Invalid arguments load the installed module but deliberately query no
        # machine state. Use the real runner, outside the shared subprocess mock.
        with patch.object(recovery.subprocess, 'run', REAL_RUN):
            result = subprocess.run([sys.executable, str(installed / 'emaki-privacy-recovery'), 'repair'],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(sorted(path.name for path in installed.iterdir()),
                         ['emaki-privacy-recovery', 'emaki-update-restore', 'emaki_i18n.py', 'emaki_paths.py'])


REAL_RUN = subprocess.run
if __name__ == '__main__':
    unittest.main()
