#!/usr/bin/env python3
"""Pure local VM-harness regressions. Never SSH, signal a VM or touch live services."""
import ast
import contextlib
import importlib.util
import json
import io
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests/vm' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guest = load('c9_guest', 'guest-greeter.py')
host = load('c9_host', 'check-greeter.py')


def state(**overrides):
    value = dict(qs=[12], regreet=[], qs_stopped=[], greeter_compositors=[11],
                 user_compositors=[], greetd='active', active_tty='tty1',
                 getty='inactive', tty2_text='', services={}, memory={})
    value.update(overrides)
    return value


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gv-', dir=CACHE)
        self.base = Path(self.work.name)
        host.OUT = self.base
        host.FAILURES.clear()

    def tearDown(self):
        self.work.cleanup()

    def quit_fixture(self, still_alive=False):
        compositor = dict(pid=11, start='123', argv=['niri'])
        sock = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=0o140000, st_uid=966))
        sock.__str__ = lambda: '/run/user/966/niri.fixture.sock'
        observed = [[compositor], [compositor]] if still_alive else [[compositor], []]
        with patch.object(guest.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=966)), \
                patch.object(guest, 'processes', side_effect=observed), \
                patch.object(guest.Path, 'glob', return_value=[sock]), \
                patch.object(guest, 'socket_identity', side_effect=[(1, 2), None]), \
                patch.object(guest, 'run', return_value=SimpleNamespace(returncode=1, stderr='EOF while parsing a value')), \
                patch.object(guest.time, 'monotonic', side_effect=[0, 1, 20]), \
                patch.object(guest.time, 'sleep'):
            return guest.niri_action('greeter')

    def test_quit_eof_requires_observed_process_and_socket_exit(self):
        self.assertTrue(self.quit_fixture()['exited'])
        with self.assertRaisesRegex(AssertionError, 'did not exit'):
            self.quit_fixture(still_alive=True)

    def test_health_excludes_restart_gap_stopped_and_user_session(self):
        self.assertTrue(host.healthy(state()))
        for changes in (dict(qs=[]), dict(regreet=[14]), dict(qs_stopped=[12]),
                        dict(user_compositors=[18]), dict(greetd='activating'), dict(active_tty='tty2')):
            with self.subTest(changes=changes):
                self.assertFalse(host.healthy(state(**changes)))

    def test_scenario_restores_before_and_after_failure(self):
        order = []
        def recovery(**kwargs):
            order.append(('restore', kwargs))
        def failure():
            order.append('operation')
            raise AssertionError('fixture failure')
        with patch.object(host, 'recover', side_effect=recovery), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(host.scenario('fake', failure))
        self.assertEqual(order, [('restore', {}), 'operation', ('restore', {'force': True})])
        row = json.loads((self.base / 'results.jsonl').read_text())
        self.assertFalse(row['passed'])
        self.assertEqual(host.FAILURES, ['fake'])

    def test_forced_recovery_replaces_healthy_greeter(self):
        operations = []
        def operation(name):
            operations.append(name)
            return {'restored': [], 'resumed': []} if name == 'restore-fixtures' else {'exited': True}
        with patch.object(host, 'guest', side_effect=operation), \
                patch.object(host, 'inspect', return_value=state()), \
                patch.object(host, 'wait', side_effect=lambda callback, timeout: callback()), \
                patch.object(host, 'wait_greeter', return_value=state()) as ready:
            host.recover(force=True)
        self.assertEqual(operations, ['restore-fixtures', 'restart-greeter'])
        ready.assert_called_once_with()

    def test_cleanup_failure_blocks_later_scenarios(self):
        with patch.object(host, 'recover', side_effect=[None, AssertionError('greeter unavailable')]), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(host.scenario('fake', lambda: 'operation completed'))
        row = json.loads((self.base / 'results.jsonl').read_text())
        self.assertFalse(row['passed'])
        self.assertEqual(row['cleanup_error'], 'greeter unavailable')

    def test_journal_failure_requires_current_attempt_and_exact_user(self):
        mark = '@100.000001'
        good = 'pam_unix(emaki-greetd:auth): authentication failure; user=arch'
        with patch.object(host, 'guest', return_value={'journal': good}) as operation:
            self.assertEqual(host.authentication_failed(mark), good)
            operation.assert_called_once_with('journal-since', since=mark)
        for text in ('old log without failure', good.replace('arch', 'other'), good.replace('arch', 'archived')):
            with patch.object(host, 'journal_since', return_value=text):
                self.assertIsNone(host.authentication_failed(mark))

    def test_session_uses_scoped_open_record(self):
        observed = state(qs=[], services={'niri.service': 'active', 'emaki-shell.service': 'active'})
        with patch.object(host, 'inspect', return_value=observed), \
                patch.object(host, 'journal_since', return_value='pam_unix(emaki-greetd:session): session opened for user arch') as read, \
                patch.object(host, 'wait', side_effect=lambda callback: callback()):
            self.assertEqual(host.session('niri.service', '@101')['journal'], read.return_value)
            read.assert_called_once_with('@101')

    def test_greetd_identity_is_allowlisted_and_read_only(self):
        text = 'MainPID=41\nNRestarts=2\nInvocationID=' + 'a' * 32 + '\nEnvironment=SECRET\n'
        with patch.object(guest, 'run', return_value=SimpleNamespace(returncode=0, stdout=text)) as run:
            self.assertEqual(guest.greetd_identity(), dict(MainPID=41, NRestarts=2, InvocationID='a' * 32))
            run.assert_called_once_with(['systemctl', 'show', 'greetd.service', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'InvocationID'])
        for malformed in (text.replace('MainPID=41', 'MainPID=0'), text.replace('NRestarts=2', 'NRestarts=-1'),
                          text.replace('a' * 32, 'invalid'), 'MainPID=41\n'):
            with self.subTest(reply=malformed), patch.object(guest, 'run', return_value=SimpleNamespace(returncode=0, stdout=malformed)):
                self.assertIsNone(guest.greetd_identity())

    def escape_fixture(self, **overrides):
        identity = dict(MainPID=41, NRestarts=2, InvocationID='a' * 32)
        initial = state(greetd_identity=identity)
        after = state(**dict(greetd_identity=identity) | overrides.get('after', {}))
        idle = state(**dict(greetd_identity=identity) | overrides.get('idle', {}))
        journal = overrides.get('journal', 'pam_unix(emaki-greetd:auth): authentication failure; user=arch')
        logged_in = state(qs=[], greetd_identity=identity,
                          services={'niri-emaki.service': 'active', 'emaki-shell.service': 'active'},
                          journal='pam_unix(emaki-greetd:session): session opened for user arch')
        with contextlib.ExitStack() as stack:
            operations = {name: stack.enter_context(patch.object(host, name, **options)) for name, options in {
                'guest': {}, 'restart': {}, 'keys': {}, 'shot': {}, 'logout': {},
                'inspect': dict(side_effect=[initial, after, idle]),
                'journal_mark': dict(side_effect=['@100.01', '@120.02']),
                'authentication_failed': dict(return_value=journal),
                'wait': dict(side_effect=lambda callback: callback()),
                'frames': dict(side_effect=lambda name, action, **kwargs: action()),
                'session': dict(return_value=logged_in)}.items()}
            operations['sleep'] = stack.enter_context(patch.object(host.time, 'sleep'))
            result = host.escape_check()
        return result, operations

    def test_escape_scenario_is_one_wrong_attempt_then_correct_with_same_uinput_device(self):
        result, calls = self.escape_fixture()
        key_calls = calls['keys'].call_args_list
        self.assertEqual(len(key_calls), 3)
        self.assertEqual(key_calls[0].args, ('text', 'key:Return', 'wait:1.8', 'key:Escape'))
        self.assertEqual(key_calls[0].kwargs, {'text': 'c9-wrong-password'})
        self.assertEqual(key_calls[1].args, tuple(['key:Escape'] * 8))
        self.assertEqual(key_calls[2].kwargs, {'text': 'arch'})
        calls['authentication_failed'].assert_called_once_with('@100.01')
        calls['session'].assert_called_once_with('niri-emaki.service', '@120.02')
        calls['sleep'].assert_called_once_with(31)
        calls['logout'].assert_called_once_with()
        calls['guest'].assert_called_once_with('select-emaki')
        self.assertIn('does not prove', result)
        self.assertTrue((self.base / 'escape-wrong-journal.txt').exists())
        saved = json.loads((self.base / 'escape-identities.json').read_text())
        self.assertEqual(saved['before']['greetd_identity'], saved['after']['greetd_identity'])

    def test_escape_scenario_rejects_daemon_restart_even_with_healthy_qs(self):
        identity = dict(MainPID=99, NRestarts=3, InvocationID='b' * 32)
        with self.assertRaisesRegex(AssertionError, 'greetd restarted'):
            self.escape_fixture(after={'greetd_identity': identity})

    def test_escape_scenario_rejects_replaced_qs_idle_fallback_and_panic_evidence(self):
        for overrides, message in (({'after': {'qs': [99]}}, 'Escape replaced'),
                                   ({'idle': {'regreet': [88]}}, 'idle Escape replaced'),
                                   ({'journal': 'pam_unix(emaki-greetd:auth): authentication failure; user=arch\nunable to cancel session'}, 'unable to cancel')):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(AssertionError, message):
                self.escape_fixture(**overrides)

    def test_tty_proof_uses_vcs2_and_active_tty(self):
        observed = state(active_tty='tty2', getty='active', tty2_text='Arch Linux\narchlinux login: ')
        with patch.object(host, 'keys') as keys, patch.object(host, 'inspect', return_value=observed), \
                patch.object(host, 'wait', side_effect=lambda callback: callback()), \
                patch.object(host, 'shot', side_effect=AssertionError('must not need screendump')):
            host.tty_proof('tty')
        self.assertIn('login:', (self.base / 'tty.txt').read_text())
        self.assertEqual(json.loads((self.base / 'tty-state.json').read_text())['active_tty'], 'tty2')
        keys.assert_called_once_with('chord:Control_L+Alt_L+F2')

    def test_virtualization_guards_accept_qemu_and_kvm(self):
        # Check all actual guards, not a detached test-only helper. Their common
        # membership comparison should accept qemu/kvm and reject bare metal.
        for filename in ('check-greeter.py', 'guest-greeter.py', 'guest-login.py'):
            tree = ast.parse((ROOT / 'tests/vm' / filename).read_text())
            matches = [node for node in ast.walk(tree) if isinstance(node, ast.Compare)
                       and any(isinstance(other, ast.Tuple) and [getattr(item, 'value', None) for item in other.elts] == ['qemu', 'kvm']
                               for other in node.comparators)]
            self.assertTrue(matches, filename)

    def test_run_test_rejects_bare_option_before_any_mutation(self):
        vm = self.base / 'vm-must-not-exist'
        result = subprocess.run(['bash', str(ROOT / 'tests/vm/run-test.sh'), '--greeter-only'],
                                env=dict(os.environ, EMAKI_VM_DIR=str(vm)), text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('requires an explicit committed ref', result.stderr)
        self.assertFalse(vm.exists())


RUNNER_STUB = r'''#!/usr/bin/python3
"""No external operations: this executable only writes inside C9_STUB_ROOT."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['C9_STUB_ROOT'])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as output:
    output.write(json.dumps([name, args]) + '\n')
if name == 'git':
    if args[:1] == ['-C']:
        args = args[2:]
    if args[:2] == ['rev-parse', '--show-toplevel']:
        print(root)
    elif args[:1] == ['rev-parse']:
        print('abc1234')
    elif args[:1] == ['log']:
        print('fixture revision')
    elif args[:2] == ['bundle', 'create']:
        Path(args[2]).write_text('fixture bundle')
    elif args[:2] == ['bundle', 'list-heads']:
        status = int(os.environ.get('C9_STUB_LIST_HEADS_RC', '0'))
        if status:
            raise SystemExit(status)
        print('abc1234 refs/heads/c9-login')
    else:
        raise SystemExit('unexpected git arguments')
elif name == 'socat':
    sys.stdin.read()
    raise SystemExit(int(os.environ.get('C9_STUB_SOCAT_RC', '0')))
elif name == 'timeout':
    raise SystemExit(int(os.environ.get('C9_STUB_TIMEOUT_RC', '0')))
elif name == 'ssh.sh':
    command = args[0]
    if command == 'cat /proc/sys/kernel/random/boot_id':
        marker = root / 'boot-read'
        print('boot-2' if marker.exists() else 'boot-1')
        marker.touch()
    elif command == 'bash -s':
        sys.stdin.read()
        print('unselected installer detail' if os.environ.get('C9_STUB_NO_MATCHES') else 'STEP fixture installed')
    elif command.startswith('echo "greetd:'):
        print('unselected boot detail' if os.environ.get('C9_STUB_NO_MATCHES') else 'greetd: active')
    else:
        sys.stdin.read()
elif name == 'grep':
    status = int(os.environ.get('C9_STUB_GREP_RC', '0'))
    if status:
        raise SystemExit(status)
    os.execv('/usr/bin/grep', ['grep', *args])
elif name == 'shot.sh':
    print('fixture screenshot: ' + args[0])
elif name in ('qemu-img', 'setsid', 'sleep', 'wait-ssh.sh', 'run.sh', 'login.sh'):
    pass
else:
    raise SystemExit('unexpected stub name')
'''


class ShellRunnerTests(unittest.TestCase):
    """Execute a copied runner with inert stubs, never real git/VM/SSH programs."""
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gr-', dir=CACHE)
        self.base = Path(self.work.name)
        self.runner = self.base / 'runner'
        self.bin = self.base / 'bin'
        self.vm = self.base / 'vm'
        for folder in (self.runner, self.bin, self.vm):
            folder.mkdir()
        shutil.copy(ROOT / 'tests/vm/run-test.sh', self.runner / 'run-test.sh')
        (self.runner / 'guest-install.sh').write_text('fixture installer input\n')
        for folder, names in ((self.bin, ('git', 'socat', 'timeout', 'qemu-img', 'setsid', 'sleep', 'grep')),
                              (self.runner, ('ssh.sh', 'wait-ssh.sh', 'run.sh', 'shot.sh', 'login.sh'))):
            for name in names:
                target = folder / name
                target.write_text(RUNNER_STUB)
                target.chmod(0o755)

    def tearDown(self):
        self.work.cleanup()

    def execute(self, pid=None, **environment):
        if pid == 'directory':
            (self.vm / 'qemu.pid').mkdir()
        elif pid is not None:
            (self.vm / 'qemu.pid').write_text(str(pid) + '\n')
        # A live local PID is read only as /proc existence. The inert socat and
        # timeout stubs never open a socket, signal it, or execute tail.
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                   EMAKI_VM_DIR=str(self.vm), C9_STUB_ROOT=str(self.base), **environment)
        result = subprocess.run(['bash', str(self.runner / 'run-test.sh'), 'c9-login', '--greeter-only'],
                                env=env, input='', text=True, capture_output=True, timeout=10)
        summaries = list(self.vm.glob('runs/*/summary.txt'))
        self.assertEqual(len(summaries), 1, result.stderr)
        summary = summaries[0].read_text()
        calls = [json.loads(line) for line in (self.base / 'calls.jsonl').read_text().splitlines()]
        return result, summary, calls

    def test_graceful_quit_waits_180_seconds_then_continues(self):
        result, summary, calls = self.execute(os.getpid())
        self.assertEqual(result.returncode, 0, result.stderr)
        waits = [args for name, args in calls if name == 'timeout']
        self.assertEqual(waits, [['180', 'tail', '--pid=' + str(os.getpid()), '-f', '/dev/null']])
        self.assertIn('waiting up to 180 seconds', summary)
        self.assertIn('QEMU PID ' + str(os.getpid()) + ' exited', summary)
        self.assertTrue(any(name == 'qemu-img' for name, _ in calls))
        self.assertFalse((self.vm / 'qemu.pid').exists())

    def test_timeout_124_is_logged_and_disk_is_not_touched(self):
        result, summary, calls = self.execute(os.getpid(), C9_STUB_TIMEOUT_RC='124')
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn('did not exit within 180 seconds (timeout rc=124)', summary)
        self.assertRegex(summary, r'ERROR: line \d+, rc=124:')
        self.assertFalse(any(name == 'qemu-img' for name, _ in calls))
        self.assertTrue((self.vm / 'qemu.pid').exists())

    def test_other_wait_failure_is_logged(self):
        result, summary, calls = self.execute(os.getpid(), C9_STUB_TIMEOUT_RC='7')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('waiting for QEMU PID', summary)
        self.assertIn('rc=7:', summary)
        self.assertFalse(any(name == 'qemu-img' for name, _ in calls))

    def test_monitor_failure_is_logged_before_any_wait_or_snapshot(self):
        result, summary, calls = self.execute(os.getpid(), C9_STUB_SOCAT_RC='17')
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn('QEMU monitor quit request failed', summary)
        self.assertIn('rc=17:', summary)
        self.assertFalse(any(name in ('timeout', 'qemu-img') for name, _ in calls))

    def test_invalid_pid_is_logged_without_monitor_access(self):
        result, summary, calls = self.execute('not-a-pid')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('invalid PID', summary)
        self.assertFalse(any(name in ('socat', 'timeout', 'qemu-img') for name, _ in calls))

    def test_unreadable_pid_is_logged(self):
        result, summary, calls = self.execute('directory')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot read VM PID file', summary)
        self.assertFalse(any(name in ('socat', 'timeout', 'qemu-img') for name, _ in calls))

    def test_summary_grep_no_match_is_visible_and_not_fatal(self):
        result, summary, _ = self.execute(C9_STUB_NO_MATCHES='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(summary.count('summary grep found no matches'), 2)
        self.assertIn('C9: ready', summary)
        self.assertIn('No VM PID file; nothing to stop', summary)

    def test_summary_grep_error_is_fatal_and_logged(self):
        result, summary, _ = self.execute(C9_STUB_GREP_RC='2')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('summary grep failed', summary)
        self.assertIn('rc=2:', summary)

    def test_unexpected_failure_err_trap_reports_line_and_status(self):
        result, summary, _ = self.execute(C9_STUB_LIST_HEADS_RC='37')
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertRegex(summary, r'ERROR: line \d+, rc=37:')
        self.assertIn('bundle list-heads', summary)
        self.assertRegex(result.stderr, r'ERROR: line \d+, rc=37:')



if __name__ == '__main__':
    unittest.main(verbosity=2)
