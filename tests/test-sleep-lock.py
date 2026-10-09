#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real process contention for sleep bookkeeping, with an external deadline."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import fcntl
import inspect
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub'), str(ROOT / 'upkeep')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_boot import refresh, update


def mutate(name):
    function, old, new = {
        'blocking': (refresh.wait_run_lock, 'fcntl.LOCK_EX | fcntl.LOCK_NB', 'fcntl.LOCK_EX'),
        'no-deadline': (refresh.wait_run_lock, 'if remaining <= 0:', 'if False:'),
        'no-timeout': (refresh.sleep_run_lock, 'timeout=SLEEP_LOCK_TIMEOUT', 'timeout=0'),
    }[name]
    source = inspect.getsource(function)
    assert source.count(old) == 1
    # Keep the polling loop alive after expiry for the removed-deadline mutant.
    if name == 'no-deadline':
        source = source.replace('min(0.1, remaining)', '0.1')
    exec(compile(source.replace(old, new), '<sleep lock mutation>', 'exec'), refresh.__dict__)


def child_run(channel, inherited, directory, command, phase, mutation):
    for fd in inherited:
        os.close(fd)
    output, errors = io.StringIO(), io.StringIO()
    directory = Path(directory)
    state, saved = directory / 'state', directory / 'saved'
    builtin_open, exists, atomic = open, Path.exists, refresh.atomic

    def private_open(path, *args, **kwargs):
        if str(path) == '/run/emaki-boot-refresh.lock':
            path = directory / 'lock'
        return builtin_open(path, *args, **kwargs)

    def installed(path):
        if str(path) in ('/.emaki-install-incomplete', '/run/archiso'):
            return False
        return exists(path)

    def publish(path, data):
        if str(path) == '/efi/EFI/Emaki/trial.env':
            values = dict(line.split('=', 1) for line in data.decode().splitlines()
                          if '=' in line and not line.startswith('#'))
            atomic(state, json.dumps(values))
        else:
            atomic(path, data)

    try:
        with ExitStack() as stack, redirect_stdout(output), redirect_stderr(errors):
            stack.enter_context(patch.object(refresh, 'open', private_open, create=True))
            stack.enter_context(patch.object(refresh.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(Path, 'exists', installed))
            stack.enter_context(patch.object(refresh.subprocess, 'run',
                                             return_value=type('Result', (), {'returncode': 1})()))
            stack.enter_context(patch.object(refresh, 'SLEEP_STATE', saved))
            stack.enter_context(patch.object(refresh, 'LOG_PATH', str(directory / 'log')))
            stack.enter_context(patch.object(refresh, 'regular', side_effect=lambda path: path.read_bytes()))
            stack.enter_context(patch.object(refresh, 'atomic', side_effect=publish))
            stack.enter_context(patch.object(refresh, 'trial_state',
                                             side_effect=lambda: json.loads(state.read_text())))
            stack.enter_context(patch.object(update, 'SLEEP', saved))
            stack.enter_context(patch.object(update, 'supported', return_value={'uuid': 'fixture'}))
            stack.enter_context(patch.object(update, 'record', return_value={'transaction': 'a' * 32}))
            stack.enter_context(patch.object(update, 'environment',
                                             side_effect=lambda: json.loads(state.read_text())))
            stack.enter_context(patch.object(update, 'private_read', side_effect=lambda path: path.read_bytes()))
            stack.enter_context(patch.object(update, 'write_environment',
                                             side_effect=lambda env: atomic(state, json.dumps(env))))
            if mutation:
                mutate(mutation)
            channel.send('ready')
            started = time.monotonic()
            result = (refresh.main(['--sleep-phase', phase]) if command == 'refresh'
                      else update.main(['sleep-' + phase]))
            elapsed = time.monotonic() - started
        channel.send({'result': result, 'elapsed': elapsed,
                      'stdout': output.getvalue(), 'stderr': errors.getvalue()})
    except BaseException:
        channel.send({'error': traceback.format_exc()})
    finally:
        channel.close()


class SleepLock(unittest.TestCase):
    def exercise(self, command, phase, *, release=None, foreign=False, mutation=None):
        with tempfile.TemporaryDirectory(prefix='sleep-lock-') as directory:
            root = Path(directory)
            before = ({'emaki_candidate': ('b' if foreign else 'a') * 32, 'emaki_trial': '1'}
                      if command == 'refresh' else
                      {'emaki_update': ('b' if foreign else 'a') * 32, 'emaki_attempt': '1',
                       'emaki_suppress': '1'})
            original = ({'emaki_candidate': 'a' * 32, 'emaki_trial': '0'} if command == 'refresh'
                        else {'emaki_update': 'a' * 32, 'emaki_attempt': '0'})
            receipt = (original if command == 'refresh' else
                       {'transaction': 'a' * 32, 'env': original})
            (root / 'state').write_text(json.dumps(before))
            (root / 'saved').write_text(json.dumps(receipt))
            (root / 'log').write_text('Persistent log sentinel\n')
            with (root / 'lock').open('a') as lock, (root / 'log').open('r+') as log:
                fcntl.flock(lock, fcntl.LOCK_EX)
                fcntl.flock(log, fcntl.LOCK_EX)
                context = multiprocessing.get_context('fork')
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(target=child_run,
                                          args=(sender, (lock.fileno(), log.fileno()), directory,
                                                command, phase, mutation))
                process.start()
                sender.close()
                try:
                    self.assertTrue(receiver.poll(5), 'child did not initialize')
                    self.assertEqual(receiver.recv(), 'ready')
                    if release is not None:
                        time.sleep(release)
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    if not receiver.poll(5):
                        result = {'watchdog': True}
                    else:
                        result = receiver.recv()
                finally:
                    if process.is_alive():
                        process.join(0.1)
                    if process.is_alive():
                        process.kill()
                    process.join(2)
                    receiver.close()
                self.assertFalse(process.is_alive(), 'child was not reaped')
            result.update(state=json.loads((root / 'state').read_text()), before=before,
                          original=original, saved=(root / 'saved').exists(),
                          receipt=json.loads((root / 'saved').read_text()) if (root / 'saved').exists() else None,
                          log=(root / 'log').read_text())
            return result

    def assert_timeout(self, result, phase):
        self.assertNotIn('watchdog', result, 'sleep exceeded the external five-second deadline')
        self.assertNotIn('error', result, result.get('error'))
        self.assertEqual(result['result'], 0)
        self.assertGreaterEqual(result['elapsed'], 1.8)
        self.assertLessEqual(result['elapsed'], 3.5)
        self.assertIn(phase, result['stderr'])
        self.assertIn('skipped', result['stderr'])
        self.assertIn('busy', result['stderr'])
        self.assertEqual(result['state'], result['before'])
        self.assertFalse(result['saved'])
        self.assertEqual(result['log'], 'Persistent log sentinel\n')

    def test_both_commands_and_phases_have_a_two_second_budget(self):
        self.assertEqual(refresh.SLEEP_LOCK_TIMEOUT, 2.0)
        for command in ('refresh', 'update'):
            for phase in ('pre', 'post'):
                with self.subTest(command=command, phase=phase):
                    result = self.exercise(command, phase)
                    self.assert_timeout(result, phase)
                    # The journal names which bookkeeping was skipped.
                    label = 'Boot loader trial' if command == 'refresh' else 'Update boot protection'
                    self.assertIn(label + ': sleep ' + phase, result['stderr'])

    def test_early_release_runs_real_bookkeeping_without_waiting_for_file_log(self):
        for command in ('refresh', 'update'):
            for phase in ('pre', 'post'):
                with self.subTest(command=command, phase=phase):
                    result = self.exercise(command, phase, release=0.2)
                    self.assertNotIn('watchdog', result)
                    self.assertNotIn('error', result, result.get('error'))
                    self.assertEqual(result['result'], 0)
                    self.assertGreaterEqual(result['elapsed'], 0.15)
                    self.assertLess(result['elapsed'], 1.5)
                    self.assertNotIn('skipped', result['stderr'])
                    self.assertEqual(result['log'], 'Persistent log sentinel\n')
                    if phase == 'post':
                        self.assertEqual(result['state'], result['original'])
                        self.assertFalse(result['saved'])
                    else:
                        expected = (result['before'] if command == 'refresh' else
                                    {'transaction': 'a' * 32, 'env': result['before']})
                        self.assertEqual(result['receipt'], expected)

    def test_early_release_never_restores_another_generation_or_transaction(self):
        for command in ('refresh', 'update'):
            with self.subTest(command=command):
                result = self.exercise(command, 'post', release=0.2, foreign=True)
                self.assertNotIn('watchdog', result)
                self.assertNotIn('error', result, result.get('error'))
                self.assertEqual(result['result'], 0)
                self.assertEqual(result['state'], result['before'])
                self.assertFalse(result['saved'])

    def test_blocking_and_missing_timeout_mutants_fail_the_same_oracle(self):
        for mutation in ('blocking', 'no-deadline', 'no-timeout'):
            with self.subTest(mutation=mutation):
                result = self.exercise('refresh', 'post', mutation=mutation)
                self.assertNotIn('error', result, result.get('error'))
                with self.assertRaises(AssertionError):
                    self.assert_timeout(result, 'post')


if __name__ == '__main__':
    unittest.main()
