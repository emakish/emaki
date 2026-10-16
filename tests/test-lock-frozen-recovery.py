#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen fallback recovery with owned mock children; no desktop, bus or PAM."""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--script', type=Path, default=ROOT / 'scripts/emaki-lock')
args, rest = parser.parse_known_args()
sys.path.insert(0, str(ROOT / 'scripts'))
loader = importlib.machinery.SourceFileLoader('frozen_lock', str(args.script))
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec)
loader.exec_module(lock)

FIXTURE = r'''#!/usr/bin/env python3
import json, os, re, sys, threading, time
from pathlib import Path
root = Path(os.environ['LOCK_FIXTURE'])
fallback = Path(sys.argv[0]).name == 'hyprlock'
if fallback:
    config = Path(sys.argv[sys.argv.index('--config') + 1]).read_text() if '--config' in sys.argv else ''
    match = re.search(r'touch -- ([^;]+);', config)
    heartbeat = Path(match[1].strip().strip("'")) if match else None
    source = re.search(r'^source = (.+)$', config, re.M)
    original = Path(source[1].replace('[', '').replace(']', '')).read_text() if source else ''
    slow = re.search(r'cmd\[update:1000\] sleep ([\d.]+);', original)
    pending = threading.Event()
    if slow:
        # Run the single serial resource gatherer independently of the main loop.
        def gather():
            while True:
                pending.set()
                time.sleep(float(slow[1]))
                if heartbeat:
                    heartbeat.touch()
                pending.clear()
                time.sleep(.04)
        threading.Thread(target=gather, daemon=True).start()
    print('onLockLocked called', flush=True)
else:
    heartbeat = None
def send(**event):
    with (root / 'events').open('a') as out:
        out.write(json.dumps(dict(version=1, **event)) + '\n')
while True:
    if fallback:
        if slow and pending.is_set():
            print('WARN ]: Trying to update label, but a resource is still pending! Skipping update.', flush=True)
        elif heartbeat and not slow:
            heartbeat.touch()
    else:
        send(event='state', secure=True, poured=True, phase='locked')
        password = root / 'password'
        if password.exists():
            value = password.read_text()
            password.unlink()
            if value == 'fixture-secret':
                send(event='state', secure=True, poured=True, phase='finished')
                send(event='authenticated-exit')
                sys.exit(0)
            (root / 'rejected').touch()
    time.sleep(.04)
'''


