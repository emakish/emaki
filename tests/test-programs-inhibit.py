#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every Programs package transaction inherits its shutdown/sleep lease."""
import contextlib
import gettext
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'update-manager'))
import emaki_update_inhibit as lease
import programs_aur_build as aur
import programs_install as install
import programs_remove as remove
import programs_undo as undo


class Transactions(unittest.TestCase):
    @contextlib.contextmanager
    def backend(self):
        reader, writer = os.pipe()
        os.set_blocking(reader, False)
        commands = []
        real_popen = subprocess.Popen

        def popen(arguments, **options):
            self.assertEqual(options.get('pass_fds'), (writer,))
            # The parent already owns the lease before the child/download starts.
            with self.assertRaises(BlockingIOError):
                os.read(reader, 1)
            commands.append(arguments)
            return real_popen([sys.executable, '-c',
                               'import os,sys; os.write(int(sys.argv[1]), b"download\\ncommit\\n")',
                               str(writer)], **options)

        try:
            with patch.object(lease.state, 'foreign_root', return_value=False), \
                    patch.object(lease, 'acquire', return_value=writer), \
                    patch.object(subprocess, 'Popen', side_effect=popen), \
                    patch.object(remove.signal, 'signal'), \
                    contextlib.redirect_stdout(io.StringIO()):
                yield commands
            self.assertEqual(os.read(reader, 1024), b'download\ncommit\n')
            self.assertEqual(os.read(reader, 1), b'')
            with self.assertRaises(OSError):
                os.fstat(writer)
        finally:
            os.close(reader)
            try:
                os.close(writer)
            except OSError:
                pass

    def aur_install(self, operation):
        with self.backend() as commands, \
                patch.object(aur, 'versions', return_value={'sample': '1'}), \
                patch.object(aur, 'run', return_value=Mock(returncode=0, stdout='sample 1\n')):
            self.assertEqual(aur.add_only(['pacman', operation, '--asdeps', '--', 'sample'],
                                         {'sample': '1'}, set()), 0)
        self.assertEqual(commands[0][1], operation)

    def test_aur_dependencies_hold_lease_before_download(self):
        self.aur_install('-S')

    def test_aur_package_install_inherits_lease(self):
        self.aur_install('-U')

    def test_aur_build_tool_cleanup_inherits_lease(self):
        with self.backend() as commands, patch.object(aur, 'run', side_effect=[
                Mock(returncode=0, stdout='sample\n', stderr=''),
                Mock(returncode=1, stdout='', stderr='')]):
            self.assertEqual(aur.remove_leftovers({}, {'sample'}), ['sample'])
        self.assertEqual(commands[0][1], '-Rn')

    def test_aur_failed_install_cleanup_inherits_lease(self):
        with self.backend() as commands, patch.object(aur, 'versions', side_effect=[{'sample': '1'}, {}, {}]):
            aur.rollback_additions({}, {'sample'})
        self.assertEqual(commands[0][1], '-Rn')

    def test_program_removal_inherits_lease(self):
        with self.backend() as commands, patch.object(remove, 'is_program', return_value=True), \
                patch.object(remove.catalog, 'is_protected', return_value=False), \
                patch.object(remove.catalog, 'assess', return_value=dict(ok=True, token='token', packages=['sample'])), \
                patch.object(remove, 'pacman', return_value=Mock(returncode=1)), patch.object(remove, 'emit') as emit:
            remove.remove('sample', 'token')
            emit.assert_called_once_with('removed', packages=['sample'])
        self.assertEqual(commands[0][1], '-Rn')

    def test_undo_inherits_lease(self):
        with self.backend() as commands, \
                patch.object(undo.receipts, 'read', return_value=dict(kind='repository', target='sample')), \
                patch.object(undo.receipts, 'undo_plan', return_value=dict(ok=True, token='token', remove=['sample'], keep=[])), \
                patch.object(undo.receipts, 'mark_undone'), \
                patch.object(undo, 'pacman', return_value=Mock(returncode=1)), patch.object(undo, 'emit') as emit:
            undo.undo('receipt', 'sample', 'token')
            emit.assert_called_once_with('undone', packages=['sample'], kept=[])
        self.assertEqual(commands[0][1], '-Rn')

    def test_kit_removal_inherits_lease(self):
        with self.backend() as commands, patch.object(install, 'preinstall_snapshot', return_value=(1, 'token')), \
                patch.object(install.receipts, 'receipts', return_value=[]), patch.object(install, 'finish') as finish:
            install.remove_kit(dict(id='go'), dict(targets=['sample']), Mock(versions=Mock(return_value={})), None)
            self.assertEqual(finish.call_args.args, ('removed',))
        self.assertEqual(commands[0][1], '-Rn')

    def test_docker_restore_and_snapshot_hold_lease(self):
        events = []

        def protected(step):
            lease.acquire.assert_called_once()
            os.fstat(lease.acquire.return_value)
            events.append(step)

        def snapshot(**kwargs):
            protected('snapshot')
            return 1, 'token'

        before = dict(uid=1000, user='sample', enabled=False, member=False)
        with self.backend() as commands, \
                patch.object(install, 'preinstall_snapshot', side_effect=snapshot), \
                patch.object(install.kits, 'receipt_for', return_value=dict(kit=dict(docker=before))), \
                patch.object(install.kits, 'restore_docker', side_effect=lambda value: protected('restore')) as restore, \
                patch.object(install.receipts, 'receipts', return_value=[]), \
                patch.object(install, 'finish') as finish:
            install.remove_kit(dict(id='docker'), dict(targets=['sample']),
                               Mock(versions=Mock(return_value={})), dict(before, member=True))
            restore.assert_called_once_with(before)
            self.assertEqual(finish.call_args.args, ('removed',))
        self.assertEqual(events, ['snapshot', 'restore'])
        self.assertEqual(commands[0][1], '-Rn')

    def test_docker_refused_lease_changes_nothing(self):
        output = io.StringIO()
        backend = Mock()
        with patch.object(lease.state, 'foreign_root', return_value=False), \
                patch.object(lease, 'acquire', side_effect=lease.AcquisitionError('refused')), \
                patch.object(install, 'preinstall_snapshot') as snapshot, \
                patch.object(install.kits, 'restore_docker') as restore, \
                patch.object(install.receipts, 'mark_undone') as mark, \
                patch.object(subprocess, 'run') as run, \
                contextlib.redirect_stdout(output):
            install.remove_kit(dict(id='docker'), dict(targets=['sample']), backend,
                               dict(uid=1000, user='sample', member=True))
        self.assert_refused(output)
        for operation in (snapshot, restore, mark, run, backend.versions):
            operation.assert_not_called()

    def test_kit_snapshot_failure_releases_lease_before_any_removal(self):
        reader, writer = os.pipe()
        try:
            with patch.object(lease.state, 'foreign_root', return_value=False), \
                    patch.object(lease, 'acquire', return_value=writer), \
                    patch.object(install, 'preinstall_snapshot', side_effect=RuntimeError('snapshot failed')), \
                    patch.object(install.kits, 'restore_docker') as restore, \
                    patch.object(subprocess, 'run') as run, \
                    self.assertRaisesRegex(RuntimeError, 'snapshot failed'):
                install.remove_kit(dict(id='docker'), dict(targets=['sample']), Mock(),
                                   dict(uid=1000, user='sample', member=True))
            restore.assert_not_called()
            run.assert_not_called()
            with self.assertRaises(OSError):
                os.fstat(writer)
            self.assertEqual(os.read(reader, 1), b'')
        finally:
            os.close(reader)
            try:
                os.close(writer)
            except OSError:
                pass

    def assert_refused(self, output):
        answer = json.loads(output.getvalue())
        self.assertFalse(answer['ok'])
        self.assertEqual(answer['code'], 'protection-unavailable')
        self.assertEqual(answer['message'],
                         'This change could not be protected from a shutdown. Nothing was changed.')

    def helper_failure(self, helper, error):
        output = io.StringIO()
        token = 'a' * 64
        identity = '20261101T000000000000Z-' + 'a' * 32
        with tempfile.TemporaryDirectory(prefix='emaki-programs-refusal-') as directory, \
                patch.object(helper, 'LOCK', str(Path(directory) / 'lock')), \
                patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.os, 'umask'), \
                patch.object(helper.signal, 'signal'), \
                patch.object(helper.emaki_update_root, 'require_update_root'), \
                patch.object(lease.state, 'foreign_root', return_value=False), \
                patch.object(lease, 'acquire', side_effect=error), \
                patch.object(remove, 'is_program', return_value=True), \
                patch.object(remove.catalog, 'is_protected', return_value=False), \
                patch.object(remove.catalog, 'assess', return_value=dict(ok=True, token=token, packages=['sample'])), \
                patch.object(undo.receipts, 'read', return_value=dict(kind='repository', target='sample')), \
                patch.object(undo.receipts, 'undo_plan', return_value=dict(ok=True, token=token, remove=['sample'], keep=[])), \
                patch.object(undo.receipts, 'mark_undone') as mark, \
                patch.object(helper, 'pacman', return_value=Mock(returncode=0)) as query, \
                patch.object(subprocess, 'Popen') as process, \
                contextlib.redirect_stdout(output):
            arguments = ['sample', token] if helper is remove else [identity, 'sample', token]
            self.assertEqual(helper.main(arguments), 1)
        process.assert_not_called()
        mark.assert_not_called()
        if helper is remove:
            query.assert_called_once_with('-Rs', '--print', '--print-format', '%n', 'sample')
        else:
            query.assert_not_called()
        return output

    def test_remove_and_undo_report_refused_lease(self):
        for helper in (remove, undo):
            with self.subTest(helper=helper.__name__):
                self.assert_refused(self.helper_failure(helper, lease.AcquisitionError('refused')))

    def test_refused_lease_message_is_translated(self):
        source = 'This change could not be protected from a shutdown. Nothing was changed.'
        for language, expected in (
                ('es', 'No se pudo proteger este cambio frente a un apagado. No se cambió nada.'),
                ('pt_BR', 'Não foi possível proteger esta alteração contra um desligamento. Nada foi alterado.')):
            with self.subTest(language=language):
                catalogue = gettext.translation('programs', ROOT / 'i18n/locale', languages=[language])
                self.assertEqual(catalogue.gettext(source), expected)

    def test_unrelated_runtime_failure_is_not_a_lease_refusal(self):
        for helper in (remove, undo):
            with self.subTest(helper=helper.__name__):
                answer = json.loads(self.helper_failure(helper, RuntimeError('other failure')).getvalue())
                self.assertFalse(answer['ok'])
                self.assertEqual(answer['code'], 'unknown')


if __name__ == '__main__':
    unittest.main()
