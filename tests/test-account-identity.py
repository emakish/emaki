#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account names and roles use the system identity, without personal data fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'shell/helpers/system-tools.py'
spec = importlib.util.spec_from_file_location('account_system', HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class AccountIdentity(unittest.TestCase):
    def identity(self, gecos, primary=1000, members=(), missing=False):
        user = SimpleNamespace(pw_name='sample', pw_gecos=gecos, pw_gid=primary)
        wheel = SimpleNamespace(gr_gid=10, gr_mem=list(members))
        with patch.object(helper.os, 'getuid', return_value=1234), \
                patch.object(helper.pwd, 'getpwuid', return_value=user) as lookup, \
                patch.object(helper.grp, 'getgrnam', return_value=wheel,
                             side_effect=KeyError if missing else None):
            result = helper.operation({'op': 'account-read'})
        lookup.assert_called_once_with(1234)
        return result

    def test_gecos_name_omits_contact_fields(self):
        result = self.identity('  Sample Person ,Room,Telephone')
        self.assertEqual(result, dict(state='ready', name='Sample Person',
                                     login='sample', administrator=False))

    def test_empty_gecos_falls_back_to_login(self):
        self.assertEqual(self.identity(',Room')['name'], 'sample')
        self.assertEqual(self.identity('   ')['name'], 'sample')

    def test_unicode_name_is_preserved(self):
        self.assertEqual(self.identity('Zoë García,Room')['name'], 'Zoë García')

    def test_primary_and_supplementary_wheel_membership(self):
        self.assertTrue(self.identity('', primary=10)['administrator'])
        self.assertTrue(self.identity('', members=['sample'])['administrator'])
        self.assertFalse(self.identity('', members=['samples'])['administrator'])

    def test_missing_wheel_is_an_ordinary_account(self):
        self.assertFalse(self.identity('', missing=True)['administrator'])

    def test_fixed_json_protocol_ignores_spoofed_environment_user(self):
        result = subprocess.run([sys.executable, '-B', str(HELPER)],
                                input='{"op":"account-read"}\n', text=True,
                                capture_output=True, check=True, timeout=5,
                                env=dict(os.environ, USER='different-account'))
        value = json.loads(result.stdout)
        user = helper.pwd.getpwuid(os.getuid())
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['login'], user.pw_name)
        self.assertEqual(value['name'], user.pw_gecos.split(',', 1)[0].strip() or user.pw_name)


if __name__ == '__main__':
    unittest.main()
