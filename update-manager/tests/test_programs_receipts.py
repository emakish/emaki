#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install receipts and "Undo this install": the undo maps to exactly that install."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import contextlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_receipts as receipts
import programs_undo as helper
import programs_flatpak as flatpak

BEFORE = {'base': '1', 'glibc': '3', 'python': '3.13'}
AFTER = {'base': '1', 'glibc': '3', 'python': '3.13', 'paint': '2-1', 'libbrush': '1-1', 'python-pillow': '11-1'}


class Pacman:
    """A read-only package database for undo previews."""
    def __init__(self, installed, reasons=None, refuse=''):
        self.installed = dict(installed)
        self.reasons = dict(reasons or {})
        self.refuse = refuse
        self.calls = []

    def __call__(self, *arguments, **_options):
        self.calls.append(arguments)
        if arguments == ('-Q',):
            return SimpleNamespace(returncode=0, stdout=''.join(f'{name} {version}\n' for name, version in
                                                                 self.installed.items()), stderr='')
        if arguments[0] == '-Qi':
            text = ''.join(f'Name            : {name}\nInstall Reason  : {self.reasons.get(name, "Installed as a dependency for another package")}\n\n'
                           for name in arguments[1:])
            return SimpleNamespace(returncode=0, stdout=text, stderr='')
        if arguments[0] == '-Rp':
            if self.refuse:
                return SimpleNamespace(returncode=1, stdout=self.refuse, stderr='error: failed to prepare transaction')
            return SimpleNamespace(returncode=0, stdout='\n'.join(arguments[3:]), stderr='')
        if arguments[0] == '-Q':
            return SimpleNamespace(returncode=0 if arguments[1] in self.installed else 1, stdout='', stderr='')
        raise AssertionError(arguments)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-receipts-')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'programs' / 'installs'

    def install(self, target='paint', before=BEFORE, after=AFTER, kind='arch'):
        return receipts.record(kind, '', target, target, before, after, '42', directory=self.directory)

    def test_receipt_records_what_appeared_and_what_was_upgraded(self):
        value = self.install(before=dict(BEFORE, glibc='2'))
        self.assertEqual(value['added'], {'paint': '2-1', 'libbrush': '1-1', 'python-pillow': '11-1'})
        self.assertEqual(value['upgraded'], {'glibc': ['2', '3']})
        self.assertEqual(value['snapshot'], '42')
        path = self.directory / (value['id'] + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)
        self.assertEqual(receipts.receipts(self.directory)[0], value)

    def test_updating_an_installed_program_requires_snapshot_undo(self):
        value = self.install(before=dict(BEFORE, paint='1-1'))
        self.assertEqual(value['added'], {'libbrush': '1-1', 'python-pillow': '11-1'})
        self.assertEqual(receipts.undo_plan(value, Pacman(AFTER))['code'], 'snapshot-unavailable')

    def test_trusted_read_refuses_files_the_person_can_write(self):
        value = self.install()
        path = self.directory / (value['id'] + '.json')
        with self.assertRaises(ValueError):
            receipts.read(path, trusted=True)  # owned by this test's user, not root
        self.assertEqual(receipts.read(path, trusted=False)['id'], value['id'])
        renamed = path.with_name('20260101T000000Z-' + 'f' * 32 + '.json')
        path.rename(renamed)
        with self.assertRaises(ValueError):
            receipts.read(renamed, trusted=False)

    def test_fifo_receipt_is_rejected_without_blocking_reads(self):
        value = self.install()
        path = self.directory / (value['id'] + '.json')
        path.unlink()
        os.mkfifo(path)
        code = """
import sys
from pathlib import Path
import unittest
import programs_receipts as receipts
path = Path(sys.argv[1])
for trusted in (True, False):
    with unittest.TestCase().assertRaisesRegex(ValueError, 'Invalid receipt file'):
        receipts.read(path, trusted=trusted)
    assert receipts.receipts(path.parent, trusted=trusted) == []
"""
        result = subprocess.run([sys.executable, '-c', code, str(path)],
                                cwd=Path(receipts.__file__).parent, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_is_the_newest_install_not_yet_undone(self):
        first = self.install()
        second = self.install()
        rows = receipts.receipts(self.directory)
        self.assertEqual(receipts.latest(rows, package='paint')['id'], second['id'])
        receipts.mark_undone(self.directory / (second['id'] + '.json'), second)
        self.assertEqual(receipts.latest(receipts.receipts(self.directory), package='paint')['id'], first['id'])
        self.assertIsNone(receipts.latest(rows, package='other'))

    def test_replacement_and_upgrade_receipts_offer_snapshot_after_restart(self):
        for before in (dict(BEFORE, oldpaint='1'), dict(BEFORE, glibc='2')):
            with self.subTest(before=before):
                value = receipts.record('arch', '', 'paint', 'paint', before, AFTER, '42',
                                        snapshot_token='42:' + 'a' * 64, directory=self.directory)
                restored = receipts.read(self.directory / (value['id'] + '.json'), trusted=False)
                plan = receipts.undo_plan(restored, Mock(side_effect=AssertionError('no package removal preview')))
                self.assertEqual((plan['mode'], plan['remove'], plan['restart']), ('snapshot', [], True))
                self.assertEqual(plan['restoreToken'], '42:' + 'a' * 64)
                self.assertEqual(restored['removed'], {'oldpaint': '1'} if 'oldpaint' in before else {})
                self.assertEqual(receipts.summary(restored)['mode'], 'snapshot')

    def test_old_upgrade_receipt_resolves_snapshot_from_public_index(self):
        value = self.install(before=dict(BEFORE, glibc='2'))
        value.pop('snapshotToken')
        value.pop('removed')
        with patch.object(receipts.os, 'geteuid', return_value=1000), \
                patch.object(receipts.restore, 'read_index', return_value=[{'id': '42:' + 'b' * 64}]):
            self.assertEqual(receipts.undo_plan(value, Pacman(AFTER))['restoreToken'], '42:' + 'b' * 64)
        self.assertTrue(receipts.valid(value))

    def test_old_receipts_are_pruned(self):
        for _ in range(receipts.KEEP + 3):
            self.install()
        self.assertEqual(len(list(self.directory.glob('*.json'))), receipts.KEEP)

    def test_undo_removes_exactly_what_the_install_added_and_still_owns(self):
        value = self.install()
        now = dict(AFTER, glibc='4')
        plan = receipts.undo_plan(value, Pacman(now))
        self.assertTrue(plan['ok'], plan)
        self.assertEqual(plan['remove'], ['libbrush', 'paint', 'python-pillow'])
        self.assertEqual(plan['upgraded'], [])
        self.assertEqual(plan['receipt'], value['id'])
        self.assertEqual(plan['snapshot'], '42')

    def test_undo_keeps_packages_marked_explicit_since_system_and_emaki_parts(self):
        after = dict(AFTER, mesa='25', **{'emaki-config': '1'})
        value = self.install(after=after)
        pacman = Pacman(after, reasons={'python-pillow': 'Explicitly installed', 'paint': 'Explicitly installed'})
        plan = receipts.undo_plan(value, pacman)
        self.assertEqual(plan['remove'], ['libbrush', 'paint'])
        self.assertEqual(plan['keep'], {'python-pillow': 'explicit', 'mesa': 'system', 'emaki-config': 'emaki'})
        removal = next(call for call in pacman.calls if call[0] == '-Rp')
        self.assertEqual(removal, ('-Rp', '--print-format', '%n', 'libbrush', 'paint'))
        self.assertNotIn('-Rs', [call[0] for call in pacman.calls])

    def test_an_optional_emaki_package_can_be_taken_back(self):
        after = dict(BEFORE, **{'emaki-sudo-key': '0.8.0-1'})
        value = self.install('emaki-sudo-key', after=after)
        self.assertEqual(receipts.undo_plan(value, Pacman(after))['remove'], ['emaki-sudo-key'])
        after = dict(BEFORE, **{'emaki-config': '2'})
        value = self.install('emaki-config', after=after)
        self.assertEqual(receipts.undo_plan(value, Pacman(after))['code'], 'emaki')

    def test_nvidia_shelf_package_requires_restore(self):
        after = dict(BEFORE, **{'emaki-nvidia': '0.8.0-1'})
        value = self.install('emaki-nvidia', after=after)
        plan = receipts.undo_plan(value, Pacman(after))
        self.assertEqual(plan, dict(ok=False, code='restore-only'))

    def test_packages_needed_by_a_later_install_refuse_with_their_names(self):
        value = self.install()
        refusal = ":: removing libbrush breaks dependency 'libbrush' required by sketch\n"
        plan = receipts.undo_plan(value, Pacman(AFTER, refuse=refusal))
        self.assertEqual((plan['ok'], plan['code'], plan['requiredBy']), (False, 'required', ['sketch']))

    def test_removed_program_and_undone_receipt_offer_nothing(self):
        value = self.install()
        self.assertEqual(receipts.undo_plan(value, Pacman(BEFORE))['code'], 'not-installed')
        self.assertEqual(receipts.undo_plan(dict(value, undone='x'), Pacman(AFTER))['code'], 'undone')

    def test_token_binds_the_receipt_and_the_installed_versions(self):
        value = self.install()
        first = receipts.undo_plan(value, Pacman(AFTER))['token']
        self.assertEqual(first, receipts.undo_plan(value, Pacman(AFTER))['token'])
        self.assertNotEqual(first, receipts.undo_plan(value, Pacman(dict(AFTER, paint='2-2')))['token'])
        other = self.install()
        self.assertNotEqual(first, receipts.undo_plan(other, Pacman(AFTER))['token'])

    def test_the_snapshot_before_the_first_transaction_is_found(self):
        root = Path(self.tmp.name) / 'snapshots'
        for number, kind, date in ((7, 'pre', '2026-10-12 10:00:00'), (9, 'pre', '2026-10-12 12:00:01'),
                                   (10, 'post', '2026-10-12 12:05:00'), (11, 'pre', '2026-10-12 12:06:00')):
            (root / str(number)).mkdir(parents=True)
            (root / str(number) / 'info.xml').write_text(
                f'<snapshot><type>{kind}</type><num>{number}</num><date>{date}</date></snapshot>')
        since = datetime(2026, 10, 12, 12, 0, 0, 500000, tzinfo=timezone.utc)
        self.assertEqual(receipts.first_snapshot(since, root), '9')
        self.assertEqual(receipts.first_snapshot(since, root / 'missing'), '')

    def test_snapshot_selection_excludes_completed_update_in_same_second(self):
        root = Path(self.tmp.name) / 'snapshots'
        for number in ('7', '9'):
            (root / number).mkdir(parents=True)
            (root / number / 'info.xml').write_text(
                '<snapshot><type>pre</type><date>2026-10-12 12:00:00</date></snapshot>')
        since = datetime(2026, 10, 12, 12, 0, 0, 500000, tzinfo=timezone.utc)
        self.assertEqual(receipts.first_snapshot(since, root, exclude={'7'}), '9')


class UndoHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-undo-')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.enterContext(patch.object(receipts, 'DIRECTORY', self.directory))
        self.value = receipts.record('arch', '', 'paint', 'paint', BEFORE, AFTER, directory=self.directory)
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        # The test owns its files; production reads them only when root owns them.
        reader = receipts.read
        self.enterContext(patch.object(helper.receipts, 'read', side_effect=lambda path, trusted=True: reader(path, False)))

    def result(self):
        return json.loads(self.output.getvalue().splitlines()[-1])

    def test_strict_arguments_refuse_before_privileged_work(self):
        for argv in ([], [self.value['id'], 'paint'], ['../x', 'paint', 'a' * 64], [self.value['id'], '--root', 'a' * 64],
                     [self.value['id'], 'paint', 'not-a-token']):
            with self.subTest(argv=argv), patch.object(helper.os, 'open') as opened:
                self.assertEqual(helper.main(argv), 1)
                opened.assert_not_called()

    def test_another_programs_receipt_or_changed_preview_removes_nothing(self):
        pacman = Pacman(AFTER)
        with patch.object(helper, 'pacman', side_effect=pacman), patch.object(helper.subprocess, 'Popen') as popen:
            helper.undo(self.value['id'], 'other', 'a' * 64)
            self.assertEqual(self.result()['code'], 'refused')
            helper.undo(self.value['id'], 'paint', 'a' * 64)
            self.assertEqual(self.result()['code'], 'changed')
            popen.assert_not_called()

    def test_reviewed_snapshot_undo_delegates_restore_and_never_removes_packages(self):
        value = receipts.record('arch', '', 'paint', 'paint', dict(BEFORE, oldpaint='1'), AFTER,
                                '42', snapshot_token='42:' + 'a' * 64, directory=self.directory)
        token = receipts.undo_plan(value, Pacman(AFTER))['token']
        with patch.object(helper.restore, 'restore_install_point', return_value=dict(ok=True, restart=True)) as run, \
                patch.object(helper.subprocess, 'Popen') as popen:
            self.assertEqual(helper.undo(value['id'], 'paint', token), 0)
        run.assert_called_once_with('42:' + 'a' * 64)
        popen.assert_not_called()
        self.assertTrue(self.result()['restart'])
        self.assertEqual(self.result()['code'], 'restored')

    def test_snapshot_failure_preserves_receipt_and_pending_restart(self):
        value = receipts.record('arch', '', 'paint', 'paint', dict(BEFORE, oldpaint='1'), AFTER,
                                '42', snapshot_token='42:' + 'a' * 64, directory=self.directory)
        token = receipts.undo_plan(value, Pacman(AFTER))['token']
        with patch.object(helper.restore, 'restore_install_point', return_value=dict(ok=False, restart=True)), \
                patch.object(helper.subprocess, 'Popen') as popen:
            self.assertEqual(helper.undo(value['id'], 'paint', token), 1)
        popen.assert_not_called()
        self.assertTrue(self.result()['restart'])
        self.assertFalse(receipts.read(self.directory / (value['id'] + '.json'), False)['undone'])

    def test_reviewed_undo_removes_exactly_the_receipt_packages_and_marks_it(self):
        pacman = Pacman(AFTER)
        token = receipts.undo_plan(self.value, pacman)['token']
        process = Mock(returncode=0)
        process.communicate.return_value = ('', '')
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)

        def gone(*arguments, **options):
            if arguments[0] == '-Q' and len(arguments) == 2:
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            return pacman(*arguments)
        with patch.object(helper, 'pacman', side_effect=gone), \
                patch.object(helper.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(helper.signal, 'signal'):
            self.assertEqual(helper.undo(self.value['id'], 'paint', token), 0)
        self.assertEqual(popen.call_args.args[0], ['pacman', '-Rn', '--noconfirm', '--',
                                                   'libbrush', 'paint', 'python-pillow'])
        self.assertTrue(self.result()['ok'])
        self.assertTrue(receipts.read(self.directory / (self.value['id'] + '.json'), False)['undone'])


class FlatpakUndoTests(unittest.TestCase):
    APP = 'app/org.example.Editor/x86_64/stable'
    RUNTIME = 'runtime/org.example.Platform/x86_64/24.08'
    OTHER = 'runtime/org.other.Platform/x86_64/1'
    ROW = dict(installation='system', origin='flathub', ref=APP)

    def receipt(self, **changes):
        value = dict(kind='flatpak', id='20261012T120000000000Z-' + 'a' * 32, target=self.APP, undone='',
                     refs=[self.APP, self.RUNTIME])
        value.update(changes)
        return value

    def test_undo_has_no_global_unused_fallback(self):
        with patch.object(flatpak, '_transaction', return_value=dict(ok=False, code='undo-scope-refused')) as tx:
            self.assertEqual(flatpak.undo_plan(self.ROW, self.receipt())['code'], 'undo-scope-refused')
        tx.assert_called_once_with('undo', self.ROW, None, None, [self.RUNTIME])

    def test_receipt_of_another_program_or_an_undone_install_is_refused(self):
        with patch.object(flatpak, '_transaction') as transaction:
            self.assertEqual(flatpak.undo_plan(self.ROW, self.receipt(target=self.OTHER))['code'], 'receipt-mismatch')
            self.assertEqual(flatpak.undo_plan(self.ROW, self.receipt(undone='x'))['code'], 'receipt-mismatch')
            transaction.assert_not_called()


if __name__ == '__main__':
    unittest.main()
