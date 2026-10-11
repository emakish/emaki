#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive acceptance captures, run INSIDE a disposable graphical VM.

Usage: python3 tests/polkit-shots.py --guest --output .cache/evidence/a8/vm
The operator enters passwords only in the shell card. This script never reads,
transports, stores or logs a password. Start from an unprivileged guest account;
remove any retained authorization by logging out and back in before the run.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time


def checked(command):
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=15)


def wait_status(path, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text().strip())
        time.sleep(.1)
    raise RuntimeError('The requesting application has not returned before the acceptance timeout')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', action='store_true', help='Confirm this is a disposable VM guest')
    parser.add_argument('--output', type=Path, default=Path('.cache/evidence/a8/vm'))
    parser.add_argument('--step-seconds', type=int, default=20, help='Time to enter the wrong password before its capture')
    parser.add_argument('--terminal', default=os.environ.get('EMAKI_TERMINAL', 'foot'), help='Terminal executable supporting -e')
    args = parser.parse_args()
    if args.step_seconds < 10:
        parser.error('--step-seconds must be at least 10')
    if not args.guest:
        parser.error('--guest is required; this script must never drive the host desktop')
    if os.geteuid() == 0:
        parser.error('Run as the unprivileged account in the guest graphical session')
    virtualization = subprocess.run(['systemd-detect-virt', '--vm'], text=True, capture_output=True, timeout=5)
    if virtualization.returncode != 0 or virtualization.stdout.strip() in ('', 'none'):
        parser.error('A virtual-machine guest was not detected; refusing to drive this session')
    if not os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('NIRI_SOCKET'):
        parser.error('Run inside the guest Emaki Wayland session')
    for program in (args.terminal, 'pkexec', 'pkcheck', 'true', 'grim', 'niri'):
        if not shutil.which(program):
            parser.error(f'Required guest program missing: {program}')
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence = {'virtualization': virtualization.stdout.strip(), 'captures': [], 'outcomes': {}}

    def capture(name):
        layers = checked(['niri', 'msg', '-j', 'layers']).stdout
        (args.output / f'{name}-layers.json').write_text(layers)
        checked(['grim', str(args.output / f'{name}.png')])
        evidence['captures'].append(name)
        return 'emaki-auth-prompt' in layers

    with tempfile.TemporaryDirectory(prefix='emaki-polkit-shots-') as temporary:
        terminal_processes = []

        def launch(name):
            status = Path(temporary) / (name + '.status')
            # Each capture must exercise a new decision in this disposable guest.
            checked(['pkcheck', '--revoke-temp'])
            # Only the application exit code is written; its authentication stays
            # entirely between polkit and the shell's native agent.
            command = 'pkexec --disable-internal-agent true; code=$?; printf "%s\\n" "$code" > ' + shlex.quote(str(status))
            terminal_processes.append(subprocess.Popen([args.terminal, '-e', 'sh', '-c', command],
                                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            return status

        try:
            print('The exclusive keyboard prevents answering this terminal while the card is open.')
            print('Sequence: wait for the first bell (initial screenshot), then enter a WRONG password.')
            print(f'After {args.step_seconds} seconds the second bell asks you to inspect the retry error; then enter the CORRECT password.')
            input('Press Enter when ready. Passwords go only into the shell card. ')
            success = launch('success')
            time.sleep(3)
            assert capture('01-prompt'), 'Authentication layer is absent; verify the agent and retained authorization'
            print('\aInitial screenshot taken. Enter the wrong password now.', flush=True)
            time.sleep(args.step_seconds)
            # A revealed password may still be present: never capture after typing.
            assert not success.exists(), 'Request returned before a correct password'
            print('\aCheck that the retry error is visible. Enter the correct password now.', flush=True)
            evidence['outcomes']['correct_password'] = wait_status(success)
            assert evidence['outcomes']['correct_password'] == 0, 'Correct password did not authorize pkexec true'
            evidence['outcomes']['retry_error_observed'] = input('Was the wrong-password error visible before retrying? Type yes: ').strip().lower() == 'yes'
            assert evidence['outcomes']['retry_error_observed'], 'Wrong-password feedback was not confirmed'
            capture('03-authorized')
            print('Second request: wait for the bell after its screenshot, then press Escape in the password field.')
            input('Press Enter to launch the Escape check. ')
            cancelled = launch('escape')
            time.sleep(3)
            assert capture('04-before-escape'), 'Second prompt absent; start a fresh session if authorization was retained'
            print('\aScreenshot taken. Press Escape in the password field now.', flush=True)
            evidence['outcomes']['escape'] = wait_status(cancelled)
            assert evidence['outcomes']['escape'] != 0, 'Escape unexpectedly authorized pkexec true'
            time.sleep(.5)
            capture('05-escape-notice')
            (args.output / 'results.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print(f'Four captures, retry observation and caller exit statuses saved in {args.output}')
        finally:
            for process in terminal_processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)


if __name__ == '__main__':
    main()
