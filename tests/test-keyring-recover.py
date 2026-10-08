#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for isolated inspection of preserved login keyrings."""
import hashlib
import io
import json
import os
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/emaki-keyring-recover'
loader = importlib.machinery.SourceFileLoader('keyring_recover', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
recover = importlib.util.module_from_spec(spec)
loader.exec_module(recover)


class Recovery(unittest.TestCase):
    def setUp(self):
        evidence = SCRIPT.parents[1] / '.cache/evidence/u5'
        evidence.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=evidence)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'keyrings'
        self.source.mkdir()
        self.original = self.source / 'login.keyring'
        self.original.write_bytes(b'encrypted fixture\x00\xff')
        self.digest = hashlib.sha256(self.original.read_bytes()).hexdigest()

    def unchanged(self):
        self.assertEqual(hashlib.sha256(self.original.read_bytes()).hexdigest(), self.digest)

    def test_private_copy_and_bus_have_no_host_activation_or_credentials(self):
        private = self.root / 'private'
        private.mkdir()
        command, env = recover.prepare(self.source, private, {
            'PATH': '/usr/bin', 'DBUS_SESSION_BUS_ADDRESS': 'old-bus',
            'GNOME_KEYRING_CONTROL': 'old-control', 'PAM_KWALLET5_LOGIN': 'old-wallet'})
        self.assertEqual(command[:2], ['dbus-run-session', '--config-file'])
        self.assertIn('--worker', command)
        self.assertEqual(env['XDG_RUNTIME_DIR'], str(private / 'runtime'))
        self.assertEqual(env['XDG_DATA_HOME'], str(private / 'data'))
        self.assertEqual(env['HOME'], str(private / 'home'))
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', env)
        self.assertNotIn('GNOME_KEYRING_CONTROL', env)
        self.assertNotIn('PAM_KWALLET5_LOGIN', env)
        self.assertNotIn('servicedir', (private / 'bus.conf').read_text())
        copied = private / 'data/keyrings/login.keyring'
        self.assertEqual(copied.read_bytes(), self.original.read_bytes())
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        copied.write_bytes(b'changed by isolated service')
        self.unchanged()

    def test_nonregular_entries_are_skipped(self):
        for kind in ('link', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                bad = self.source / 'unexpected'
                if kind == 'link':
                    bad.symlink_to(self.original)
                elif kind == 'directory':
                    bad.mkdir()
                else:
                    os.mkfifo(bad)
                destination = self.root / ('copy-' + kind)
                recover.copy_keyrings(self.source, destination)
                self.assertEqual(list(destination.iterdir()), [destination / 'login.keyring'])
                self.unchanged()
                if kind != 'directory':
                    bad.unlink()
                else:
                    bad.rmdir()

    def test_noninteractive_run_never_starts_a_service(self):
        with mock.patch.object(recover.sys.stdin, 'isatty', return_value=False), \
             mock.patch.object(recover.subprocess, 'Popen') as start:
            with self.assertRaises(SystemExit):
                recover.main([])
        start.assert_not_called()
        self.unchanged()

    def test_cancelled_or_failed_worker_preserves_original(self):
        for failure in (KeyboardInterrupt(), OSError('unlock failed')):
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(recover.sys.stdin, 'isatty', return_value=True), \
                 mock.patch.object(recover.sys.stdout, 'isatty', return_value=True), \
                 mock.patch.object(recover, 'worker', side_effect=failure), \
                 mock.patch.object(recover.signal, 'signal'):
                self.assertEqual(recover.main(['--worker', str(self.root)]),
                                 130 if isinstance(failure, KeyboardInterrupt) else 1)
                self.unchanged()

    def test_password_is_written_only_to_stdin(self):
        password = 'recovery fixture password'
        process = mock.Mock()
        process.poll.return_value = None
        bus = mock.Mock()
        bus.call_sync.return_value.unpack.return_value = (True,)
        service = mock.Mock()
        locked_collection = mock.Mock()
        locked_collection.get_locked.return_value = True
        locked_collection.get_label.return_value = 'Locked fixture'
        service.get_collections.return_value = [locked_collection]
        gio = mock.Mock()
        gio.bus_get_sync.return_value = bus
        secret = types.SimpleNamespace(
            ServiceFlags=types.SimpleNamespace(OPEN_SESSION=1, LOAD_COLLECTIONS=2),
            Service=mock.Mock())
        secret.Service.get_sync.return_value = service
        repository = types.SimpleNamespace(Gio=gio, GLib=mock.Mock(), Secret=secret)
        modules = {'gi': mock.Mock(), 'gi.repository': repository}
        with mock.patch.dict(recover.sys.modules, modules), \
             mock.patch.object(recover.getpass, 'getpass', return_value=password), \
             mock.patch.object(recover.subprocess, 'Popen', return_value=process) as start, \
             mock.patch('builtins.input', return_value='n'), \
             mock.patch.object(recover, 'migrate_unlocked'):
            self.assertEqual(recover.worker(self.root), 1)
        process.stdin.write.assert_called_once_with(password.encode())
        process.stdin.close.assert_called_once()
        self.assertNotIn(password, repr(start.call_args))
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        locked_collection.load_items_sync.assert_not_called()
        self.unchanged()

    def test_wrong_login_password_is_clear_and_locked_collection_can_be_migrated(self):
        for unlock, complete, failure, extra_locked in (
                (False, False, None, False), (True, True, None, False),
                (True, False, None, False), (True, False, RuntimeError('copy failed'), False),
                (True, True, None, True)):
            with self.subTest(unlock=unlock, complete=complete, failure=failure,
                              extra_locked=extra_locked):
                process = mock.Mock()
                process.poll.return_value = None
                collection = mock.Mock()
                collection.get_locked.return_value = True
                collection.get_label.return_value = 'Login'
                collection.get_object_path.return_value = '/collection/login'
                collection.get_items.return_value = [mock.Mock()]
                collection.get_items.return_value[0].get_label.return_value = ''
                collection.get_items.return_value[0].get_object_path.return_value = '/item/1'
                service = mock.Mock()
                service.get_collections.return_value = [collection]
                if extra_locked:
                    other = mock.Mock()
                    other.get_locked.return_value = True
                    other.get_label.return_value = 'Other collection'
                    service.get_collections.return_value.append(other)
                bus = mock.Mock()
                bus.call_sync.return_value.unpack.side_effect = [
                    (True,), ('/collection/login',)] + ([(False,), ('Saved item',)] if unlock else [])
                gio = mock.Mock()
                gio.bus_get_sync.return_value = bus
                secret = types.SimpleNamespace(
                    ServiceFlags=types.SimpleNamespace(OPEN_SESSION=1, LOAD_COLLECTIONS=2),
                    Service=mock.Mock())
                secret.Service.get_sync.return_value = service
                glib = mock.Mock()
                glib.Error = RuntimeError
                glib.Variant.side_effect = lambda signature, value: (signature, value)
                repository = types.SimpleNamespace(Gio=gio, GLib=glib, Secret=secret)
                with mock.patch.dict(recover.sys.modules, {
                        'gi': mock.Mock(), 'gi.repository': repository}), \
                     mock.patch.object(recover.getpass, 'getpass', return_value='fixture'), \
                     mock.patch.object(recover.subprocess, 'Popen', return_value=process), \
                     mock.patch('builtins.input', side_effect=['y' if unlock else 'n', 'n']), \
                     mock.patch.object(recover, 'unlock_collection') as unlock_call, \
                     mock.patch.object(recover, 'migrate_unlocked', return_value=complete,
                                       side_effect=failure) as migrate, \
                     mock.patch.object(recover, 'reveal_items') as reveal, \
                     mock.patch.object(recover.sys, 'stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(recover.worker(self.root),
                                     0 if unlock and complete and not extra_locked else 1)
                self.assertIn('Wrong password:', output.getvalue())
                migrate.assert_called_once_with(bus, gio, replace_portal_keys=False)
                self.assertEqual(unlock_call.call_count, int(unlock))
                self.assertEqual(reveal.call_count, int(unlock))
                if unlock:
                    self.assertIn('1. "Saved item"', output.getvalue())
                    self.assertNotIn('1. ""', output.getvalue())
                    self.assertEqual(bus.call_sync.call_args.args[1:5], (
                        '/item/1', 'org.freedesktop.DBus.Properties', 'Get',
                        ('(ss)', ('org.freedesktop.Secret.Item', 'Label'))))
                if failure:
                    self.assertIn('Copying saved secrets to the wallet failed.', output.getvalue())
                self.unchanged()

    def test_several_items_can_be_revealed_and_invalid_selection_retried(self):
        items = [object(), object()]
        with mock.patch('builtins.input', side_effect=['no', '1', '2', '']), \
             mock.patch.object(recover, 'display_item') as reveal:
            recover.reveal_items(items)
        self.assertEqual(reveal.call_args_list, [mock.call(items[0]), mock.call(items[1])])

    def test_worker_cancellation_is_silent(self):
        with mock.patch.object(recover.sys.stdin, 'isatty', return_value=True), \
             mock.patch.object(recover.sys.stdout, 'isatty', return_value=True), \
             mock.patch.object(recover, 'worker', side_effect=KeyboardInterrupt), \
             mock.patch.object(recover.signal, 'signal'), \
             mock.patch.object(recover.sys, 'stderr', new_callable=io.StringIO) as output:
            self.assertEqual(recover.main(['--worker', str(self.root)]), 130)
        self.assertNotIn('cancelled', output.getvalue())

    def test_unlock_uses_secret_struct_on_private_bus_and_closes_session(self):
        bus = mock.Mock()
        bus.call_sync.return_value.unpack.return_value = ('', '/session/1')
        collection = mock.Mock()
        collection.get_object_path.return_value = '/collection/other'
        glib = mock.Mock()
        glib.Variant.side_effect = lambda signature, value: (signature, value)
        recover.unlock_collection(bus, collection, 'fixture password', mock.Mock(), glib)
        calls = bus.call_sync.call_args_list
        self.assertEqual(calls[1].args[1], '/org/freedesktop/secrets')
        self.assertEqual(calls[1].args[3], 'UnlockWithMasterPassword')
        self.assertEqual(calls[1].args[4], ('(o(oayays))', (
            '/collection/other', ('/session/1', b'', b'fixture password', 'text/plain'))))
        self.assertEqual(calls[2].args[3], 'Close')

    def test_migration_connects_explicit_desktop_bus(self):
        gio = mock.Mock()
        gio.DBusConnectionFlags.AUTHENTICATION_CLIENT = 1
        gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION = 2
        transfer = mock.Mock(return_value={
            'items': {'one': {'status': 'migrated'}, 'two': {'status': 'conflict'}},
            'complete': False})
        source = object()
        report = self.root / 'migration.json'
        with mock.patch.dict(recover.os.environ, {
                'EMAKI_RECOVERY_BUS': 'unix:path=desktop-bus',
                'EMAKI_RECOVERY_REPORT': str(report)}), \
             mock.patch.object(recover.runpy, 'run_path', return_value={'transfer': transfer, 'call': mock.Mock(side_effect=[(True,), (':1.8',), (':1.8',), (':1.8',)])}):
            self.assertFalse(recover.migrate_unlocked(source, gio))
        gio.DBusConnection.new_for_address_sync.assert_called_once_with(
            'unix:path=desktop-bus', 3, None, None)
        destination = gio.DBusConnection.new_for_address_sync.return_value
        transfer.assert_called_once_with(source, destination, report, flush=False,
                                         replace_portal_keys=False)
        destination.close_sync.assert_called_once_with(None)


    def test_recovery_prints_recorded_errors_without_transport_details_or_false_zero(self):
        gio = mock.Mock()
        gio.DBusConnectionFlags.AUTHENTICATION_CLIENT = 1
        gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION = 2
        transfer = mock.Mock(return_value={
            'items': {}, 'complete': False,
            'errors': ['The destination wallet is locked or unavailable.',
                       'GDBus.Error:org.freedesktop.DBus.Error.Failed: private details']})
        with mock.patch.dict(recover.os.environ, {
                'EMAKI_RECOVERY_BUS': 'unix:path=desktop-bus',
                'EMAKI_RECOVERY_REPORT': str(self.root / 'report.json')}), \
             mock.patch.object(recover.runpy, 'run_path', return_value={'transfer': transfer, 'call': mock.Mock(side_effect=[(True,), (':1.8',), (':1.8',), (':1.8',)])}), \
             mock.patch.object(recover.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertFalse(recover.migrate_unlocked(object(), gio, replace_portal_keys=True))
        self.assertIn('The destination wallet is locked or unavailable.', output.getvalue())
        self.assertIn('number of items needing recovery is unknown', output.getvalue())
        self.assertNotIn('needing recovery: 0', output.getvalue())
        self.assertNotIn('private details', output.getvalue())
        self.assertNotIn('GDBus.Error', output.getvalue())
        self.assertEqual(transfer.call_args.kwargs,
                         {'flush': False, 'replace_portal_keys': True})

    def test_migration_completion_requires_a_destination_and_complete_report(self):
        target = mock.MagicMock()
        target.__enter__.return_value = (object(), False)
        transfer = mock.Mock(return_value={
            'items': {'one': {'status': 'migrated'}}, 'complete': True})
        with mock.patch.dict(recover.os.environ, {'EMAKI_RECOVERY_REPORT': ''}), \
             mock.patch.object(recover, 'recovery_target', return_value=target), \
             mock.patch.object(recover.runpy, 'run_path', return_value={'transfer': transfer}), \
             mock.patch.object(recover.sys, 'stdout', new_callable=io.StringIO):
            self.assertFalse(recover.migrate_unlocked(object(), mock.Mock()))
            transfer.assert_not_called()
            with mock.patch.dict(recover.os.environ, {
                    'EMAKI_RECOVERY_REPORT': str(self.root / 'report.json')}):
                self.assertTrue(recover.migrate_unlocked(object(), mock.Mock()))

    def test_replacement_option_reaches_worker_only_when_requested(self):
        for arguments, replace in (([], False), (['--replace-portal-keys'], True)):
            with self.subTest(replace=replace), \
                 mock.patch.object(recover.sys.stdin, 'isatty', return_value=True), \
                 mock.patch.object(recover.sys.stdout, 'isatty', return_value=True), \
                 mock.patch.object(recover, 'worker', return_value=0) as worker, \
                 mock.patch.object(recover.signal, 'signal'):
                self.assertEqual(recover.main(['--worker', str(self.root)] + arguments), 0)
            worker.assert_called_once_with(self.root, replace_portal_keys=replace)



    def test_private_target_waits_for_shutdown_and_password_stays_on_stdin(self):
        gio = mock.Mock()
        gio.DBusConnectionFlags.AUTHENTICATION_CLIENT = 1
        gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION = 2
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        started = []
        def launch(command, **kwargs):
            started.append((command, kwargs))
            (Path(command[-1]) / 'ready').write_text(json.dumps({'address': 'private-bus'}))
            return process
        migration = {'call': mock.Mock(return_value=(False,))}
        with mock.patch.dict(recover.os.environ, {
                'EMAKI_RECOVERY_BUS': 'desktop-bus',
                'EMAKI_RECOVERY_TARGET': json.dumps({'HOME': '/target-home'})}), \
             mock.patch.object(recover.getpass, 'getpass', return_value='destination password'), \
             mock.patch.object(recover.subprocess, 'Popen', side_effect=launch), \
             mock.patch.object(recover, 'stop') as stop:
            with recover.recovery_target(gio, migration) as (destination, private):
                self.assertTrue(private)
                self.assertIs(destination, gio.DBusConnection.new_for_address_sync.return_value)
                self.assertFalse((Path(started[0][0][-1]) / 'done').exists())
            process.wait.assert_called_once_with(timeout=5)
            stop.assert_called_once_with(process, group=True)
        command, kwargs = started[0]
        self.assertIn('emaki-wallet-stage', command[-2])
        self.assertEqual(kwargs['env']['HOME'], '/target-home')
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', kwargs['env'])
        self.assertEqual(kwargs['env']['EMAKI_DESKTOP_BUS'], 'desktop-bus')
        self.assertNotIn('destination password', repr(started))
        process.stdin.write.assert_called_once_with(b'destination password')
        process.stdin.close.assert_called_once()
        self.assertEqual(destination.close_sync.call_count, 2)
        self.assertFalse(Path(command[-1]).exists())

    def test_private_target_failure_cannot_leave_writer_running(self):
        gio = mock.Mock()
        process = mock.Mock()
        process.poll.return_value = 1
        with mock.patch.dict(recover.os.environ, {
                'EMAKI_RECOVERY_BUS': '',
                'EMAKI_RECOVERY_TARGET': json.dumps({'HOME': '/target-home'})}), \
             mock.patch.object(recover.getpass, 'getpass', return_value='fixture'), \
             mock.patch.object(recover.subprocess, 'Popen', return_value=process), \
             mock.patch.object(recover, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'could not be unlocked'):
                with recover.recovery_target(gio, {}):
                    self.fail('An unready wallet must not be used.')
            stop.assert_called_once_with(process, group=True)
        gio.DBusConnection.new_for_address_sync.assert_not_called()



    def test_recovery_refuses_other_secret_service_provider(self):
        gio = mock.Mock()
        gio.DBusConnectionFlags.AUTHENTICATION_CLIENT = 1
        gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION = 2
        call = mock.Mock(side_effect=[(True,), (':1.4',), (':1.8',), (':1.8',)])
        with mock.patch.dict(recover.os.environ, {'EMAKI_RECOVERY_BUS': 'desktop-bus'}), \
             mock.patch.object(recover.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(RuntimeError, 'expected wallet provider'):
                with recover.recovery_target(gio, {'call': call}):
                    self.fail('A different provider must never receive secrets.')
        start.assert_not_called()
        gio.DBusConnection.new_for_address_sync.return_value.close_sync.assert_called_once()

    def test_recovery_lock_excludes_another_migration(self):
        report = self.root / 'state/report.json'
        with recover.recovery_lock(report):
            with self.assertRaisesRegex(RuntimeError, 'Another password migration'):
                with recover.recovery_lock(report):
                    self.fail('Concurrent migration must not proceed.')
        with recover.recovery_lock(report):
            pass

    def test_failed_private_shutdown_invalidates_report(self):
        result = {'complete': True, 'portal_keys_safe': True, 'saved': True, 'items': {}}
        transfer = mock.Mock(return_value=result)
        save = mock.Mock()
        target = mock.MagicMock()
        target.__enter__.return_value = (object(), True)
        target.__exit__.side_effect = RuntimeError('private target failed')
        with mock.patch.dict(recover.os.environ, {
                'EMAKI_RECOVERY_REPORT': str(self.root / 'report.json')}), \
             mock.patch.object(recover, 'recovery_target', return_value=target), \
             mock.patch.object(recover.runpy, 'run_path', return_value={
                 'transfer': transfer, 'write_json': save}), \
             mock.patch.object(recover.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertFalse(recover.migrate_unlocked(object(), mock.Mock()))
        self.assertFalse(result['complete'])
        self.assertFalse(result['portal_keys_safe'])
        self.assertFalse(result['saved'])
        self.assertIn('did not finish saving', output.getvalue())
        save.assert_called_once_with(self.root / 'report.json', result)


if __name__ == '__main__':
    unittest.main()
