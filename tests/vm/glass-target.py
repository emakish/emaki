#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport bridge for the installed desktop/glass suite and development defaults."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from suite_target import Target, add_arguments

HERE = Path(__file__).resolve().parent
ARGUMENTS = 'EMAKI_GLASS_TARGET_ARGS'


def target(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return Target.from_args(parser.parse_args(arguments if arguments is not None else
                                              json.loads(os.environ.get(ARGUMENTS, '[]'))))


def login(guest, command):
    command = shlex.join(['python3', '/tmp/guest-login.py', guest.user, command])
    command, data = guest.privileged_command(command, guest.password)
    return subprocess.run(guest.ssh_argv() + [command], input=data.encode()).returncode


def helper(guest, name, arguments):
    if name not in ('guest-strip-click.sh', 'guest-hover-frames.sh', 'guest-open-timing.sh'):
        raise ValueError('unknown glass helper')
    destination = '/tmp/emaki-' + name
    result = subprocess.run(guest.ssh_argv() + ['cat > ' + shlex.quote(destination)],
                            input=(HERE / name).read_bytes())
    if result.returncode:
        return result.returncode
    command = shlex.join(['bash', destination, *arguments])
    if guest.installed:
        command = 'EMAKI_VM_PASSWORD_STDIN=1 ' + command
        return subprocess.run(guest.ssh_argv() + [command],
                              input=(guest.password + '\n').encode()).returncode
    return subprocess.run(guest.ssh_argv() + [command], stdin=subprocess.DEVNULL).returncode


def wallpaper(guest):
    if guest.installed:
        return subprocess.run(guest.ssh_argv() + [
            'cp /usr/share/emaki/wallpaper/ring.png "$HOME/wallpaper.png"']).returncode
    return subprocess.run(guest.ssh_argv() + ['cat > "$HOME/wallpaper.png"'],
                          input=(HERE.parents[1] / 'art/wallpaper/ring.png').read_bytes()).returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        raise ValueError('an action is required')
    action, *arguments = argv
    if action == 'launch':
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('output')
        add_arguments(parser)
        args = parser.parse_args(arguments)
        guest = Target.from_args(args)
        probe = guest.remote('systemd-detect-virt --vm')
        if probe.returncode or probe.stdout.strip() not in ('qemu', 'kvm'):
            raise ValueError('disposable QEMU/KVM required')
        # Store only paths and transport options, never loaded fixture credentials.
        options = []
        for key in ('fixture', 'dir', 'ssh_port', 'key', 'known_hosts', 'transport'):
            value = getattr(args, key)
            if value is not None:
                options.extend(['--' + key.replace('_', '-'), str(value)])
        environment = dict(os.environ, **{ARGUMENTS: json.dumps(options), 'EMAKI_GLASS_RUNNING': '1'})
        return subprocess.run(['bash', str(HERE / 'login.sh'), args.output], env=environment).returncode
    if action == 'shot':
        parser = argparse.ArgumentParser(description='Capture the selected VM host display')
        parser.add_argument('output')
        parser.add_argument('--monitor', action='store_true')
        add_arguments(parser)
        inherited = json.loads(os.environ.get(ARGUMENTS, '[]'))
        args = parser.parse_args(inherited + arguments)
        guest = Target.from_args(args)
        command = guest.shot_argv(args.output)
        if args.monitor:
            command.append('--monitor')
        return subprocess.run(command).returncode
    if action == 'status':
        guest = target(json.loads(os.environ.get(ARGUMENTS, '[]')) + arguments)
        command = ('export XDG_RUNTIME_DIR=/run/user/$(id -u); '
                   'export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; '
                   'export WAYLAND_DISPLAY=$(ls "$XDG_RUNTIME_DIR" | '
                   "grep -E '^wayland-[0-9]+$' | head -1); emaki-shell call dock status")
        return subprocess.run(guest.ssh_argv() + [command]).returncode
    guest = target()
    if action == 'login':
        return login(guest, arguments[0])
    if action == 'helper':
        return helper(guest, arguments[0], arguments[1:])
    if action == 'wallpaper':
        return wallpaper(guest)
    if action == 'remote':
        return subprocess.run(guest.ssh_argv() + arguments).returncode
    raise ValueError('unknown glass action')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise SystemExit('BAD: glass target: ' + str(error))
