#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise migration and login notices with in-memory stores and no session bus."""
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from gi.repository import GLib

ROOT = Path(__file__).resolve().parents[1]
RENDER = runpy.run_path(str(ROOT / 'scripts/render-paths'))
PATHS = RENDER['paths']()
SECRET = 'org.freedesktop.Secret'


class Reply:
    def __init__(self, *values):
        self.values = values

    def unpack(self):
        return self.values


SERVICE_PATH = '/org/freedesktop/secrets'
COLLECTION_PATH = SERVICE_PATH + '/collection/kdewallet'
SESSION_PATH = SERVICE_PATH + '/session/1'


class Store:
    """6.30 protocol from src/api/KWallet/org.freedesktop.Secrets.*.xml.

    Runtime paths and failed-create behavior follow kwalletfreedesktopservice.cpp,
    kwalletfreedesktopcollection.cpp and kwalletfreedesktopitem.cpp in v6.30.0.
    """
    def __init__(self):
        self.collections = {COLLECTION_PATH: {'label': 'Login', 'locked': False}}
        self.items = {}
        self.calls = []
        self.fail_create_labels = set()
        self.fail_after_create = False
        self.corrupt_created = False
        self.fail_close = False
        self.closed = False

    def add(self, path=None, label='Saved account', attrs=None,
            raw=b'fixture\x00\xff', content_type='application/octet-stream',
            collection=COLLECTION_PATH):
        path = path or collection + '/' + str(len(self.items))
        self.items[path] = {'label': label, 'attributes': attrs if attrs is not None else {'service': 'fixture'},
                            'raw': raw, 'type': content_type, 'collection': collection}
        return path

    def call_sync(self, name, path, interface, method, parameters, *_):
        args = parameters.unpack() if parameters is not None else ()
        self.calls.append((name, path, interface, method, args))
        assert name == 'org.freedesktop.secrets', name
        def signature(expected):
            assert (parameters.get_type_string() if parameters is not None else None) == expected
        if interface == SECRET + '.Service':
            assert path == SERVICE_PATH, path
            if method == 'OpenSession':
                signature('(sv)')
                assert args[0] == 'plain'
                return Reply('', SESSION_PATH)
            if method == 'ReadAlias':
                signature('(s)')
                assert args == ('default',)
                return Reply(COLLECTION_PATH)
            if method == 'Lock':
                signature('(ao)')
                assert args[0] == [COLLECTION_PATH]
                if self.fail_close:
                    raise OSError('untrusted bus failure')
                if self.collections[COLLECTION_PATH]['locked']:
                    raise OSError("InvalidArgs: Can't lock object at path " + COLLECTION_PATH)
                self.closed = True
                self.collections[COLLECTION_PATH]['locked'] = True
                return Reply([COLLECTION_PATH], '/')
        if interface == 'org.freedesktop.DBus.Properties' and method == 'Get':
            signature('(ss)')
            kind, key = args
            if kind == SECRET + '.Service':
                assert path == SERVICE_PATH and key == 'Collections'
                return Reply(list(self.collections))
            if kind == SECRET + '.Collection':
                assert path in self.collections
                if key == 'Items':
                    return Reply([p for p, item in self.items.items() if item['collection'] == path])
                assert key in ('Label', 'Locked')
                return Reply(self.collections[path][key.lower()])
            assert kind == SECRET + '.Item' and path in self.items
            assert key in ('Label', 'Attributes')
            return Reply(self.items[path][key.lower()])
        if interface == SECRET + '.Item':
            assert path in self.items and path.startswith(SERVICE_PATH + '/collection/')
            if method == 'GetSecret':
                signature('(o)')
                assert args == (SESSION_PATH,)
                item = self.items[path]
                return Reply((SESSION_PATH, [], list(item['raw']), item['type']))
            if method == 'Delete':
                signature(None)
                del self.items[path]
                return Reply('/')
            if method == 'SetSecret':
                signature('((oayays))')
                value, = args
                assert value[0] == SESSION_PATH
                self.items[path].update(raw=bytes(value[2]), type=value[3])
                return Reply()
        if interface == SECRET + '.Collection' and method == 'CreateItem':
            assert path in self.collections and not self.collections[path]['locked']
            signature('(a{sv}(oayays)b)')
            properties, value, replace = args
            assert replace is False and value[0] == SESSION_PATH
            label = properties[SECRET + '.Item.Label']
            assert label, '6.30 rejects empty item labels'
            if label in self.fail_create_labels:
                raise OSError('untrusted bus failure containing secret fixture')
            created = self.add(label=label, attrs=properties[SECRET + '.Item.Attributes'],
                               raw=b'', content_type=value[3], collection=path)
            # 6.30 inserts the object before it checks text encoding.
            if value[3].startswith('text/'):
                bytes(value[2]).decode('utf-8')
            if self.fail_after_create:
                raise OSError('untrusted bus failure after creation')
            self.items[created]['raw'] = b'' if self.corrupt_created else bytes(value[2])
            return Reply(created, '/')
        if interface == SECRET + '.Session' and method == 'Close':
            assert path == SESSION_PATH
            signature(None)
            return Reply()
        raise AssertionError((name, path, interface, method, args))


