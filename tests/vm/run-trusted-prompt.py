#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive acceptance of the installed trusted prompt in a disposable QEMU VM.

Run from the repository on the host. Requires an unlocked guest, the real shell
started through spawn-trusted-shell-at-startup, and the test-only binding from
trusted-prompt-bind.kdl included by the disposable guest configuration.
Pass an explicit guest command which opens the real shell's authentication UI.
No synthetic trusted fixture is accepted as the shell. The operator supplies
physical VM-console input and visual observations; this is not unattended QA.
Example:
    python3 tests/vm/run-trusted-prompt.py --dir /tmp/test-vm \
        --trigger-shell 'THE_REAL_GUEST_PROMPT_TRIGGER' --output /tmp/prompt-proof
Only disposable non-secret text may be typed into the prompt. Never enter or
record a real password. Unlock the guest through its console when instructed.
"""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import time

from suite_target import Target, add_arguments

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('lock_suite', HERE / 'run-lock.py')
lock_suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock_suite)
DISCOVER = lock_suite.DISCOVER

IPC_CHECK = r'''
import json, os, socket
s = socket.socket(socket.AF_UNIX)
s.settimeout(5)
s.connect(os.environ['NIRI_SOCKET'])
s.sendall(b'{"Action":"ShowTrustedPromptMark"}\n')
r = s.makefile('rb').readline(65536)
reply = json.loads(r)
assert 'Err' in reply and 'parsing request' in reply['Err'], repr(reply)
print(json.dumps(reply))
'''


def require(result):
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or 'guest command failed')
    return result.stdout


def attest(evidence, name, instruction):
    print('\n' + instruction)
    answer = input('Type PASS only if every observation holds (anything else fails): ').strip()
    evidence['observations'][name] = answer == 'PASS'
    if answer != 'PASS':
        raise RuntimeError('operator acceptance failed: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    parser.add_argument('--trigger-shell', required=True,
                        help='guest command opening the real shell prompt; no credentials')
    parser.add_argument('--lock-command', default='emaki-lock', help='guest session lock command')
    parser.add_argument('--output', type=Path, required=True, help='new/empty host evidence directory')
    parser.add_argument('--expected-compositor', default='26.04 (v26.04+emaki.15)',
                        help='required running compositor version, not installed package metadata')
    parser.add_argument('--refusal-log', default='denied untrusted emaki-auth-prompt layer surface',
                        help='exact compositor refusal log substring')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error('interactive VM-console observation is required')
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        parser.error('--output must be a new or empty directory')
    target = Target.from_args(args)
    # DISCOVER refuses non-QEMU sessions before any guest command is started.
    environment = json.loads(require(target.remote('python3 -c ' + shlex.quote(DISCOVER))))
    if environment['USER'] != target.user:
        raise RuntimeError('guest account mismatch')
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = {'automated': {}, 'observations': {}, 'complete': False}
    stage = require(target.remote('mktemp -d /tmp/emaki-trusted-prompt.XXXXXXXX')).strip()
    if not stage.startswith('/tmp/emaki-trusted-prompt.') or any(c.isspace() for c in stage):
        raise RuntimeError('invalid guest staging directory')
    evidence['guest_evidence'] = stage
    prefix = ['env', '-u', 'WAYLAND_SOCKET', *[k + '=' + v for k, v in environment.items()]]

    def guest(argv, timeout=35):
        return target.remote(shlex.join(prefix + argv), timeout=timeout)

    def trusted_status():
        return json.loads(require(guest(['niri-emaki', 'msg', '--json', 'trusted-shell'])))

    def layers():
        return json.loads(require(guest(['niri', 'msg', '--json', 'layers'])))

    def read_state(name):
        return json.loads(require(guest(['cat', stage + '/' + name + '.json'])))

    def start(command, label):
        # Commands are explicit test input, never inferred from external content.
        script = 'nohup ' + shlex.join(prefix + ['sh', '-c', command]) + ' > /dev/null 2>&1 < /dev/null & echo $!'
        pid = require(target.remote(script)).strip()
        if not pid.isdigit():
            raise RuntimeError('invalid fixture process id')
        return pid

    def prompt_present():
        found = [layer for layer in layers() if layer['namespace'] == 'emaki-auth-prompt']
        return trusted_status()['prompt_active'] and bool(found) and all(layer['layer'] == 'Overlay' and
                                   layer['keyboard_interactivity'] == 'Exclusive' for layer in found)

    def wait_prompt():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if prompt_present():
                return
            time.sleep(.3)
        raise RuntimeError('real shell overlay/exclusive prompt did not appear in compositor layers')

    def deny(label):
        before = require(guest(['date', '+%Y-%m-%d %H:%M:%S'])).strip()
        result = guest(['python3', stage + '/client.py', '--output', stage + '/' + label + '.json'])
        evidence['automated'][label] = json.loads(require(result))
        # Store only the number of matching refusal records, never whole journals.
        journal = require(guest(['journalctl', '--user', '--since', before, '--no-pager', '-o', 'cat']))
        count = sum(args.refusal_log in line for line in journal.splitlines())
        if count != 1:
            raise RuntimeError('expected one compositor refusal log, got ' + str(count))
        evidence['automated'][label]['refusal_log_count'] = count

    def unchanged(before, after, label):
        for key in ('keyboard_enter', 'keyboard_key', 'pointer_enter', 'pointer_button'):
            if after[key] != before[key]:
                raise RuntimeError(label + ': competing overlay received ' + key)
        evidence['automated'][label] = {'before': before, 'after': after}

    competitor = None
    late_competitor = None
    try:
        require(target.remote('base64 -d > ' + shlex.quote(stage + '/client.py'),
                              data=base64.b64encode((HERE / 'trusted-prompt-client.py').read_bytes()).decode()))
        version = json.loads(require(guest(['niri-emaki', 'msg', '--json', 'version'])))
        if version.get('compositor') != args.expected_compositor:
            raise RuntimeError('running compositor does not match the candidate build')
        evidence['automated']['running_version'] = version
        status = trusted_status()
        if not status['configured'] or not status['running']:
            raise RuntimeError('the compositor is not supervising the real shell')
        evidence['automated']['trusted_shell'] = status
        if prompt_present():
            raise RuntimeError('start with no active authentication prompt')
        deny('denial_before_prompt')
        evidence['automated']['ipc_refused'] = json.loads(require(guest(['python3', '-c', IPC_CHECK])))
        competitor = start(shlex.join(['python3', stage + '/client.py', '--namespace',
                                      'emaki-untrusted-test-overlay', '--output',
                                      stage + '/competitor.json', '--seconds', '1800']), 'competitor')
        time.sleep(1)
        baseline = read_state('competitor')
        if not baseline['mapped']:
            raise RuntimeError('ordinary competing overlay failed to map')
        attest(evidence, 'ordinary_overlay_receives_input',
               'In the guest, click the RED test rectangle at top left and type a disposable\n'
               'non-secret letter. Confirm it is visible before the real prompt opens.')
        active = read_state('competitor')
        if active['keyboard_key'] <= baseline['keyboard_key'] or active['pointer_button'] <= baseline['pointer_button']:
            raise RuntimeError('competing overlay did not demonstrate both keyboard and pointer input before prompt')
        start(args.trigger_shell, 'trigger')
        wait_prompt()
        attest(evidence, 'real_shell_prompt',
               'Confirm the actual installed shell authentication UI opened for the requested\n'
               'operation. A synthetic trusted client does not satisfy this check.')
        deny('denial_during_prompt')
        late_competitor = start(shlex.join(['python3', stage + '/client.py', '--namespace',
                                           'emaki-untrusted-late-overlay', '--output',
                                           stage + '/late.json', '--seconds', '1800']), 'late')
        time.sleep(1)
        late_baseline = read_state('late')
        if not late_baseline['mapped'] or any(late_baseline[key] for key in
                                             ('keyboard_enter', 'keyboard_key', 'pointer_enter', 'pointer_button')):
            raise RuntimeError('late competing overlay failed to map or stole keyboard focus')
        baseline = read_state('competitor')
        attest(evidence, 'prompt_input_isolation',
               'Confirm the normal desktop remains visible without dimming, and neither\n'
               'competing overlay covers the card. Type a disposable non-secret sentinel\n'
               'into the prompt; click its controls.\n'
               'Try clicking the competing rectangle and a background application. Confirm\n'
               'only the prompt receives input. Do not submit an authorization or close it.')
        unchanged(baseline, read_state('competitor'), 'prompt_input_isolation')
        unchanged(late_baseline, read_state('late'), 'late_prompt_input_isolation')
        baseline = read_state('competitor')
        attest(evidence, 'physical_proof',
               'Press the fixture-only Mod+Ctrl+F12 through the VM console. Confirm the\n'
               'white three-bar compositor mark appears in the reserved top band outside\n'
               'the prompt buffer against a black backdrop,\n'
               'other clients are hidden during its three-second interval, the actual\n'
               'card remains whole below the band, its keyboard keeps focus, and pointer/\n'
               'touch input waits until expiry. Confirm the normal scene returns afterward.\n'
               'Inspect every output, including the card top edge on the smallest one.\n'
               'A screenshot alone does not prove input isolation or compositor origin.')
        unchanged(baseline, read_state('competitor'), 'proof_input_isolation')
        evidence['automated']['ipc_refused_during_prompt'] = json.loads(require(guest(['python3', '-c', IPC_CHECK])))
        attest(evidence, 'ipc_cannot_show_mark',
               'The direct IPC attempt was rejected. Confirm it did not display the mark.\n'
               'Wait for any previous physical proof interval to finish before continuing.')
        baseline = read_state('competitor')
        start(args.lock_command, 'lock')
        attest(evidence, 'lock_hides_and_blocks_prompt',
               'Confirm the real session lock appeared and the prompt and proof mark are\n'
               'hidden on every output. Press the fixture proof key: the lock must become\n'
               'blank for three seconds, with no mark, and then return still locked.\n'
               'During that curtain, disposable typing and clicks must reach no client.\n'
               'After expiry, input must reach the lock, never the\n'
               'hidden prompt or competing overlay. Keep the guest locked for the next check.')
        unchanged(baseline, read_state('competitor'), 'locked_input_isolation')
        deny('denial_while_locked')
        start(args.trigger_shell, 'locked_trigger')
        attest(evidence, 'locked_request_and_unlock',
               'A second real request was started while locked. Confirm no authentication\n'
               'surface or proof mark appears. Unlock through the VM console (do not log\n'
               'the password); verify previous prompt text was cleared/cancelled and no\n'
               'text typed while locked reached an authentication request. Cancel requests.')
        # Prompt timeout belongs to the real UI, so it must be observed with its trigger.
        start(args.trigger_shell, 'timeout_trigger')
        wait_prompt()
        print('Waiting 125 seconds for the real prompt timeout; leave the VM idle.')
        for remaining in (65, 5, 0):
            time.sleep(60 if remaining else 5)
            print('Timeout observation: ' + str(remaining) + ' seconds remaining', flush=True)
        attest(evidence, 'real_prompt_timeout',
               'Leave this real prompt idle for more than 120 seconds. Confirm it denies\n'
               'the request and closes, without retaining the disposable text. Then press\n'
               'the proof key with no prompt: no mark may appear.')
        if prompt_present():
            raise RuntimeError('prompt remains mapped after reported timeout')
        evidence['complete'] = True
        print('PASS: automatic protocol checks and all recorded VM observations completed')
    finally:
        for pid in (competitor, late_competitor):
            if pid:
                target.remote('kill -- ' + pid)
        (args.output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Evidence: ' + str(args.output / 'acceptance.json'))
        print('Guest fixture evidence retained: ' + stage)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, EOFError, KeyboardInterrupt) as error:
        raise SystemExit('FAIL: ' + str(error))
