#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline target propagation through greeter and startup helpers."""
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent / 'vm'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('target_greeter_host', 'check-greeter.py')
guest = load('target_greeter_guest', 'guest-greeter.py')
startup = load('target_startup_guest', 'guest-session-start.py')


class FakeTransport:
    user = 'acceptance'
    password = 'fixture-secret'

    def __init__(self):
        self.calls = []

    def remote(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')


class TargetPropagation(unittest.TestCase):
    def test_privileged_helpers_use_target_account_and_stdin(self):
        target = FakeTransport()
        with patch.object(host, 'TARGET', target):
            host.guest('select-emaki')
            host.keys('text', 'key:Return', text=target.password)
        command, options = target.calls[0]
        self.assertEqual(json.loads(shlex.split(command)[-1])['user'], 'acceptance')
        self.assertTrue(options['privileged'])
        command, options = target.calls[1]
        self.assertNotIn(target.password, command)
        self.assertEqual(options['data'], target.password)
        self.assertTrue(options['privileged'])

    def test_pam_failure_is_bound_to_selected_user(self):
        with patch.object(host, 'TARGET', FakeTransport()), patch.object(host, 'journal_since') as journal:
            journal.return_value = 'pam_unix(emaki-greetd:auth): authentication failure; user=arch'
            self.assertIsNone(host.authentication_failed('mark'))
            journal.return_value = journal.return_value.replace('user=arch', 'user=acceptance')
            self.assertEqual(host.authentication_failed('mark'), journal.return_value)

    def test_successful_pam_session_requires_exact_account(self):
        prefix = 'pam_unix(emaki-greetd:session): session opened for user '
        with patch.object(host, 'TARGET', FakeTransport()):
            for suffix in ('', ' by (uid=0)', '(uid=2034) by (uid=0)'):
                self.assertTrue(host.pam_session_opened(prefix + 'acceptance' + suffix))
            for username in ('acceptance2', 'acceptance-other', 'arch'):
                self.assertFalse(host.pam_session_opened(prefix + username + '(uid=2034)'))

    def test_fallback_screenshots_satisfy_frame_capture(self):
        def capture(argv, **kwargs):
            Path(argv[-1]).write_bytes(b'frame evidence')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(host, 'OUT', Path(directory)), \
                patch.object(host, 'CAPTURE_NOTES', []), \
                patch.object(host.socket, 'socket', side_effect=OSError('no monitor')), \
                patch.object(host.subprocess, 'run', side_effect=capture):
            host.frames('fallback', lambda: None, after=.08)
            sources = json.loads((Path(directory) / 'fallback-sources.json').read_text())
            self.assertTrue(sources)
            self.assertTrue(all(row['source'] == 'host-display-fallback' for row in sources))
            self.assertTrue(list(Path(directory).glob('fallback-*.png')))

    def test_guest_dispatch_uses_selected_account(self):
        with patch.object(guest, 'niri_action', return_value={'ok': True}) as action:
            guest.dispatch({'op': 'logout', 'user': 'acceptance'})
        action.assert_called_once_with('acceptance')
        with self.assertRaises(ValueError):
            guest.dispatch({'op': 'logout', 'user': '../root'})

    def test_wallpaper_child_uses_target_uid_home_and_account(self):
        account = SimpleNamespace(pw_uid=2034, pw_gid=2035, pw_dir='/home/acceptance')
        with patch.object(startup.pwd, 'getpwnam', return_value=account) as lookup, \
                patch.object(startup.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as run:
            startup.fixture_as_user('prepare', 'a' * 32, 'acceptance')
        lookup.assert_called_once_with('acceptance')
        self.assertEqual(run.call_args.args[0][-2:], ['--user', 'acceptance'])
        self.assertEqual(run.call_args.kwargs['user'], 2034)
        self.assertEqual(run.call_args.kwargs['env']['HOME'], '/home/acceptance')


if __name__ == '__main__':
    unittest.main()
