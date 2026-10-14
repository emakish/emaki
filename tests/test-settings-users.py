#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Users backend: account rules, refusals and read-back, then the real D-Bus client.

The rule tests drive scripts/emaki-settings-users with an in-process fake of AccountsService and
logind. The bus tests inject a private connection into the client against synthetic services
(tests/fixtures/fake-accounts-service.py) on a private dbus-daemon: no system bus, no root, no
account of this computer is read or changed.
"""
import ctypes
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import warnings

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/emaki-settings-users'
loader = importlib.machinery.SourceFileLoader('settings_users', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
users = importlib.util.module_from_spec(spec)
loader.exec_module(users)
PASSWORD = 'correct horse 42!'
# The managed terminal: runs the command after -- with what the person types on stdin and
# keeps what the window shows; FAKE_TERMINAL_OPEN=0 is a terminal that fails to open.
FAKE_TERMINAL = r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
base = Path(os.environ['FAKE_TERMINAL_DIR'])
assert sys.argv[1] == '--', sys.argv
if os.environ['FAKE_TERMINAL_OPEN'] != '1':
    sys.exit(1)
with open(base / 'typed') as typed, open(base / 'window', 'w') as window:
    sys.exit(subprocess.run(sys.argv[2:], stdin=typed, stdout=window, stderr=window).returncode)
'''


def verify(password, hashed):
    library = ctypes.CDLL('libcrypt.so.2')
    library.crypt.restype = ctypes.c_char_p
    library.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    return library.crypt(password.encode(), hashed.encode()) == hashed.encode()


class FakeAccounts:
    """The Accounts interface of the program, over a dict; failures by method name."""

    def __init__(self, rows, sessions=()):
        self.rows = {row['Uid']: dict(row) for row in rows}
        self.sessions_set = set(sessions)
        self.calls = []
        self.deny, self.fail, self.late, self.other = set(), set(), set(), set()
        # Accounts FindUserById answers with an object that no longer exists.
        self.stale = set()

    def entries(self):
        return [(row['UserName'], uid, row.get('Shell', '/bin/bash')) for uid, row in self.rows.items()]

    def entries_names(self):
        return {name for name, _uid, _shell in self.entries()}

    def check(self, name):
        if name in self.deny:
            raise users.Refusal('authorization_refused')
        if name in self.fail:
            raise users.Refusal('accounts_service_failed', sent=True)

    def late_failure(self, name):
        if name in self.late:
            raise users.Refusal('accounts_service_failed', sent=True)

    def find(self, uid):
        return f'/User{uid}' if uid in self.rows else None

    def properties(self, path):
        uid = int(path[5:])
        if uid in self.stale or uid not in self.rows:
            raise users.Refusal('account_lookup_failed')
        return dict(self.rows[uid])

    def sessions(self):
        return set(self.sessions_set)

    def create(self, login, name, account_type):
        self.calls.append(('create', login, name, account_type))
        self.check('create')
        uid = max([999] + list(self.rows)) + 1
        self.rows[uid] = {'Uid': uid, 'UserName': login, 'RealName': name, 'AccountType': account_type,
                          'Locked': True, 'SystemAccount': False, 'HomeDirectory': '/home/' + login}
        return f'/User{uid}'

    def delete(self, uid, remove_files):
        self.calls.append(('delete', uid, remove_files))
        self.check('delete')
        del self.rows[uid]

    def set_name(self, path, name):
        self.calls.append(('set_name', path, name))
        self.check('set_name')
        self.rows[int(path[5:])]['RealName'] = 'Another writer' if 'set_name' in self.other else name
        self.late_failure('set_name')

    def set_password(self, path, hashed):
        self.calls.append(('set_password', path, hashed))
        self.check('set_password')
        self.rows[int(path[5:])]['Locked'] = False

    def set_account_type(self, path, account_type):
        self.calls.append(('set_account_type', path, account_type))
        self.check('set_account_type')
        self.rows[int(path[5:])]['AccountType'] = account_type
        self.late_failure('set_account_type')


class FakeSystem(users.System):
    def __init__(self, accounts, extra=(), groups=()):
        super().__init__()
        self.accounts, self.extra, self.groups = accounts, list(extra), set(groups)
        self.homes, self.retained = set(), []

    def entries(self):
        return self.accounts.entries() + self.extra

    def uid_range(self):
        return 1000, 60000

    def taken(self, login):
        return login in {name for name, _uid, _shell in self.entries()} or login in self.groups

    def home_exists(self, home):
        return home in self.homes

    def retain_home(self, user):
        if user['home'] not in self.homes:
            return None
        self.homes.remove(user['home'])
        self.retained.append((user['home'], user['uid']))
        return user['home'] + '.removed-20261011'


def account(uid, login, administrator=False, locked=False, name='', system=False):
    return {'Uid': uid, 'UserName': login, 'RealName': name, 'AccountType': int(administrator),
            'Locked': locked, 'SystemAccount': system, 'HomeDirectory': '/home/' + login}


class Rules(unittest.TestCase):
    def setUp(self):
        # Rule tests isolate account decisions; the socket lock has its own test below.
        self.lock_patch = mock.patch.object(users, 'lock', return_value=mock.Mock())
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.accounts = FakeAccounts([account(1000, 'ada', True, name='Ada'), account(1001, 'bo'),
                                      account(1002, 'cy', True)], sessions=[1000])
        self.system = FakeSystem(self.accounts, extra=[('daemon', 2, '/usr/bin/nologin'),
                                                      ('svc', 1500, '/usr/bin/nologin'),
                                                      ('nobody', 65534, '/usr/bin/nologin')])

    def run_command(self, *argv, stdin='', uid=1000):
        out = io.StringIO()
        saved = sys.stdout
        sys.stdout = out
        try:
            code = users.main(list(argv) + ['--json'], io.StringIO(stdin), self.accounts, self.system, uid)
        finally:
            sys.stdout = saved
        return code, json.loads(out.getvalue())

    def logins(self, reply):
        return [row['login'] for row in reply['users']]

    def test_an_account_the_service_cannot_describe_is_shown_and_left_alone(self):
        # AccountsService can answer FindUserById with an object it no longer exports.
        self.accounts.stale.add(1002)
        code, reply = self.run_command('status')
        self.assertEqual(code, 0)
        cy = reply['users'][2]
        self.assertEqual((cy['login'], cy['available'], cy['administrator']), ('cy', False, False))
        self.assertEqual(reply['administrators'], 1)
        for argv in (('set-name', 'cy', 'C'), ('set-administrator', 'cy', 'no'), ('remove', 'cy', '--home', 'keep')):
            code, reply = self.run_command(*argv)
            self.assertEqual((code, reply['reason']), (1, 'account_unavailable'))
        # An unknown role never counts: ada is now the last usable administrator.
        self.accounts.sessions_set.clear()
        code, reply = self.run_command('set-administrator', 'ada', 'no', uid=1001)
        self.assertEqual(reply['reason'], 'last_administrator')
        self.assertEqual(self.accounts.calls, [])

    def test_a_removal_waits_until_the_service_drops_the_account(self):
        looks = []
        original = self.accounts.properties

        def lingering(path):
            looks.append(path)
            if path == '/User1001' and 'bo' not in self.accounts.entries_names() and looks.count(path) <= 5:
                return {'UserName': 'bo'}
            return original(path)
        self.accounts.properties = lingering
        code, reply = self.run_command('remove', 'bo', '--home', 'keep')
        self.assertEqual(code, 0)
        self.assertNotIn('bo', self.logins(reply))
        self.assertGreaterEqual(looks.count('/User1001'), 5)

    def test_status_lists_people_like_the_login_screen(self):
        self.accounts.rows[1003] = account(1003, 'robot', system=True)
        self.accounts.rows[1004] = dict(account(1004, 'shell-less'), Shell='/bin/false')
        code, reply = self.run_command('status')
        self.assertEqual(code, 0)
        self.assertEqual(reply['status'], 'read')
        self.assertEqual(self.logins(reply), ['ada', 'bo', 'cy'])
        ada = reply['users'][0]
        self.assertEqual(ada, {'login': 'ada', 'name': 'Ada', 'uid': 1000, 'administrator': True, 'locked': False,
                               'current': True, 'signed_in': True, 'home': '/home/ada', 'available': True})
        self.assertEqual(reply['administrators'], 2)
        self.assertNotIn('path', json.dumps(reply))

    def test_add_standard_and_administrator_with_a_hash_only(self):
        for login, flag, account_type in (('dee', 'no', 0), ('eve', 'yes', 1)):
            code, reply = self.run_command('add', login, 'Dee Person', '--administrator', flag, stdin=PASSWORD + '\n')
            self.assertEqual((code, reply['status']), (0, 'applied'), reply)
            self.assertIn(login, self.logins(reply))
            create = [call for call in self.accounts.calls if call[0] == 'create'][-1]
            self.assertEqual(create, ('create', login, 'Dee Person', account_type))
            hashed = [call for call in self.accounts.calls if call[0] == 'set_password'][-1][2]
            self.assertTrue(hashed.startswith('$'), hashed)
            self.assertNotIn(PASSWORD, hashed)
            self.assertTrue(verify(PASSWORD, hashed))
        self.assertNotIn(PASSWORD, json.dumps(self.accounts.calls[0]))

    def test_add_refuses_bad_input_before_any_call(self):
        cases = [(('BadLogin', 'X'), PASSWORD, 'invalid_login'), (('9lives', 'X'), PASSWORD, 'invalid_login'),
                 (('a' * 33, 'X'), PASSWORD, 'invalid_login'), (('dee', 'A:B'), PASSWORD, 'invalid_name'),
                 (('dee', 'A,B'), PASSWORD, 'invalid_name'), (('dee', 'x' * 129), PASSWORD, 'invalid_name'),
                 (('dee', 'Tab\there'), PASSWORD, 'invalid_name'), (('dee', 'X'), '', 'invalid_password'),
                 (('dee', 'X'), 'пароль', 'invalid_password'), (('dee', 'X'), 'a\tb', 'invalid_password'),
                 (('dee', 'X'), 'a' * 1025, 'invalid_password'), (('bo', 'X'), PASSWORD, 'login_taken'),
                 (('daemon', 'X'), PASSWORD, 'login_taken')]
        self.system.groups.add('wheel')
        cases.append((('wheel', 'X'), PASSWORD, 'login_taken'))
        for args, password, reason in cases:
            with self.subTest(args=args, reason=reason):
                code, reply = self.run_command('add', *args, '--administrator', 'no', stdin=password + '\n')
                self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', reason))
        self.assertEqual(self.accounts.calls, [])

    def test_add_takes_back_the_account_when_the_password_is_refused(self):
        self.accounts.deny.add('set_password')
        code, reply = self.run_command('add', 'dee', '', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', 'authorization_refused'))
        self.assertNotIn('dee', self.logins(reply))
        self.assertEqual(self.accounts.calls[-1], ('delete', 1003, False))

    def test_failed_add_preserves_a_preexisting_home_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'dee'
            home.mkdir()
            sentinel = home / 'private'
            sentinel.write_text('retained data')
            original = self.accounts.delete
            def delete(uid, remove_files):
                if remove_files:
                    shutil.rmtree(home)
                original(uid, remove_files)
            self.accounts.delete = delete
            self.accounts.deny.add('set_password')
            code, reply = self.run_command('add', 'dee', 'Dee', '--administrator', 'no', stdin=PASSWORD)
            self.assertEqual((code, reply['status']), (1, 'rejected'))
            self.assertEqual(sentinel.read_text(), 'retained data')
            self.assertEqual(self.accounts.calls[-1], ('delete', 1003, False))

    def test_failed_add_secures_a_reused_home_before_rollback(self):
        self.system.homes.add('/home/dee')
        self.accounts.deny.add('set_password')
        original = self.accounts.delete
        def delete(uid, remove_files):
            self.assertEqual(self.system.retained, [('/home/dee', uid)])
            self.assertFalse(remove_files)
            original(uid, remove_files)
        self.accounts.delete = delete
        code, reply = self.run_command('add', 'dee', 'Dee', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'change_unconfirmed'))
        self.assertNotIn('dee', self.logins(reply))

    def test_rollback_never_deletes_a_replacement_account(self):
        def replaced(_path, _hashed):
            del self.accounts.rows[1003]
            self.accounts.rows[1010] = account(1010, 'dee')
            raise users.Refusal('accounts_service_failed', sent=True)
        self.accounts.set_password = replaced
        code, reply = self.run_command('add', 'dee', 'Dee', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        self.assertIn(1010, self.accounts.rows)
        self.assertFalse(any(call[0] == 'delete' for call in self.accounts.calls))

    def test_rollback_stops_when_home_protection_fails(self):
        self.accounts.deny.add('set_password')
        with mock.patch.object(self.system, 'retain_home', side_effect=users.Refusal('home_retention_failed')):
            code, reply = self.run_command('add', 'dee', 'Dee', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        self.assertIn(1003, self.accounts.rows)
        self.assertFalse(any(call[0] == 'delete' for call in self.accounts.calls))

    def test_add_without_password_and_without_cleanup_is_uncertain(self):
        self.accounts.deny.update({'set_password', 'delete'})
        code, reply = self.run_command('add', 'dee', '', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'account_without_password'))
        self.assertIn('dee', self.logins(reply))

    def test_add_refused_authorization_changes_nothing(self):
        self.accounts.deny.add('create')
        code, reply = self.run_command('add', 'dee', '', '--administrator', 'yes', stdin=PASSWORD)
        self.assertEqual((code, reply['reason']), (1, 'authorization_refused'))
        self.assertNotIn(1003, self.accounts.rows)

    def test_remove_keeps_or_deletes_the_home_folder_as_asked(self):
        self.system.homes.add('/home/bo')
        code, reply = self.run_command('remove', 'bo', '--home', 'keep')
        self.assertEqual((code, reply['status']), (0, 'applied'))
        self.assertEqual(self.accounts.calls, [('delete', 1001, False)])
        self.assertEqual(self.system.retained, [('/home/bo', 1001)])
        self.accounts.rows[1001] = account(1001, 'bo')
        code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual(self.accounts.calls[-1], ('delete', 1001, True))
        self.assertNotIn('bo', self.logins(reply))

    def test_retention_happens_before_the_uid_can_be_reused(self):
        self.system.homes.add('/home/bo')
        original = self.accounts.delete
        def deleting(uid, files):
            self.assertEqual(self.system.retained, [('/home/bo', 1001)])
            self.assertNotIn('/home/bo', self.system.homes)
            original(uid, files)
        self.accounts.delete = deleting
        code, reply = self.run_command('remove', 'bo', '--home', 'keep')
        self.assertEqual((code, reply['status']), (0, 'applied'))

    def test_failed_retention_never_deletes_the_account(self):
        with mock.patch.object(self.system, 'retain_home', side_effect=users.Refusal('home_retention_failed', sent=True)):
            code, reply = self.run_command('remove', 'bo', '--home', 'keep')
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        self.assertEqual(self.accounts.calls, [])

    def test_failed_deletion_after_retention_is_uncertain(self):
        self.system.homes.add('/home/bo')
        self.accounts.deny.add('delete')
        code, reply = self.run_command('remove', 'bo', '--home', 'keep')
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        self.assertIn('bo', self.logins(reply))

    def test_partial_delete_failure_is_not_applied_and_leftovers_are_secured(self):
        self.system.homes.add('/home/bo')
        def partial(uid, _remove_files):
            del self.accounts.rows[uid]
            raise users.Refusal('accounts_service_failed', sent=True)
        self.accounts.delete = partial
        code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'home_removal_failed'))
        self.assertEqual(self.system.retained, [('/home/bo', 1001)])
        self.assertNotIn('bo', self.logins(reply))

    def test_delete_requires_home_absence_even_after_a_success_reply(self):
        self.system.homes.add('/home/bo')
        code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual((code, reply['status']), (3, 'uncertain'))

    def test_delete_cannot_confirm_an_unreadable_home_filesystem(self):
        with mock.patch.object(self.system, 'home_exists', side_effect=users.Refusal('home_removal_failed', sent=True)):
            code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'home_removal_failed'))

    def test_late_delete_failure_with_remaining_account_is_uncertain(self):
        self.accounts.fail.add('delete')
        code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        self.assertIn('bo', self.logins(reply))

    def test_lost_delete_reply_is_applied_only_when_account_and_home_are_gone(self):
        original = self.accounts.delete
        def deleted(uid, files):
            original(uid, files)
            raise users.Refusal('accounts_service_failed', sent=True)
        self.accounts.delete = deleted
        code, reply = self.run_command('remove', 'bo', '--home', 'delete')
        self.assertEqual((code, reply['status']), (0, 'applied'))

    def test_home_absence_check_does_not_hide_io_errors(self):
        system = users.System()
        for failure in (PermissionError(), OSError('filesystem error')):
            with mock.patch.object(users.os, 'lstat', side_effect=failure):
                with self.assertRaises(users.Refusal):
                    system.home_exists('/home/test')
        with mock.patch.object(users.os, 'lstat', side_effect=FileNotFoundError()):
            self.assertFalse(system.home_exists('/home/test'))

    def test_remove_never_takes_the_current_or_a_signed_in_account(self):
        code, reply = self.run_command('remove', 'ada', '--home', 'keep')
        self.assertEqual(reply['reason'], 'current_user')
        self.accounts.sessions_set.add(1001)
        code, reply = self.run_command('remove', 'bo', '--home', 'keep', uid=1002)
        self.assertEqual((code, reply['reason']), (1, 'user_signed_in'))
        self.assertEqual(self.accounts.calls, [])

    def test_the_last_usable_administrator_stays(self):
        self.accounts.rows[1002]['Locked'] = True
        code, reply = self.run_command('set-administrator', 'ada', 'no', uid=1001)
        self.assertEqual((code, reply['reason']), (1, 'last_administrator'))
        self.accounts.sessions_set.clear()
        code, reply = self.run_command('remove', 'ada', '--home', 'delete', uid=1001)
        self.assertEqual((code, reply['reason']), (1, 'last_administrator'))
        # A locked administrator may lose the role: a usable one remains.
        code, reply = self.run_command('set-administrator', 'cy', 'no', uid=1001)
        self.assertEqual((code, reply['status']), (0, 'applied'))
        self.assertEqual(self.accounts.calls, [('set_account_type', '/User1002', 0)])

    def test_administrator_role_changes_and_read_back(self):
        code, reply = self.run_command('set-administrator', 'bo', 'yes')
        self.assertEqual((code, reply['administrators']), (0, 3))
        code, reply = self.run_command('set-administrator', 'ada', 'no')
        self.assertEqual((code, reply['status']), (0, 'applied'))
        self.assertFalse(reply['users'][0]['administrator'])
        calls = len(self.accounts.calls)
        code, reply = self.run_command('set-administrator', 'ada', 'no')
        self.assertEqual((code, reply['status'], len(self.accounts.calls)), (0, 'applied', calls))

    def test_a_change_the_account_does_not_show_is_reported(self):
        # The service failed before it changed anything: the read-back shows the old value.
        self.accounts.fail.add('set_account_type')
        code, reply = self.run_command('set-administrator', 'bo', 'yes')
        self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', 'accounts_service_failed'))
        # The answer was lost after the change: the read-back shows it.
        self.accounts.fail.clear()
        self.accounts.late.add('set_account_type')
        code, reply = self.run_command('set-administrator', 'bo', 'yes')
        self.assertEqual((code, reply['status']), (0, 'applied'))
        # Neither the old nor the requested value: another writer.
        self.accounts.late.add('set_name')
        self.accounts.other.add('set_name')
        code, reply = self.run_command('set-name', 'bo', 'Bo')
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'change_unconfirmed'))
        self.assertEqual(reply['users'][1]['name'], 'Another writer')

    def test_set_name_and_password(self):
        code, reply = self.run_command('set-name', 'bo', 'Bo Person')
        self.assertEqual((code, reply['users'][1]['name']), (0, 'Bo Person'))
        code, reply = self.run_command('set-name', 'bo', 'Bad:Name')
        self.assertEqual(reply['reason'], 'invalid_name')
        code, reply = self.run_command('set-password', 'bo', stdin='new password\n')
        self.assertEqual((code, reply['status']), (0, 'applied'))
        hashed = self.accounts.calls[-1][2]
        self.assertTrue(verify('new password', hashed))
        self.accounts.deny.add('set_password')
        code, reply = self.run_command('set-password', 'bo', stdin='new password\n')
        self.assertEqual((code, reply['reason']), (1, 'authorization_refused'))
        self.accounts.deny.clear()
        self.accounts.fail.add('set_password')
        code, reply = self.run_command('set-password', 'bo', stdin='new password\n')
        self.assertEqual((code, reply['status']), (3, 'uncertain'))
        code, reply = self.run_command('set-password', 'bo', stdin='x' * 1100 + '\n')
        self.assertEqual(reply['reason'], 'invalid_password')

    def test_only_listed_accounts_can_be_changed(self):
        for argv in (('set-name', 'daemon', 'X'), ('set-administrator', 'svc', 'yes'), ('remove', 'root', '--home', 'keep'),
                     ('set-password', 'nobody')):
            with self.subTest(argv=argv):
                code, reply = self.run_command(*argv, stdin=PASSWORD)
                self.assertEqual((code, reply['reason']), (1, 'user_not_found'))
        code, reply = self.run_command('set-name', '../x', 'X')
        self.assertEqual(reply['reason'], 'invalid_login')
        self.assertEqual(self.accounts.calls, [])

    def test_usage(self):
        for argv in ((), ('status', 'extra'), ('add', 'dee', 'X'), ('remove', 'bo'), ('remove', 'bo', '--home', 'maybe'),
                     ('set-administrator', 'bo', 'true'), ('set-password', 'bo', PASSWORD), ('unknown',)):
            with self.subTest(argv=argv):
                code, reply = self.run_command(*argv)
                self.assertEqual((code, reply), (2, {'schema_version': 1, 'status': 'rejected', 'reason': 'invalid_request'}))

    def test_one_change_at_a_time(self):
        self.lock_patch.stop()
        held = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            held.bind(users.LOCK)
        except OSError:
            held.close()
            self.skipTest('unix sockets are unavailable')
        try:
            saved = users.LOCK_WAIT
            users.LOCK_WAIT = 0.2
            code, reply = self.run_command('set-name', 'bo', 'Bo')
            self.assertEqual((code, reply['reason']), (1, 'users_busy'))
            code, reply = self.run_command('status')
            self.assertEqual(code, 0)
        finally:
            users.LOCK_WAIT = saved
            held.close()
        self.assertEqual(self.accounts.calls, [])

    def test_hash_uses_the_system_default_method(self):
        hashed = users.crypt_password(PASSWORD)
        self.assertTrue(verify(PASSWORD, hashed))
        self.assertNotEqual(hashed, users.crypt_password(PASSWORD))

    def test_flag_shaped_names_stay_positional(self):
        for name in ('--json', '--terminal', '--password-prompt'):
            code, reply = self.run_command('set-name', 'bo', name)
            self.assertEqual((code, reply['users'][1]['name']), (0, name))
            with mock.patch.object(users, 'through_terminal', return_value=({'status': 'read'}, 0)) as terminal:
                code, _reply = self.run_command('add', 'dee', name, '--administrator', 'no', '--terminal')
                self.assertEqual(code, 0)
                self.assertEqual(terminal.call_args.args[0], ['add', 'dee', name, '--administrator', 'no'])
        code, reply = self.run_command('add', 'dee', '--terminal', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual((code, reply['users'][-1]['name']), (0, '--terminal'))

    def test_password_prompt_refuses_echo_fallback_before_reading(self):
        stream = mock.Mock()
        stream.isatty.return_value = True
        def fallback(_prompt):
            warnings.warn('Cannot control echo on the terminal.', users.getpass.GetPassWarning)
            stream.readline()
            return PASSWORD
        with mock.patch.object(users.getpass, 'getpass', side_effect=fallback):
            self.assertIsNone(users.ask('Password: ', stream, io.StringIO()))
        stream.readline.assert_not_called()

    def test_uid_range_comes_from_login_defs(self):
        with tempfile.TemporaryDirectory() as temporary:
            defs = Path(temporary) / 'login.defs'
            defs.write_text('# comment\nUID_MIN   2000\nUID_MAX 3000 # end\n')
            passwd = Path(temporary) / 'passwd'
            passwd.write_text('a:x:1500:1500::/home/a:/bin/bash\nb:x:2500:2500::/home/b:/bin/zsh\n'
                              'c:x:2600:2600::/home/c:/sbin/nologin\nbroken line\n')
            system = users.System(str(passwd), None, str(defs))
            self.assertEqual(system.candidates(), [(2500, 'b')])
            self.assertTrue(system.taken('a'))
            self.assertFalse(system.taken('z'))


class PrivateClient(unittest.TestCase):
    def test_wrapper_injects_private_services_and_isolates_home_operations(self):
        path = ROOT / 'tests/fixtures/users-client.py'
        spec = importlib.util.spec_from_file_location('users_client_fixture', path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        env = {'EMAKI_USERS_BUS_ADDRESS': 'unix:abstract=private-users-test',
               'EMAKI_USERS_PASSWD': '/fixture/passwd', 'EMAKI_USERS_GROUP': '/fixture/group',
               'EMAKI_USERS_LOGIN_DEFS': '/fixture/login.defs', 'EMAKI_USERS_TEST_UID': '1001'}
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, 'argv', [str(path), 'status']), \
                mock.patch.object(fixture.users, 'Accounts') as accounts, \
                mock.patch.object(fixture.users, 'main', return_value=0) as main:
            self.assertEqual(fixture.main(), 0)
        accounts.assert_called_once_with(env['EMAKI_USERS_BUS_ADDRESS'])
        self.assertEqual(Path(fixture.users.__file__), path)
        self.assertEqual(main.call_args.args, (['status'],))
        injected = main.call_args.kwargs
        self.assertIs(injected['accounts'], accounts.return_value)
        self.assertEqual(injected['uid'], 1001)
        self.assertEqual(injected['system'].passwd, env['EMAKI_USERS_PASSWD'])
        with mock.patch.object(os.path, 'lexists', side_effect=AssertionError('host home access')):
            self.assertFalse(injected['system'].home_exists('/home/cy'))
            self.assertIsNone(injected['system'].retain_home({'home': '/home/cy', 'uid': 1002}))


@unittest.skipUnless(shutil.which('dbus-daemon'), 'dbus-daemon is not installed')
class Bus(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='settings-users-')
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        # An abstract socket name: dbus-daemon refuses socket paths near the length limit, and a
        # checkout (or the export check's TMPDIR) can be that deep (GOTCHAS: socket path limit).
        name = 'emaki-users-test-' + os.urandom(8).hex()
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.bind('\0' + name + '-probe')
        except OSError:
            self.skipTest('unix sockets are unavailable')
        self.state, self.passwd, self.log = base / 'state.json', base / 'passwd', base / 'calls.log'
        self.group, self.defs = base / 'group', base / 'login.defs'
        self.group.write_text('wheel:x:998:ada\n')
        self.defs.write_text('UID_MIN 1000\nUID_MAX 60000\n')
        self.state.write_text(json.dumps({'users': {
            '1000': account(1000, 'ada', True, name='Ada'), '1001': account(1001, 'bo')}, 'sessions': [1000],
            'extra_passwd': ['daemon:x:2:2::/:/usr/bin/nologin']}))
        config = base / 'bus.conf'
        config.write_text(f'''<busconfig><type>session</type><listen>unix:abstract={name}</listen>
<auth>EXTERNAL</auth><policy context="default"><allow send_destination="*" eavesdrop="true"/>
<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>''')
        self.daemon = subprocess.Popen(['dbus-daemon', '--nofork', '--config-file', str(config)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop, self.daemon)
        self.address = f'unix:abstract={name}'
        deadline = time.monotonic() + 10
        while True:
            client = socket.socket(socket.AF_UNIX)
            try:
                client.connect('\0' + name)
                break
            except OSError:
                self.assertLess(time.monotonic(), deadline, 'private bus did not start')
                self.assertIsNone(self.daemon.poll(), 'private bus exited')
                time.sleep(0.05)
            finally:
                client.close()
        self.start_service()

    def start_service(self):
        ready = Path(self.temporary.name) / 'ready'
        ready.unlink(missing_ok=True)
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address)
        self.service = subprocess.Popen([sys.executable, '-B', '-W', 'ignore::DeprecationWarning',
                                         str(ROOT / 'tests/fixtures/fake-accounts-service.py'),
                                         str(self.state), str(self.passwd), str(self.log), str(ready)], env=env)
        self.addCleanup(self.stop, self.service)
        deadline = time.monotonic() + 10
        while not ready.exists():
            self.assertLess(time.monotonic(), deadline, 'fake service did not start')
            self.assertIsNone(self.service.poll())
            time.sleep(0.05)

    @staticmethod
    def stop(process):
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def program(self, *argv, stdin='', uid=None, typed=None):
        """Run the program; `typed` is what the person types in the fake managed terminal."""
        base = Path(self.temporary.name)
        terminal = base / 'bin/emaki-terminal'
        terminal.parent.mkdir(exist_ok=True)
        terminal.write_text(FAKE_TERMINAL)
        terminal.chmod(0o755)
        (base / 'typed').write_text(typed if typed is not None else '')
        env = dict(os.environ, EMAKI_USERS_BUS_ADDRESS=self.address, EMAKI_USERS_PASSWD=str(self.passwd),
                   EMAKI_USERS_GROUP=str(self.group), EMAKI_USERS_LOGIN_DEFS=str(self.defs),
                   EMAKI_USERS_TEST_UID=str(os.getuid() if uid is None else uid),
                   FAKE_TERMINAL_DIR=str(base), FAKE_TERMINAL_OPEN='1' if typed is not None else '0',
                   PATH=str(terminal.parent) + ':' + os.environ['PATH'])
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/fixtures/users-client.py'),
                                 *argv, '--json'], input=stdin, text=True,
                                capture_output=True, env=env, timeout=30)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def window(self):
        path = Path(self.temporary.name) / 'window'
        return path.read_text() if path.exists() else None

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def mutations(self):
        return [call for call in self.calls() if call['method'] not in ('FindUserById', 'GetAll', 'ListUsers')]

    def test_status_over_the_bus(self):
        code, reply = self.program('status')
        self.assertEqual(code, 0, reply)
        self.assertEqual([row['login'] for row in reply['users']], ['ada', 'bo'])
        self.assertEqual(reply['users'][0]['signed_in'], True)
        self.assertEqual(reply['users'][0]['current'], os.getuid() == 1000)
        self.assertTrue(all(not call['interactive'] for call in self.calls()))

    def test_changes_ask_for_authentication_and_send_only_a_hash(self):
        code, reply = self.program('add', 'cy', 'Cy Person', '--administrator', 'yes', stdin=PASSWORD + '\n')
        self.assertEqual((code, reply['status']), (0, 'applied'), reply)
        self.assertIn('cy', [row['login'] for row in reply['users']])
        self.assertEqual(reply['administrators'], 2)
        self.assertNotIn(PASSWORD, self.log.read_text())
        state = json.loads(self.state.read_text())
        self.assertTrue(verify(PASSWORD, state['passwords']['cy']))
        code, reply = self.program('set-name', 'bo', 'Bo Person')
        self.assertEqual(code, 0, reply)
        code, reply = self.program('set-administrator', 'bo', 'yes')
        self.assertEqual(code, 0, reply)
        code, reply = self.program('remove', 'cy', '--home', 'delete')
        self.assertEqual(code, 0, reply)
        self.assertEqual(json.loads(self.state.read_text())['deleted'], [{'uid': 1002, 'remove_files': True}])
        methods = [call['method'] for call in self.mutations()]
        self.assertEqual(methods, ['CreateUser', 'SetPassword', 'SetRealName', 'SetAccountType', 'DeleteUser'])
        self.assertTrue(all(call['interactive'] for call in self.mutations()))

    def test_service_refusals_map_to_fixed_reasons(self):
        data = json.loads(self.state.read_text())
        data['deny'] = ['SetRealName']
        data['fail'] = ['SetAccountType']
        self.state.write_text(json.dumps(data))
        self.stop(self.service)
        self.start_service()
        code, reply = self.program('set-name', 'bo', 'Bo')
        self.assertEqual((code, reply['reason']), (1, 'authorization_refused'))
        code, reply = self.program('set-administrator', 'bo', 'yes')
        self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', 'accounts_service_failed'))
        self.assertNotIn('private detail', json.dumps(reply))
        code, reply = self.program('add', 'ada', '', '--administrator', 'no', stdin=PASSWORD)
        self.assertEqual(reply['reason'], 'login_taken')

    def test_settings_asks_for_passwords_in_the_terminal(self):
        code, reply = self.program('add', 'cy', 'Cy Person', '--administrator', 'no', '--terminal',
                                   typed=PASSWORD + '\n' + PASSWORD + '\n')
        self.assertEqual((code, reply['status'], reply['action']), (0, 'applied', 'add'), reply)
        self.assertIn('cy', [row['login'] for row in reply['users']])
        self.assertTrue(verify(PASSWORD, json.loads(self.state.read_text())['passwords']['cy']))
        self.assertIn('New password for cy: ', self.window())
        self.assertIn('The account is ready.', self.window())
        self.assertNotIn(PASSWORD, self.window() + self.log.read_text())
        code, reply = self.program('set-password', 'bo', '--terminal', typed='first try\nsecond\nfinal one\nfinal one\n')
        self.assertEqual((code, reply['status']), (0, 'applied'), reply)
        self.assertIn('The passwords do not match.', self.window())
        self.assertIn('Password changed.', self.window())
        self.assertTrue(verify('final one', json.loads(self.state.read_text())['passwords']['bo']))

    def test_terminal_refusals_and_a_window_without_answer(self):
        code, reply = self.program('add', 'bo', '', '--administrator', 'no', '--terminal', typed=PASSWORD + '\n')
        self.assertEqual((code, reply['reason']), (1, 'login_taken'))
        self.assertIsNone(self.window(), 'a refusal known in advance opens no window')
        code, reply = self.program('set-password', 'bo', '--terminal', typed='')
        self.assertEqual((code, reply['status'], reply['reason']), (1, 'rejected', 'password_not_entered'))
        self.assertIn('Password not changed.', self.window())
        code, reply = self.program('set-password', 'bo', '--terminal', typed='пароль\n\nx\x07\n')
        self.assertEqual(reply['reason'], 'password_not_entered')
        self.assertIn('English (US) keyboard', self.window())
        code, reply = self.program('set-password', 'bo', '--terminal')
        self.assertEqual((code, reply['status'], reply['reason']), (3, 'uncertain', 'password_window_unconfirmed'))
        self.assertEqual([row['login'] for row in reply['users']], ['ada', 'bo'])
        code, reply = self.program('remove', 'bo', '--home', 'keep', '--terminal')
        self.assertEqual(code, 2)
        code, reply = self.program('--password-prompt', '/tmp/x', 'remove', 'bo', '--home', 'keep')
        self.assertEqual(code, 2)
        self.assertEqual([call['method'] for call in self.mutations()], [])

    def test_no_service_is_a_refusal(self):
        self.stop(self.service)
        code, reply = self.program('status')
        self.assertEqual((code, reply['reason']), (1, 'accounts_service_unavailable'))


if __name__ == '__main__':
    unittest.main()
