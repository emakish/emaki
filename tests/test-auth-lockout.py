#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise service-local lockout observation with real PAM and private tallies."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import tempfile

ROOT = Path(__file__).resolve().parent.parent
pam = ctypes.CDLL(ctypes.util.find_library('pam'))
libc = ctypes.CDLL(None)


class Message(ctypes.Structure):
    _fields_ = [('style', ctypes.c_int), ('text', ctypes.c_char_p)]


class Response(ctypes.Structure):
    _fields_ = [('text', ctypes.c_void_p), ('code', ctypes.c_int)]


Callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int,
                           ctypes.POINTER(ctypes.POINTER(Message)),
                           ctypes.POINTER(ctypes.POINTER(Response)), ctypes.c_void_p)


class Conversation(ctypes.Structure):
    _fields_ = [('callback', Callback), ('data', ctypes.c_void_p)]


libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
libc.calloc.restype = ctypes.c_void_p
libc.strdup.argtypes = [ctypes.c_char_p]
libc.strdup.restype = ctypes.c_void_p
pam.pam_start_confdir.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                ctypes.POINTER(Conversation), ctypes.c_char_p,
                                ctypes.POINTER(ctypes.c_void_p)]
pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
pam.pam_setcred.argtypes = [ctypes.c_void_p, ctypes.c_int]
pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]


def authenticate(directory, service, password=None):
    messages = []

    @Callback
    def converse(count, incoming, outgoing, data):
        responses = ctypes.cast(libc.calloc(count, ctypes.sizeof(Response)),
                                ctypes.POINTER(Response))
        outgoing[0] = responses
        for index in range(count):
            message = incoming[index].contents
            if message.style in (1, 2) and password is not None:
                responses[index].text = libc.strdup(password)
            elif message.style in (3, 4):
                messages.append(message.text.decode())
            else:
                return 19
        return 0

    conversation = Conversation(converse, None)
    handle = ctypes.c_void_p()
    user = pwd.getpwuid(os.getuid()).pw_name.encode()
    result = pam.pam_start_confdir(service.encode(), user, ctypes.byref(conversation),
                                 os.fsencode(directory), ctypes.byref(handle))
    assert result == 0, result
    try:
        result = pam.pam_authenticate(handle, 0)
        return result, messages
    finally:
        pam.pam_end(handle, result)


def establish_credentials(directory, service):
    @Callback
    def refuse(count, incoming, outgoing, data):
        return 19

    conversation = Conversation(refuse, None)
    handle = ctypes.c_void_p()
    user = pwd.getpwuid(os.getuid()).pw_name.encode()
    result = pam.pam_start_confdir(service.encode(), user, ctypes.byref(conversation),
                                 os.fsencode(directory), ctypes.byref(handle))
    assert result == 0, result
    try:
        result = pam.pam_setcred(handle, 0x2)  # PAM_ESTABLISH_CRED
        return result
    finally:
        pam.pam_end(handle, result)


def check_greeter_session(directory):
    """greetd starts the greeter's own session with pam_setcred and no pam_authenticate. Run it
    over the host's real system-local-login: pam_exec answers setcred with PAM_IGNORE, and a
    stack that lets that decide the result leaves the machine without a greeter."""
    for source in (Path('/usr/lib/pam.d'), Path('/etc/pam.d')):
        for entry in source.iterdir():
            if entry.is_file():
                shutil.copyfile(entry, directory / entry.name)
    assert (directory / 'system-local-login').is_file(), 'the host has no system-local-login (pambase)'
    inner = (ROOT / 'greetd/pam-auth').read_text()
    inner = inner.replace('/usr/bin/python3 -I -B /usr/bin/emaki-wallet-start --password-account', '/bin/true')
    inner = inner.replace('/usr/bin/python3 -I /usr/bin/emaki-wallet-migrate --pam-unlock', '/bin/true')
    inner = inner.replace('pam_kwallet5.so', 'pam_permit.so')
    (directory / 'emaki-greetd-auth').write_text(inner)
    (directory / 'emaki-greetd').write_text('\n'.join(
        line for line in (ROOT / 'greetd/pam').read_text().splitlines() if line.startswith('auth')) + '\n')
    credentials = establish_credentials(directory, 'emaki-greetd')
    assert credentials == 0, f'greeter session credentials refused ({credentials})'
    print('PASS: the greeter session establishes credentials without a password')


