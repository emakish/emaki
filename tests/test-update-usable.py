#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Boot acceptance requires live, local sessions and actual greeter programs."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'grub'))
from emaki_boot import usable

BOOT = '12345678-1234-5678-9abc-123456789abc'


class Observation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)
        self.put('sys/kernel/random/boot_id', BOOT)
        self.patch = patch.object(usable, 'PROC', self.proc)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.diagnostics = io.StringIO()
        stderr = patch.object(usable.sys, 'stderr', self.diagnostics)
        stderr.start()
        self.addCleanup(stderr.stop)
        usable._last_rejection = None
        self.service = dict(ActiveState='active', SubState='running', MainPID='10', InvocationID='abc')
        self.session = dict(Id='c1', Active='yes', State='active', Remote='no', Class='greeter',
                            Type='tty', User='975', Name='greeter', Leader='11', Scope='session-c1.scope')
        self.procfile(10, 'greetd', uid='0', scope='greetd.service')
        # greetd keeps the PAM worker privileged, unlike the compositor/UI.
        self.procfile(11, 'greetd', uid='0')
        self.procfile(12, 'niri-emaki')
        self.procfile(13, 'quickshell', ['qs', '-p', usable.GREETER])
        self.runner = patch.object(usable.subprocess, 'run', side_effect=self.run_command)
        self.run_mock = self.runner.start()
        self.addCleanup(self.runner.stop)

    def put(self, name, text):
        path = self.proc / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def procfile(self, pid, executable, args=None, uid='975', scope='session-c1.scope', start='123'):
        self.put(f'{pid}/stat', f'{pid} ({executable}) S ' + '0 ' * 18 + start)
        self.put(f'{pid}/status', 'Uid:\t' + '\t'.join([uid] * 4) + '\n')
        self.put(f'{pid}/cgroup', '0::/user.slice/' + scope + '\n')
        self.put(f'{pid}/cmdline', '\0'.join(args or [executable]) + '\0')
        (self.proc / str(pid) / 'exe').symlink_to('/usr/bin/' + executable)

    def run_command(self, args, **kwargs):
        self.assertGreater(kwargs['timeout'], 0)
        self.assertLessEqual(kwargs['timeout'], 1)
        if args[0].endswith('systemctl'):
            self.assertEqual(args[:3], ('/usr/bin/systemctl', 'show', 'greetd.service'))
            data = self.service
            requested = [name for arg in args[3:] for name in arg.removeprefix('--property=').split(',')]
        elif args[1] == 'list-sessions':
            self.assertEqual(args, ('/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager'))
            return SimpleNamespace(stdout='c1 975 greeter seat0 tty1\n')
        else:
            self.assertEqual(args[:3], ('/usr/bin/loginctl', 'show-session', 'c1'))
            self.assertTrue(all(arg.startswith('--property=') for arg in args[3:]))
            data = self.session
            # loginctl 262 treats each property option as one exact name.
            requested = [arg.removeprefix('--property=') for arg in args[3:]]
        return SimpleNamespace(stdout='\n'.join(key + '=' + value for key, value in data.items()
                                                if key in requested))

    def test_loginctl_fake_uses_exact_property_names(self):
        for names, expected in [(('--property=Id', '--property=Active'), 'Id=c1\nActive=yes'),
                                (('--property=Id,Active',), ''),
                                (('--property=Nonexistent',), '')]:
            with self.subTest(names=names):
                result = self.run_command(('/usr/bin/loginctl', 'show-session', 'c1', *names), timeout=1)
                self.assertEqual(result.stdout, expected)

    def test_rejection_diagnostics_repeat_only_after_change_or_success(self):
        with patch.dict(self.session, {'Active': 'no'}):
            self.assertIsNone(usable.observe())
            self.assertIsNone(usable.observe())
        self.assertEqual(self.diagnostics.getvalue().count('Boot observation not usable:'), 1)
        self.assertIn('session properties', self.diagnostics.getvalue())
        self.assertIsNotNone(usable.observe())
        with patch.dict(self.session, {'Active': 'no'}):
            self.assertIsNone(usable.observe())
        self.assertEqual(self.diagnostics.getvalue().count('Boot observation not usable:'), 2)
        (self.proc / '13/exe').unlink()
        self.assertIsNone(usable.observe())
        self.assertIn('greeter compositor or interface is missing', self.diagnostics.getvalue())

    def test_query_timeout_is_diagnosed(self):
        self.run_mock.side_effect = subprocess.TimeoutExpired('loginctl', 1)
        self.assertIsNone(usable.observe())
        self.assertIn('query timed out', self.diagnostics.getvalue())

    def test_greeter_requires_live_programs_with_session_scope(self):
        self.assertIn(':greeter:', usable.observe())
        self.put('13/cgroup', '0::/user.slice/session-c2.scope\n')
        self.assertIsNone(usable.observe())

    def test_active_service_without_pam_session_is_not_accepted(self):
        self.session.clear()
        self.assertIsNone(usable.observe())

    def test_start_limit_and_restart_are_not_accepted(self):
        for key, value in [('ActiveState', 'failed'), ('SubState', 'auto-restart'), ('MainPID', '999')]:
            with self.subTest(key=key), patch.dict(self.service, {key: value}):
                self.assertIsNone(usable.observe())

    def test_compositor_without_ui_does_not_accept(self):
        (self.proc / '13/exe').unlink()
        self.assertIsNone(usable.observe())

    def test_ui_without_compositor_does_not_accept(self):
        (self.proc / '12/exe').unlink()
        self.assertIsNone(usable.observe())

    def test_wrong_qml_or_uid_cannot_supply_evidence(self):
        self.put('13/cmdline', 'qs\0-p\0/tmp/greeter.qml\0')
        self.assertIsNone(usable.observe())
        self.put('13/cmdline', 'qs\0-p\0' + usable.GREETER + '\0')
        self.put('13/status', 'Uid:\t1000\t1000\t1000\t1000\n')
        self.assertIsNone(usable.observe())

    def test_regreet_recovery_ui_accepts(self):
        (self.proc / '13/exe').unlink()
        (self.proc / '13/exe').symlink_to('/usr/bin/regreet')
        self.assertIn(':greeter:', usable.observe())

    def test_graphical_user_session_does_not_require_greetd(self):
        self.service['ActiveState'] = 'failed'
        self.session.update(Class='user', Name='person')
        for kind in ('wayland', 'x11'):
            self.session['Type'] = kind
            self.assertIn(':user:', usable.observe())
        self.session['Type'] = 'tty'
        self.assertIsNone(usable.observe())

    def test_wrong_or_inactive_session_is_rejected(self):
        for key, value in [('Id', 'c2'), ('Scope', 'session-c2.scope'), ('Active', 'no'),
                           ('State', 'closing'), ('Remote', 'yes'), ('User', ''),
                           ('Class', 'manager'), ('Name', 'person'), ('Leader', '999')]:
            with self.subTest(key=key), patch.dict(self.session, {key: value}):
                self.assertIsNone(usable.observe())

    def test_dead_ui_and_reused_pid_change_evidence(self):
        before = usable.observe()
        self.put('13/stat', '13 (quickshell) S ' + '0 ' * 18 + '456')
        self.assertNotEqual(before, usable.observe())
        self.put('13/stat', '13 (quickshell) Z ' + '0 ' * 18 + '456')
        self.assertIsNone(usable.observe())

    def test_boot_changed_during_observation_is_rejected(self):
        run = self.run_command
        def changed(args, **kwargs):
            result = run(args, **kwargs)
            self.put('sys/kernel/random/boot_id', 'aaaaaaaa-1234-5678-9abc-123456789abc')
            return result
        self.run_mock.side_effect = changed
        self.assertIsNone(usable.observe())

    def test_shared_deadline_expires_before_commands(self):
        with patch.object(usable.time, 'monotonic', side_effect=[0, 6]):
            self.assertIsNone(usable.observe())
        self.run_mock.assert_not_called()

    def test_timeout_missing_files_or_malformed_boot_fail_closed(self):
        self.run_mock.side_effect = subprocess.TimeoutExpired('loginctl', 1)
        self.assertIsNone(usable.observe())
        self.run_mock.side_effect = self.run_command
        self.put('sys/kernel/random/boot_id', 'bad')
        self.assertIsNone(usable.observe())
        (self.proc / 'sys/kernel/random/boot_id').unlink()
        self.assertIsNone(usable.observe())


