#!/usr/bin/env python3
"""Disposable QEMU ONLY, root: operations for host check-greeter.py.

No password handling, arbitrary commands, host sockets, or greetd restarts.
Both software QEMU and the project's KVM acceleration pass the VM guard.
Exact uid/argv/start-time checks constrain process signalling to the greeter.
"""
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import time

QML = Path('/usr/share/emaki/shell/greeter.qml')
DESKTOP = Path('/usr/share/wayland-sessions/niri-emaki.desktop')
STATE = Path('/var/lib/emaki-greeter/state/state.json')
BACKUP = Path('/run/c9-greeter-fixture')


def run(argv, **kwargs):
    return subprocess.run(argv, text=True, capture_output=True, timeout=15, **kwargs)


def processes(uid):
    result = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != uid:
                continue
            argv = (path / 'cmdline').read_bytes().split(b'\0')
            argv = [word.decode() for word in argv if word]
            start = (path / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            environment = dict(item.split('=', 1) for item in (path / 'environ').read_bytes().decode().split('\0') if '=' in item)
            status = (path / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            result.append(dict(pid=int(path.name), start=start, argv=argv, environment=environment, status=status))
        except (OSError, ValueError, UnicodeError):
            continue
    return result


def executable(process):
    return Path(process['argv'][0]).name if process['argv'] else ''


def quickshell(process):
    argv = process['argv']
    return executable(process) in ('qs', 'quickshell') and any(argv[i:i + 2] == ['-p', str(QML)] for i in range(len(argv) - 1))


def greeter_processes():
    return processes(pwd.getpwnam('greeter').pw_uid)


def socket_identity(path):
    try:
        info = path.lstat()
        return info.st_dev, info.st_ino
    except FileNotFoundError:
        return None


def niri_action(username):
    account = pwd.getpwnam(username)
    before = [item for item in processes(account.pw_uid) if executable(item) in ('niri', 'niri-emaki')]
    assert len(before) == 1, 'expected exactly one compositor process for ' + username
    # Only this uid's runtime directory is eligible, never inherited SSH state.
    sockets = sorted(Path(f'/run/user/{account.pw_uid}').glob('niri.*.sock'))
    sockets = [path for path in sockets if stat.S_ISSOCK(path.lstat().st_mode) and path.lstat().st_uid == account.pw_uid]
    assert len(sockets) == 1, 'expected exactly one compositor socket for ' + username
    old_socket = socket_identity(sockets[0])
    env = {'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': f'/run/user/{account.pw_uid}', 'NIRI_SOCKET': str(sockets[0])}
    result = run(['sudo', '-n', '-u', username, 'env', *[key + '=' + value for key, value in env.items()], 'niri', 'msg', 'action', 'quit', '--skip-confirmation'])
    # niri can close IPC before acknowledging its own quit. EOF is successful
    # only when both that exact process identity and its socket disappear.
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        alive = any(item['pid'] == before[0]['pid'] and item['start'] == before[0]['start']
                    for item in processes(account.pw_uid) if executable(item) in ('niri', 'niri-emaki'))
        if not alive and socket_identity(sockets[0]) != old_socket:
            return dict(exited=True, acknowledgement=result.returncode,
                        detail='quit acknowledged' if result.returncode == 0 else 'IPC closed during confirmed compositor exit')
        time.sleep(.1)
    raise AssertionError('compositor did not exit after quit: ' + (result.stderr.strip() or str(result.returncode)))


def backup(target, name):
    saved = BACKUP / name
    assert not saved.exists(), 'fixture backup already exists; restore it first: ' + str(saved)
    info = target.lstat()
    assert stat.S_ISREG(info.st_mode)
    saved.write_bytes(target.read_bytes())
    (BACKUP / (name + '.meta')).write_text(json.dumps([info.st_mode & 0o777, info.st_uid, info.st_gid]))


def restore(target, name):
    saved = BACKUP / name
    if not saved.exists():
        return False
    mode, uid, gid = json.loads((BACKUP / (name + '.meta')).read_text())
    temporary = target.with_name(target.name + '.c9-restore')
    temporary.write_bytes(saved.read_bytes())
    temporary.chmod(mode)
    os.chown(temporary, uid, gid)
    temporary.replace(target)
    saved.unlink()
    (BACKUP / (name + '.meta')).unlink()
    return True


def greetd_identity():
    """Read-only daemon identity; a healthy replacement must not hide an abort."""
    result = run(['systemctl', 'show', 'greetd.service', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'InvocationID'])
    if result.returncode:
        return None
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    try:
        pid, restarts = int(fields['MainPID']), int(fields['NRestarts'])
        invocation = fields['InvocationID']
        if pid <= 1 or restarts < 0 or len(invocation) != 32 or any(ch not in '0123456789abcdef' for ch in invocation):
            return None
        return dict(MainPID=pid, NRestarts=restarts, InvocationID=invocation)
    except (KeyError, ValueError):
        return None


def observe():
    current = greeter_processes()
    arch = pwd.getpwnam('arch')
    services = {}
    for unit in ('niri.service', 'niri-emaki.service', 'emaki-shell.service'):
        result = run(['sudo', '-n', '-u', 'arch', 'env', f'XDG_RUNTIME_DIR=/run/user/{arch.pw_uid}', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{arch.pw_uid}/bus', 'systemctl', '--user', 'is-active', unit])
        services[unit] = result.stdout.strip()
    try:
        memory = json.loads(STATE.read_text())
    except (OSError, ValueError):
        memory = {}
    journal = run(['journalctl', '-b', '-u', 'greetd', '--no-pager', '-n', '100']).stdout
    user_processes = processes(arch.pw_uid)
    try:
        tty2_text = Path('/dev/vcs2').read_bytes().decode('utf-8', errors='replace')
    except OSError:
        tty2_text = ''
    return dict(qs=[item['pid'] for item in current if quickshell(item)],
                qs_stopped=[item['pid'] for item in current if quickshell(item) and item['status'] in ('T', 't')],
                qs_crash_handler_disabled=all(item['environment'].get('QS_DISABLE_CRASH_HANDLER') == '1' for item in current if quickshell(item)),
                greeter_compositors=[item['pid'] for item in current if executable(item) in ('niri', 'niri-emaki')],
                user_compositors=[item['pid'] for item in user_processes if executable(item) in ('niri', 'niri-emaki')],
                greetd=run(['systemctl', 'is-active', 'greetd.service']).stdout.strip(),
                greetd_identity=greetd_identity(),
                tty2_text=tty2_text,
                regreet=[item['pid'] for item in current if executable(item) == 'regreet'],
                services=services, memory=memory, journal=journal,
                active_tty=Path('/sys/class/tty/tty0/active').read_text().strip(),
                getty=run(['systemctl', 'is-active', 'getty@tty2.service']).stdout.strip())


def dispatch(request):
    op = request['op']
    if op == 'inspect':
        return observe()
    if op == 'journal-mark':
        return {'since': '@' + format(time.time(), '.6f')}
    if op == 'journal-since':
        since = request['since']
        assert isinstance(since, str) and since.startswith('@') and len(since) < 40
        assert float(since[1:]) > 0
        result = run(['journalctl', '-b', '-u', 'greetd', '--no-pager', '--output=short-precise', '--since=' + since])
        assert result.returncode == 0, result.stderr
        return {'journal': result.stdout}
    if op == 'restore-fixtures':
        restored = [name for target, name in ((QML, 'greeter.qml'), (DESKTOP, 'niri-emaki.desktop')) if restore(target, name)]
        resumed = []
        for item in greeter_processes():
            if quickshell(item) and item['status'] in ('T', 't'):
                dispatch(dict(op='kill', pid=item['pid'], signal='SIGCONT'))
                resumed.append(item['pid'])
        return {'restored': restored, 'resumed': resumed}
    if op == 'kill':
        pid = request['pid']
        sig = {'SIGKILL': signal.SIGKILL, 'SIGSTOP': signal.SIGSTOP, 'SIGCONT': signal.SIGCONT, 'SIGSEGV': signal.SIGSEGV, 'SIGABRT': signal.SIGABRT}[request['signal']]
        before = next(item for item in greeter_processes() if item['pid'] == pid and quickshell(item))
        handle = os.pidfd_open(pid)
        try:
            after = next(item for item in greeter_processes() if item['pid'] == pid and quickshell(item))
            assert before['start'] == after['start'], 'process identity changed'
            signal.pidfd_send_signal(handle, sig)
        finally:
            os.close(handle)
    elif op == 'logout':
        return niri_action('arch')
    elif op == 'restart-greeter':
        assert not any(executable(item) in ('niri', 'niri-emaki') for item in processes(pwd.getpwnam('arch').pw_uid)), 'user session still active'
        return niri_action('greeter')
    elif op == 'break-qml':
        backup(QML, 'greeter.qml')
        QML.write_text('This is deliberately invalid QML for the disposable C9 VM.\n')
    elif op == 'restore-qml':
        restore(QML, 'greeter.qml')
    elif op == 'break-session':
        backup(DESKTOP, 'niri-emaki.desktop')
        DESKTOP.write_text('[Desktop Entry]\nName=niri (Emaki)\nType=Application\nExec=/usr/bin/false\n')
    elif op == 'restore-session':
        restore(DESKTOP, 'niri-emaki.desktop')
    elif op == 'select-emaki':
        greeter = pwd.getpwnam('greeter')
        data = dict(version=1, last_user='arch', seeded=True, users={'arch': {'last_session': 'niri-emaki.desktop', 'failed_session': ''}}, pending_launch=None)
        temporary = STATE.with_name('state.c9.json')
        temporary.write_text(json.dumps(data))
        temporary.chmod(0o600)
        os.chown(temporary, greeter.pw_uid, greeter.pw_gid)
        temporary.replace(STATE)
    else:
        raise ValueError('unknown VM fixture operation')
    return {'ok': True}


def main():
    assert os.getuid() == 0, 'VM fixture requires root'
    virtualization = run(['systemd-detect-virt', '--vm'])
    assert virtualization.returncode == 0 and virtualization.stdout.strip() in ('qemu', 'kvm'), 'requires disposable QEMU/KVM'
    BACKUP.mkdir(mode=0o700, exist_ok=True)
    info = BACKUP.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o077 == 0
    print(json.dumps(dispatch(json.loads(sys.argv[1]))))


if __name__ == '__main__':
    main()
