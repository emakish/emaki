# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Observe live login sessions without trusting user-written readiness markers.

The caller owns the acceptance deadline and requires an unchanged observation
for its stability window. greetd opens PAM before executing its command, so a
logind greeter session alone is insufficient: its compositor and UI must exist.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import time

PROC = Path('/proc')
GREETER = '/usr/share/emaki/shell/greeter.qml'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_last_rejection = None


def unavailable(reason):
    """Journal a changed rejection reason without repeating it every poll."""
    global _last_rejection
    if reason != _last_rejection:
        print('Boot observation not usable: ' + reason, file=sys.stderr, flush=True)
        _last_rejection = reason
    return None


def read(path, limit=65536):
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Observation exceeds its limit')
    return value.decode()


def process(pid, uid=None, scope=None):
    """Return a running process identity; verify kernel ownership and scope."""
    if not str(pid).isdecimal() or int(pid) <= 0:
        return None
    path = PROC / str(pid)
    try:
        fields = read(path / 'stat').rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X'):
            return None
        if uid is not None:
            owners = next(line.split()[1:] for line in read(path / 'status').splitlines()
                          if line.startswith('Uid:'))
            if len(owners) != 4 or any(value != str(uid) for value in owners):
                return None
        if scope is not None:
            groups = [line.split(':', 2)[2] for line in read(path / 'cgroup').splitlines()]
            if not any(scope in group.split('/') for group in groups):
                return None
        return str(pid) + ':' + fields[19]
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def greeter_processes(uid, scope):
    compositor, interface = [], []
    for path in PROC.iterdir():
        if not path.name.isdecimal():
            continue
        identity = process(path.name, uid, scope)
        if identity is None:
            continue
        try:
            executable = Path(os.readlink(path / 'exe')).name
            args = read(path / 'cmdline').rstrip('\0').split('\0')
            if executable in ('niri', 'niri-emaki'):
                compositor.append(identity)
            elif executable == 'regreet':
                interface.append(identity)
            elif executable in ('qs', 'quickshell') and any(
                    args[index:index + 2] == ['-p', GREETER]
                    for index in range(len(args) - 1)):
                interface.append(identity)
        except (OSError, ValueError):
            continue
    if compositor and interface:
        return ','.join(sorted(compositor + interface))
    return None


def observe():
    """Return a boot/session/process identity, or None when evidence is absent.

    Commands share a five-second budget and each gets at most one second.
    Greeter sessions are classed by greetd before pam_open_session; their type
    can remain tty despite running a Wayland compositor. User sessions must
    explicitly be local active graphical sessions.
    """
    global _last_rejection
    deadline = time.monotonic() + 5

    def command(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Observation deadline expired')
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, check=True, timeout=min(1, remaining),
                                env={'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'})
        if len(result.stdout) > 65536:
            raise ValueError('Command observation exceeds its limit')
        return result.stdout

    def properties(*args):
        return dict(line.split('=', 1) for line in command(*args).splitlines() if '=' in line)

    try:
        boot = read(PROC / 'sys/kernel/random/boot_id').strip()
        if not UUID.fullmatch(boot):
            return unavailable('invalid boot identity')
        service = properties('/usr/bin/systemctl', 'show', 'greetd.service',
                             '--property=ActiveState,SubState,MainPID,InvocationID')
        daemon = process(service.get('MainPID', ''))
        greeter_active = (service.get('ActiveState') == 'active'
                          and service.get('SubState') == 'running' and daemon is not None)
        rows = command('/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager').splitlines()
        if len(rows) > 64:
            return unavailable('session count exceeds observation limit')
        reasons = set()
        for row in rows:
            session = row.split()[0]
            if not re.fullmatch(r'[a-zA-Z0-9_-]+', session):
                continue
            state = properties('/usr/bin/loginctl', 'show-session', session,
                               *('--property=' + name for name in (
                                   'Id', 'Active', 'State', 'Remote', 'Class', 'Type',
                                   'User', 'Name', 'Leader', 'Scope')))
            scope = 'session-' + session + '.scope'
            if (state.get('Id') != session or state.get('Active') != 'yes'
                    or state.get('State') != 'active' or state.get('Remote') != 'no'
                    or state.get('Scope') != scope or not state.get('User', '').isdecimal()):
                reasons.add('session properties are absent or not local and active')
                continue
            # PAM's session leader can remain root while its children drop privileges.
            leader = process(state.get('Leader', ''), scope=scope)
            if leader is None:
                reasons.add('session leader is absent or outside its scope')
                continue
            identity = None
            if state.get('Class') == 'user' and state.get('Type') in ('wayland', 'x11'):
                identity = 'user:' + leader
            elif greeter_active and state.get('Class') == 'greeter' and state.get('Name') == 'greeter':
                children = greeter_processes(state['User'], scope)
                if children:
                    identity = 'greeter:' + daemon + ':' + service.get('InvocationID', '') + ':' + children
                else:
                    reasons.add('greeter compositor or interface is missing from its session scope')
            elif state.get('Class') == 'greeter' and not greeter_active:
                reasons.add('greetd is not running with a live main process')
            else:
                reasons.add('session is not a graphical user or supported greeter')
            if identity:
                if read(PROC / 'sys/kernel/random/boot_id').strip() == boot:
                    _last_rejection = None
                    return boot + ':' + session + ':' + identity
                reasons.add('boot identity changed during observation')
        return unavailable('; '.join(sorted(reasons)) or 'no eligible login sessions')
    except subprocess.TimeoutExpired:
        return unavailable('session or service query timed out')
    except subprocess.CalledProcessError:
        return unavailable('session or service query failed')
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return unavailable('observation data unavailable, malformed, or over budget')