def check_wallet_boundary(directory, tally, config, audit_available):
    """Use the shipped controls with private password and wallet stand-ins."""
    record = directory / 'wallet-token'
    checker = directory / 'password-check'
    checker.write_text('#!/usr/bin/python3\nimport sys\n'
                       'raise SystemExit(0 if sys.stdin.buffer.read().rstrip(b"\\0") '
                       '== b"correct-password" else 1)\n')
    checker.chmod(0o700)
    wallet = directory / 'wallet-record'
    wallet.write_text('#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\n'
                      f'Path({str(record)!r}).write_bytes(sys.stdin.buffer.read().rstrip(b"\\0"))\n')
    wallet.chmod(0o700)
    # Keep pam_exec for the wallet start: its setcred answers PAM_IGNORE, which the
    # greeter's own session (setcred without authentication) must survive.
    starter = directory / 'wallet-start'
    starter.write_text('#!/bin/sh\nexit 0\n')
    starter.chmod(0o700)
    inner = (ROOT / 'greetd/pam-auth').read_text()
    inner = inner.replace('system-local-login', 'private-login')
    inner = inner.replace('/usr/bin/python3 -I -B /usr/bin/emaki-wallet-start --password-account', str(starter))
    inner = inner.replace('pam_kwallet5.so', 'pam_permit.so')
    inner = inner.replace('/usr/bin/python3 -I /usr/bin/emaki-wallet-migrate --pam-unlock', str(wallet))
    (directory / 'emaki-greetd-auth').write_text(inner)
    outer = (ROOT / 'greetd/pam').read_text()
    (directory / 'wallet-observed').write_text('\n'.join(
        line + (f' conf={config}' if 'pam_faillock.so' in line else '')
        for line in outer.splitlines() if line.startswith('auth')) + '\n')
    (directory / 'private-login').write_text(
        f'auth required pam_faillock.so preauth conf={config}\n'
        f'auth [success=1 default=bad] pam_exec.so quiet expose_authtok {checker}\n'
        f'auth [default=die] pam_faillock.so authfail conf={config}\n'
        'auth required pam_permit.so\n')
    for entry in tally.iterdir():
        entry.unlink()
    for attempt in range(1, 3):
        result, _ = authenticate(directory, 'wallet-observed', b'mistyped-password')
        assert result != 0, result
        assert not record.exists(), 'wallet received a refused password'
        records = list(tally.iterdir())
        assert len(records) == 1 and len(records[0].read_bytes()) == attempt * 64, records
    result, _ = authenticate(directory, 'wallet-observed', b'correct-password')
    assert record.read_bytes() == b'correct-password', 'wallet token changed'
    assert result == (0 if audit_available else 4), result
    print('PASS: refused passwords never reach wallet; accepted token forwarded; failures counted')
    if not audit_available:
        print('BLOCKED: accepted-password verdict masked by denied audit socket; rerun unrestricted')


def check():
    audit_available = True
    try:
        with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 9):
            pass
    except PermissionError:
        audit_available = False
        assert os.environ.get('EMAKI_TEST_SANDBOX') == '1', (
            'NETLINK_AUDIT denied: set EMAKI_TEST_SANDBOX=1 for the explicit restricted subset')
    evidence = ROOT / '.cache/evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lockout-', dir=evidence) as temporary:
        directory = Path(temporary)
        tally = directory / 'tallies'
        tally.mkdir()
        config = directory / 'faillock.conf'
        config.write_text(f'dir = {tally}\ndeny = 3\nunlock_time = 120\nfail_interval = 900\nnodelay\nno_log_info\n')
        observer = f'auth [default=ignore] pam_faillock.so preauth conf={config}\n'
        check_wallet_boundary(directory, tally, config, audit_available)
        greeter = directory / "greeter-session"
        greeter.mkdir()
        check_greeter_session(greeter)
        inherited = directory / 'inherited'
        for source, service in [('packaging/emaki-desktop/emaki-lock.pam', 'login'),
                                ('greetd/pam', 'emaki-greetd-auth')]:
            content = (ROOT / source).read_text()
            assert re.search(r'^auth\s+substack\s+' + service + r'$', content, re.M), source
            assert re.search(r'^auth\s+\[default=ignore\]\s+pam_faillock.so preauth$', content, re.M), source
            if source == 'greetd/pam':
                inner = directory / 'emaki-greetd-auth'
                inner.write_text(inner.read_text().replace('private-login', 'inherited'))
                (directory / 'observed').write_text('auth substack emaki-greetd-auth\n' + observer)
            else:
                (directory / 'observed').write_text('auth substack inherited\n' + observer)
            # Reproduce the distribution's short-circuiting failure route, with
            # no password module and a private tally directory only.
            inherited.write_text(f'auth required pam_faillock.so preauth conf={config}\n'
                                 f'auth [default=die] pam_faillock.so authfail conf={config}\n')
            for record in tally.iterdir():
                record.unlink()
            for attempt in range(1, 4):
                result, messages = authenticate(directory, 'observed')
                assert result != 0, (source, attempt, result)
                durations = [message for message in messages if 'left to unlock' in message]
                if not audit_available and attempt == 3 and result == 4 and not durations:
                    # Restricted audit sockets can prevent pam_faillock from
                    # persisting the threshold record. Keep that proof boundary
                    # explicit, then test observation using a private fixture.
                    records = list(tally.iterdir())
                    assert len(records) == 1
                    data = records[0].read_bytes()
                    assert len(data) == 128, (result, len(data))
                    records[0].write_bytes(data + data[-64:])
                    print('SKIP: actual third-failure tally write denied by local audit permissions')
                    result, messages = authenticate(directory, 'observed')
                    durations = [message for message in messages if 'left to unlock' in message]
                assert bool(durations) == (attempt == 3), (source, attempt, messages)
            assert durations and all(message == '(2 minutes left to unlock)' for message in durations), durations
            before = {record.name: record.read_bytes() for record in tally.iterdir()}
            (directory / 'observe-only').write_text('auth required pam_permit.so\n' + observer)
            result, messages = authenticate(directory, 'observe-only')
            assert result == (0 if audit_available else 4) and '(2 minutes left to unlock)' in messages, (result, messages)
            after = {record.name: record.read_bytes() for record in tally.iterdir()}
            assert before == after, 'preauth must never add or clear a failure'
            # A done-style successful inherited stack stays successful even if
            # a stale tally makes the optional observer return a refusal.
            inherited.write_text('auth sufficient pam_permit.so\nauth required pam_deny.so\n')
            result, _ = authenticate(directory, 'observed')
            assert result == (0 if audit_available else 4), result
            inherited.write_text('auth requisite pam_deny.so\nauth required pam_permit.so\n')
            result, _ = authenticate(directory, 'observed')
            assert result != 0, result
            print(f'PASS: {source}: threshold notice and read-only tally')
            if audit_available:
                print('PASS: success and rejection unchanged')
            else:
                print('BLOCKED: real PAM verdicts masked by denied audit socket; rerun outside restricted environment')


if __name__ == '__main__':
    check()
