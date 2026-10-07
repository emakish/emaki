#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account and transport shared by disposable desktop acceptance suites."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def add_arguments(parser):
    parser.add_argument('--fixture', type=Path, help='release installation plan JSON (account and password)')
    parser.add_argument('--dir', type=Path, help='VM transport directory')
    parser.add_argument('--ssh-port', type=int)
    parser.add_argument('--key', type=Path, help='SSH private key')
    parser.add_argument('--known-hosts', type=Path, help='SSH known_hosts file')
    parser.add_argument('--transport', choices=('ssh', 'iso'),
                        help='default: iso with --fixture, development ssh otherwise')


class Target:
    def __init__(self, *, fixture=None, directory=None, port=None, key=None,
                 known_hosts=None, transport=None):
        self.fixture = Path(fixture).resolve() if fixture else None
        self.installed = self.fixture is not None
        data = json.loads(self.fixture.read_text()) if self.fixture else {}
        config = data.get('config', data)
        account = config['user'] if self.fixture else {'login': 'arch', 'password': 'arch'}
        self.user = account['login']
        self.password = account['password']
        if not isinstance(self.user, str) or not re.fullmatch('[a-z_][a-z0-9_-]*', self.user):
            raise ValueError('invalid fixture account')
        if not isinstance(self.password, str) or any(c in self.password for c in '\r\n\0'):
            raise ValueError('fixture password must be a single line without NUL')
        # Existing installation plans need no transport section. Optional values
        # bind a saved plan to a disposable VM; command-line values take priority.
        settings = data.get('transport', {})
        self.transport = transport or settings.get('kind') or ('iso' if self.fixture else 'ssh')
        if self.transport not in ('ssh', 'iso'):
            raise ValueError('invalid guest transport')
        iso = self.transport == 'iso'
        prefix = 'EMAKI_ISO_' if iso else 'EMAKI_'
        default_vm = Path.home() / 'VMs' / ('iso-vm' if iso else 'emaki-vm')
        chosen = directory or settings.get('dir') or os.environ.get(prefix + 'VM_DIR')
        # A release fixture must name its VM; falling back to a development VM would test the wrong machine.
        if self.fixture and not chosen:
            raise ValueError('a fixture needs the VM directory (--dir, transport.dir or ' + prefix + 'VM_DIR)')
        self.vm = Path(chosen or default_vm).resolve()
        port_value = port if port is not None else settings.get('ssh_port')
        if port_value is None:
            port_value = os.environ.get(prefix + 'SSH_PORT')
        if port_value is None and iso and (self.vm / 'ssh-port').is_file():
            port_value = (self.vm / 'ssh-port').read_text().strip()
        self.port = int(port_value if port_value is not None else (2223 if iso else 2222))
        if not 1 <= self.port <= 65535:
            raise ValueError('invalid SSH port')
        default_key = Path.home() / 'VMs/emaki-vm/id_vm' if iso else self.vm / 'id_vm'
        self.key = Path(key or settings.get('key') or os.environ.get(prefix + 'SSH_KEY', default_key)).resolve()
        supplied_hosts = known_hosts or settings.get('known_hosts') or os.environ.get(prefix + 'KNOWN_HOSTS')
        self.known_hosts = Path(supplied_hosts).resolve() if supplied_hosts else None

    @classmethod
    def from_args(cls, args):
        return cls(fixture=args.fixture, directory=args.dir, port=args.ssh_port,
                   key=args.key, known_hosts=args.known_hosts, transport=args.transport)

    def ssh_argv(self):
        if self.transport == 'iso':
            args = [str(HERE / 'iso-ssh.sh'), '--dir', str(self.vm), '--ssh-port', str(self.port),
                    '--user', self.user, '--key', str(self.key)]
            if self.known_hosts:
                args += ['--known-hosts', str(self.known_hosts)]
            return args + ['--']
        return ['ssh', '-p', str(self.port), '-i', str(self.key),
                '-o', 'UserKnownHostsFile=' + str(self.known_hosts or self.vm / 'known_hosts'),
                '-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=15', self.user + '@127.0.0.1']

    def privileged_command(self, command, data=''):
        data = data if data is not None else ''
        if self.installed:
            # Authenticate separately: cached or NOPASSWD sudo must not leave
            # the authentication line in the keyboard/login helper's stdin.
            prefix = ('IFS= read -r emaki_suite_password && '
                      'printf \'%s\\n\' "$emaki_suite_password" | sudo -S -p "" -v && '
                      'unset emaki_suite_password && sudo -n -- ')
            # A shell may replace itself with its last command. Keep it alive
            # until sudo exits so validation and execution share timestamp scope.
            suffix = '; emaki_suite_status=$?; exit "$emaki_suite_status"'
            return prefix + command + suffix, self.password + '\n' + data
        return 'sudo -n -- ' + command, data

    def remote(self, command, *, data=None, timeout=35, privileged=False):
        if privileged:
            command, data = self.privileged_command(command, data)
        return subprocess.run(self.ssh_argv() + [command], input=data, text=True,
                              capture_output=True, timeout=timeout)

    def shot_argv(self, output):
        return [sys.executable, str(HERE / 'iso-shot.py'), '--dir', str(self.vm), str(output)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument('--privileged', action='store_true', help='authenticate sudo using fixture stdin')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        target = Target.from_args(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('a remote command is required after --')
    # ssh accepts one shell command, or several arguments joined by spaces.
    remote = ' '.join(command)
    if args.privileged:
        remote, prefix = target.privileged_command(remote)
        result = subprocess.run(target.ssh_argv() + [remote],
                                input=prefix.encode() + sys.stdin.buffer.read())
        return result.returncode
    os.execvp(target.ssh_argv()[0], target.ssh_argv() + [remote])


if __name__ == '__main__':
    raise SystemExit(main())