class Fixture(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence/u5/tmp'
        evidence.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='wallet-', dir=evidence)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {'PATH': '/usr/bin', 'HOME': str(self.base),
                    'XDG_DATA_HOME': str(self.base / 'data'),
                    'XDG_STATE_HOME': str(self.base / 'state'),
                    'XDG_CONFIG_HOME': str(self.base / 'config'),
                    'XDG_RUNTIME_DIR': str(self.base / 'runtime')}
        self.helper = runpy.run_path(str(ROOT / 'scripts/emaki-wallet-migrate'))
        self.globals = self.helper['transfer'].__globals__
        self.report = self.helper['report_path'](self.env)
        self.source, self.target = Store(), Store()

    def old_store(self):
        old = Path(self.env['XDG_DATA_HOME']) / 'keyrings/login.keyring'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'original encrypted keyring bytes\x00\xff')
        return old

    def transfer(self, **kwargs):
        return self.helper['transfer'](self.source, self.target, self.report, **kwargs)

    def statuses(self, result):
        return [item['status'] for item in result['items'].values()]


class Migration(Fixture):
    def test_generic_binary_labels_attributes_and_original_remain_exact(self):
        old = self.old_store()
        original = old.read_bytes()
        attrs = {'service': 'example', 'account': 'person', 'custom': 'space and unicode λ'}
        raw = b'private fixture value\x00\xff\x80'
        self.source.add(label='Label λ', attrs=attrs, raw=raw)
        result = self.transfer()
        saved = next(iter(self.target.items.values()))
        self.assertEqual((saved['label'], saved['attributes'], saved['raw']), ('Label λ', attrs, raw))
        self.assertTrue(result['complete'])
        self.assertEqual(self.statuses(result), ['migrated'])
        self.assertTrue(self.target.closed)
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(self.report.stat().st_mode & 0o777, 0o600)
        text = self.report.read_text()
        for secret in ('private fixture value', 'custom', 'space and unicode', 'account', 'person'):
            self.assertNotIn(secret, text)

    def test_portal_master_key_is_binary_item_with_native_label(self):
        raw = bytes(range(128, 192))
        self.source.add(attrs={'app_id': 'org.example.Application',
                               'xdg:schema': 'org.freedesktop.portal.Secret'}, raw=raw)
        result = self.transfer()
        self.assertTrue(result['complete'])
        saved, = self.target.items.values()
        self.assertEqual(saved['label'], 'xdg-desktop-portal/org.example.Application')
        self.assertEqual((saved['raw'], saved['type']), (raw, 'application/octet-stream'))
        self.assertTrue(result['portal_keys_safe'])

    def test_locked_collection_remains_pending_then_recovers_when_unlocked(self):
        self.source.add()
        self.source.collections[COLLECTION_PATH]['locked'] = True
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertEqual(result['collections'][COLLECTION_PATH]['status'], 'locked')
        self.assertFalse(self.target.items)
        self.assertFalse(any(call[3] == 'GetSecret' for call in self.source.calls))
        self.source.collections[COLLECTION_PATH]['locked'] = False
        self.target.collections[COLLECTION_PATH]['locked'] = False
        result = self.transfer()
        self.assertTrue(result['complete'])
        self.assertEqual(self.statuses(result), ['migrated'])

    def test_duplicate_attributes_and_labels_keep_distinct_values(self):
        self.source.add(COLLECTION_PATH + '/0', raw=b'first secret')
        self.source.add(COLLECTION_PATH + '/1', raw=b'second secret')
        result = self.transfer()
        self.assertEqual(sorted(item['raw'] for item in self.target.items.values()),
                         [b'first secret', b'second secret'])
        self.assertEqual(self.statuses(result), ['migrated', 'migrated'])
        self.assertTrue(result['complete'])

    def test_one_failed_write_is_recorded_without_losing_successful_items(self):
        self.source.add(COLLECTION_PATH + '/0', label='Fails', raw=b'one')
        self.source.add(COLLECTION_PATH + '/1', label='Works', raw=b'two')
        self.target.fail_create_labels.add('Fails')
        result = self.transfer()
        self.assertEqual(self.statuses(result), ['failed', 'migrated'])
        self.assertFalse(result['complete'])
        self.assertNotIn('untrusted bus failure', self.report.read_text())
        self.assertEqual(json.loads(self.report.read_text()), result)

    def test_close_failure_does_not_claim_saved_items_are_durable(self):
        self.source.add()
        self.target.fail_close = True
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertEqual(self.statuses(result), ['pending'])
        self.assertTrue(result['errors'])
        self.target.fail_close = False
        result = self.transfer()
        self.assertTrue(result['complete'])
        self.assertEqual(len(self.target.items), 1)

    def test_existing_different_generic_password_is_never_overwritten(self):
        self.source.add(raw=b'old password')
        self.target.add(raw=b'new password')
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertEqual(self.statuses(result), ['failed'])
        self.assertEqual(next(iter(self.target.items.values()))['raw'], b'new password')
        self.assertFalse(any(call[3] == 'CreateItem' for call in self.target.calls))
        self.assertIn('already exists', next(iter(result['items'].values()))['reason'])

    def test_existing_different_portal_key_requires_explicit_recovery(self):
        self.source.add(attrs={'app_id': 'org.example.App', 'xdg:schema': 'org.freedesktop.portal.Secret'},
                        raw=b'o' * 64)
        target = self.target.add(label='xdg-desktop-portal/org.example.App', attrs={}, raw=b'n' * 64)
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertFalse(result['portal_keys_safe'])
        self.assertEqual(self.target.items[target]['raw'], b'n' * 64)
        self.assertFalse(any(call[3] == 'SetSecret' for call in self.target.calls))
        self.target.collections[COLLECTION_PATH]['locked'] = False
        result = self.transfer(replace_portal_keys=True)
        self.assertTrue(result['complete'])
        self.assertEqual(self.target.items[target]['raw'], b'o' * 64)
        self.assertEqual(len(self.target.items), 1)

    def test_recovery_without_old_key_cannot_replace_portal_key(self):
        self.target.add(label='xdg-desktop-portal/org.example.App', attrs={}, raw=b'n' * 64)
        self.transfer(replace_portal_keys=True)
        self.assertFalse(any(call[3] == 'SetSecret' for call in self.target.calls))

    def test_binary_reported_as_text_is_preserved(self):
        self.source.add(raw=b'\xff\x80\x00', content_type='text/plain')
        result = self.transfer()
        self.assertTrue(result['complete'])
        saved, = self.target.items.values()
        self.assertEqual((saved['raw'], saved['type']), (b'\xff\x80\x00', 'application/octet-stream'))

    def test_created_item_is_deleted_after_failed_creation_or_verification(self):
        for failure in ('fail_after_create', 'corrupt_created'):
            with self.subTest(failure=failure):
                self.source, self.target = Store(), Store()
                self.report.unlink(missing_ok=True)
                self.source.add()
                setattr(self.target, failure, True)
                result = self.transfer()
                self.assertFalse(result['complete'])
                self.assertFalse(self.target.items)
                self.assertTrue(any(call[3] == 'Delete' for call in self.target.calls))

    def test_empty_label_gets_stable_fallback(self):
        self.source.add(label='')
        result = self.transfer()
        self.assertTrue(result['complete'])
        self.assertEqual(next(iter(self.target.items.values()))['label'], 'Saved password')

    def test_live_recovery_does_not_lock_wallet_or_claim_saved(self):
        self.source.add()
        result = self.transfer(flush=False)
        self.assertTrue(result['complete'])
        self.assertFalse(result['saved'])
        self.assertEqual(self.statuses(result), ['migrated'])
        self.assertFalse(any(call[3] == 'Lock' for call in self.target.calls))

    def test_generic_conflict_does_not_block_verified_portal_keys(self):
        self.source.add(raw=b'old password')
        self.target.add(raw=b'new password')
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertTrue(result['source_read'])
        self.assertTrue(result['portal_keys_safe'])

    def test_deadline_cannot_be_swallowed_by_item_handler(self):
        self.source.add()
        original = self.source.call_sync
        def expired(*args):
            if args[3] == 'GetSecret':
                raise self.helper['MigrationInterrupted']('deadline')
            return original(*args)
        self.source.call_sync = expired
        with self.assertRaises(self.helper['MigrationInterrupted']):
            self.transfer()

    def test_failed_cleanup_cannot_swallow_deadline(self):
        for portal in (False, True):
            with self.subTest(portal=portal):
                self.source, self.target = Store(), Store()
                if portal:
                    self.source.add(attrs={'app_id': 'org.example.App',
                                           'xdg:schema': 'org.freedesktop.portal.Secret'}, raw=b'o' * 64)
                    self.target.add(label='xdg-desktop-portal/org.example.App', attrs={}, raw=b'n' * 64)
                else:
                    self.source.add()
                original = self.target.call_sync
                interrupted = False
                def failed(*args):
                    nonlocal interrupted
                    method = args[3]
                    if (portal and method == 'SetSecret') or (not portal and method == 'GetSecret'):
                        if not interrupted:
                            interrupted = True
                            raise self.helper['MigrationInterrupted']('deadline')
                        raise OSError('cleanup failed')
                    if method == 'Delete':
                        raise OSError('cleanup failed')
                    return original(*args)
                self.target.call_sync = failed
                with self.assertRaises(self.helper['MigrationInterrupted']):
                    self.transfer(replace_portal_keys=portal)

    def test_report_makes_repeated_transfer_idempotent(self):
        self.source.add()
        first = self.transfer()
        self.target.closed = False
        self.target.collections[COLLECTION_PATH]['locked'] = False
        second = self.transfer()
        self.assertEqual(first, second)
        self.assertEqual(len([call for call in self.target.calls if call[3] == 'CreateItem']), 1)
        identity = hashlib.sha256((COLLECTION_PATH + '\0' + COLLECTION_PATH + '/0').encode()).hexdigest()
        self.assertEqual(second['items'][identity]['status'], 'migrated')

    def test_locked_target_never_writes_source_or_destination(self):
        self.source.add()
        self.target.collections[COLLECTION_PATH]['locked'] = True
        result = self.transfer()
        self.assertFalse(result['complete'])
        self.assertIn('locked or unavailable', result['errors'][0])
        self.assertFalse(self.target.items)
        self.assertFalse(any(call[3] in ('CreateItem', 'Delete', 'SetSecret') for call in self.source.calls))

    def test_locked_target_discards_previous_successes_and_notice_counts(self):
        self.source.add()
        self.assertTrue(self.transfer()['complete'])
        result = self.transfer(flush=False)
        self.assertFalse(result['complete'])
        self.assertFalse(result['source_read'])
        self.assertEqual(result['items'], {})
        self.assertEqual(result['collections'], {})
        notices = []
        self.helper['run_login'](self.env, notices.append)
        self.assertIn('Copied items: 0.', notices[0])
        self.assertIn('number of items needing recovery is not known yet', notices[0])
        self.assertNotIn('Items needing recovery: 0.', notices[0])


