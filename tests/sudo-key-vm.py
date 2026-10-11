#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive disposable graphical VM acceptance, never a host sudo probe.

Run twice, in clean login sessions before and after installing the optional package:
  python3 tests/sudo-key-vm.py --guest --package absent --marker PATH --output /tmp/sudo-absent
  python3 tests/sudo-key-vm.py --guest --package present --marker PATH --output /tmp/sudo-present
Add --turn-case with --package present for the separate turn-count acceptance.
Its temporary requester executable is a fixture, not an installed assistant.
Use --self-test on a build host (mocks only; does not launch sudo).

Passwords belong only in the authentication card or sudo's terminal prompt. This
script never requests, records or transports credentials. Manual observations are
required: a successful command alone cannot prove that authentication was silent.
The same detached parent survives every no-terminal request, including revocation.
Ordinary repeat probes preserve sudo's stock five-minute cache. Key probes explicitly
invalidate that parent's timestamp first, so a cached authorization cannot masquerade
as a working key. Separate probes prove revocation leaves sudo's own cache intact.
Initial password and stopped-service baselines also clear their own timestamps.
Run with the guest's stock sudo timestamp defaults; local overrides change expectations.
"""
import argparse
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


class Refused(RuntimeError):
    pass


def guest_guard(explicit):
    """Complete every guard before any desktop operation or sudo invocation."""
    if not explicit:
        raise Refused('--guest is required; refusing a host authentication probe')
    if os.geteuid() == 0:
        raise Refused('Run as an unprivileged guest account')
    try:
        result = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True,
                                text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused('Could not identify a virtual-machine guest') from error
    if result.returncode or result.stdout.strip() in ('', 'none'):
        raise Refused('A virtual-machine guest was not detected')
    if not os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('NIRI_SOCKET'):
        raise Refused('Run inside the guest Emaki graphical session')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Refused('Run from an interactive guest terminal')
    return result.stdout.strip()


def confirm(message, timeout):
    print(message + '\nType yes to confirm (never type a password here): ', end='', flush=True)
    if not select.select([sys.stdin], [], [], timeout)[0]:
        raise Refused('Manual confirmation timed out; observation is not proven')
    if sys.stdin.readline().strip().lower() != 'yes':
        raise Refused('Observation was not confirmed; acceptance is not proven')


def worker(connection, timeout):
    """Persistent no-controlling-terminal parent; pipe carries only control/status."""
    os.setsid()
    with open(os.devnull, 'r+b', buffering=0) as null:
        for descriptor in (0, 1, 2):
            os.dup2(null.fileno(), descriptor)
    while True:
        request = connection.recv()
        if request == 'stop':
            return
        if isinstance(request, dict) and request.get('report') in ('turn-start', 'turn-end'):
            result = subprocess.run(['emaki-sudo', request['report'], request['token'], '--json'],
                                    capture_output=True, text=True, timeout=5)
            connection.send({'exit_code': result.returncode, 'stdout': result.stdout})
            continue
        if request not in ('run', 'fresh'):
            raise RuntimeError('Unknown probe request')
        try:
            if request == 'fresh':
                subprocess.run(['sudo', '-k'], check=True, timeout=5)
            result = subprocess.run(['sudo', 'true'], timeout=timeout)
            connection.send({'exit_code': result.returncode, 'parent_pid': os.getpid()})
        except subprocess.TimeoutExpired:
            connection.send({'timeout': True, 'parent_pid': os.getpid()})


def prompt_visible():
    result = subprocess.run(['niri', 'msg', '-j', 'layers'], check=True,
                            text=True, capture_output=True, timeout=5)
    return 'emaki-auth-prompt' in result.stdout


def key_status():
    result = subprocess.run(['emaki-sudo', 'status', '--json'], check=True,
                            text=True, capture_output=True, timeout=5)
    return json.loads(result.stdout)


def require_key(status, owner, present, session=False):
    keys = [key for key in status['keys'] if key['ownerPid'] == owner]
    if bool(keys) != present:
        raise Refused('Recorded key state does not match the expected parent authorization')
    if present:
        key = keys[0]
        if key['session'] != session:
            raise Refused('Recorded key lifetime is not the selected lifetime')
        remaining = key['remainingSeconds']
        if (session and remaining != -1) or (not session and not 0 < remaining <= 900):
            raise Refused('Recorded key expiry does not match the selected lifetime')


def observe(poll, timeout):
    """Sample prompt visibility until caller exits; an empty sample is not proof."""
    seen = False
    samples = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = prompt_visible() or seen
        samples += 1
        outcome = poll()
        if outcome is not None:
            return {'prompt_observed': seen, 'layer_samples': samples, **outcome}
        time.sleep(.1)
    raise Refused('Request timed out; authentication outcome is not proven')


def validate(outcome, expected_prompt, expected_success=True):
    if outcome.get('timeout') or 'exit_code' not in outcome:
        raise Refused('Plain sudo true did not finish')
    if (outcome['exit_code'] == 0) != expected_success:
        raise Refused('Plain sudo true had an unexpected authentication result')
    if outcome['prompt_observed'] != expected_prompt:
        raise Refused('Authentication layer observation did not match the required behavior')


def base_repeat_probe(run_step):
    run_step('without-package-repeat', 'Plain sudo from the SAME parent must complete '
             'silently within the renewing five-minute sudo timestamp window. No key '
             'controls or panel indicator may appear.', False)


def wait_for_lock(timeout):
    session = os.environ.get('XDG_SESSION_ID')
    if not session:
        raise Refused('XDG_SESSION_ID is required to verify the locked-session probe')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(['loginctl', 'show-session', session, '-p', 'LockedHint', '--value'],
                                check=True, capture_output=True, text=True, timeout=5)
        if result.stdout.strip() == 'yes':
            return
        time.sleep(.1)
    raise Refused('Session never reported locked; locked-key behavior is not proven')


def require_turn_key(status, owner, remaining):
    keys = [key for key in status['keys'] if key['ownerPid'] == owner]
    if remaining == 0:
        if keys:
            raise Refused('Exhausted or revoked turn key is still present')
        return
    if len(keys) != 1 or keys[0].get('kind') != 'turns':
        raise Refused('Expected one turn key for the fixture requester')
    if keys[0].get('remainingTurns') != remaining:
        raise Refused('Turn count did not match the expected remaining replies')
    if not 0 < keys[0]['remainingSeconds'] <= 4 * 60 * 60:
        raise Refused('Turn grant did not expose its four-hour hard cap')


def turn_case(args, evidence):
    """Guest-only synthetic process-tree test; no personal agent configuration."""
    token = 'vm-turn-session'
    evidence['not_proven'].append('Elapsed four-hour turn cap and actual installed assistant hooks are not exercised.')
    with tempfile.TemporaryDirectory(prefix='emaki-turn-vm-') as directory:
        executable = Path(directory) / 'claude'
        # A real executable basename lets the root service recognize the fixture.
        # argv[0] spoofing or a symlink would not change the kernel executable path.
        shutil.copy2(sys.executable, executable)
        connection, child = multiprocessing.get_context('fork').Pipe()
        process = subprocess.Popen([str(executable), str(Path(__file__).resolve()),
                                    '--guest', '--turn-worker', str(child.fileno()),
                                    '--timeout', str(args.timeout)], pass_fds=(child.fileno(),))
        child.close()
        evidence['turn_fixture_pid'] = process.pid
        try:
            def report(event):
                connection.send({'report': event, 'token': token})
                if not connection.poll(10):
                    raise Refused('Turn fixture did not answer its report request')
                outcome = connection.recv()
                if outcome['exit_code']:
                    raise Refused('Same-tree turn report was rejected: ' + outcome['stdout'])
                return outcome

            def grant(name):
                report('turn-start')
                confirm('The fixture will request sudo. Select turns, type 7 and choose '
                        'Allow with sudo key. Keep the card open for two seconds before entering '
                        'the password. Confirm readiness.', args.timeout)
                connection.send('fresh')
                step = {'name': name, 'timestamp_invalidated': True}
                evidence['steps'].append(step)
                step.update(observe(lambda: connection.recv() if connection.poll() else None,
                                    args.timeout + 10))
                validate(step, True)
                step['status_after'] = key_status()
                require_turn_key(step['status_after'], process.pid, 7)
                confirm('Confirm the panel indicator shows 7 turns left.', args.timeout)

            grant('turn-grant-seven')
            foreign = subprocess.run(['emaki-sudo', 'turn-end', token, '--json'],
                                     capture_output=True, text=True, timeout=5)
            evidence['foreign_turn_report'] = {'exit_code': foreign.returncode, 'stdout': foreign.stdout}
            if foreign.returncode == 0:
                raise Refused('A report outside the granted process tree was accepted')
            require_turn_key(key_status(), process.pid, 7)
            for remaining in range(6, -1, -1):
                report('turn-end')
                status = key_status()
                require_turn_key(status, process.pid, remaining)
                evidence['steps'].append({'name': 'turn-end', 'remaining': remaining,
                                          'status_after': status})
                report('turn-end')
                require_turn_key(key_status(), process.pid, remaining)
                confirm('Confirm the panel shows ' + (str(remaining) + ' turns left.' if remaining
                        else 'no key after turn exhaustion.'), args.timeout)
                if remaining:
                    report('turn-start')
                    connection.send('fresh')
                    step = {'name': 'turn-key-silent', 'timestamp_invalidated': True,
                            'remaining': remaining}
                    evidence['steps'].append(step)
                    step.update(observe(lambda: connection.recv() if connection.poll() else None,
                                        args.timeout + 10))
                    validate(step, False)
                    require_turn_key(key_status(), process.pid, remaining)
            grant('turn-exhaustion-reprompts')
            confirm('Revoke the turn key using the panel. Confirm its indicator disappears.', args.timeout)
            require_turn_key(key_status(), process.pid, 0)
            evidence['turn_revoke_status'] = key_status()
            grant('turn-revoke-reprompts')
            connection.send('stop')
            process.wait(timeout=5)
            time.sleep(2)
            evidence['turn_exit_status'] = key_status()
            require_turn_key(evidence['turn_exit_status'], process.pid, 0)
            confirm('Confirm the turn key disappeared after its requester exited.', args.timeout)
            evidence['result'] = 'listed turn acceptance steps passed; remaining checks not proven'
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--guest', action='store_true')
    parser.add_argument('--package', choices=('absent', 'present'))
    parser.add_argument('--marker', type=Path, help='Installed sudo-key marker from the generated platform paths')
    parser.add_argument('--output', type=Path, default=Path('.cache/evidence/a9e/vm'))
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--turn-case', action='store_true')
    parser.add_argument('--turn-worker', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProbeTests)
        return 0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1
    if args.timeout < 10 or args.timeout > 600:
        parser.error('--timeout must be between 10 and 600 seconds')
    if args.turn_worker is not None:
        guest_guard(args.guest)
        worker(Connection(args.turn_worker), args.timeout)
        return 0
    evidence = {'result': 'not proven', 'steps': [], 'not_proven': [
        'Elapsed typed time expiry and the four-hour turn cap are not exercised.',
        'Layer polling can miss a brief prompt; manual observations are required.',
        'Missing module, missing shell and root login need separate guest checks.',
        'Refusal of new prompts and grants while locked needs a separate guest check.',
    ]}
    process = None
    terminal = None
    guarded = False
    try:
        evidence['virtualization'] = guest_guard(args.guest)
        guarded = True
        if args.package is None:
            raise Refused('--package absent or --package present is required')
        if args.marker is None or not args.marker.is_absolute():
            raise Refused('--marker (absolute installed path) is required')
        for executable in ('sudo', 'true', 'niri', 'emaki-sudo'):
            if not shutil.which(executable):
                raise Refused(f'Required guest program missing: {executable}')
        installed = args.marker.is_file()
        evidence['package'] = args.package
        evidence['marker_present'] = installed
        if installed != (args.package == 'present'):
            raise Refused('Package expectation does not match installed marker')
        evidence['initial_status'] = key_status()
        if evidence['initial_status']['keyAvailable'] != installed:
            raise Refused('Broker package availability disagrees with marker detection')
        if evidence['initial_status']['keys']:
            raise Refused('Existing keys must be revoked before acceptance begins')
        if args.turn_case:
            if not installed:
                raise Refused('--turn-case requires --package present')
            turn_case(args, evidence)
            return 0
        if installed:
            confirm('Confirm installation showed the warning screen, offered optional snapguard on '
                    'btrfs, and disclosed the lack of rollback when snapguard is absent. Complete '
                    'that screen before continuing.', args.timeout)
        confirm('Start in a clean guest login with no retained authorization. Confirm the package '
                'is in the requested state, sudo has stock timestamp defaults, and terminal sudo '
                'has no retained timestamp.', args.timeout)
        args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = multiprocessing.get_context('fork')
        connection, child = context.Pipe()
        process = context.Process(target=worker, args=(child, args.timeout))
        process.start()
        child.close()
        evidence['persistent_parent_pid'] = process.pid

        def run_step(name, instructions, expected_prompt, expected_success=True, *, fresh=False, locked=False):
            confirm(instructions + ' Wait at least two seconds with any card open before entering '
                    'the password. Confirm readiness.', args.timeout)
            step = {'name': name, 'status_before': key_status()}
            evidence['steps'].append(step)
            if locked:
                wait_for_lock(args.timeout)
                step['locked_before_request'] = True
            step['timestamp_invalidated'] = fresh
            connection.send('fresh' if fresh else 'run')
            step.update(observe(lambda: connection.recv() if connection.poll() else None, args.timeout + 10))
            if locked:
                wait_for_lock(1)
                step['locked_after_request'] = True
            step['status_after'] = key_status()
            validate(step, expected_prompt, expected_success)
            confirm('Confirm the command completed and ' + ('the expected authentication card appeared '
                    'with correct request details' if expected_prompt else 'no authentication window appeared'), args.timeout)
            step['manual_observation_confirmed'] = True

        run_step('wrong-password', 'Enter one deliberately incorrect password in the card. Confirm '
                 'it is rejected; if a retry card appears, press Esc. Never enter a password here.',
                 True, False, fresh=True)
        confirm('Confirm the incorrect password was rejected before any retry cancellation.', args.timeout)
        if key_status()['keys']:
            raise Refused('Incorrect password created a key')
        run_step('escape', 'Press Esc when the card opens. The command must fail.', True, False)
        if key_status()['keys']:
            raise Refused('Cancellation created a key')
        if installed:
            run_step('grant-typed-minutes', 'The detached request must open the card. Expand Sudo key, '
                     'select For a while, type 17 minutes and choose Allow with sudo key. Check the request '
                     'label, process chain, command, working folder, user and unavailable/redacted fields.', True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, True)
            confirm('Confirm the panel indicator and JSON status show a live 17-minute key.', args.timeout)
            run_step('same-parent-key-silent', 'The next plain sudo from the SAME parent must finish '
                     'silently after its sudo timestamp is explicitly invalidated.', False, fresh=True)
            confirm('Press Super+Ctrl+Esc now. Confirm the panel key indicator disappeared.', args.timeout)
            evidence['shortcut_revoke_status'] = key_status()
            if evidence['shortcut_revoke_status']['keys']:
                raise Refused('Shortcut did not revoke every key')
            run_step('shortcut-preserves-sudo-cache', 'Within five minutes of the last command, '
                     'the SAME parent must still finish silently after revocation using sudo memory.', False)
            run_step('same-parent-after-shortcut', 'After explicit timestamp invalidation the SAME '
                     'parent must ask again. Leave Sudo key disabled and enter the password.', True, fresh=True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, False)
            run_step('grant-for-panel-revoke', 'Type 17 minutes again after timestamp invalidation.',
                     True, fresh=True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, True)
            confirm('Click the panel key revoke action once. Confirm its indicator disappeared.', args.timeout)
            evidence['panel_revoke_status'] = key_status()
            require_key(evidence['panel_revoke_status'], process.pid, False)
            run_step('panel-preserves-sudo-cache', 'Within five minutes of the last command, '
                     'the SAME parent must still finish silently after panel revocation using sudo memory.', False)
            run_step('same-parent-after-panel', 'After explicit timestamp invalidation the SAME parent '
                     'must ask again. Select The whole session and enter the password.', True, fresh=True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, True, session=True)
            run_step('key-while-locked', 'After confirming readiness, lock the guest with Super+L. '
                     'Keep it locked for at least ten seconds, then unlock. The probe waits for '
                     'logind to confirm the lock, invalidates the timestamp, and runs plain sudo '
                     'while locked. It must succeed without a card.', False, fresh=True, locked=True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, True, session=True)
            confirm('Restart only the guest shell using systemctl --user restart emaki-shell.service. '
                    'After it returns, confirm the same key indicator reappeared.', args.timeout)
            require_key(key_status(), process.pid, True, session=True)
            run_step('key-after-shell-restart', 'The retained session key must answer after shell '
                     'restart and timestamp invalidation without a card.', False, fresh=True)
            confirm('Manually suspend and resume this guest, then unlock it. Confirm the same key '
                    'indicator remains. Never suspend the host.', args.timeout)
            require_key(key_status(), process.pid, True, session=True)
            run_step('key-after-suspend', 'The retained session key must answer after guest suspend '
                     'and timestamp invalidation without a card.', False, fresh=True)
            require_key(evidence['steps'][-1]['status_after'], process.pid, True, session=True)
        else:
            run_step('without-package-first', 'The card must open with no Sudo key controls. '
                     'Enter the password only in the card.', True)
            base_repeat_probe(run_step)
            if any(step['status_after']['keys'] for step in evidence['steps']):
                raise Refused('A key exists without the optional package')
            confirm('Confirm AI Assistants settings also contain no Sudo key row.', args.timeout)
        confirm('Next plain sudo runs in THIS terminal. It must ask for its password here and never '
                'open a window. This baseline explicitly clears its timestamp first. Enter the password '
                'only after sudo displays its terminal prompt.', args.timeout)
        subprocess.run(['sudo', '-k'], check=True, timeout=5)
        terminal = subprocess.Popen(['sudo', 'true'])
        terminal_step = {'name': 'terminal-password', 'status_before': key_status(),
                         'timestamp_precondition': {'command': ['sudo', '-k'], 'exit_code': 0}}
        evidence['steps'].append(terminal_step)
        terminal_step.update(observe(lambda: None if terminal.poll() is None else
                                    {'exit_code': terminal.returncode}, args.timeout))
        validate(terminal_step, False)
        confirm('Confirm sudo asked for its password in this terminal and no window appeared.', args.timeout)
        terminal_step['manual_observation_confirmed'] = True
        confirm('Repeat terminal sudo within five minutes. It must now finish silently using '
                'sudo memory, with no terminal password or window.', args.timeout)
        terminal = subprocess.Popen(['sudo', 'true'])
        repeat = {'name': 'terminal-cached-repeat', 'timestamp_invalidated': False}
        evidence['steps'].append(repeat)
        repeat.update(observe(lambda: None if terminal.poll() is None else
                              {'exit_code': terminal.returncode}, args.timeout))
        validate(repeat, False)
        confirm('Confirm terminal sudo completed without asking for a password anywhere.', args.timeout)
        repeat['manual_observation_confirmed'] = True
        connection.send('stop')
        process.join(timeout=5)
        if process.is_alive():
            raise Refused('Persistent parent did not exit')
        time.sleep(2)
        evidence['after_agent_exit_status'] = key_status()
        require_key(evidence['after_agent_exit_status'], process.pid, False)
        if installed:
            confirm('Confirm the session key disappeared from JSON status and the panel after '
                    'the persistent parent exited.', args.timeout)
        try:
            confirm('In a SEPARATE guest root console, stop emaki-sudo.service. Keep that root '
                    'console open for recovery; restore afterward with systemctl start emaki-sudo.service. '
                    'The probe will clear this terminal timestamp with sudo -k. Confirm readiness. '
                    'Do not stop the service on the host.', args.timeout)
            stopped = subprocess.run(['systemctl', 'is-active', 'emaki-sudo.service'],
                                     capture_output=True, text=True, timeout=5)
            if stopped.stdout.strip() != 'inactive':
                raise Refused('Root service must be inactive for the terminal fallback check')
            subprocess.run(['sudo', '-k'], check=True, timeout=5)
            terminal = subprocess.Popen(['sudo', 'true'])
            fallback = {'name': 'service-stopped-terminal-password',
                        'timestamp_precondition': {'command': ['sudo', '-k'], 'exit_code': 0}}
            evidence['steps'].append(fallback)
            fallback.update(observe(lambda: None if terminal.poll() is None else
                                    {'exit_code': terminal.returncode}, args.timeout))
            validate(fallback, False)
            confirm('Confirm sudo asked in this terminal and succeeded with the service stopped.', args.timeout)
            fallback['manual_observation_confirmed'] = True
        finally:
            print('RESTORE NOW in the separate guest root console: systemctl start emaki-sudo.service',
                  flush=True)
        confirm('Restore emaki-sudo.service in the separate guest root console and confirm it is active.', args.timeout)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'emaki-sudo.service'], check=True, timeout=5)
        evidence['service_restored'] = True
        evidence['result'] = 'listed acceptance steps passed; remaining checks not proven'
    except (Refused, OSError, subprocess.SubprocessError, ValueError, EOFError) as error:
        evidence['failure'] = str(error)
        print(f'Refused or not proven: {error}', file=sys.stderr)
    finally:
        if terminal is not None and terminal.poll() is None:
            terminal.terminate()
            try:
                terminal.wait(timeout=3)
            except subprocess.TimeoutExpired:
                terminal.kill()
                terminal.wait(timeout=3)
        if process is not None and process.is_alive():
            # The worker owns a new session; terminate the entire guest probe group.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=3)
        if guarded:
            args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
            (args.output / 'results.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print(f'Acceptance record: {args.output / "results.json"}')
    return 1 if evidence.get('failure') else 0


class ProbeTests(unittest.TestCase):
    def test_explicit_guard_never_runs_command(self):
        with mock.patch.object(subprocess, 'run') as run:
            with self.assertRaises(Refused):
                guest_guard(False)
            run.assert_not_called()

    def test_root_guard_never_runs_command(self):
        with mock.patch.object(os, 'geteuid', return_value=0), mock.patch.object(subprocess, 'run') as run:
            with self.assertRaises(Refused):
                guest_guard(True)
            run.assert_not_called()

    def test_non_vm_refused_after_detection_only(self):
        with mock.patch.object(os, 'geteuid', return_value=1000), mock.patch.object(
                subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, 'none\n')) as run:
            with self.assertRaises(Refused):
                guest_guard(True)
            self.assertEqual(run.call_args.args[0], ['systemd-detect-virt', '--vm'])
            self.assertEqual(run.call_count, 1)

    def test_observe_records_layer(self):
        with mock.patch(__name__ + '.prompt_visible', side_effect=[True, False]), mock.patch.object(time, 'sleep'):
            outcomes = iter([None, {'exit_code': 0}])
            outcome = observe(lambda: next(outcomes), 2)
        self.assertTrue(outcome['prompt_observed'])
        self.assertEqual(outcome['layer_samples'], 2)
        validate(outcome, True)
        with self.assertRaises(Refused):
            validate(outcome, False)

    def test_success_without_required_prompt_fails(self):
        with self.assertRaises(Refused):
            validate({'exit_code': 0, 'prompt_observed': False}, True)

    def test_timeout_never_passes(self):
        with self.assertRaises(Refused):
            validate({'timeout': True, 'prompt_observed': True}, True)

    def test_key_state_checks_owner_expiry_and_exit(self):
        status = {'keys': [{'ownerPid': 123, 'session': False, 'remainingSeconds': 899}]}
        require_key(status, 123, True)
        require_key(status, 124, False)
        with self.assertRaises(Refused):
            require_key(status, 123, False)
        with self.assertRaises(Refused):
            require_key(status, 123, True, session=True)
        status['keys'][0]['remainingSeconds'] = 0
        with self.assertRaises(Refused):
            require_key(status, 123, True)

    def test_turn_key_checks_count_kind_owner_and_cap(self):
        key = {'ownerPid': 123, 'kind': 'turns', 'remainingTurns': 3,
               'remainingSeconds': 14400}
        status = {'keys': [key]}
        require_turn_key(status, 123, 3)
        require_turn_key(status, 124, 0)
        for field, value in (('remainingTurns', 2), ('kind', 'time'),
                             ('remainingSeconds', 14401), ('remainingSeconds', 0)):
            with self.subTest(field=field, value=value):
                changed = {'keys': [{**key, field: value}]}
                with self.assertRaises(Refused):
                    require_turn_key(changed, 123, 3)
        with self.assertRaises(Refused):
            require_turn_key(status, 123, 0)
        require_turn_key({'keys': []}, 123, 0)

    def test_turn_worker_reports_without_sudo(self):
        connection = mock.Mock()
        connection.recv.side_effect = [{'report': 'turn-start', 'token': 'fixture'},
                                       {'report': 'turn-end', 'token': 'fixture'}, 'stop']
        with mock.patch.object(os, 'setsid'), mock.patch.object(os, 'dup2'), mock.patch.object(
                subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}')) as run:
            worker(connection, 10)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['emaki-sudo', 'turn-start', 'fixture', '--json'],
                          ['emaki-sudo', 'turn-end', 'fixture', '--json']])
        self.assertEqual(connection.send.call_count, 2)

    def test_worker_uses_plain_sudo_and_same_parent(self):
        connection = mock.Mock()
        connection.recv.side_effect = ['run', 'run', 'stop']
        with mock.patch.object(os, 'setsid'), mock.patch.object(os, 'dup2'), mock.patch.object(
                subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            worker(connection, 10)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['sudo', 'true'], ['sudo', 'true']])
        self.assertEqual(connection.send.call_args_list[0], connection.send.call_args_list[1])

    def test_base_repeat_observes_cache_without_invalidation(self):
        run_step = mock.Mock()
        base_repeat_probe(run_step)
        self.assertEqual(run_step.call_count, 1)
        self.assertFalse(run_step.call_args.args[2])
        self.assertEqual(run_step.call_args.kwargs, {})

    def test_fresh_key_probe_invalidates_only_before_selected_request(self):
        connection = mock.Mock()
        connection.recv.side_effect = ['fresh', 'run', 'stop']
        with mock.patch.object(os, 'setsid'), mock.patch.object(os, 'dup2'), mock.patch.object(
                subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            worker(connection, 10)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['sudo', '-k'], ['sudo', 'true'], ['sudo', 'true']])

    def test_locked_probe_requires_observed_lock(self):
        with mock.patch.dict(os.environ, {'XDG_SESSION_ID': 'test'}), mock.patch.object(
                subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'yes\n')) as run:
            wait_for_lock(1)
        self.assertEqual(run.call_args.args[0],
                         ['loginctl', 'show-session', 'test', '-p', 'LockedHint', '--value'])
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(subprocess, 'run') as run:
            with self.assertRaises(Refused):
                wait_for_lock(1)
            run.assert_not_called()

    def test_wrong_password_and_escape_require_failure(self):
        validate({'exit_code': 1, 'prompt_observed': True}, True, False)
        with self.assertRaises(Refused):
            validate({'exit_code': 0, 'prompt_observed': True}, True, False)

    def test_graphical_guard_does_not_require_x11(self):
        with mock.patch.object(os, 'geteuid', return_value=1000), mock.patch.dict(
                os.environ, {'WAYLAND_DISPLAY': 'wayland-test', 'NIRI_SOCKET': 'test'}, clear=True), \
                mock.patch.object(sys.stdin, 'isatty', return_value=True), \
                mock.patch.object(sys.stdout, 'isatty', return_value=True), \
                mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'kvm\n')):
            self.assertEqual(guest_guard(True), 'kvm')


if __name__ == '__main__':
    raise SystemExit(main())
