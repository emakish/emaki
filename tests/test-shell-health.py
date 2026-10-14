#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise shell recovery without a graphical session or a real user manager."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    loader = importlib.machinery.SourceFileLoader('shell_health', str(ROOT / 'scripts/emaki-shell-health'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ShellHealth(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-shell-health-')
        self.addCleanup(self.temp.cleanup)
        # Keep intentional symlink cases distinct from a runner's short TMPDIR alias.
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.override = self.home / '.config/emaki/shell'
        self.packaged = self.base / 'packaged'
        self.runtime = self.base / 'runtime'
        for path in (self.override, self.packaged, self.runtime):
            path.mkdir(parents=True)
        self.invocation = 'a' * 32
        self.env = mock.patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.runtime),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_STATE_HOME': str(self.home / '.local/state'),
            'INVOCATION_ID': self.invocation,
            'MONITOR_INVOCATION_ID': self.invocation,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # Never probe the live compositor or inherit a running unit's stop result.
        for name in ('NIRI_SOCKET', 'SERVICE_RESULT'):
            os.environ.pop(name, None)
        stdout = mock.patch('sys.stdout', new_callable=io.StringIO)
        self.stdout = stdout.start()
        self.addCleanup(stdout.stop)
        self.helper = load_helper()
        self.helper.PACKAGED = self.packaged
        patch = mock.patch.object(Path, 'home', return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.calls = []
        self.journal = ''
        self.fail_action = None
        patch = mock.patch.object(self.helper.subprocess, 'run', side_effect=self.command)
        patch.start()
        self.addCleanup(patch.stop)

    def command(self, argv, **kwargs):
        argv = list(map(str, argv))
        self.calls.append(argv)
        output = ''
        if Path(argv[0]).name == 'journalctl':
            output = self.journal
        elif 'show' in argv:
            output = self.invocation + '\n'
        failed = self.fail_action is not None and self.fail_action in argv
        result = subprocess.CompletedProcess(argv, int(failed), output, '')
        if failed and kwargs.get('check'):
            raise subprocess.CalledProcessError(1, argv, output=output)
        return result

    def launch(self, directory=None):
        directory = directory or self.override
        return self.helper.select_shell(str(directory), record=True)

    def test_standalone_launch_without_runtime_keeps_explicit_override(self):
        del os.environ['XDG_RUNTIME_DIR']
        self.assertEqual(self.launch(), str(self.override))
        self.assertFalse(self.calls)

    def broken_file(self, relative='components/Panel.qml'):
        source = self.override / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'broken personal contents\n')
        target = self.packaged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('import QtQuick\nItem {}\n')
        self.journal = ('Failed to load configuration\n'
                        f'  caused by {source.as_uri()}[12:4]: Unexpected token\n')
        return source

    def actions(self, action):
        return [args for args in self.calls
                if Path(args[0]).name == 'systemctl' and action in args]

    def assert_notice_without_restart(self):
        self.assertFalse(self.actions('restart'))
        self.assertTrue(any('emaki-shell-recovery.service' in args
                            for args in self.actions('start')))

    def consume_notice(self):
        self.stdout.seek(0)
        self.stdout.truncate()
        self.helper.repair_notice()
        return self.stdout.getvalue()

    def test_broken_override_preserved_and_packaged_restart_selected(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())
        backups = list(source.parent.glob(source.name + '.broken-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))
        self.assertEqual(len(self.actions('reset-failed')), 1)
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertIn('--no-block', self.actions('restart')[0])
        self.assertIn('emaki-shell.service', self.actions('restart')[0])
        log = self.helper.runtime_dir() / 'errors.log'
        self.assertIn('Unexpected token', log.read_text())
        self.assertIn('Unexpected token', self.stdout.getvalue())
        self.assertTrue(any(self.invocation in ' '.join(args)
                            for args in self.calls if Path(args[0]).name == 'journalctl'))

    def trusted_launch(self):
        os.environ['EMAKI_SHELL_PROCESS_ID'] = '123'
        self.launch()
        os.environ.pop('EMAKI_SHELL_PROCESS_ID')
        # An explicitly started heal service does not receive OnFailure metadata.
        os.environ.pop('MONITOR_INVOCATION_ID', None)

    def test_trusted_failure_reconciles_compositor_journal_and_restarts_trusted_shell(self):
        source = self.broken_file()
        self.trusted_launch()
        self.helper.reconcile()
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))
        self.assertFalse(self.actions('restart'), 'must not restart a skipped legacy service')
        self.assertTrue(any(Path(args[0]).name == 'emaki-shell-trusted' and args[1:] == ['restart']
                            for args in self.calls))
        show = next(args for args in self.calls if 'show' in args)
        self.assertEqual(show[-1], 'niri-emaki.service')
        query = next(args for args in self.calls if args[0] == 'journalctl')
        self.assertIn(f'_SYSTEMD_INVOCATION_ID={self.invocation}', query)
        self.assertIn('--since', query)

    def test_trusted_stale_invocation_or_missing_launch_window_only_opens_recovery(self):
        for stale in (True, False):
            with self.subTest(stale=stale):
                source = self.broken_file()
                original = source.read_bytes()
                self.trusted_launch()
                record = self.helper.runtime_dir() / 'launch.json'
                data = json.loads(record.read_text())
                if stale:
                    data['invocation'] = 'b' * 32
                else:
                    del data['since']
                record.write_text(json.dumps(data))
                self.calls.clear()
                self.helper.reconcile()
                self.assert_notice_without_restart()
                self.assertFalse(any(Path(args[0]).name == 'emaki-shell-trusted'
                                     for args in self.calls))
                self.assertEqual(source.read_bytes(), original)

    def test_trusted_packaged_failure_opens_recovery_without_another_automatic_retry(self):
        self.broken_file()
        self.trusted_launch()
        (self.helper.runtime_dir() / 'packaged').touch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertFalse(any(Path(args[0]).name == 'emaki-shell-trusted' for args in self.calls))

    def test_failed_trusted_restart_opens_manual_recovery(self):
        self.broken_file()
        self.trusted_launch()
        self.fail_action = 'restart'
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))
        # The repair already wrote its backup notice; manual recovery keeps it.
        backup = next(self.override.glob('components/Panel.qml.broken-*'))
        self.assertEqual(json.loads(self.consume_notice()),
                         {'backup': str(backup.relative_to(self.override)), 'crashed': True})
        self.assertEqual(self.consume_notice(), '')

    def test_manual_recovery_leaves_crash_notice_consumed_once(self):
        self.broken_file()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        pending = self.helper.runtime_dir() / 'repair-notice.json'
        self.assertEqual(json.loads(pending.read_text()),
                         {'crashed': True, 'boot_id': self.helper.boot_id()})
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})
        self.assertFalse(pending.exists())
        self.assertEqual(self.consume_notice(), '')

    def test_crash_notice_write_failure_still_opens_recovery(self):
        self.broken_file()
        original = self.helper.write_state

        def write_state(path, text):
            if path.name == 'repair-notice.json':
                raise OSError('read-only runtime')
            original(path, text)

        with mock.patch.object(self.helper, 'write_state', side_effect=write_state):
            self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertFalse((self.helper.runtime_dir() / 'repair-notice.json').exists())

    def test_malformed_pending_notice_prints_nothing_and_is_removed(self):
        state = self.helper.runtime_dir()
        pending = state / 'repair-notice.json'
        for payload in (b'not json', b'\xff\xfe', b'[]', b'"text"', b'{}',
                        b'{"backup": 3}', b'{"backup": null, "crashed": true}',
                        b'{"crashed": "yes"}', b'{"backup": "a.qml", "crashed": 1}'):
            with self.subTest(payload=payload):
                pending.write_bytes(payload)
                self.assertEqual(self.consume_notice(), '')
                self.assertFalse(pending.exists())
                self.assertFalse(list(state.glob('repair-notice*')))

    def fresh_runtime(self, name):
        runtime = self.base / name
        runtime.mkdir()
        return mock.patch.dict(os.environ, {
            'XDG_RUNTIME_DIR': str(runtime),
            'XDG_STATE_HOME': str(self.base / ('state-' + name)),
        })

    def stopped(self, result):
        # The legacy unit's ExecStopPost passes systemd's stop result.
        with mock.patch.dict(os.environ, {'SERVICE_RESULT': result}):
            self.helper.stopped()

    def test_explicit_crash_hook_offers_report_once_per_boot(self):
        # A direct hook and later recovery share the same offer budget.
        self.helper.crashed()
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})
        # A crash loop, or any later crash, offers nothing more this boot.
        self.helper.crashed()
        self.helper.crashed()
        self.assertEqual(self.consume_notice(), '')

    def test_legacy_crash_before_restart_offers_report_once_per_boot(self):
        for result in ('exit-code', 'signal', 'core-dump', 'watchdog', 'oom-kill'):
            with self.subTest(result=result), self.fresh_runtime(result):
                self.stopped(result)
                self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})
                self.stopped(result)
                self.helper.crashed()
                self.assertEqual(self.consume_notice(), '')

    def test_clean_stop_leaves_no_notice_and_keeps_the_offer(self):
        for result in ('success', 'exec-condition', 'start-limit-hit', 'timeout', ''):
            self.stopped(result)
        self.helper.stopped()
        self.assertEqual(self.consume_notice(), '')
        self.assertFalse((self.helper.runtime_dir() / 'running.json').exists())
        self.assertFalse((self.helper.state_dir() / 'crash-offered').exists())
        self.stopped('signal')
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})

    def test_shell_lost_with_its_compositor_is_not_a_crash(self):
        # At logout niri exits first and the shell then fails on its Wayland connection.
        with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(self.runtime / 'niri.gone.sock')}):
            self.stopped('exit-code')
            self.helper.crashed()
        self.assertEqual(self.consume_notice(), '')
        # A socket path has a 108-byte limit; package builds run under a deep TMPDIR, so bind in a short directory.
        short = tempfile.TemporaryDirectory(prefix='nl-', dir='/tmp')
        self.addCleanup(short.cleanup)
        path = Path(short.name) / 'niri.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            try:
                server.bind(str(path))
            except PermissionError:
                print('NOTE: local socket bind is forbidden; successful compositor probe is mocked.',
                      file=sys.stderr)
                with mock.patch.object(self.helper.socket, 'socket') as socket_factory:
                    with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(path)}):
                        self.stopped('exit-code')
                    socket_factory.return_value.__enter__.return_value.connect.assert_called_once_with(str(path))
            else:
                server.listen()
                with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(path)}):
                    self.stopped('exit-code')
        with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(path)}):
            self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})

    def test_recovery_after_an_offered_crash_does_not_offer_again(self):
        self.helper.crashed()
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})
        self.broken_file()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(self.consume_notice(), '')

    def test_repair_after_an_offered_crash_still_names_the_backup(self):
        self.helper.crashed()
        self.consume_notice()
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        backup = str(next(source.parent.glob('*.broken-*')).relative_to(self.override))
        self.assertEqual(json.loads(self.consume_notice()), {'backup': backup, 'crashed': True})

    def test_crash_commands_succeed_from_the_command_line(self):
        for command in ('crashed', 'stopped'):
            with mock.patch.object(sys, 'argv', ['emaki-shell-health', command]):
                self.assertEqual(self.helper.main(), 0)
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})

    def test_legacy_supervisor_keeps_its_optional_stop_hook(self):
        unit = (ROOT / 'systemd/emaki-shell.service').read_text()
        # A failing helper must never fail the unit and so open recovery.
        self.assertIn('\nExecStopPost=-@EMAKI_BINDIR@/emaki-shell-health stopped\n', unit)

    def start_marker(self, pid=1001, token='a' * 32, boot='boot-one', alive=()):
        with mock.patch.object(self.helper, 'boot_id', return_value=boot), \
                mock.patch.object(self.helper, 'process_alive', side_effect=lambda p: p == pid or p in alive):
            self.helper.started(pid, token)
        return self.helper.runtime_dir() / 'running.json'

    def lifecycle_notice(self, boot='boot-one'):
        with mock.patch.object(self.helper, 'boot_id', return_value=boot):
            return self.consume_notice()

    def test_single_dead_shell_offers_once_after_its_replacement_starts(self):
        marker = self.start_marker()
        self.assertEqual(json.loads(marker.read_text()),
                         {'boot_id': 'boot-one', 'pid': 1001, 'token': 'a' * 32})
        self.assertEqual(self.lifecycle_notice(), '')
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})
        self.start_marker(pid=1003, token='c' * 32)
        self.assertEqual(self.lifecycle_notice(), '')
        self.assertEqual(json.loads(marker.read_text())['pid'], 1003)
        self.assertEqual(self.calls, [], 'recording a crash must not open recovery or send a report')

    def test_reboot_ignores_previous_running_shell_and_pending_crash(self):
        marker = self.start_marker()
        self.start_marker(pid=1002, token='b' * 32)
        self.assertTrue((marker.parent / 'repair-notice.json').exists())
        self.start_marker(pid=1003, token='c' * 32, boot='boot-two')
        self.assertEqual(self.lifecycle_notice(boot='boot-two'), '')
        self.assertEqual(json.loads(marker.read_text())['boot_id'], 'boot-two')
        # A real crash in the new boot gets its own offer despite the old stamp.
        self.start_marker(pid=1004, token='d' * 32, boot='boot-two')
        self.assertEqual(json.loads(self.lifecycle_notice(boot='boot-two')), {'crashed': True})

    def test_clean_exit_removes_its_marker_without_spending_the_offer(self):
        marker = self.start_marker()
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'):
            self.helper.clean_exit(1001, 'a' * 32)
        self.assertFalse(marker.exists())
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(self.lifecycle_notice(), '')
        self.start_marker(pid=1003, token='c' * 32)
        self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})

    def test_live_previous_shell_and_same_process_reload_are_not_crashes(self):
        self.start_marker()
        marker = self.start_marker(pid=1002, token='b' * 32, alive=(1001,))
        self.assertEqual(json.loads(marker.read_text())['pid'], 1001)
        marker = self.start_marker(pid=1001, token='c' * 32)
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'):
            # A late callback from the earlier generation cannot erase its successor.
            self.helper.clean_exit(1001, 'a' * 32)
            self.helper.clean_exit(1002, 'b' * 32)
        self.assertEqual(json.loads(marker.read_text())['token'], 'c' * 32)
        self.assertEqual(self.lifecycle_notice(), '')

    def test_clean_receipt_prevents_a_false_crash_before_detached_cleanup(self):
        marker = self.start_marker()
        receipt = marker.parent / ('clean-' + 'a' * 32)
        receipt.write_text('1001')
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(self.lifecycle_notice(), '')
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'):
            self.helper.clean_exit(1001, 'a' * 32)
        self.assertEqual(json.loads(marker.read_text())['pid'], 1002)

    def test_clean_receipt_blocks_registration_after_a_fast_clean_exit(self):
        marker = self.helper.runtime_dir() / 'running.json'
        (marker.parent / ('clean-' + 'a' * 32)).write_text('1001')
        self.start_marker()
        self.assertFalse(marker.exists())
        self.assertEqual(self.lifecycle_notice(), '')

    def test_requested_restart_receipt_suppresses_only_the_requested_exit(self):
        self.start_marker()
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'), \
                mock.patch.object(self.helper, 'process_alive', return_value=True):
            self.assertEqual(self.helper.restart_begin(), {'pid': 1001, 'token': 'a' * 32})
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(self.lifecycle_notice(), '')
        self.start_marker(pid=1003, token='c' * 32)
        self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})

    def test_cancelled_restart_restores_crash_detection(self):
        self.start_marker()
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'), \
                mock.patch.object(self.helper, 'process_alive', return_value=True):
            self.helper.restart_begin()
            self.helper.restart_cancel(1001, 'a' * 32)
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})

    def test_cancelled_restart_cannot_erase_a_completed_clean_exit_receipt(self):
        marker = self.start_marker()
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'), \
                mock.patch.object(self.helper, 'process_alive', return_value=True):
            self.helper.restart_begin()
            (marker.parent / ('clean-' + 'a' * 32)).write_text('1001')
            self.helper.restart_cancel(1001, 'a' * 32)
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(self.lifecycle_notice(), '')

    def test_clean_legacy_stop_removes_dead_owner_but_preserves_live_owner(self):
        marker = self.start_marker()
        with mock.patch.object(self.helper, 'process_alive', return_value=True):
            self.stopped('success')
        self.assertTrue(marker.exists())
        with mock.patch.object(self.helper, 'process_alive', return_value=False):
            self.stopped('success')
        self.assertFalse(marker.exists())
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(self.lifecycle_notice(), '')

    def test_next_login_ignores_dead_marker_without_spending_crash_offer(self):
        with mock.patch.object(self.helper, 'compositor_gone', return_value=False):
            with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(self.runtime / 'old-session')}):
                self.start_marker()
            with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(self.runtime / 'new-session')}):
                self.start_marker(pid=1002, token='b' * 32)
                self.assertEqual(self.lifecycle_notice(), '')
                self.start_marker(pid=1003, token='c' * 32)
                self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})

    def test_next_login_with_retained_runtime_ignores_old_pending_notice(self):
        with mock.patch.object(self.helper, 'compositor_gone', return_value=False):
            with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(self.runtime / 'old-session')}):
                self.start_marker()
                self.start_marker(pid=1002, token='b' * 32)
            with mock.patch.dict(os.environ, {'NIRI_SOCKET': str(self.runtime / 'new-session')}):
                self.start_marker(pid=1003, token='c' * 32)
                self.assertEqual(self.lifecycle_notice(), '')

    def test_new_runtime_in_same_boot_keeps_the_used_offer(self):
        self.start_marker()
        self.start_marker(pid=1002, token='b' * 32)
        self.assertEqual(json.loads(self.lifecycle_notice()), {'crashed': True})
        replacement = self.base / 'runtime-after-logout'
        replacement.mkdir()
        with mock.patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(replacement)}):
            self.start_marker(pid=1003, token='c' * 32)
            self.assertEqual(self.lifecycle_notice(), '')
            self.start_marker(pid=1004, token='d' * 32)
            self.assertEqual(self.lifecycle_notice(), '')

    def test_delayed_start_for_dead_process_does_not_replace_current_marker(self):
        marker = self.start_marker()
        original = marker.read_bytes()
        with mock.patch.object(self.helper, 'boot_id', return_value='boot-one'), \
                mock.patch.object(self.helper, 'process_alive', return_value=False):
            self.helper.started(1002, 'b' * 32)
        self.assertEqual(marker.read_bytes(), original)
        self.assertEqual(self.lifecycle_notice(), '')

    def test_relative_qml_cause_is_set_aside(self):
        source = self.broken_file('Panel.qml')
        self.journal = 'Failed to load configuration\n  caused by @Panel.qml[12:4]: Syntax error\n'
        self.launch()
        self.helper.reconcile()
        self.assertEqual(source.read_bytes(), (self.packaged / 'Panel.qml').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)

    def test_normal_launch_records_override_without_starting_services(self):
        self.assertEqual(self.launch(), str(self.override))
        record = json.loads((self.helper.runtime_dir() / 'launch.json').read_text())
        self.assertEqual(record['shell'], str(self.override))
        self.assertEqual(record['invocation'], self.invocation)
        self.assertEqual(self.calls, [])

    def test_last_javascript_cause_is_repaired_without_moving_panel(self):
        panel = self.broken_file()
        original = panel.read_bytes()
        script = self.broken_file('components/helpers.js')
        self.journal = ('Failed to load configuration\n'
                        '  caused by @shell.qml[13:1]: Type Panel unavailable\n'
                        '  caused by @components/Panel.qml[2:1]: Script qs:@/qs/components/helpers.js unavailable\n'
                        '  caused by @components/helpers.js[1:29]: Expected token `identifier`\n')
        self.launch()
        self.helper.reconcile()
        self.assertEqual(panel.read_bytes(), original)
        self.assertEqual(script.read_bytes(), (self.packaged / 'components/helpers.js').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertFalse(list(panel.parent.glob('Panel.qml.broken-*')))

    def test_import_failures_do_not_move_working_files(self):
        source = self.broken_file('shell.qml')
        original = source.read_bytes()
        self.launch()
        for message in ('module "Fake" plugin "fakeplugin" not found',
                        'module "X" is not installed',
                        'Script qs:@/qs/components/helpers.js unavailable',
                        'Cannot load library /usr/lib/qt6/qml/Fake/libfakeplugin.so: missing symbol'):
            with self.subTest(message=message):
                self.calls.clear()
                self.journal = f'Failed to load configuration\n  caused by @shell.qml[2:1]: {message}\n'
                self.helper.reconcile()
                self.assert_notice_without_restart()
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(source.parent.glob('*.broken-*')))

    def test_unsupported_or_external_last_cause_preserves_panel(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        for cause in ('@components/data.json[1:29]: Unexpected token',
                      'file:///usr/share/Fake/helpers.js[1:29]: Syntax error',
                      '@components/missing.js[1:29]: Syntax error',
                      'an unrecognised error format'):
            with self.subTest(cause=cause):
                self.calls.clear()
                self.journal = ('Failed to load configuration\n'
                                '  caused by @components/Panel.qml[2:1]: Type unavailable\n'
                                f'  caused by {cause}\n')
                self.helper.reconcile()
                self.assert_notice_without_restart()
                self.assertEqual(source.read_bytes(), original)

    def test_arbitrary_home_directory_is_never_repaired(self):
        self.override = self.home / 'Projects/panel'
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(source.read_bytes(), original)

    def test_custom_xdg_config_root_is_repaired(self):
        os.environ['XDG_CONFIG_HOME'] = str(self.home / 'settings')
        self.override = self.home / 'settings/emaki/shell'
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())

    def test_git_ancestors_and_nested_worktrees_are_preserved(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        for parent in (source.parent, self.override, self.override.parent,
                       self.home / '.config', self.home):
            for kind in ('directory', 'file', 'symlink'):
                with self.subTest(parent=parent, kind=kind):
                    entry = parent / '.git'
                    if kind == 'directory':
                        entry.mkdir()
                    elif kind == 'file':
                        entry.write_text('gitdir: /missing/worktree\n')
                    else:
                        entry.symlink_to(self.base / 'missing-git')
                    self.calls.clear()
                    self.helper.reconcile()
                    self.assert_notice_without_restart()
                    self.assertEqual(source.read_bytes(), original)
                    entry.rmdir() if kind == 'directory' else entry.unlink()

    def test_successful_repair_notice_is_consumed_once_after_restart(self):
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        pending = self.helper.runtime_dir() / 'repair-notice.json'
        self.assertTrue(pending.exists())
        backup = str(next(source.parent.glob('*.broken-*')).relative_to(self.override))
        self.assertEqual(json.loads(self.consume_notice()), {'backup': backup, 'crashed': True})
        self.assertFalse(pending.exists())
        self.assertEqual(self.consume_notice(), '')
        self.assertTrue((self.helper.runtime_dir() / 'repair.txt').exists())

    def test_packaged_failure_never_restarts(self):
        source = self.broken_file()
        self.launch(self.packaged)
        self.journal = 'Failed to load configuration\n' + (self.packaged / 'components/Panel.qml').as_uri() + ':12:4: Bad token\n'
        self.helper.reconcile()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_second_failure_does_not_repeat_repair(self):
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.calls.clear()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(len(list(source.parent.glob(source.name + '.broken-*'))), 1)

    def test_missing_launch_record_does_not_touch_override(self):
        source = self.broken_file()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_missing_invocation_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        record = self.helper.runtime_dir() / 'launch.json'
        data = json.loads(record.read_text())
        data['invocation'] = ''
        record.write_text(json.dumps(data))
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_stale_failed_invocation_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        self.invocation = 'b' * 32
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_monitor_invocation_mismatch_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        os.environ['MONITOR_INVOCATION_ID'] = 'c' * 32
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_outside_home_override_is_preserved(self):
        outside = self.base / 'outside'
        outside.mkdir()
        source = outside / 'Panel.qml'
        source.write_text('broken contents')
        (self.packaged / source.name).write_text('packaged contents')
        self.launch(outside)
        self.journal = f'Failed to load configuration\n{source.as_uri()}[12:4]: Syntax error\n'
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_identical_packaged_file_is_preserved(self):
        source = self.broken_file()
        source.write_bytes((self.packaged / 'components/Panel.qml').read_bytes())
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_only_deepest_cause_is_set_aside(self):
        source = self.broken_file()
        entry = self.override / 'shell.qml'
        entry.write_text('personal entry point')
        (self.packaged / 'shell.qml').write_text('packaged entry point')
        self.journal = ('Failed to load configuration\n'
                        '  caused by @shell.qml[13:1]: Type Panel unavailable\n'
                        f'  caused by {source.as_uri()}[12:4]: Syntax error\n')
        self.launch()
        self.helper.reconcile()
        self.assertTrue(entry.exists())
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)

    def test_failed_restore_preserves_original_and_opens_notice_without_restart(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        with mock.patch.object(self.helper.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            self.helper.reconcile()
        self.assert_notice_without_restart()
        backups = list(source.parent.glob(source.name + '.broken-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))
        self.assertEqual(json.loads(self.consume_notice()), {'crashed': True})

    def test_file_symlink_is_preserved(self):
        source = self.broken_file()
        source.unlink()
        target = self.home / 'unrelated.qml'
        target.write_bytes(b'personal data')
        source.symlink_to(target)
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.is_symlink())
        self.assertEqual(target.read_bytes(), b'personal data')

    def test_symlink_ancestor_is_preserved(self):
        source = self.broken_file()
        directory = source.parent
        moved = self.home / 'other-components'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_no_packaged_counterpart_means_no_move(self):
        source = self.broken_file()
        (self.packaged / 'components/Panel.qml').unlink()
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_runtime_error_without_load_failure_means_no_move(self):
        source = self.broken_file()
        self.journal = f'{source.as_uri()}:12:4: ReferenceError: missing is not defined\n'
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_reset_failure_prevents_restart(self):
        self.broken_file()
        self.launch()
        self.fail_action = 'reset-failed'
        self.helper.reconcile()
        self.assert_notice_without_restart()

    def test_restart_failure_shows_recovery(self):
        self.broken_file()
        self.launch()
        self.fail_action = 'restart'
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertTrue(any('emaki-shell-recovery.service' in args
                            for args in self.actions('start')))


if __name__ == '__main__':
    unittest.main()