class AuthenticationBridge(Fixture):
    def bridge(self, failure=None):
        runtime = tempfile.TemporaryDirectory(prefix='wallet-', dir='/tmp')
        self.addCleanup(runtime.cleanup)
        self.env['XDG_RUNTIME_DIR'] = runtime.name
        old = self.old_store()
        before = old.read_bytes()
        root = self.base / 'bridge'
        root.mkdir(mode=0o700)
        source, target = mock.Mock(name='source'), mock.Mock(name='target')
        source.poll.return_value = 0
        target.poll.return_value = None if failure else 0
        target.returncode = 0
        events = []
        result = {}
        password = b'bridge password fixture'
        def wait(path, timeout, process=None):
            if path.name == 'request':
                if failure == 'request':
                    raise RuntimeError('fixture request timed out')
                return {'environment': self.env}
            if path.name == 'ready':
                if failure == path.parent.name:
                    raise RuntimeError('fixture readiness timed out')
                return {'address': path.parent.name + '-bus'}
            if path.name == 'done':
                return {}
            raise AssertionError(path)
        real_write = self.helper['write_json']
        def write(path, value):
            relative = path.relative_to(root) if path.is_relative_to(root) else Path(*path.parts[-2:])
            events.append(('write', str(relative)))
            if path.name == 'result':
                result.update(value)
            real_write(path, value)
        def stop(process, group=False):
            events.append(('stop', 'target' if process is target else 'source'))
        def target_wait(**kwargs):
            events.append(('wait', 'target'))
            if failure == 'exit' and events.count(('wait', 'target')) == 1:
                raise subprocess.TimeoutExpired('stage', 10)
        target.wait.side_effect = target_wait
        transfer = mock.Mock(return_value={'complete': True, 'items': {}, 'collections': {}, 'errors': []})
        if failure == 'transfer':
            transfer.side_effect = self.helper['MigrationInterrupted']('fixture copying timed out')
        def launch(command, **kwargs):
            scratch = Path(command[-1]).parent
            self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
            self.assertEqual((scratch / 'done').readlink(), root / 'done')
            (root / 'done').touch()
            self.assertTrue((scratch / 'done').exists())
            (root / 'done').unlink()
            return source if command[0] == 'dbus-run-session' else target
        with mock.patch.dict(self.globals, {'wait_file': wait, 'write_json': write,
                                          'stop': stop, 'connect': mock.Mock(), 'transfer': transfer}), \
             mock.patch('sys.stdin', types.SimpleNamespace(buffer=io.BytesIO(password))), \
             mock.patch('resource.setrlimit'), mock.patch('signal.alarm'), \
             mock.patch('subprocess.Popen', side_effect=launch) as start:
            self.helper['bridge_worker'](root)
        self.assertFalse(root.exists())
        self.assertEqual(list(Path(runtime.name).iterdir()), [])
        self.assertEqual(old.read_bytes(), before)
        self.assertNotIn(password.decode(), json.dumps(result))
        return root, start, source, target, events, result, password

    def test_bridge_uses_isolated_source_and_stdin_only_credentials(self):
        root, start, source, target, events, result, password = self.bridge()
        source_call, target_call = start.call_args_list
        command = source_call.args[0]
        self.assertEqual(command[:2], ['dbus-run-session', '--config-file'])
        scratch = Path(command[-1]).parent
        self.assertEqual(scratch.parent, Path(self.env['XDG_RUNTIME_DIR']))
        self.assertEqual(command[-3:], [str(ROOT / 'scripts/emaki-wallet-migrate'), '--source', str(scratch / 'source')])
        self.assertNotIn('--worker', command)
        self.assertEqual(source_call.kwargs['env']['XDG_DATA_HOME'], str(scratch / 'source/data'))
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', source_call.kwargs['env'])
        self.assertEqual(target_call.args[0][-2:], [str(ROOT / 'scripts/emaki-wallet-stage'), str(scratch / 'target')])
        self.assertEqual(target_call.kwargs['env'], self.env)
        for process in (source, target):
            process.stdin.write.assert_called_once_with(password)
            process.stdin.close.assert_called_once()
        self.assertNotIn(password.decode(), repr(start.call_args_list))
        self.assertTrue(result['complete'])

    def test_bridge_socket_paths_do_not_grow_with_home(self):
        self.env['HOME'] = str(self.base / ('h' * 100))
        _, start, _, _, _, result, _ = self.bridge()
        source_root = Path(start.call_args_list[0].args[0][-1])
        target_root = Path(start.call_args_list[1].args[0][-1])
        self.assertLessEqual(len(os.fsencode(source_root / 'runtime/dbus-XXXXXXXXXX')), 99)
        self.assertLessEqual(len(os.fsencode(source_root / 'control/control')), 107)
        self.assertLessEqual(len(os.fsencode(target_root / 'environment')), 107)
        self.assertTrue(result['complete'])

    def test_bridge_runtime_rejects_symlinks_public_permissions_and_other_owners(self):
        with tempfile.TemporaryDirectory(prefix='wallet-', dir='/tmp') as directory:
            runtime = Path(directory)
            env = dict(self.env, XDG_RUNTIME_DIR=directory)
            os.chmod(runtime, 0o755)
            with self.assertRaisesRegex(RuntimeError, 'private and owned'):
                self.helper['bridge_scratch'](env)
            os.chmod(runtime, 0o700)
            link = self.base / 'runtime-link'
            link.symlink_to(runtime, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'private and owned'):
                self.helper['bridge_scratch'](dict(env, XDG_RUNTIME_DIR=str(link)))
            with mock.patch('os.getuid', return_value=os.getuid() + 1):
                with self.assertRaisesRegex(RuntimeError, 'private and owned'):
                    self.helper['bridge_scratch'](env)
            scratch = self.helper['bridge_scratch'](env)
            self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
            scratch.rmdir()
            long_runtime = runtime / ('r' * 100)
            long_runtime.mkdir(mode=0o700)
            with self.assertRaisesRegex(RuntimeError, 'path is too long'):
                self.helper['bridge_scratch'](dict(env, XDG_RUNTIME_DIR=str(long_runtime)))

    def test_stage_exits_and_all_groups_stop_before_result_is_published(self):
        _, _, _, _, events, _, _ = self.bridge()
        result_index = events.index(('write', 'result'))
        for event in (('write', 'target/done'), ('wait', 'target'),
                      ('stop', 'target'), ('stop', 'source')):
            self.assertLess(events.index(event), result_index)

    def test_request_source_target_and_transfer_failures_cleanup_before_result(self):
        for failure in ('request', 'source', 'target', 'transfer', 'exit'):
            with self.subTest(failure=failure):
                # Each fixture run has its own original and control root.
                if (self.base / 'data').exists():
                    (self.base / 'data/keyrings/login.keyring').unlink()
                    (self.base / 'data/keyrings').rmdir()
                _, start, _, _, events, result, _ = self.bridge(failure)
                self.assertFalse(result['complete'])
                self.assertIn('Run emaki-keyring-recover', result['error'])
                result_index = events.index(('write', 'result'))
                stops = [event for event in events if event[0] == 'stop']
                self.assertEqual(len(stops), len(start.call_args_list))
                for stop in stops:
                    self.assertLess(events.index(stop), result_index)

    def test_pam_drops_privileges_before_creating_private_worker(self):
        legacy = self.base / '.local/share/keyrings'
        legacy.mkdir(parents=True)
        (legacy / 'login.keyring').write_bytes(b'old encrypted store')
        events = []
        account = types.SimpleNamespace(pw_name='fixture', pw_uid=1001, pw_gid=1002,
                                        pw_dir=str(self.base))
        process = mock.Mock(pid=12345)
        password = b'pam password fixture'
        real_directory = self.helper['bridge_directory']
        def directory(home):
            events.append('directory')
            return real_directory(home)
        def start(*args, **kwargs):
            events.append('start')
            return process
        with mock.patch('sys.stdin', types.SimpleNamespace(buffer=io.BytesIO(password + b'\0tail'))), \
             mock.patch('resource.setrlimit'), \
             mock.patch('pwd.getpwnam', return_value=account), \
             mock.patch('os.getuid', side_effect=lambda: 1001 if 'setuid' in events else 0), \
             mock.patch('os.initgroups', side_effect=lambda *args: events.append('groups')), \
             mock.patch('os.setgid', side_effect=lambda *args: events.append('setgid')), \
             mock.patch('os.setuid', side_effect=lambda *args: events.append('setuid')), \
             mock.patch.dict(self.globals, {'bridge_directory': directory,
                                          'process_identity': lambda pid: ['fixture-boot', '123']}), \
             mock.patch('subprocess.Popen', side_effect=start) as popen:
            # Directory ownership is represented by the dropped fixture account.
            with mock.patch.object(Path, 'lstat', autospec=True) as lstat:
                actual = os.stat
                def fake_lstat(path):
                    info = actual(path, follow_symlinks=False)
                    return types.SimpleNamespace(st_mode=info.st_mode, st_uid=1001)
                lstat.side_effect = fake_lstat
                self.helper['pam_unlock']({'PAM_USER': 'fixture', 'PAM_TYPE': 'auth',
                                           'PYTHONPATH': 'untrusted'})
        self.assertEqual(events[:5], ['groups', 'setgid', 'setuid', 'directory', 'start'])
        process.stdin.write.assert_called_once_with(password)
        self.assertEqual(popen.call_args.kwargs['env'], {'PATH': '/usr/bin', 'HOME': str(self.base)})
        self.assertIn('-I', popen.call_args.args[0])
        self.assertNotIn(password.decode(), repr(popen.call_args))
        for path in self.base.rglob('*'):
            if path.is_file():
                self.assertNotIn(password, path.read_bytes())

    def test_pam_passwordless_or_non_auth_session_does_not_start_worker(self):
        for password, kind in ((b'', 'auth'), (b'secret', 'open_session')):
            with mock.patch('sys.stdin', types.SimpleNamespace(buffer=io.BytesIO(password))), \
                 mock.patch('resource.setrlimit'), mock.patch('subprocess.Popen') as start:
                self.helper['pam_unlock']({'PAM_TYPE': kind})
            start.assert_not_called()

    def test_fresh_account_does_not_start_password_worker(self):
        account = types.SimpleNamespace(pw_name='fixture', pw_uid=os.getuid(),
                                        pw_gid=os.getgid(), pw_dir=str(self.base))
        with mock.patch('sys.stdin', types.SimpleNamespace(buffer=io.BytesIO(b'password'))), \
             mock.patch('resource.setrlimit'), mock.patch('pwd.getpwnam', return_value=account), \
             mock.patch('os.initgroups'), mock.patch('os.setgid'), mock.patch('os.setuid'), \
             mock.patch('subprocess.Popen') as start:
            self.helper['pam_unlock']({'PAM_USER': 'fixture', 'PAM_TYPE': 'auth'})
        start.assert_not_called()
        self.assertFalse((self.base / '.cache/emaki-wallet-login').exists())

    def test_timeout_signals_raise_for_cleanup_instead_of_exiting_immediately(self):
        import signal
        with mock.patch('signal.signal') as register:
            self.helper['main']([])
        handlers = {call.args[0]: call.args[1] for call in register.call_args_list}
        for signum in (signal.SIGTERM, signal.SIGALRM):
            with self.assertRaises(self.helper['MigrationInterrupted']):
                handlers[signum](signum, None)

    def test_source_worker_password_pipe_and_cleanup_after_timeout(self):
        root = self.base / 'source-worker'
        root.mkdir()
        process = mock.Mock()
        password = b'source worker password fixture'
        with mock.patch('sys.stdin', types.SimpleNamespace(buffer=io.BytesIO(password))), \
             mock.patch('resource.setrlimit'), \
             mock.patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'private-source'}), \
             mock.patch.dict(self.globals, {'connect': mock.Mock(),
                                           'call': mock.Mock(return_value=(True,)),
                                           'wait_file': mock.Mock(side_effect=RuntimeError('fixture timeout')),
                                           'stop': mock.Mock()}) as _, \
             mock.patch('subprocess.Popen', return_value=process) as start:
            stopped = self.globals['stop']
            with self.assertRaisesRegex(RuntimeError, 'fixture timeout'):
                self.helper['source_worker'](root)
            stopped.assert_called_once_with(process)
        process.stdin.write.assert_called_once_with(password)
        process.stdin.close.assert_called_once()
        self.assertNotIn(password.decode(), repr(start.call_args))
        self.assertIn('--unlock', start.call_args.args[0])
        self.assertEqual(json.loads((root / 'ready').read_text()), {'address': 'private-source'})
        for path in root.rglob('*'):
            if path.is_file():
                self.assertNotIn(password, path.read_bytes())

    def test_existing_public_wallet_prevents_stage_request(self):
        self.old_store()
        self.env['DBUS_SESSION_BUS_ADDRESS'] = 'public-bus'
        bus = mock.Mock()
        with mock.patch.dict(self.globals, {'connect': mock.Mock(return_value=bus),
                                          'call': mock.Mock(return_value=(True,)),
                                          'bridge_directory': mock.Mock(side_effect=AssertionError('stage requested'))}):
            self.helper['snapshot'](self.env)
        report = json.loads(self.report.read_text())
        self.assertFalse(report['complete'])
        self.assertIn('existing wallet service', report['errors'][0])
        bus.close_sync.assert_called_once_with(None)


