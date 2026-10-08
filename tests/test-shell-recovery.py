#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shell recovery without a graphical session or access to the user manager."""
import configparser
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Recovery(unittest.TestCase):
    def test_terminal_survives_shell_failure_and_ends_at_logout(self):
        shell = configparser.ConfigParser(interpolation=None)
        shell.read(ROOT / 'systemd/emaki-shell.service')
        recovery = configparser.ConfigParser(interpolation=None)
        recovery.read(ROOT / 'systemd/emaki-shell-recovery.service')
        self.assertEqual(shell['Unit']['OnFailure'], 'emaki-shell-heal.service')
        self.assertIn('emaki-shell-recovery.service', shell['Unit']['Conflicts'].split())
        self.assertEqual(recovery['Unit']['PartOf'], 'graphical-session.target')
        self.assertEqual(recovery['Unit']['Requisite'], 'graphical-session.target')
        self.assertNotIn('emaki-shell.service', recovery['Unit'].get('PartOf', ''))
        self.assertEqual(recovery['Service']['ExecStart'],
                         '/usr/bin/kitty --class emaki-shell-recovery --title "Emaki panel recovery" /usr/bin/emaki-shell recover')
        self.assertNotIn('Restart', recovery['Service'])
        heal = configparser.ConfigParser(interpolation=None)
        heal.read(ROOT / 'systemd/emaki-shell-heal.service')
        self.assertEqual(heal['Unit']['OnFailure'], 'emaki-shell-recovery.service')
        self.assertEqual(heal['Unit']['Requisite'], 'graphical-session.target')
        self.assertEqual(heal['Unit']['PartOf'], 'graphical-session.target')
        self.assertNotIn('Conflicts', heal['Unit'])
        self.assertNotIn('Restart', heal['Service'])
        self.assertEqual(heal['Service']['ExecStart'], '/usr/bin/emaki-shell-health reconcile')

    def test_prompt_is_reserved_for_terminal_failure(self):
        shell = configparser.ConfigParser(interpolation=None)
        shell.read(ROOT / 'systemd/emaki-shell.service')
        # systemd v262 service_enter_dead skips FAILED_BEFORE_AUTO_RESTART only
        # in direct mode. unit_notify starts OnFailure on every UNIT_FAILED entry.
        # Exhausting the start limit still enters SERVICE_FAILED in direct mode:
        # service_can_start calls service_enter_dead with allow_restart=false.
        self.assertEqual(shell['Service']['Restart'], 'on-failure')
        self.assertEqual(shell['Service'].get('RestartMode'), 'direct',
                         'normal mode opens the recovery prompt for transient crashes')
        self.assertGreater(int(shell['Unit']['StartLimitIntervalSec']), 0)
        self.assertGreater(int(shell['Unit']['StartLimitBurst']), 1)
        self.assertEqual(shell['Unit']['OnFailure'], 'emaki-shell-heal.service')

    def test_slow_crash_loop_exhausts_start_limit(self):
        shell = configparser.ConfigParser(interpolation=None)
        shell.read(ROOT / 'systemd/emaki-shell.service')
        interval = float(shell['Unit']['StartLimitIntervalSec'])
        burst = int(shell['Unit']['StartLimitBurst'])
        delay = float(shell['Service']['RestartSec'])

        def run_loop(window):
            # Deterministic simulation of systemd's fixed rate-limit windows, not a
            # live user-manager test. Each attempt loads for 2.5 s then fails.
            now = window_start = 0.0
            starts_in_window = 0
            for starts in range(200):
                if now - window_start > window:
                    window_start = now
                    starts_in_window = 0
                if starts_in_window >= burst:
                    return starts, now
                starts_in_window += 1
                now += 2.5 + delay
            return None

        self.assertIsNone(run_loop(60), 'old window must reproduce endless retries')
        self.assertEqual(run_loop(interval), (20, 70.0),
                         'slow crashes must stop after 20 starts and reach OnFailure')

    def invoke(self, command, input_text, fail_reset=False):
        with tempfile.TemporaryDirectory(prefix='shell-recovery-') as directory:
            base = Path(directory)
            checker = base / 'emaki-qt-check'
            checker.write_text('#!/bin/sh\nexit 0\n')
            checker.chmod(0o700)
            manager = base / 'systemctl'
            manager.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                               + ('exit 1\n' if fail_reset else 'exit 0\n'))
            manager.chmod(0o700)
            env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                       CALLS=str(base / 'calls'), XDG_RUNTIME_DIR=str(base))
            result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/emaki-shell'), command],
                                    input=input_text, text=True, capture_output=True, env=env,
                                    timeout=5)
            calls = (base / 'calls').read_text().splitlines() if (base / 'calls').exists() else []
            return result, calls

    def test_enter_clears_exhausted_limit_before_restart(self):
        result, calls = self.invoke('recover', '\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Press Enter to restart it.', result.stdout)
        self.assertEqual(calls, ['--user reset-failed emaki-shell.service',
                                 '--user restart emaki-shell.service'])

    def test_closing_prompt_does_not_restart(self):
        result, calls = self.invoke('recover', '')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_manual_restart_also_clears_limit(self):
        result, calls = self.invoke('restart', '')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, ['--user reset-failed emaki-shell.service',
                                 '--user restart emaki-shell.service'])

    def test_failed_reset_does_not_attempt_restart(self):
        result, calls = self.invoke('recover', '\n', fail_reset=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ['--user reset-failed emaki-shell.service'])

    def test_launcher_fallback_only_after_failed_toggle(self):
        with tempfile.TemporaryDirectory(prefix='shell-launcher-') as directory:
            base = Path(directory)
            for name, body in [('qs', 'exit "$QS_STATUS"'),
                               ('fuzzel', 'printf "%s\\n" "$@" > "$FALLBACK"'),
                               ('emaki-config-path',
                                'test "$1" = fuzzel && test "$2" = fuzzel.ini || exit 1\n'
                                'printf "%s\\n" "$FUZZEL_CONFIG"')]:
                script = base / name
                script.write_text('#!/bin/sh\n' + body + '\n')
                script.chmod(0o700)
            env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                       XDG_RUNTIME_DIR=str(base),
                       FALLBACK=str(base / 'fallback'),
                       FUZZEL_CONFIG=str(base / 'config with spaces/fuzzel.ini'))
            for status, args, expected in [
                    ('0', ['launcher', 'toggle'], False),
                    ('1', ['launcher', 'toggle'], True),
                    ('1', ['launcher', 'close'], False),
                    ('1', ['drawer', 'toggle'], False),
                    ('1', ['launcher', 'toggle', 'extra'], False)]:
                with self.subTest(status=status, args=args):
                    (base / 'fallback').unlink(missing_ok=True)
                    result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/emaki-shell'),
                                             'call', *args], env=dict(env, QS_STATUS=status),
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual((base / 'fallback').exists(), expected)
                    self.assertEqual(result.returncode, 0 if expected else int(status))
                    if expected:
                        self.assertEqual((base / 'fallback').read_text().splitlines(),
                                         ['--config', env['FUZZEL_CONFIG']])

    def test_output_selection_tracks_hotplug_without_losing_override(self):
        script = (ROOT / 'shell/OutputSelection.js').read_text().replace('.pragma library', '')
        subprocess.run(['node', '-e', script + '''
const assert = require('node:assert/strict');
const external = {name: 'HDMI-A-1'}, internal = {name: 'eDP-1'};
assert.equal(select([], ''), null);
assert.equal(select([external], ''), external);
assert.equal(select([external, internal], ''), internal);
assert.equal(select([internal, external], ''), internal);
assert.equal(select([external, internal], 'HDMI-A-1'), external);
assert.equal(select([external], 'eDP-1'), null);
for (const name of ['LVDS-1', 'LVDS1', 'DSI-1']) {
    const panel = {name};
    assert.equal(select([external, panel], ''), panel);
}
'''], check=True, timeout=5)

    def test_wrapper_start_and_ipc_both_use_recovered_selection(self):
        with tempfile.TemporaryDirectory(prefix='shell-selection-') as directory:
            base = Path(directory)
            state = base / 'emaki-shell-health'
            state.mkdir()
            (state / 'packaged').write_text('one attempt claimed\n')
            qs = base / 'qs'
            qs.write_text('''#!/usr/bin/python3
import os
from pathlib import Path
import sys
Path(os.environ['CALLS']).write_text('\\n'.join(sys.argv[1:]) + '\\n')
if 'ipc' not in sys.argv:
    print('READY', flush=True)
    sys.stdin.read(1)
''')
            qs.chmod(0o700)
            qt_check = base / 'emaki-qt-check'
            qt_check.write_text('#!/bin/sh\nexit 0\n')
            qt_check.chmod(0o700)
            env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                       XDG_RUNTIME_DIR=str(base), EMAKI_SHELL_DIR=str(base / 'override'),
                       INVOCATION_ID='a' * 32, CALLS=str(base / 'calls'))
            process = subprocess.Popen([str(ROOT / 'scripts/emaki-shell')], env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0],
                                'shell did not become ready')
                self.assertEqual(process.stdout.readline(), 'READY\n')
                launch = json.loads((state / 'launch.json').read_text())
                self.assertIn(launch.get('runtime_shell', '/usr/share/emaki/shell'),
                              (base / 'calls').read_text().splitlines())
                self.assertNotIn(env['EMAKI_SHELL_DIR'], (base / 'calls').read_text())
                result = subprocess.run([str(ROOT / 'scripts/emaki-shell'),
                                         'call', 'launcher', 'status'],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(launch.get('runtime_shell', '/usr/share/emaki/shell'),
                              (base / 'calls').read_text().splitlines())
                self.assertNotIn(env['EMAKI_SHELL_DIR'], (base / 'calls').read_text())
            finally:
                process.kill()
                process.communicate(timeout=5)
            self.assertEqual(launch['shell'], '/usr/share/emaki/shell')
            self.assertEqual(launch['invocation'], 'a' * 32)

    def test_ipc_selection_never_invokes_health_helper(self):
        with tempfile.TemporaryDirectory(prefix='shell-ipc-') as directory:
            base = Path(directory)
            shutil.copyfile(ROOT / 'scripts/emaki-shell', base / 'emaki-shell')
            shutil.copy2(ROOT / 'scripts/emaki-session-files', base / 'emaki-session-files')
            shutil.copy2(ROOT / 'scripts/emaki_session_state.py', base / 'emaki_session_state.py')
            for name, body in [('emaki-shell-health', 'echo unexpected > "$HEALTH_CALL"; exit 1'),
                               ('qs', 'printf "%s\\n" "$@" > "$CALLS"')]:
                script = base / name
                script.write_text('#!/bin/sh\n' + body + '\n')
                script.chmod(0o700)
            env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                       XDG_RUNTIME_DIR=str(base), EMAKI_SHELL_DIR=str(base / 'override'),
                       HEALTH_CALL=str(base / 'health-call'), CALLS=str(base / 'calls'))
            for repaired in (False, True):
                if repaired:
                    # The state directory belongs to the runtime, separate from binaries.
                    runtime = base / 'runtime'
                    (runtime / 'emaki-shell-health').mkdir(parents=True)
                    (runtime / 'emaki-shell-health/packaged').touch()
                    env['XDG_RUNTIME_DIR'] = str(runtime)
                result = subprocess.run(['/bin/sh', str(base / 'emaki-shell'),
                                         'call', 'launcher', 'status'], env=env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = '/usr/share/emaki/shell' if repaired else env['EMAKI_SHELL_DIR']
                self.assertIn(expected, (base / 'calls').read_text().splitlines())
                self.assertFalse((base / 'health-call').exists())


if __name__ == '__main__':
    unittest.main()