class FrozenFallback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='frozen-lock-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('qs', 'hyprlock'):
            executable = self.bin / name
            executable.write_text(FIXTURE)
            executable.chmod(0o700)
        config = self.root / 'config/hypr/hyprlock.conf'
        config.parent.mkdir(parents=True)
        config.write_text('general {\n}\n')
        self.env = patch.dict(os.environ, dict(PATH=str(self.bin) + ':' + os.environ['PATH'],
            HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / 'config'),
            XDG_CONFIG_DIRS=str(self.root / 'config'), LOCK_FIXTURE=str(self.root)), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.s = lock.Supervisor(self.root, start_seconds=.5, heartbeat_seconds=.25)
        self.s.fallback_heartbeat_seconds = .25
        self.s.authentication_pending = False
        self.s.release_authentication = Mock()
        self.events_read = 0
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.s.retire(kill=True)
        self.s.selector.close()

    def step(self):
        if self.s.child is not None and self.s.child.stdout:
            self.s.read_fallback(self.s.child.stdout)
        events = self.root / 'events'
        if events.exists():
            lines = events.read_text().splitlines()
            for line in lines[self.events_read:]:
                self.s.connections['fixture'] = dict(trusted=True)
                self.s.handle('fixture', json.loads(line))
                self.s.connections.pop('fixture')
            self.events_read = len(lines)
        self.s.tick()

    def until(self, condition, seconds=2):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.step()
            if condition():
                return
            time.sleep(.01)
        self.fail('owned locker did not reach the expected state')

    def start(self):
        self.s.spawn(True)
        self.until(lambda: self.s.snapshot()['state'] == 'locked')
        self.assertTrue(self.s.retained_lock)

    def test_stale_confirmed_fallback_is_not_reported_ready(self):
        self.start()
        self.s.last_heartbeat = time.monotonic() - 1
        self.assertNotEqual(self.s.snapshot()['state'], 'locked')

    def test_stopped_fallback_recovers_and_password_entry_works(self):
        self.start()
        frozen = self.s.child
        with patch.object(lock, 'RETRY_BACKOFF', (.04, .08, .12)):
            os.kill(frozen.pid, signal.SIGSTOP)
            self.until(lambda: self.s.backend == 'quickshell' and self.s.snapshot()['state'] == 'locked')
        self.assertEqual(frozen.returncode, -signal.SIGKILL)
        self.assertEqual(self.s.cycles, 0)
        self.assertTrue(self.s.retained_lock)
        self.s.release_authentication.assert_not_called()
        (self.root / 'password').write_text('wrong')
        self.until(lambda: (self.root / 'rejected').exists())
        self.assertEqual(self.s.snapshot()['state'], 'locked')
        self.s.release_authentication.assert_not_called()
        (self.root / 'password').write_text('fixture-secret')
        self.until(lambda: self.s.finished)
        self.assertEqual(self.s.phase, 'unlocked')
        self.assertFalse(self.s.retained_lock)
        self.s.release_authentication.assert_called_once_with()

    def test_progress_and_supervisor_gap_keep_the_same_fallback(self):
        self.start()
        child = self.s.child
        until = time.monotonic() + .7
        while time.monotonic() < until:
            self.step()
            time.sleep(.01)
        self.assertIs(self.s.child, child)
        self.s.last_tick -= 120
        self.s.started -= 120
        self.s.readiness_since -= 120
        self.s.last_heartbeat -= 120
        self.s.account_stall()
        self.assertEqual(self.s.snapshot()['state'], 'locked')
        self.step()
        self.assertIs(self.s.child, child)
        self.s.release_authentication.assert_not_called()

    def test_no_outputs_defers_recovery_but_returning_outputs_do_not(self):
        self.start()
        frozen = self.s.child
        os.kill(frozen.pid, signal.SIGSTOP)
        self.s.fallback_heartbeat.progressed = lambda: False
        self.s.last_heartbeat -= 1
        with patch.object(self.s, 'fallback_outputs_absent', return_value=True):
            self.step()
        self.assertIs(self.s.child, frozen)
        self.assertTrue(self.s.retained_lock)
        self.s.last_heartbeat -= 1
        with patch.object(self.s, 'fallback_outputs_absent', return_value=False):
            self.step()
        self.assertEqual(frozen.returncode, -signal.SIGKILL)
        self.assertEqual(self.s.phase, 'recovering')
        self.assertTrue(self.s.retained_lock)
        self.s.release_authentication.assert_not_called()

    def test_repeated_frozen_fallbacks_preserve_crash_budget_and_lock(self):
        with patch.object(lock, 'RETRY_BACKOFF', (0, 0)):
            self.s.cycles = 1  # A real earlier exit must neither be forgotten nor spent again.
            for attempt in range(3):
                self.s.fallback_attempts = 0
                self.start()
                self.s.fallback_heartbeat.progressed = lambda: False
                self.s.last_heartbeat -= 1
                self.step()
                self.assertTrue(self.s.retained_lock)
                self.assertNotEqual(self.s.phase, 'unlocked')
            self.assertFalse(self.s.finished)
            self.assertEqual(self.s.phase, 'recovering')
            self.assertEqual(self.s.cycles, 1)
            self.s.release_authentication.assert_not_called()

            # Actual non-zero exits still consume and exhaust their original budget.
            for attempt in range(2):
                self.s.fallback_attempts = 0
                self.start()
                self.s.child.kill()
                self.s.child.wait()
                self.step()
                self.assertTrue(self.s.retained_lock)
                self.assertNotEqual(self.s.phase, 'unlocked')
            self.assertTrue(self.s.finished)
            self.assertEqual(self.s.phase, 'failed')
            self.assertEqual(self.s.cycles, 2)

    def test_slow_personal_command_keeps_responsive_fallback(self):
        config = self.root / 'config/hypr/hyprlock.conf'
        original = 'label {\n    text = cmd[update:1000] sleep 4; echo slow\n}\n'
        config.write_text(original)
        self.start()
        child = self.s.child
        end = time.monotonic() + 4.2
        while time.monotonic() < end:
            self.step()
            self.assertIs(self.s.child, child)
            self.assertEqual(self.s.snapshot()['state'], 'locked')
            time.sleep(.01)
        self.assertEqual(self.s.cycles, 0)
        self.assertEqual(config.read_text(), original)
        self.s.release_authentication.assert_not_called()

    def test_fallback_deadline_allows_long_command_before_expiry(self):
        self.start()
        self.s.fallback_heartbeat_seconds = getattr(lock, 'FALLBACK_HEARTBEAT_SECONDS', .25)
        self.s.last_heartbeat = time.monotonic() - 30
        self.assertEqual(self.s.snapshot()['state'], 'locked')
        self.s.last_heartbeat = time.monotonic() - 61
        self.assertNotEqual(self.s.snapshot()['state'], 'locked')

    def test_only_main_loop_messages_count_without_confirming_or_unlocking(self):
        self.start()
        for line, progress in [
                (b'WARN ]: Trying to update label, but a resource is still pending! Skipping update.', True),
                (b'\x1b[33mWARN from hyprlock ]: Trying to update label, but a resource is still pending! Skipping update.\x1b[0m', True),
                (b'[WARN] Trying to update label, but a resource is still pending! Skipping update.', True),
                (b'[LOG] Authenticating', True),
                (b'DEBUG ]: Clearing password buffer', True),
                (b'worker: Authenticating', False),
                (b'[LOG] unrelated resource worker message', False)]:
            self.s.last_heartbeat = time.monotonic() - 1
            self.s.secure = self.s.poured = False
            before = self.s.last_heartbeat
            read, write = os.pipe()
            with os.fdopen(read, 'rb') as stream, os.fdopen(write, 'wb') as output:
                output.write(line + b'\n')
                output.flush()
                self.s.read_fallback(stream)
            self.assertEqual(self.s.last_heartbeat > before, progress, line)
            self.assertFalse(self.s.secure)
            self.assertFalse(self.s.poured)
            self.assertNotEqual(self.s.snapshot()['state'], 'locked')
            self.s.release_authentication.assert_not_called()

    def test_only_valid_empty_or_disabled_output_inventory_defers_recovery(self):
        for value, absent in [({'Ok': {'Outputs': {}}}, True),
                              ({'Ok': {'Outputs': {'one': {'logical': None}}}}, True),
                              ({'Ok': {'Outputs': {'one': {'logical': {'x': 0}}}}}, False),
                              ({'Ok': {'Outputs': {'one': {}}}}, False),
                              ({'Ok': {'Outputs': []}}, False),
                              ({'Ok': 'Handled'}, False), ({'Err': 'unavailable'}, False),
                              ([], False)]:
            stream = MagicMock()
            stream.__enter__.return_value = stream
            response = (json.dumps(value) + '\n').encode()
            stream.recv.side_effect = [response[:3], response[3:]]
            with patch.dict(os.environ, NIRI_SOCKET='private-fixture'), \
                    patch.object(lock.socket, 'socket', return_value=stream):
                self.assertEqual(self.s.fallback_outputs_absent(), absent, value)
            stream.connect.assert_called_once_with('private-fixture')
            stream.sendall.assert_called_once_with(b'"Outputs"\n')

    def test_config_selection_keeps_personal_accent_and_system_precedence(self):
        env = dict(os.environ)
        personal = Path(env['XDG_CONFIG_HOME']) / 'hypr/hyprlock.conf'
        self.assertEqual(lock.fallback_config(env), personal)
        accent = self.root / 'accent.conf'
        with patch.object(lock, 'hyprlock_config', return_value=['--config', str(accent)]):
            self.assertEqual(lock.fallback_config(env), accent)
        personal.unlink()
        home = self.root / '.config/hypr/hyprlock.conf'
        home.parent.mkdir(parents=True)
        home.write_text('general {\n}\n')
        self.assertEqual(lock.fallback_config(env), home)
        home.unlink()
        system = self.root / 'system/hypr/hyprlock.conf'
        system.parent.mkdir(parents=True)
        system.write_text('general {\n}\n')
        env['XDG_CONFIG_DIRS'] = str(system.parent.parent)
        self.assertEqual(lock.fallback_config(env), system)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *rest])