class LoginNotice(Fixture):
    def test_login_units_and_installed_help(self):
        start = RENDER['substitute']((ROOT / 'systemd/emaki-wallet-start.service').read_text(), PATHS)
        self.assertIn('Before=xdg-desktop-autostart.target emaki-shell.service', start)
        self.assertIn('ExecStart=/usr/bin/emaki-wallet-start', start)
        notice = configparser.ConfigParser()
        notice.read_string(RENDER['substitute']((ROOT / 'systemd/emaki-wallet-migrate.service').read_text(), PATHS))
        self.assertIn('emaki-shell.service', notice['Unit']['After'].split())
        self.assertIn('emaki-wallet-start.service', notice['Unit']['After'].split())
        self.assertEqual(notice['Service']['ExecStart'], '/usr/bin/emaki-wallet-migrate --automatic')
        self.assertGreaterEqual(int(notice['Service']['TimeoutStartSec']), 120 * 4)
        for path in ('systemd/niri-emaki.service', 'systemd/niri-wallet.conf'):
            session = configparser.ConfigParser(interpolation=None, strict=False)
            session.read(ROOT / path)
            for unit in ('emaki-wallet-start.service', 'emaki-wallet-migrate.service'):
                self.assertIn(unit, session['Unit']['Wants'].split())
        expected = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        makefile = (ROOT / 'Makefile').read_text()
        for name in ('emaki-wallet-start', 'emaki-wallet-migrate', 'emaki-wallet-stage'):
            self.assertIn('/usr/bin/' + name, expected)
            self.assertIn('scripts/' + name, makefile)
        self.assertIn('/usr/lib/systemd/user/niri.service.d/50-emaki-wallet.conf', expected)
        help_text = subprocess.check_output([str(ROOT / 'scripts/emaki-wallet-migrate'), '--help'], text=True)
        for text in ('first graphical login', 'raw bytes', 'original keyring files',
                     'emaki-keyring-recover', '/usr/share/doc/emaki/ZONES.md'):
            self.assertIn(text, help_text)
        self.assertNotIn('no automatic import', help_text)

    def test_notice_ends_busctl_options_and_checks_delivery(self):
        with mock.patch.object(subprocess, 'run') as send:
            self.helper['notify']('Saved password copy needs attention.')
        argv = send.call_args.args[0]
        self.assertEqual(argv[:4], ['busctl', '--user', 'call', '--'])
        self.assertEqual(argv[-1], '-1')
        self.assertTrue(send.call_args.kwargs['check'])
        self.assertEqual(send.call_args.kwargs['timeout'], 3)

    def test_failed_attempt_retries_on_next_login_and_preserves_original(self):
        old = self.old_store()
        before = old.read_bytes()
        self.helper['snapshot'](self.env)
        self.assertTrue(json.loads(self.report.read_text())['attempted'])
        directory = self.helper['bridge_directory'](self.env['HOME'])
        root = directory / 'login-retry'
        root.mkdir()
        owner = {'directory': root.name, 'pid': 12345, 'identity': ['boot', '123']}
        self.helper['write_json'](root / 'owner', owner)
        self.helper['write_json'](directory / 'current', owner)
        def completed(path, timeout):
            self.assertEqual(path, root / 'result')
            report = self.helper['read_report'](self.report)
            report.update(complete=True, portal_keys_safe=True, errors=[])
            self.helper['write_json'](self.report, report)
            return report
        with mock.patch.dict(self.globals, {'process_identity': lambda pid: ['boot', '123'],
                                          'wait_file': completed}):
            self.helper['snapshot'](self.env)
        self.assertTrue((root / 'request').is_file())
        self.assertTrue(json.loads(self.report.read_text())['complete'])
        self.assertEqual(old.read_bytes(), before)

    def test_stale_current_never_waits_for_dead_worker(self):
        self.old_store()
        directory = self.helper['bridge_directory'](self.env['HOME'])
        root = directory / 'login-dead'
        root.mkdir()
        owner = {'directory': root.name, 'pid': 12345, 'identity': ['old-boot', '123']}
        self.helper['write_json'](root / 'owner', owner)
        self.helper['write_json'](directory / 'current', owner)
        with mock.patch.dict(self.globals, {'process_identity': lambda pid: ['new-boot', '123'],
                                          'wait_file': mock.Mock(side_effect=AssertionError('stale worker waited'))}):
            self.helper['snapshot'](self.env)
        self.assertFalse((directory / 'current').exists())
        self.assertFalse(root.exists())
        self.assertFalse(json.loads(self.report.read_text())['complete'])

    def test_cleanup_removes_crash_copies_and_preserves_live_worker(self):
        directory = self.helper['bridge_directory'](self.env['HOME'])
        roots = {}
        for state, pid in (('dead', 12345), ('live', 12346)):
            root = directory / ('login-' + state)
            (root / 'source/data/keyrings').mkdir(parents=True)
            (root / 'source/data/keyrings/login.keyring').write_bytes(b'encrypted scratch')
            self.helper['write_json'](root / 'owner', {'pid': pid, 'identity': ['boot', '123']})
            roots[state] = root
        abandoned = directory / 'login-abandoned'
        abandoned.mkdir()
        os.utime(abandoned, (0, 0))
        recent = directory / 'login-starting'
        recent.mkdir()
        with mock.patch.dict(self.globals, {'process_identity': lambda pid: ['boot', '123'] if pid == 12346 else None}):
            self.helper['cleanup_bridges'](directory)
        self.assertFalse(roots['dead'].exists())
        self.assertTrue((roots['live'] / 'source/data/keyrings/login.keyring').exists())
        self.assertFalse(abandoned.exists())
        self.assertTrue(recent.exists())

    def test_success_and_failure_notice_delivered_once_per_account(self):
        old = self.old_store()
        before = old.read_bytes()
        for complete in (False, True):
            with self.subTest(complete=complete):
                self.helper['write_json'](self.report, {'items': {}, 'collections': {},
                                                      'errors': [], 'complete': complete})
                notice = mock.Mock()
                for _ in range(4):
                    self.helper['run_login'](self.env, notice)
                notice.assert_called_once()
                self.assertTrue(notice.call_args.args[0].startswith(self.helper['SUCCESS' if complete else 'FAILURE']))
                self.assertIn(str(self.report), notice.call_args.args[0])
                self.assertTrue(json.loads(self.report.read_text())['notified'])
                self.assertEqual(old.read_bytes(), before)

    def test_failed_delivery_can_retry_without_claiming_notification_sent(self):
        self.old_store()
        notice = mock.Mock(side_effect=subprocess.CalledProcessError(1, 'busctl'))
        with self.assertRaises(subprocess.CalledProcessError):
            self.helper['run_login'](self.env, notice)
        self.assertFalse(self.report.exists())
        notice.side_effect = None
        self.helper['run_login'](self.env, notice)
        self.helper['run_login'](self.env, notice)
        self.assertEqual(notice.call_count, 2)

    def test_automatic_delivery_waits_for_slow_first_login(self):
        self.old_store()
        notice = mock.Mock(side_effect=[subprocess.CalledProcessError(1, 'busctl')] * 15 + [None])
        real_login = self.helper['run_login']
        with mock.patch.dict(self.globals, {'run_login': lambda env: real_login(env, notice)}), \
             mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch('time.sleep') as sleep, mock.patch('signal.signal'):
            self.helper['main'](['--automatic'])
        self.assertEqual(notice.call_count, 16)
        self.assertEqual(sleep.call_args_list, [mock.call(1)] * 15)
        self.assertTrue(json.loads(self.report.read_text())['notified'])

    def test_fresh_login_is_silent_without_startup_error(self):
        notice = mock.Mock()
        self.helper['run_login'](self.env, notice)
        self.helper['snapshot'](self.env)
        notice.assert_not_called()
        self.assertFalse(self.report.exists())

    def test_runtime_failure_is_reported_even_on_fresh_logins(self):
        error = Path(self.env['XDG_RUNTIME_DIR']) / 'emaki/wallet-start-error'
        error.parent.mkdir(parents=True)
        error.write_text('The password service could not start [KWallet].')
        notice = mock.Mock()
        for _ in range(2):
            self.helper['run_login'](self.env, notice)
        self.assertEqual(notice.call_args_list, [mock.call(error.read_text())] * 2)
        self.assertFalse(self.report.exists())

    def test_unknown_or_unreadable_legacy_store_still_needs_notice(self):
        directory = Path(self.env['XDG_DATA_HOME']) / 'keyrings'
        directory.parent.mkdir(parents=True)
        directory.symlink_to(directory.parent / 'missing')
        self.assertTrue(self.helper['legacy_present'](self.env))
        directory.unlink()
        with mock.patch.object(Path, 'iterdir', side_effect=PermissionError('unreadable')):
            self.assertTrue(self.helper['legacy_present'](self.env))


if __name__ == '__main__':
    unittest.main()
