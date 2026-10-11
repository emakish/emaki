#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account authorization boundaries without changing any host account."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


rules = load('users_rules', ROOT / 'tests/test-settings-users.py')
users = rules.users
paths = types.SimpleNamespace(BINDIR='/packaged/bin', LIBEXECDIR='/packaged/libexec', TRUSTED_PATH='/trusted/bin')
with mock.patch.dict(sys.modules, emaki_paths=paths):
    helper = load('users_root', ROOT / 'scripts/emaki-settings-users-root')


class Authorization(unittest.TestCase):
    def setUp(self):
        self.accounts = rules.FakeAccounts([rules.account(1000, 'ada', True), rules.account(1001, 'bo')])
        self.system = rules.FakeSystem(self.accounts)
        self.client = users.Users(self.accounts, self.system, 1000, authorize=True)

    def test_own_name_keeps_stock_path_other_changes_always_authorize(self):
        with mock.patch.object(users, 'lock', return_value=mock.Mock()), \
                mock.patch.object(users, 'authorized_change', return_value=({}, 0)) as authorize:
            users.run(['set-name', 'ada', 'Ada'], self.client, io.StringIO())
            authorize.assert_not_called()
            for args in (['set-name', 'bo', 'Bo'], ['set-name', 'bo', 'Again'],
                         ['set-administrator', 'bo', 'yes'], ['remove', 'bo', '--home', 'keep'],
                         ['set-password', 'bo'], ['add', 'cy', 'Cy', '--administrator', 'no']):
                users.run(args, self.client, io.StringIO(rules.PASSWORD))
            self.assertEqual(authorize.call_count, 6)
        self.assertEqual(self.accounts.calls, [('set_name', '/User1000', 'Ada')])

    def test_every_invocation_uses_fixed_helper_and_only_stdin_contains_hash(self):
        reply = {'schema_version': 1, 'status': 'applied', 'action': 'set-password', 'login': 'bo'}
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(reply))
        with mock.patch.dict(sys.modules, emaki_paths=paths), \
                mock.patch.object(users.subprocess, 'run', return_value=result) as invoke:
            for _ in range(2):
                answer, code = users.authorized_change(['set-password', 'bo'], self.client, io.StringIO(rules.PASSWORD))
                self.assertEqual((answer, code), (reply, 0))
            self.assertEqual(invoke.call_count, 2)
            args, kwargs = invoke.call_args
            self.assertEqual(args[0], ['pkexec', '/packaged/libexec/emaki-settings-users-root', 'apply', 'set-password', 'bo'])
            self.assertNotIn(rules.PASSWORD, str(args) + str(kwargs))
            self.assertTrue(rules.verify(rules.PASSWORD, kwargs['input'].rstrip('\n')))
            self.assertNotIn('env', kwargs)

    def test_refusal_and_missing_receipt_never_claim_applied(self):
        for result, expected in ((types.SimpleNamespace(returncode=126, stdout=''), (1, 'rejected')),
                                 (types.SimpleNamespace(returncode=127, stdout=''), (1, 'rejected')),
                                 (types.SimpleNamespace(returncode=0, stdout=''), (3, 'uncertain'))):
            with mock.patch.dict(sys.modules, emaki_paths=paths), mock.patch.object(users.subprocess, 'run', return_value=result):
                reply, code = users.authorized_change(['set-name', 'bo', 'Bo'], self.client, io.StringIO())
            self.assertEqual((code, reply['status']), expected)

    def test_invalid_request_is_refused_before_an_authentication_prompt(self):
        for args, reason in ((['set-name', 'bo', 'Bad:Name'], 'invalid_name'),
                             (['set-name', 'missing', 'Name'], 'user_not_found'),
                             (['add', 'bo', 'Bo', '--administrator', 'no'], 'login_taken'),
                             (['add', 'BadLogin', 'Bo', '--administrator', 'no'], 'invalid_login')):
            with mock.patch.dict(sys.modules, emaki_paths=paths), mock.patch.object(users.subprocess, 'run') as invoke:
                with self.assertRaises(users.Refusal) as failure:
                    users.authorized_change(args, self.client, io.StringIO(rules.PASSWORD))
                self.assertEqual(failure.exception.reason, reason)
                invoke.assert_not_called()

    def test_policy_never_retains_authentication_and_binds_argv(self):
        policy = ET.parse(ROOT / 'polkit/org.emaki.settings-users.policy')
        action = policy.find("action[@id='org.emaki.settings-users']")
        self.assertIsNotNone(action)
        self.assertEqual([entry.text for entry in action.find('defaults')], ['auth_admin'] * 3)
        annotations = {entry.attrib['key']: entry.text for entry in action.findall('annotate')}
        self.assertEqual(annotations['org.freedesktop.policykit.exec.argv1'], 'apply')
        self.assertEqual(annotations['org.freedesktop.policykit.exec.path'], '@EMAKI_LIBEXECDIR@/emaki-settings-users-root')

    def invoke_root(self, argv, uid=0, caller='1000'):
        out = io.StringIO()
        env = {'PKEXEC_UID': caller, 'EMAKI_USERS_BUS_ADDRESS': 'untrusted', 'EMAKI_USERS_PASSWD': 'untrusted',
               'PATH': '/untrusted', 'PYTHONPATH': '/untrusted'}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(helper.os, 'geteuid', return_value=uid), \
                mock.patch.object(helper, 'backend', return_value=users), mock.patch('sys.stdout', out), \
                mock.patch.object(users, 'Accounts', return_value=self.accounts) as accounts, \
                mock.patch.object(users, 'System', return_value=self.system) as system, \
                mock.patch.object(users, 'lock', return_value=mock.Mock()):
            code = helper.main(argv)
            clean = dict(os.environ)
        return code, json.loads(out.getvalue()), clean, accounts, system

    def test_root_boundary_requires_root_and_pkexec_identity(self):
        for uid, caller in ((1000, '1000'), (0, ''), (0, '-1'), (0, '١')):
            code, reply, _env, accounts, _system = self.invoke_root(['apply', 'set-name', 'bo', 'Bo'], uid, caller)
            self.assertEqual((code, reply['reason']), (1, 'authorization_refused'))
            accounts.assert_not_called()

    def test_root_rejects_arbitrary_verbs_options_and_targets(self):
        for argv in ([], ['status'], ['apply', 'status'], ['other', 'set-name', 'bo', 'Bo'],
                     ['apply', 'set-name', 'bo', 'Bo', '--json'], ['apply', 'set-password', 'bo', '--terminal'],
                     ['apply', 'set-name', '../bo', 'Bo'], ['apply', 'add', 'cy', 'Bad:Name', '--administrator', 'no']):
            _code, reply, _env, accounts, _system = self.invoke_root(argv)
            self.assertEqual(reply['reason'], 'invalid_request', argv)
            accounts.assert_not_called()

    def test_root_uses_original_uid_and_ignores_untrusted_environment(self):
        code, reply, env, accounts, system = self.invoke_root(['apply', 'remove', 'ada', '--home', 'keep'])
        self.assertEqual((code, reply['reason']), (1, 'current_user'))
        self.assertEqual(env, {'PATH': paths.TRUSTED_PATH, 'LC_ALL': 'C'})
        accounts.assert_called_once_with()
        system.assert_called_once_with()
        self.assertEqual(self.accounts.calls, [])
        code, reply, _env, _accounts, _system = self.invoke_root(['apply', 'set-name', 'bo', '--terminal'])
        self.assertEqual((code, reply['users'][1]['name']), (0, '--terminal'))

    def test_root_accepts_hash_over_stdin_without_hashing_it_again(self):
        hashed = users.crypt_password(rules.PASSWORD)
        with mock.patch('sys.stdin', io.StringIO(hashed + '\n')):
            code, reply, _env, _accounts, _system = self.invoke_root(['apply', 'set-password', 'bo'])
        self.assertEqual((code, reply['status']), (0, 'applied'))
        self.assertEqual(self.accounts.calls[-1], ('set_password', '/User1001', hashed))
        with mock.patch('sys.stdin', io.StringIO(rules.PASSWORD)):
            code, reply, _env, _accounts, _system = self.invoke_root(['apply', 'set-password', 'bo'])
        self.assertEqual((code, reply['reason']), (1, 'invalid_password'))


if __name__ == '__main__':
    unittest.main()
