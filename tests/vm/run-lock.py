#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run installed lock acceptance in a disposable, logged-in QEMU guest.

Only test helpers and QML fixtures are staged. Credentials travel over stdin.
Use --scope-only to check the real client's service-scope isolation alone.
"""
import argparse
import base64
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

from suite_target import Target, add_arguments

ROOT = Path(__file__).resolve().parents[2]
FILES = ('tests/vm/check-lock.py', 'tests/vm/check-lock-scope.py',
         'tests/vm/guest-keys.py', 'tests/vm/guest-frames.sh',
         'tests/fixtures/ClockTest.qml', 'tests/fixtures/SystemFixture.qml')
DISCOVER = r'''
import json, os, pathlib, pwd, stat, subprocess
assert subprocess.check_output(['systemd-detect-virt','--vm'], text=True).strip() in ('qemu','kvm'), 'disposable QEMU guest required'
user = pwd.getpwuid(os.getuid())
compositors = []
for proc in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        if proc.stat().st_uid != user.pw_uid:
            continue
        executable = (proc/'cmdline').read_bytes().split(b'\0', 1)[0].decode()
        if pathlib.Path(executable).name not in ('niri', 'niri-emaki'):
            continue
        compositors.append(proc)
    except (OSError, ValueError):
        continue
assert len(compositors) == 1, 'expected exactly one owned niri session'
runtime = '/run/user/' + str(user.pw_uid)
assert pathlib.Path(runtime).stat().st_uid == user.pw_uid, 'runtime directory owner mismatch'
def owned_sockets(pattern):
    found = []
    for path in pathlib.Path(runtime).glob(pattern):
        info = path.lstat()
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == user.pw_uid:
            found.append(path)
    return found
sockets = owned_sockets('niri.*.sock')
displays = owned_sockets('wayland-*')
assert len(sockets) == len(displays) == 1, 'expected unique owned niri and Wayland sockets'
assert sockets[0].name == 'niri.' + displays[0].name + '.' + compositors[0].name + '.sock', 'compositor and display sockets do not match'
environment = dict(XDG_RUNTIME_DIR=runtime, NIRI_SOCKET=str(sockets[0]), WAYLAND_DISPLAY=displays[0].name,
                   DBUS_SESSION_BUS_ADDRESS='unix:path='+runtime+'/bus', HOME=user.pw_dir, USER=user.pw_name)
print(json.dumps(environment))
'''



def checked(target, command, **kwargs):
    result = target.remote(command, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr or 'guest command failed')
    return result.stdout


def run_suite(target, output, *, scope_only=False, two_outputs=False):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('output must be an empty directory: ' + str(output))
    environment = json.loads(checked(target, 'python3 -c ' + shlex.quote(DISCOVER)))
    if environment['USER'] != target.user:
        raise RuntimeError('transport account does not match fixture account')
    stage = checked(target, 'mktemp -d /tmp/emaki-lock-suite.XXXXXXXX').strip()
    if not stage.startswith('/tmp/emaki-lock-suite.') or any(c.isspace() for c in stage):
        raise RuntimeError('invalid guest staging directory')
    output.mkdir(parents=True, exist_ok=True)
    for relative in FILES:
        destination = stage + '/' + relative
        checked(target, 'mkdir -p ' + shlex.quote(str(Path(destination).parent))
                + ' && base64 -d > ' + shlex.quote(destination),
                data=base64.b64encode((ROOT / relative).read_bytes()).decode())
    suite = 'check-lock-scope.py' if scope_only else 'check-lock.py'
    argv = ['env', *[key + '=' + value for key, value in environment.items()],
            'python3', stage + '/tests/vm/' + suite, '--user', target.user,
            '--credentials-stdin', '--output', stage + '/evidence']
    if two_outputs:
        argv.append('--two-outputs')
    result = None
    try:
        result = target.remote(shlex.join(argv), data=json.dumps({'password': target.password}), timeout=900)
        (output / 'suite.log').write_text(result.stdout + result.stderr)
    finally:
        # Stream large frame archives directly to evidence storage, never to /tmp.
        with (output / 'guest-evidence.tar').open('wb') as stream:
            evidence = subprocess.run([*target.ssh_argv(), 'tar -C ' + shlex.quote(stage)
                                       + ' -cf - evidence'], stdout=stream, stderr=subprocess.PIPE,
                                      timeout=120)
        if evidence.returncode:
            (output / 'retrieval-error.log').write_bytes(evidence.stderr)
    if result.returncode == 0 and evidence.returncode == 0:
        checked(target, 'rm -rf -- ' + shlex.quote(stage))
    else:
        print('Guest evidence retained at ' + stage, file=sys.stderr)
    return result.returncode or evidence.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence' / ('lock-' + time.strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--scope-only', action='store_true')
    parser.add_argument('--two-outputs', action='store_true')
    args = parser.parse_args()
    if args.scope_only and args.two_outputs:
        parser.error('--two-outputs requires the full lock suite')
    return run_suite(Target.from_args(args), args.output, scope_only=args.scope_only, two_outputs=args.two_outputs)


if __name__ == '__main__':
    raise SystemExit(main())
