#!/usr/bin/env python3
"""Session/user memory, path boundaries and the real Quickshell helper protocol."""
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
SPEC = importlib.util.spec_from_file_location('greeter_state', ROOT / 'shell/helpers/greeter-state.py')
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)


def populate(base):
    for name in ('state', 'sessions', 'users'):
        (base / name).mkdir(mode=0o700)
    (base / 'passwd').write_text('root:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000::/home/alice:/bin/bash\n')
    (base / 'login.defs').write_text('UID_MIN 1000\nUID_MAX 60000\n')
    (base / 'boot').write_text('fixture-boot\n')
    (base / 'sessions/niri-emaki.desktop').write_text('[Desktop Entry]\nName=niri (Emaki)\nType=Application\nExec=niri-emaki-session\n')
    (base / 'sessions/niri.desktop').write_text('[Desktop Entry]\nName=Niri\nType=Application\nExec=niri-session\n')
    return helper.Paths(state=base / 'state', users=base / 'users', passwd=base / 'passwd',
                        login_defs=base / 'login.defs', sessions=base / 'sessions',
                        regreet=base / 'regreet.toml', boot_id=base / 'boot')


class StateTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gs-', dir=CACHE)
        self.base = Path(self.work.name)
        self.paths = populate(self.base)

    def tearDown(self):
        self.work.cleanup()

    def test_greeter_command_keeps_original_console_silent(self):
        binary = self.base / 'bin'
        binary.mkdir()
        (binary / 'bash').symlink_to('/bin/bash')
        for tool in ('mktemp', 'rm'):
            (binary / tool).symlink_to('/usr/bin/' + tool)
        journal = binary / 'systemd-cat'
        journal.write_text('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\n'
                           'assert sys.argv[1:]==["-t","emaki-greeter"]\n'
                           'Path(os.environ["FIXTURE_JOURNAL"]).write_text(sys.stdin.read())\n')
        journal.chmod(0o755)
        for name in ('qs', 'regreet', 'niri-emaki'):
            executable = binary / name
            executable.write_text('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\n'
                                  'name=Path(sys.argv[0]).name\n'
                                  'with open(os.environ["FIXTURE_COMMANDS"],"a") as f: f.write(name+"\\n")\n'
                                  'print(name+"-output"); print(name+"-error",file=sys.stderr)\n'
                                  'if name=="qs" and os.environ.get("FIXTURE_ACCEPTED")=="1":\n'
                                  ' import subprocess; subprocess.run([os.environ["FIXTURE_WRAPPER"],"--success"],check=True)\n'
                                  'if name=="niri-emaki" and os.environ.get("FIXTURE_ACCEPTED")=="1": assert Path(os.environ["EMAKI_GREETER_SUCCESS_FILE"]).read_text()=="accepted\\n"\n'
                                  'sys.exit(int(os.environ["FIXTURE_QS_EXIT"]) if name=="qs" else 0)\n')
            executable.chmod(0o755)
        command = str(ROOT / 'scripts/emaki-greeter-run')
        env = dict(os.environ, PATH=str(binary), FIXTURE_JOURNAL=str(self.base / 'journal'),
                   FIXTURE_COMMANDS=str(self.base / 'commands'), XDG_RUNTIME_DIR=str(self.base),
                   EMAKI_GREETER_COMPOSITOR='niri-emaki', FIXTURE_WRAPPER=command)
        for logger in (True, False):
            if not logger:
                journal.unlink()
            for code, accepted in ((0, False), (1, False), (255, True)):
                (self.base / 'commands').write_text('')
                result = subprocess.run(['/bin/sh', '-c', command], env=dict(env, FIXTURE_QS_EXIT=str(code), FIXTURE_ACCEPTED=str(int(accepted))),
                                        capture_output=True, text=True, timeout=3)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
                expected = ['qs', 'niri-emaki'] if accepted else (['qs', 'regreet', 'niri-emaki'] if code else ['qs', 'niri-emaki'])
                self.assertEqual((self.base / 'commands').read_text().splitlines(), expected)
                if logger:
                    logged = (self.base / 'journal').read_text()
                    for name in expected:
                        self.assertIn(name + '-output', logged)
                        self.assertIn(name + '-error', logged)

    def test_greeter_setup_failure_still_runs_ui_and_quits(self):
        binary = self.base / 'bin'
        binary.mkdir()
        for name in ('qs', 'regreet', 'niri'):
            executable = binary / name
            executable.write_text('#!/bin/bash\nprintf "%s\\n" "${0##*/}" >> "$RESULT"\n'
                                  'if [[ ${0##*/} == qs ]]; then exit "$QS_EXIT"; fi\n')
            executable.chmod(0o700)
        (binary / 'mktemp').symlink_to('/usr/bin/mktemp')
        env = dict(PATH=str(binary), RESULT=str(self.base / 'commands'),
                   XDG_RUNTIME_DIR=str(self.base / 'missing'))
        for code in (0, 1):
            (self.base / 'commands').write_text('')
            subprocess.run(['/bin/bash', str(ROOT / 'scripts/emaki-greeter-run')],
                           env=dict(env, QS_EXIT=str(code)), check=True, timeout=2)
            self.assertEqual((self.base / 'commands').read_text().splitlines(),
                             ['qs', 'regreet', 'niri'] if code else ['qs', 'niri'])
        # Even a failed success-marker write must still request compositor quit.
        subprocess.run(['/bin/bash', str(ROOT / 'scripts/emaki-greeter-run'), '--success'],
                       env=dict(env, EMAKI_GREETER_SUCCESS_FILE=str(self.base / 'missing/file')),
                       check=True, capture_output=True, timeout=2)
        self.assertEqual((self.base / 'commands').read_text().splitlines()[-1], 'niri')

    def test_compositor_journal_silence_and_stock_fallback(self):
        binary = self.base / 'bin'
        binary.mkdir()
        (binary / 'cat').symlink_to('/usr/bin/cat')
        dbus = binary / 'dbus-run-session'
        dbus.write_text('#!/usr/bin/python3\nimport os,sys\n'
                        'print("dbus-output",flush=True); print("dbus-error",file=sys.stderr,flush=True)\n'
                        'os.execvp(sys.argv[1], sys.argv[1:])\n')
        dbus.chmod(0o700)
        compositor = ('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                      'Path(os.environ["RESULT"]).write_text(json.dumps([sys.argv,os.environ.get("EMAKI_GREETER_KEEP_FRAME"),os.environ.get("EMAKI_GREETER_COMPOSITOR")]))\n'
                      'print("compositor-output",flush=True); print("compositor-error",file=sys.stderr,flush=True)\n'
                      'print("x"*262144,flush=True)\n'
                      'sys.exit(7)\n')
        stock = binary / 'niri'
        stock.write_text(compositor)
        stock.chmod(0o700)
        fork = binary / 'niri-emaki'
        logger = binary / 'systemd-cat'
        log = self.base / 'journal'
        for installed in (True, False):
            if installed:
                fork.write_text(compositor)
                fork.chmod(0o700)
            else:
                fork.unlink()
            for mode in ('working', 'failed', 'missing'):
                with self.subTest(fork=installed, logger=mode):
                    log.unlink(missing_ok=True)
                    if mode == 'missing':
                        logger.unlink()
                    else:
                        logger.write_text('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\n'
                                          'print("logger-output"); print("logger-error",file=sys.stderr)\n'
                                          + ('sys.exit(1)\n' if mode == 'failed' else
                                             'data=sys.stdin.read()\n'
                                             'Path(os.environ["JOURNAL"]).write_text(repr(sys.argv[1:])+"\\n"+data)\n'))
                        logger.chmod(0o700)
                    result = subprocess.run([str(ROOT / 'scripts/emaki-greeter-compositor')],
                                            env=dict(PATH=str(binary), RESULT=str(self.base / 'result'),
                                                     JOURNAL=str(log), EMAKI_GREETER_KEEP_FRAME='stale'),
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (7, '', ''))
                    argv, keep, selected = json.loads((self.base / 'result').read_text())
                    expected = 'niri-emaki' if installed else 'niri'
                    self.assertEqual(Path(argv[0]).name, expected)
                    self.assertEqual(argv[1:], ['--config', '/usr/share/emaki/greetd/niri.kdl'])
                    self.assertEqual(selected, expected)
                    self.assertEqual(keep, '1' if installed else None)
                    if mode == 'working':
                        deadline = time.monotonic() + 3
                        while (not log.exists() or log.stat().st_size < 262144) and time.monotonic() < deadline:
                            time.sleep(.01)
                        logged = log.read_text()
                        self.assertTrue(logged.startswith("['-t', 'emaki-greeter-compositor']\n"))
                        for message in ('dbus-output', 'dbus-error', 'compositor-output', 'compositor-error'):
                            self.assertIn(message, logged)

    def op(self, operation, now=100, **kwargs):
        return helper.operate(dict(op=operation, **kwargs), self.paths, now)

    def state(self):
        return json.loads((self.paths.state / 'state.json').read_text())

    def write_state(self, value):
        path = self.paths.state / 'state.json'
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def add_bob(self):
        with self.paths.passwd.open('a') as output:
            output.write('bob:x:1001:1001::/home/bob:/bin/bash\n')

    def launch(self, session='niri-emaki.desktop', now=100):
        return self.op('launch', user='alice', session=session, now=now)

    def test_missing_single_account_defaults_and_private_state(self):
        result = self.op('load')
        self.assertEqual((result['user'], result['sessionId'], result['command']),
                         ('alice', 'niri-emaki.desktop', ['niri-emaki-session']))
        self.assertFalse(result['multipleUsers'])
        self.assertEqual(result['wallpaperRoot'], str(self.paths.users / 'alice'))
        self.assertEqual(stat.S_IMODE((self.paths.state / 'state.json').stat().st_mode), 0o600)
        self.assertTrue(self.state()['seeded'])

    def test_uid_bounds_and_disabled_accounts(self):
        self.paths.login_defs.write_text('UID_MIN 1001 # local policy\nUID_MAX 2000\n')
        self.add_bob()
        with self.paths.passwd.open('a') as output:
            output.write('disabled:x:1002:1002::/home/disabled:/usr/bin/nologin\n')
            output.write('service:x:2001:2001::/home/service:/bin/bash\n')
        self.assertEqual(self.op('load')['user'], 'bob')

    def test_ambiguous_user_step_and_unknown_username(self):
        self.add_bob()
        self.assertEqual(self.op('load')['user'], '')
        self.assertTrue(self.op('load')['multipleUsers'])
        result = self.op('select', user='../alice')
        self.assertEqual((result['user'], result['message'], result['command']), ('', 'Unknown user', []))
        self.assertEqual(self.op('select', user='root')['message'], 'Unknown user')
        self.assertEqual(self.op('select', user='alice')['user'], 'alice')
        self.assertEqual(self.op('select', user='')['message'], '')
        self.assertEqual(self.state()['last_user'], '')

    def test_seed_regreet_once_and_remember_each_session(self):
        self.add_bob()
        self.paths.regreet.write_text('last_user = "bob"\n[user_to_last_sess]\nbob = "Niri"\nalice = "niri (Emaki)"\n')
        result = self.op('load')
        self.assertEqual((result['user'], result['sessionId']), ('bob', 'niri.desktop'))
        self.assertEqual(self.op('select', user='alice')['sessionId'], 'niri-emaki.desktop')
        self.paths.regreet.write_text('last_user = "alice"\n')
        self.assertEqual(self.op('load')['user'], 'bob')
        self.op('launch', user='alice', session='niri-emaki.desktop')
        self.assertEqual(self.op('load', now=131)['user'], 'alice')

    def test_bad_regreet_is_optional(self):
        self.paths.regreet.write_text('not toml')
        self.assertEqual(self.op('load')['user'], 'alice')

    def test_corrupt_state_recovers(self):
        self.op('load')
        (self.paths.state / 'state.json').write_text('{broken')
        self.assertEqual(self.op('load')['sessionId'], 'niri-emaki.desktop')
        self.assertEqual(self.state()['version'], 1)

    def test_deeply_nested_state_recovers(self):
        self.op('load')
        (self.paths.state / 'state.json').write_text('[' * 2000 + '0' + ']' * 2000)
        self.assertEqual(self.op('load')['sessionId'], 'niri-emaki.desktop')

    def test_no_local_account_requires_external_fallback(self):
        self.paths.passwd.write_text('root:x:0:0:root:/root:/bin/bash\n')
        for request in ('load', 'select'):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, 'no locally resolvable'):
                self.op(request, user='ldap-user')

    def test_missing_pending_key_recovers(self):
        state = helper.empty_state()
        del state['pending_launch']
        self.write_state(state)
        self.assertEqual(self.op('load')['user'], 'alice')
        self.assertIsNone(self.state()['pending_launch'])

    def test_non_utf8_state_recovers(self):
        self.op('load')
        (self.paths.state / 'state.json').write_bytes(b'\xff\xfe')
        self.assertEqual(self.op('load')['sessionId'], 'niri-emaki.desktop')
        self.assertEqual(self.state()['version'], 1)

    def test_invalid_pending_schema_recovers(self):
        state = helper.empty_state()
        state['pending_launch'] = dict(user='alice', session='niri-emaki.desktop', launched_at=float('nan'), boot_id='fixture-boot')
        self.write_state(state)
        self.assertEqual(self.op('load')['user'], 'alice')
        self.assertIsNone(self.state()['pending_launch'])

    def test_removed_remembered_account_uses_single_remaining(self):
        state = helper.empty_state()
        state['last_user'] = 'deleted'
        self.write_state(state)
        self.assertEqual(self.op('load')['user'], 'alice')

    def test_stock_default_when_emaki_missing_or_broken(self):
        self.paths.sessions.joinpath('niri-emaki.desktop').write_text('broken')
        self.assertEqual(self.op('load')['command'], ['niri-session'])
        self.paths.sessions.joinpath('niri.desktop').unlink()
        with self.assertRaises(ValueError):
            self.op('load')

    def test_early_emaki_exit_uses_stock_next(self):
        self.launch()
        result = self.op('load', now=129.999)
        self.assertEqual(result['sessionId'], 'niri.desktop')
        self.assertEqual(self.state()['users']['alice']['failed_session'], 'niri-emaki.desktop')
        self.assertIsNone(self.state()['pending_launch'])
        self.assertEqual(self.op('load', now=150)['sessionId'], 'niri.desktop')

    def test_thirty_seconds_is_not_early(self):
        self.launch()
        self.assertEqual(self.op('load', now=130)['sessionId'], 'niri-emaki.desktop')
        self.assertEqual(self.state()['users']['alice']['failed_session'], '')

    def test_boot_change_and_clock_reversal_do_not_mark_failure(self):
        self.launch()
        self.paths.boot_id.write_text('other-boot')
        self.assertEqual(self.op('load', now=101)['sessionId'], 'niri-emaki.desktop')
        self.launch()
        self.assertEqual(self.op('load', now=99)['sessionId'], 'niri-emaki.desktop')

    def test_stock_fallback_is_one_authenticated_login(self):
        self.assertEqual(self.op('load')['sessionId'], 'niri-emaki.desktop')
        self.launch()
        self.assertEqual(self.op('load', now=101)['sessionId'], 'niri.desktop')
        # Reopening the greeter or changing the name without authenticating
        # must not consume the fallback.
        self.assertEqual(self.op('load', now=105)['sessionId'], 'niri.desktop')
        self.assertEqual(self.op('select', user='alice')['sessionId'], 'niri.desktop')
        self.launch('niri.desktop', now=110)
        self.assertEqual(self.state()['users']['alice'],
                         dict(last_session='niri-emaki.desktop', failed_session=''))
        result = self.op('load', now=500)
        self.assertEqual(result['sessionId'], 'niri-emaki.desktop')
        self.launch(now=510)
        self.assertEqual(self.op('load', now=600)['sessionId'], 'niri-emaki.desktop')
        self.assertEqual(self.state()['users']['alice']['failed_session'], '')

    def test_short_stock_fallback_does_not_repeat_but_next_emaki_failure_does(self):
        self.launch()
        self.assertEqual(self.op('load', now=101)['sessionId'], 'niri.desktop')
        self.launch('niri.desktop', now=102)
        self.assertEqual(self.op('load', now=103)['sessionId'], 'niri-emaki.desktop')
        self.launch(now=104)
        self.assertEqual(self.op('load', now=105)['sessionId'], 'niri.desktop')

    def test_launch_revalidates_session_and_user(self):
        self.op('load')
        with self.assertRaises(ValueError):
            self.op('launch', user='alice', session='arbitrary.desktop')
        with self.assertRaises(ValueError):
            self.op('launch', user='root', session='niri.desktop')
        self.assertIsNone(self.state()['pending_launch'])

    def test_failed_emaki_without_stock_requires_external_fallback(self):
        self.launch()
        self.op('load', now=101)
        self.paths.sessions.joinpath('niri.desktop').unlink()
        with self.assertRaises(ValueError):
            self.op('load', now=102)

    def test_owned_state_directory_permissions_repaired_but_symlink_refused(self):
        self.paths.state.chmod(0o750)
        self.assertEqual(self.op('load')['user'], 'alice')
        self.assertEqual(stat.S_IMODE(self.paths.state.stat().st_mode), 0o700)
        self.paths.state.rename(self.base / 'elsewhere')
        self.paths.state.symlink_to(self.base / 'elsewhere')
        with self.assertRaises(OSError):
            self.op('load')

    def test_unsafe_state_and_lock_entries_self_heal_without_following_targets(self):
        for name in ('state.json', '.lock'):
            for kind in ('symlink', 'hardlink', 'fifo', 'permissions', 'directory', 'oversize'):
                with self.subTest(name=name, kind=kind):
                    path = self.paths.state / name
                    path.unlink(missing_ok=True)
                    target = self.base / 'target'
                    target.write_text('DO NOT READ OR MODIFY')
                    if kind == 'symlink':
                        path.symlink_to(target)
                    elif kind == 'hardlink':
                        os.link(target, path)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    elif kind == 'directory':
                        path.mkdir()
                        (path / 'never-traverse').symlink_to(target)
                    else:
                        path.write_text('x' * (helper.MAX_FILE + 1) if kind == 'oversize' else '{}')
                        path.chmod(0o600 if kind == 'oversize' else 0o644)
                    old = path.lstat()
                    prior = set(self.paths.state.glob('.invalid-*'))
                    self.assertEqual(self.op('load')['user'], 'alice')
                    self.assertEqual(target.read_text(), 'DO NOT READ OR MODIFY')
                    retired, = set(self.paths.state.glob('.invalid-*')) - prior
                    self.assertEqual(retired.lstat().st_ino, old.st_ino)
                    self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    # The next login uses healed files, not a permanent fallback.
                    self.assertEqual(self.op('load')['user'], 'alice')

    def test_foreign_owned_state_entry_rebuilds_without_reading_it(self):
        path = self.paths.state / 'state.json'
        path.write_text('FOREIGN CONTENT')
        path.chmod(0o600)
        original = os.stat
        def foreign(name, *args, **kwargs):
            metadata = original(name, *args, **kwargs)
            if name == 'state.json' and kwargs.get('dir_fd') is not None:
                return SimpleNamespace(st_mode=metadata.st_mode, st_uid=os.geteuid() + 1,
                                       st_nlink=1, st_size=metadata.st_size)
            return metadata
        with patch.object(helper.os, 'stat', side_effect=foreign):
            self.assertEqual(self.op('load')['user'], 'alice')
        retired, = self.paths.state.glob('.invalid-state.json-*')
        self.assertEqual(retired.read_text(), 'FOREIGN CONTENT')
        self.assertEqual(self.op('load')['user'], 'alice')

    def test_directory_lock_serializes_repairs(self):
        # A second helper cannot race .lock replacement: the parent inode, not
        # the replaceable lock entry, serializes repair and all state operations.
        with helper.locked_state(self.paths.state):
            result = subprocess.run([sys.executable, '-I', '-c',
                'import fcntl, os, sys\n'
                'fd=os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n'
                'try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
                'except BlockingIOError: sys.exit(0)\n'
                'sys.exit(1)', str(self.paths.state)], timeout=3, check=False)
            self.assertEqual(result.returncode, 0)

    def test_symlink_session_refused(self):
        entry = self.paths.sessions / 'niri-emaki.desktop'
        entry.rename(self.base / 'desktop')
        entry.symlink_to(self.base / 'desktop')
        self.assertEqual(self.op('load')['sessionId'], 'niri.desktop')

    def test_no_home_reads_even_when_passwd_contains_home(self):
        original = os.open
        def checked(path, *args, **kwargs):
            self.assertNotIn('/home/', str(path))
            return original(path, *args, **kwargs)
        with patch.object(helper.os, 'open', side_effect=checked):
            self.op('load')
            self.launch()

    def test_exec_preserves_argv_through_greetd_shell_join(self):
        path = self.paths.sessions / 'niri-emaki.desktop'
        path.write_text('[Desktop Entry]\nType=Application\nName=Emaki session\nExec=/usr/bin/session "two words" "literal;code" "literal$HOME" %% %c %k %f\n')
        result = self.op('load')
        actual = shlex.split(' '.join(result['command']))
        self.assertEqual(actual, ['/usr/bin/session', 'two words', 'literal;code', 'literal$HOME', '%', 'Emaki session', str(path)])
        self.assertEqual(helper.exec_words('session ""'), ['session', ''])

    def test_unsafe_or_unsupported_exec_uses_stock(self):
        path = self.paths.sessions / 'niri-emaki.desktop'
        for value in ('session; evil', "session 'single quotes'", 'session %Z', 'session %fembedded', 'session "unclosed'):
            path.write_text('[Desktop Entry]\nType=Application\nExec=' + value + '\n')
            with self.subTest(value=value):
                self.assertEqual(self.op('load')['sessionId'], 'niri.desktop')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StateTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    # Real Process+stdin/stdout lifecycle, including crashed, malformed and hung helpers.
    with tempfile.TemporaryDirectory(prefix='gq-', dir=CACHE) as work:
        base = Path(work)
        paths = populate(base)
        for name in ('r', 'cache', 'config', 'data', 'tmp', 'qml', 'qml/helpers'):
            (base / name).mkdir(mode=0o700)
        shutil.copy(ROOT / 'shell/GreeterState.qml', base / 'qml/GreeterState.qml')
        shutil.copy(ROOT / 'tests/fixtures/GreeterStateTest.qml', base / 'qml/check.qml')
        target = base / 'qml/helpers/greeter-state.py'
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(base / 'r'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_DATA_HOME=str(base / 'data'),
                   TMPDIR=str(base / 'tmp'), PYTHONDONTWRITEBYTECODE='1',
                   NIRI_SOCKET='', GREETD_SOCK='', HOME=str(base),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'none'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'none-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        implementation = ROOT / 'shell/helpers/greeter-state.py'
        parameters = ','.join(key + '=Path(' + repr(str(value)) + ')' for key, value in vars(paths).items())
        scripts = {
            'success': f'''import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('state_implementation', {str(implementation)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
value = module.operate(json.loads(sys.stdin.readline()), module.Paths({parameters}), now=100)
print(json.dumps(value), flush=True)
''',
            'malformed': 'print("not JSON", flush=True)\n',
            'failed': 'raise SystemExit(1)\n',
            'hang': 'import time\ntime.sleep(20)\n',
        }
        for mode, script in scripts.items():
            target.write_text(script)
            process = subprocess.run(['qs', '-p', str(base / 'qml/check.qml'), '--no-color'],
                                     env=dict(env, EMAKI_GREETER_STATE_TEST=mode), text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12)
            assert process.returncode == 0 and 'GREETER_STATE_PASS' in process.stdout, process.stdout
            assert 'ReferenceError' not in process.stdout and 'TypeError' not in process.stdout, process.stdout
            print('PASS: GreeterState real helper protocol:', mode)
        # Load the actual entry/component tree offscreen. Replace only compositor
        # window plumbing; Auth, State, Surface, Visual and their bindings stay real.
        shutil.copytree(ROOT / 'shell', base / 'qml', dirs_exist_ok=True)
        target.write_text(scripts['success'])
        entry = (ROOT / 'shell/greeter.qml').read_text()
        entry = entry.replace('import QtQuick\n', 'import QtQuick\nimport QtQuick.Window\n')
        entry = entry.replace('PanelWindow {', 'Window {')
        entry = entry.replace('model: Quickshell.screens', 'model: [null]')
        entry = entry.replace('required property ShellScreen modelData',
                              'width: 960\n            height: 640\n            visible: true\n            required property ShellScreen modelData')
        entry = entry.replace('            screen: modelData\n', '')
        entry = entry.replace('screen: panel.screen', 'screen: panel.modelData')
        entry = re.sub(r'            anchors \{\n(?:                [^\n]*\n)+            }\n', '', entry)
        entry = re.sub(r'            (?:exclusiveZone|exclusionMode|WlrLayershell\.[A-Za-z]+):[^\n]*\n', '', entry)
        marker = entry.rfind('}')
        entry = entry[:marker] + '''
    Timer {
        interval: 3000
        running: true
        onTriggered: {
            if (!memory.ready || memory.user !== "alice" || controller.user !== "alice" || controller.usernameMode || !controller.selectionReady || !lockSession.poured) {
                console.error("GREETER_ENTRY_FAILED state/binding/readiness", memory.ready, memory.user, controller.user, controller.usernameMode, controller.selectionReady, lockSession.poured, lockSession.phase, Object.keys(lockSession.surfaces));
                Qt.exit(1);
            } else {
                console.log("GREETER_ENTRY_PASS");
                Qt.quit();
            }
        }
    }
}
'''
        (base / 'qml/check-entry.qml').write_text(entry)
        process = subprocess.run(['qs', '-p', str(base / 'qml/check-entry.qml'), '--no-color'],
                                 env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        assert process.returncode == 0 and 'GREETER_ENTRY_PASS' in process.stdout, process.stdout
        assert 'ReferenceError' not in process.stdout and 'TypeError' not in process.stdout and 'Binding loop' not in process.stdout, process.stdout
        print('PASS: greeter actual entry/components load, bind and pour offscreen')
    print('PASS: greeter state, automatic session fallback, private paths and Exec quoting')
