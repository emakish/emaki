#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise update/restore protocol integration without host mutation."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'update-manager'))
import backend

spec = importlib.util.spec_from_file_location('apply_helper', ROOT / 'update-manager/apply.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
TOKEN = '12:' + 'a' * 64


class Protocol(unittest.TestCase):
    def test_invalid_restore_tokens_refuse_before_cache_or_authorization(self):
        for arguments in (['--restore'], ['--restore', '12'], ['--restore', '../x'],
                          ['--restore', TOKEN, 'extra'], ['--restore-points', 'extra']):
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['backend', *arguments]), \
                    patch.object(backend.subprocess, 'Popen') as opening, \
                    patch.object(backend.os, 'geteuid') as uid, patch.object(sys, 'stderr', io.StringIO()), \
                    self.assertRaises(SystemExit):
                backend.main()
            opening.assert_not_called()
            uid.assert_not_called()

    def test_restore_authentication_cancellation_and_connection_loss_are_explicit(self):
        for operation in ('--restore', '--restore-points'):
            for code, pending in ((126, False), (1, True)):
                process = Mock()
                process.__enter__ = Mock(return_value=process)
                process.__exit__ = Mock(return_value=False)
                process.stdout = io.StringIO('unreadable\n')
                process.wait.return_value = code
                with patch.object(backend.subprocess, 'Popen', return_value=process) as opening, \
                        patch.object(backend, 'rollback_pending', return_value=pending), \
                        patch.object(backend, 'emit') as emit:
                    backend.restore(operation, TOKEN if operation == '--restore' else None)
                args = opening.call_args.args[0]
                if operation == '--restore-points':
                    self.assertEqual(args, [str(ROOT / 'update-manager/emaki-update-restore'), 'list'])
                else:
                    self.assertEqual(args, ['pkexec', str(ROOT / 'update-manager/emaki-update-restore'), 'restore', TOKEN])
                self.assertEqual(emit.call_args.kwargs.get('restorePending', emit.call_args.kwargs.get('restart')), pending)
                self.assertNotIn('shell', opening.call_args.kwargs)

    def test_only_restore_protocol_is_relayed(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        answer = dict(type='restorePoints', points=[dict(id=TOKEN)], error='')
        process.stdout = io.StringIO(json.dumps(dict(type='finished', ok=True)) + '\n' + json.dumps(answer) + '\n')
        process.wait.return_value = 0
        with patch.object(backend.subprocess, 'Popen', return_value=process), patch.object(backend, 'emit') as emit:
            backend.restore('--restore-points')
        emit.assert_called_once_with('restorePoints', points=answer['points'], error='')

    def test_local_state_keeps_restart_available_for_pending_restore_after_dismissal(self):
        with patch.object(backend.reminder, 'status', return_value=dict(restart=False, reminder=False, restartPackages=[])), \
                patch.object(backend, 'rollback_pending', return_value=True):
            state = backend.local_state(Path('/unused'))
        self.assertTrue(state['restorePending'])
        self.assertTrue(state['restart'])
        self.assertFalse(state['reminder'])

    def test_restore_policy_uses_distinct_authentication_without_retained_permission(self):
        action = ET.parse(ROOT / 'update-manager/org.emaki.update.restore.policy').getroot().find('action')
        self.assertEqual(action.attrib['id'], 'org.emaki.update.restore')
        self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin')
        self.assertEqual(action.findtext('defaults/allow_inactive'), 'no')
        self.assertEqual(action.findtext('defaults/allow_any'), 'no')
        annotations = {item.attrib['key']: item.text for item in action.findall('annotate')}
        self.assertEqual(annotations['org.freedesktop.policykit.exec.path'], '@EMAKI_LIBEXECDIR@/emaki-update-restore')
        self.assertEqual(annotations['org.freedesktop.policykit.exec.argv1'], 'restore')
        self.assertIn('restore the system', action.findtext('message'))
        self.assertNotIn('read or', action.findtext('message'))


class UpdateRoot(unittest.TestCase):
    def test_normal_and_unsupported_roots_can_update(self):
        for mode in ('normal', 'unsupported'):
            with patch.object(helper, 'run', return_value=Mock(returncode=0, stdout=json.dumps(dict(mode=mode)))):
                helper.require_update_root()

    def test_pending_snapshot_unknown_and_failed_status_refuse(self):
        for mode in ('pending', 'snapshot', 'unknown', None):
            with patch.object(helper, 'run', return_value=Mock(returncode=0, stdout=json.dumps(dict(mode=mode)))), \
                    self.assertRaises(RuntimeError):
                helper.require_update_root()
        for response in (Mock(returncode=1, stdout=''), Mock(returncode=0, stdout='[]')):
            with patch.object(helper, 'run', return_value=response), self.assertRaises(RuntimeError):
                helper.require_update_root()

    def test_restore_refusal_cannot_read_versions_or_start_service(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = os.open(Path(directory) / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
            with patch.object(helper.os, 'open', return_value=descriptor), \
                    patch.object(helper, 'require_update_root', side_effect=RuntimeError('Restart first.')), \
                    patch.object(helper, 'versions') as versions, \
                    patch.object(helper.subprocess, 'Popen') as opening, patch.object(helper, 'emit') as emit:
                self.assertEqual(helper.apply(), 1)
            versions.assert_not_called()
            opening.assert_not_called()
            self.assertTrue(emit.call_args.kwargs['notStarted'])


if __name__ == '__main__':
    unittest.main()
