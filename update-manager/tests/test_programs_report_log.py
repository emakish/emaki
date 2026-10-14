#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transaction journal selection, bounded diagnostics and failure isolation."""
from pathlib import Path
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine


class ReportLogTests(unittest.TestCase):
    def setUp(self):
        self.log = self.enterContext(patch.object(engine, 'syslog'))
        self.engine = engine.Engine(SimpleNamespace(), '/unused-cache')
        self.emit = Mock()

    def records(self):
        return [call.args[1] for call in self.log.syslog.call_args_list]

    def test_refused_plan_records_one_line_and_no_transaction_tokens(self):
        refusal = dict(ok=False, code='metadata-unavailable', message='Repository metadata is incomplete.\nRefresh.',
                       token='private-token', transaction=dict(token='private-nested-token'))
        with patch.object(self.engine, '_plan', return_value=refusal):
            self.assertIs(self.engine.plan('install', 'private-key'), refusal)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0], 'action=plan event=failed code=metadata-unavailable message=Repository metadata is incomplete. Refresh.')
        self.assertNotIn('private-', str(self.log.mock_calls))

    def test_plan_exception_records_once_and_success_is_quiet(self):
        with patch.object(self.engine, '_plan', side_effect=engine.arch.MetadataUnavailable()):
            self.assertEqual(self.engine.plan('install')['code'], 'metadata-unavailable')
        self.assertEqual(len(self.records()), 1)
        self.log.reset_mock()
        with patch.object(self.engine, '_plan', return_value={'ok': True, 'token': 'private-token'}):
            self.assertTrue(self.engine.plan('install')['ok'])
        self.assertEqual(self.records(), [])

    def test_progress_failure_and_tokens_are_not_dumped(self):
        fields = dict(message='[PLACEHOLDER] Applying transaction.', stage='apply', percent=25,
                      token='private-progress-token')
        answer = dict(ok=False, code='failed', message='[PLACEHOLDER] Transaction failed.',
                      details=dict(error='disk full', token='private-details-token'),
                      plan=dict(token='private-plan-token'))
        def execute(action, key, token, emit):
            emit('progress', **fields)
            return answer
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.assertIs(self.engine.execute('install', 'private-key', 'private-token', self.emit), answer)
        self.emit.assert_called_once_with('progress', **fields)
        records = self.records()
        self.assertEqual([record.splitlines()[0] for record in records],
                         ['action=install event=started', 'action=install event=failed'])
        self.assertIn('details.error: disk full', records[-1])
        self.assertNotIn('private-', str(self.log.mock_calls))
        self.assertEqual(self.log.openlog.call_args.args[0], 'emaki-programs-engine')
        self.assertIsNone(self.engine.rows)

    def test_many_progress_ticks_preserve_only_transaction_boundaries(self):
        def execute(action, key, token, emit):
            for percent in range(1000):
                emit('progress', stage='download', percent=percent, message='Downloading packages')
            return {'ok': True}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('install', '', '', self.emit)
        self.assertEqual(self.emit.call_count, 1000)
        self.assertEqual([record.splitlines()[0] for record in self.records()],
                         ['action=install event=started', 'action=install event=finished'])

    def test_error_events_preserve_raw_diagnostics_and_stdout(self):
        fields = {'error': 'password="two words"\nconnection failed',
                  'token': 'private-error-token'}
        def execute(action, key, token, emit):
            emit('error', **fields)
            return {'ok': False, 'code': 'failed'}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('install', '', '', self.emit)
        self.emit.assert_called_once_with('error', **fields)
        self.assertEqual([record.splitlines()[0] for record in self.records()],
                         ['action=install event=started', 'action=install event=error',
                          'action=install event=failed'])
        self.assertIn(fields['error'], self.records()[1])
        self.assertNotIn('private-error-token', str(self.log.mock_calls))

    def test_update_identifier_and_success(self):
        with patch.object(self.engine, '_execute', return_value={'ok': True}):
            self.assertEqual(self.engine.execute('update', '', 'unused', self.emit), {'ok': True})
        self.assertEqual(self.log.openlog.call_args.args[0], 'emaki-update-manager')
        self.assertEqual(self.records()[-1], 'action=update event=finished\nok: True')

    def test_success_does_not_journal_error_words_or_intermediate_events(self):
        events = [('progress', {'message': 'Installing python-error-handler'}),
                  ('progress', {'message': 'Downloading liberror package', 'percent': 50}),
                  ('progress', {'message': 'Flatpak warning [error: recovered failure]'}),
                  ('error', {'error': 'Recovered temporary failure'})]
        def execute(action, key, token, emit):
            for kind, fields in events:
                emit(kind, **fields)
                self.assertEqual(len(self.records()), 1)
            return {'ok': True}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('install', '', '', self.emit)
        self.assertEqual([(call.args[0], call.kwargs) for call in self.emit.call_args_list], events)
        self.assertEqual([record.splitlines()[0] for record in self.records()],
                         ['action=install event=started', 'action=install event=finished'])

    def test_partial_result_keeps_structured_cause_after_routine_messages(self):
        for status in ({'ok': True, 'status': 'partial'},
                       {'ok': True, 'code': 'partial'},
                       {'ok': True, 'partial': True},
                       {'ok': False, 'status': 'failed'}):
            with self.subTest(status=status):
                self.log.reset_mock()
                def execute(action, key, token, emit):
                    emit('progress', stage='failed', message='Remote summary unavailable')
                    emit('progress', stage='download', message='Downloading remaining packages')
                    self.assertEqual(len(self.records()), 1)
                    return status
                with patch.object(self.engine, '_execute', side_effect=execute):
                    self.assertIs(self.engine.execute('update', '', '', self.emit), status)
                self.assertEqual(len(self.records()), 3)
                self.assertIn('Remote summary unavailable', self.records()[1])
                self.assertNotIn('Downloading remaining packages', '\n'.join(self.records()))
                self.assertTrue(self.records()[-1].startswith('action=update event=failed\n'))

    def test_update_progress_failure_cause_reaches_journal(self):
        cause = 'flatpak: error: Unable to load summary from remote flathub: Could not resolve hostname'
        backend = SimpleNamespace()
        events = [('progress', {'message': 'Updating all repository packages. You can hide this window; the update will keep running.'}),
                  ('progress', {'message': cause})]
        def apply(cache):
            for kind, fields in events:
                backend.emit(kind, **fields)
            backend.emit('finished', ok=False, message='System updates finished, but Flatpak updates did not finish. Review the details before trying again.')
        backend.apply = apply
        token = 'a' * 64
        with patch.object(engine, 'update_backend', return_value=backend), patch.object(
                self.engine, 'plan', return_value={'ok': True, 'token': token}):
            answer = self.engine.execute('update', '', token, self.emit)
        self.assertFalse(answer['ok'])
        self.assertEqual([(call.args[0], call.kwargs) for call in self.emit.call_args_list], events)
        self.assertIn(cause, '\n'.join(self.records()))
        self.assertEqual(len(self.records()), 3)

    def test_failure_progress_content_and_stage_survive_later_ticks(self):
        for fields in ({'stage': 'failed', 'message': 'Remote summary unavailable'},
                       {'error': 'Remote summary unavailable'},
                       {'message': 'Flatpak update stopped [error: Remote summary unavailable]'},
                       {'message': 'Flatpak: Remote summary unavailable; ERROR: fetch failed'},
                       {'message': 'error: Remote summary unavailable'}):
            with self.subTest(fields=fields):
                self.log.reset_mock()
                def execute(action, key, token, emit):
                    emit('progress', **fields)
                    for percent in range(1000):
                        emit('progress', stage='download', percent=percent)
                    return {'ok': False}
                with patch.object(self.engine, '_execute', side_effect=execute):
                    self.engine.execute('update', '', '', self.emit)
                self.assertIn('Remote summary unavailable', '\n'.join(self.records()))
                self.assertEqual(len(self.records()), 3)

    def test_real_update_backend_retains_flatpak_exceptions(self):
        backend = engine.update_backend()
        causes = [ValueError('[PLACEHOLDER] The Flatpak package list contains duplicate entries.'),
                  OSError('Flatpak executable is missing'),
                  RuntimeError('The update could not finish. [error: Remote "flathub" not found]'),
                  RuntimeError('The update could not finish. [pacman returned an error]')]
        for error in causes:
            with self.subTest(error=error):
                self.log.reset_mock()
                self.emit.reset_mock()
                helper = Mock()
                helper.stdout = io.StringIO(json.dumps({'type': 'finished', 'ok': True}) + '\n')
                helper.wait.return_value = 0
                token = 'a' * 64
                with patch.object(engine, 'update_backend', return_value=backend), patch.object(
                        self.engine, 'plan', return_value={'ok': True, 'token': token}), patch.object(
                        backend.subprocess, 'Popen') as popen, patch.object(
                        backend.flatpaks, 'update', side_effect=error), patch.object(
                        backend, 'local_state', return_value={}), patch.object(
                        backend.reminder, 'notify_completed') as notify, patch.object(
                        backend.reminder, 'offer_report') as offer:
                    # Popen is patched module-wide, so the report offer (its own subprocess.run) is stubbed too.
                    popen.return_value.__enter__.return_value = helper
                    answer = self.engine.execute('update', '', token, self.emit)
                offer.assert_called_with('update')
                self.assertFalse(answer['ok'])
                self.emit.assert_any_call('progress', stage='failed', message=str(error))
                self.assertIn(str(error), self.records()[1])
                self.assertEqual(len(self.records()), 3)
                notify.assert_not_called()

    def test_unlabelled_final_failure_detail_is_retained(self):
        cause = "[Errno 2] No such file or directory: 'flatpak'"
        def execute(action, key, token, emit):
            emit('progress', message=cause)
            for percent in range(1000):
                emit('progress', stage='download', percent=percent)
            return {'ok': False, 'message': 'Flatpak updates did not finish.'}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('update', '', '', self.emit)
        self.assertIn(cause, '\n'.join(self.records()))
        self.assertEqual(len(self.records()), 3)

    def test_last_error_line_survives_post_transaction_progress(self):
        causes = ['error: failed to commit transaction',
                  'error: package verification failed']
        def execute(action, key, token, emit):
            for cause in causes:
                emit('progress', message=cause)
            emit('progress', message=':: Running post-transaction hooks...')
            emit('progress', message='Installing python-error-handler')
            return {'ok': False, 'message': 'Repository updates did not finish.'}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('update', '', '', self.emit)
        self.assertEqual(len(self.records()), 3)
        self.assertIn(causes[-1], self.records()[1])
        self.assertNotIn(causes[0], '\n'.join(self.records()))
        self.assertNotIn('post-transaction', '\n'.join(self.records()))
        self.assertNotIn('python-error-handler', '\n'.join(self.records()))
        self.assertEqual(self.emit.call_count, 4)

    def test_routine_progress_defers_to_transaction_failure_result(self):
        for result in ({'ok': False, 'message': 'Repository updates did not finish.'},
                       {'ok': True, 'partial': True, 'error': 'Some updates failed.'}):
            with self.subTest(result=result):
                self.log.reset_mock()
                def execute(action, key, token, emit):
                    emit('progress', message=':: Running post-transaction hooks...')
                    emit('progress', message='Installing python-error-handler')
                    return result
                with patch.object(self.engine, '_execute', side_effect=execute):
                    self.assertIs(self.engine.execute('update', '', '', self.emit), result)
                self.assertEqual(len(self.records()), 2)
                self.assertTrue(self.records()[-1].startswith('action=update event=failed\n'))
                self.assertIn(result.get('error', result.get('message')), self.records()[-1])
                self.assertNotIn('post-transaction', '\n'.join(self.records()))
                self.assertNotIn('python-error-handler', '\n'.join(self.records()))

    def test_multiline_progress_keeps_only_last_error_line(self):
        def execute(action, key, token, emit):
            emit('progress', message='error: first cause\nflatpak: error: final cause\n'
                 ':: Running post-transaction hooks...')
            return {'ok': False}
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.engine.execute('update', '', '', self.emit)
        self.assertEqual(len(self.records()), 3)
        self.assertIn('flatpak: error: final cause', self.records()[1])
        self.assertNotIn('first cause', '\n'.join(self.records()))
        self.assertNotIn('post-transaction', '\n'.join(self.records()))

    def test_exception_is_rethrown_without_dumping_its_arguments(self):
        error = RuntimeError('private-token')
        with patch.object(self.engine, '_execute', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.engine.execute('remove', '', '', self.emit)
        self.assertIs(caught.exception, error)
        self.assertIn('error: RuntimeError', self.records()[-1])
        self.assertNotIn('private-token', str(self.log.mock_calls))
        self.assertIsNone(self.engine.rows)

    def test_logging_unavailable_preserves_emission_result_and_invalidation(self):
        self.log.openlog.side_effect = OSError('journal unavailable')
        answer = {'ok': False}
        def execute(action, key, token, emit):
            self.engine.rows = {'stale': True}
            emit('progress', message='[PLACEHOLDER] Applying transaction.')
            return answer
        with patch.object(self.engine, '_execute', side_effect=execute):
            self.assertIs(self.engine.execute('install', '', '', self.emit), answer)
        self.emit.assert_called_once()
        self.assertIsNone(self.engine.rows)

    def test_emitter_exception_keeps_original_behavior(self):
        error = RuntimeError('output disconnected')
        self.emit.side_effect = error
        def execute(action, key, token, emit):
            emit('progress', message='[PLACEHOLDER] Applying transaction.')
        with patch.object(self.engine, '_execute', side_effect=execute):
            with self.assertRaises(RuntimeError) as caught:
                self.engine.execute('install', '', '', self.emit)
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.engine.rows)

    def test_flatpak_user_transaction_passes_through_same_journal_wrapper(self):
        row = {'kind': 'flatpak', 'installation': 'user'}
        token = 'a' * 64
        def plan(action, key):
            self.engine.rows = {'app': row}
            return {'ok': True, 'token': token, 'transaction': {'token': 'native-private'}}
        def execute(action, actual_row, native_token, emit):
            self.assertIs(actual_row, row)
            self.assertEqual(native_token, 'native-private')
            emit('progress', kind='flatpak', percent=50)
            return {'ok': False, 'code': 'transaction-failed'}
        with patch.object(self.engine, 'plan', side_effect=plan), patch.object(
                engine.flatpak, 'execute', side_effect=execute):
            answer = self.engine.execute('install', 'app', token, self.emit)
        self.assertFalse(answer['ok'])
        self.assertEqual(len(self.records()), 2)
        self.assertNotIn('native-private', str(self.log.mock_calls))
        self.emit.assert_called_once_with('progress', kind='flatpak', percent=50)

    def test_log_messages_are_bounded_utf8_and_preserve_redaction_input(self):
        secret = 'password="two words"\n-----BEGIN PRIVATE KEY-----\n'
        engine.report_event('install', 'error', {'message': secret + 'é' * 10000})
        message = self.log.syslog.call_args.args[1]
        self.assertLessEqual(len(message.encode()), 4096)
        self.assertIn(secret, message)


if __name__ == '__main__':
    unittest.main()