class DecisionMutants(unittest.TestCase):
    def test_observation_guards_are_exercised(self):
        original = globals()['usable']
        source = Path(original.__file__).read_text()
        mutations = (
            ("*('--property=' + name for name in (", "*('--property=Nonexistent' for name in ("),
            ("""*('--property=' + name for name in (
                                   'Id', 'Active', 'State', 'Remote', 'Class', 'Type',
                                   'User', 'Name', 'Leader', 'Scope'))""",
             "'--property=Id,Active,State,Remote,Class,Type,User,Name,Leader,Scope'"),
            ("fields[0] in ('Z', 'X')", "False"),
            ("any(value != str(uid) for value in owners)", "False"),
            ("scope in group.split('/')", "True"),
            ("if compositor and interface:", "if compositor:"),
            ("if compositor and interface:", "if interface:"),
            ("['-p', GREETER]", "['-p', '/tmp/greeter.qml']"),
            ("if remaining <= 0:", "if False:"),
            ("service.get('ActiveState') == 'active'", "True"),
            ("service.get('SubState') == 'running'", "True"),
            ("and daemon is not None", "and True"),
            ("state.get('Id') != session", "False"),
            ("state.get('Active') != 'yes'", "False"),
            ("state.get('State') != 'active'", "False"),
            ("state.get('Remote') != 'no'", "False"),
            ("state.get('Scope') != scope", "False"),
            ("if leader is None:", "if False:"),
            ("state.get('Type') in ('wayland', 'x11')", "True"),
            ("state.get('Class') == 'greeter'", "True"),
            ("state.get('Name') == 'greeter'", "True"),
            ("+ ':' + fields[19]", "+ ':fixed'"),
            (".strip() == boot", ".strip() != ''"),
        )
        try:
            for old, new in mutations:
                with self.subTest(decision=old):
                    self.assertIn(old, source)
                    mutant = ModuleType('usable_mutant')
                    exec(compile(source.replace(old, new, 1), original.__file__, 'exec'), mutant.__dict__)
                    globals()['usable'] = mutant
                    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Observation)
                    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
                    self.assertFalse(result.wasSuccessful(), 'Decision mutant survived')
        finally:
            globals()['usable'] = original


if __name__ == '__main__':
    unittest.main()
