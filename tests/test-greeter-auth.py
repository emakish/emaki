#!/usr/bin/env python3
"""Real framed greetd workers + production QML auth/session, isolated offscreen.

No passwords enter IPC/files/logs: fixture actions select a fixed dummy password.
Every attempt owns a fresh socket; cancellation acknowledgements never enter
a later attempt's stream. The restricted subset also tests framing in memory.
"""
from runtime_fixture import runtime_path
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import resource
import shutil
import signal
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
from collections import deque
from unittest.mock import Mock, patch
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
QS = shutil.which('qs')
assert QS, 'qs is required'


_ipc_limit_reported = False


def checked_output(output):
    """Permit only the unavailable optional IPC listener in restricted tests."""
    global _ipc_limit_reported
    if os.environ.get('EMAKI_TEST_SANDBOX') == '1':
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.bind('\0emaki-auth-' + str(os.getpid()))
        except PermissionError:
            if not _ipc_limit_reported:
                print('BLOCKED: Quickshell IPC listener denied; file-transport assertions run, IPC remains unproven')
                _ipc_limit_reported = True
            output = '\n'.join(line for line in output.splitlines()
                               if not line.startswith(' ERROR quickshell.ipc: Failed to start IPC server on path '))
    return output
FIXTURE_MARKER = {"v": 1, "token": "0123456789abcdef0123456789abcdef",
                  "createdMs": 1, "idleMode": "lake", "clock": 2, "introStart": .9}
spec = importlib.util.spec_from_file_location('greetd_server', ROOT / 'tests/fixtures/greetd_server.py')
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


SCRIPTED_TRANSPORT = (ROOT / 'tests/fixtures/greeter/auth_transport.py').read_text()


def core_limit():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@contextmanager
def harness(socket_mode='server'):
    with tempfile.TemporaryDirectory(prefix='ga-', dir=CACHE) as work:
        profile = Path(work)
        for part in ('r', 'cache', 'config', 'data', 'state', 'tmp', 'qml'):
            (profile / part).mkdir(mode=0o700)
        qml = profile / 'qml'
        for name in ('AuthController', 'GreeterAuth', 'LockSession', 'GreeterSession'):
            shutil.copy(ROOT / 'shell' / (name + '.qml'), qml)
        (qml / 'helpers').mkdir()
        shutil.copy(ROOT / 'shell/helpers/greeter-auth.py', qml / 'helpers')
        shutil.copy(ROOT / 'tests/fixtures/greeter/GreeterAuthTest.qml', qml / 'check.qml')
        (qml / 'qmldir').write_text(''.join(f'{name} 1.0 {name}.qml\n' for name in
                                          ('AuthController', 'GreeterAuth', 'LockSession', 'GreeterSession')))
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_STATE_HOME=str(profile / 'state'),
                   HOME=str(profile), TMPDIR=str(profile / 'tmp'), LC_ALL='C',
                   EMAKI_AUTH_FIXTURE=str(profile),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'none-system'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS',
                    'GREETD_SOCK', 'QT_LOGGING_RULES'):
            env.pop(key, None)
        server = None
        if socket_mode == 'server':
            server = server_module.GreetdServer(runtime_path(profile) / 'g.sock')
            env['GREETD_SOCK'] = str(runtime_path(profile) / 'g.sock')
        elif socket_mode.startswith('scripted'):
            env['GREETD_SOCK'] = 'fixture-only'
            (qml / 'helpers/greeter-auth.py').rename(qml / 'helpers/greeter-auth-real.py')
            (qml / 'helpers/greeter-auth.py').write_text(SCRIPTED_TRANSPORT)
            if socket_mode == 'scripted-queued':
                (qml / 'fixture-mode').write_text('queued-verdict')
            if socket_mode == 'scripted-rejection':
                (qml / 'fixture-mode').write_text('reject-once')
        elif socket_mode == 'no-hello':
            env['GREETD_SOCK'] = 'fixture-only'
            (qml / 'helpers/greeter-auth.py').write_text('import time\ntime.sleep(3)\n')
        elif socket_mode == 'absent':
            env['GREETD_SOCK'] = str(profile / 'absent.sock')
        log_path = profile / 'qs.log'
        with log_path.open('w') as log:
            proc = subprocess.Popen([QS, '-p', str(qml / 'check.qml'), '--no-color'],
                                    env=env, stdout=log, stderr=subprocess.STDOUT,
                                    preexec_fn=core_limit)
            serial = 0

            def call(method, *args):
                nonlocal serial
                serial += 1
                tmp = profile / 'request.tmp'
                tmp.write_text(json.dumps(dict(id=serial, method=method, args=args)))
                tmp.replace(profile / 'request.json')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        assert proc.returncode == 0 and method == "action" and args == ("launch",), log_path.read_text()
                        return True
                    try:
                        reply = json.loads((profile / 'reply.json').read_text())
                        if reply['id'] == serial:
                            return reply['value']
                    except (FileNotFoundError, json.JSONDecodeError):
                        pass
                    time.sleep(.005)
                raise AssertionError(('fixture timeout', method, log_path.read_text()))

            def state():
                return call('state')

            def action(name):
                if name == 'wait-verdict':
                    deadline = time.monotonic() + 3
                    while not (qml / 'verdict-held').exists():
                        assert time.monotonic() < deadline, 'verdict barrier not reached'
                        time.sleep(.005)
                    return True
                if name == 'release-verdict':
                    (qml / 'release-verdict').write_text('go')
                    return True
                return call('action', name)

            def wait(predicate, timeout=3):
                deadline = time.monotonic() + timeout
                latest = None
                while time.monotonic() < deadline:
                    latest = state()
                    if predicate(latest):
                        return latest
                    time.sleep(.01)
                raise AssertionError(('state timeout', latest, log_path.read_text()))

            def wait_log(fragment, timeout=3):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    output = log_path.read_text()
                    if fragment in output:
                        return
                    assert proc.poll() is None, output
                    time.sleep(.005)
                raise AssertionError(('log barrier timeout', fragment, log_path.read_text()))

            try:
                wait(lambda value: value['attempt'] == 0)
                yield action, state, wait, server, proc, wait_log
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                if server:
                    server.close()
        # Only the deliberate missing-helper warning is allowed.
        output = checked_output(log_path.read_text())
        assert 'Failed to start IPC server' not in output, output
        assert 'fixture-éЖ' not in output and 'bad\x00input' not in output, output
        for line in output.splitlines():
            if not any(level in line for level in ('ERROR', 'WARN')):
                continue
            assert any(allowed in line for allowed in (
                'Process failed to start, likely because the binary could not be found. Command: QList("/fixture/no-such-auth-helper")',
            )), line


def begin(action, server):
    action('ready')
    assert action('password')
    action('submit')
    assert server.request('create_session')['username'] == 'fixture-user'


def answer(server):
    server.prompt()
    event = server.request('post_auth_message_response')
    assert event['has_response'] and event['response_matches'] and event['response_length'] == 11, event


def session_contract():
    with tempfile.TemporaryDirectory(prefix='gs-', dir=CACHE) as work:
        profile = Path(work)
        for part in ('r', 'cache', 'config', 'data', 'state', 'tmp', 'qml'):
            (profile / part).mkdir(mode=0o700)
        qml = profile / 'qml'
        for name in ('AuthController', 'LockSession', 'GreeterSession'):
            shutil.copy(ROOT / 'shell' / (name + '.qml'), qml)
        shutil.copy(ROOT / 'tests/fixtures/greeter/GreeterSessionTest.qml', qml / 'check.qml')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_STATE_HOME=str(profile / 'state'),
                   HOME=str(profile), TMPDIR=str(profile / 'tmp'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'GREETD_SOCK', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        result = subprocess.run([QS, '-p', str(qml / 'check.qml'), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=5, preexec_fn=core_limit)
        output = checked_output(result.stdout + result.stderr)
        assert result.returncode == 0 and 'GREETER_SESSION_CONTRACT_OK' in output, output
        assert 'Failed to start IPC server' not in output, output
        assert 'ERROR' not in output and 'WARN' not in output, output
    print('PASS: GreeterSession inherits real auth/attempt guards, requests handoff once and melts controls then freezes the plate with input disabled')


def isolated_auth_protocol_checks():
    spec = importlib.util.spec_from_file_location('greeter_auth', ROOT / 'shell/helpers/greeter-auth.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    password = 'fixture-éЖ🔒'
    def frame(value):
        data = json.dumps(value).encode()
        return struct.pack('=i', len(data)) + data
    class Stream:
        def __init__(self, replies):
            self.incoming = bytearray(b''.join(map(frame, replies)))
            self.requests = []
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def settimeout(self, _value): pass
        def connect(self, path): assert path == 'fixture-only'
        def recv(self, count):
            part = bytes(self.incoming[:min(2, count)])
            del self.incoming[:len(part)]
            return part
        def sendall(self, packet):
            size, = struct.unpack('=i', packet[:4])
            assert size == len(packet) - 4
            value = json.loads(packet[4:])
            if value['type'] == 'post_auth_message_response' and value['response'] is not None:
                assert value.pop('response') == password
                value['response_checked'] = True
            self.requests.append(value)
    def run(replies, controls):
        worker.ABANDONED = worker.RETIRED = False
        stream = Stream(replies)
        events = []
        controls = iter(controls)
        def control():
            value = next(controls)
            if value['op'] == 'respond': assert events[-1] == {'event': 'prompt'}
            elif value['op'] == 'launch':
                assert events[-1] == {'event': 'ready'}
                assert not any(item['type'] == 'start_session' for item in stream.requests)
            return value.copy()
        with patch.dict(os.environ, GREETD_SOCK='fixture-only'), patch.object(worker.socket, 'socket', return_value=stream), patch.object(worker, 'command', side_effect=control), patch.object(worker, 'emit', side_effect=lambda event, **fields: events.append(dict(event=event, **fields))):
            code = worker.main()
        assert password not in json.dumps(events, ensure_ascii=False)
        return code, events, stream.requests
    begin = {'op': 'begin', 'user': 'fixture-user', 'attempt': 7}
    response = {'op': 'respond', 'password': password}
    launch = {'op': 'launch', 'cmd': ['/usr/bin/niri-session'], 'env': ['XDG_SESSION_TYPE=wayland']}
    prompt = {'type': 'auth_message', 'auth_message_type': 'secret', 'auth_message': 'Password:'}
    success = {'type': 'success'}
    dead_ack = {'type': 'error', 'error_type': 'error', 'description': 'worker exited'}
    code, events, requests = run([prompt, success, success], [begin, response, launch])
    assert code == 0 and [e['event'] for e in events] == ['hello', 'prompt', 'ready', 'launched']
    assert [r['type'] for r in requests] == ['create_session', 'post_auth_message_response', 'start_session']
    for kind, expected in [('auth_error', 'rejected'), ('error', 'technical')]:
        for ack in (success, dead_ack):
            code, events, requests = run([prompt, {'type': 'error', 'error_type': kind}, ack], [begin, response])
            assert code == 0 and [e['event'] for e in events] == ['hello', 'prompt', expected, 'cleaned']
            assert events[-1] == dict(event='cleaned', reason=expected, ok=ack == success, cleared=True)
            assert requests[-1] == {'type': 'cancel_session'}
    code, events, requests = run([{'type': 'auth_message', 'auth_message_type': 'error', 'auth_message': 'Account locked'}, {'type': 'error', 'error_type': 'auth_error'}, dead_ack], [begin])
    assert events[1] == {'event': 'message', 'message': 'Account locked', 'error': True}
    assert requests[1]['response'] is None
    for replies in ([dict(prompt, auth_message_type='visible'), success], [prompt, prompt, success]):
        code, events, requests = run(replies, [begin, response])
        assert code == 0 and events[-2]['event'] == 'unsupported' and requests[-1]['type'] == 'cancel_session'
        assert sum(r.get('response_checked', False) for r in requests) <= 1
    code, events, requests = run([success], [begin])
    assert code == 1 and events[-1] == {'event': 'transport'} and len(requests) == 1
    # Regression negative control: the old close-before-verdict ordering MUST fail.
    fake = server_module.GreetdServer.__new__(server_module.GreetdServer)
    fake.connected = threading.Event(); fake.connected.set()
    fake.lock = threading.RLock(); fake.auth_connection = 0; fake.closed = {0}; fake.panicked = False
    fake.configuring = dict(user='fixture-user', ready=False, alive=True)
    fake.pending = {0: deque([('post_auth_message_response', fake.configuring)])}
    fake.connections = {0: Stream([])}; fake.scheduled = False
    try:
        fake.error('auth_error')
    except AssertionError as error:
        assert 'greetd abort' in str(error) and fake.panicked
    else:
        raise AssertionError('fake server accepted the old greetd-aborting ordering')
    fake.panicked = False
    fake.configuring = dict(user='fixture-user', ready=False, alive=True)
    fake.pending = {0: deque([('create_session', fake.configuring)])}
    try:
        fake.error('error', 'live worker')
    except BrokenPipeError:
        assert fake.configuring is None and not fake.panicked
    else:
        raise AssertionError('dead-client live-worker automatic cancellation was not modelled')
    print('PASS: real worker native framing/single secret/launch gate; same-connection cancel success or exited-worker error; dead-client panic and live-worker auto-cancel controls')


def worker_lifetime_checks():
    # The socket-free transport below blocks a real outstanding native frame;
    # control pipes, supervisor fork, parent-death binding and retirement are real.
    with tempfile.TemporaryDirectory(prefix='guardian-', dir=CACHE) as name:
        profile = Path(name)
        stub = profile / 'owner.py'
        stub.write_text(r'''import importlib.util, json, os, pathlib, socket, struct, sys
profile = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('worker', sys.argv[2])
worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
class Stream:
    def __init__(self, *_args): self.data = bytearray(); self.pending = None
    def __enter__(self): return self
    def __exit__(self, *_args): (profile / 'closed').write_text('yes')
    def settimeout(self, _value): pass
    def connect(self, _path): pass
    def sendall(self, packet):
        value = json.loads(packet[4:]); kind = value['type']; value.pop('response', None)
        with (profile / 'wire').open('a') as log: log.write(json.dumps(dict(type=kind, pid=os.getpid())) + '\n')
        if kind == 'create_session': reply = dict(type='auth_message', auth_message_type='secret', auth_message='Password:')
        elif kind == 'post_auth_message_response':
            (profile / 'pending').write_text(str(os.getpid())); self.pending = True; return
        else:
            raise AssertionError('retired owner sent a delayed cancel or launch into replacement session')
        raw = json.dumps(reply).encode(); self.data.extend(struct.pack('=i', len(raw)) + raw)
    def recv(self, count):
        if self.pending:
            if worker.RETIRED and not (profile / 'retired').exists():
                (profile / 'retired.tmp').write_text(str(os.getpid()))
                (profile / 'retired.tmp').replace(profile / 'retired')
            if not (profile / 'release').exists(): raise socket.timeout()
            raw = json.dumps(dict(type='error', error_type='auth_error')).encode()
            self.data.extend(struct.pack('=i', len(raw)) + raw); self.pending = None
        part = bytes(self.data[:min(count, 2)]); del self.data[:len(part)]; return part
worker.socket.socket = Stream
os._exit(worker.entrypoint())
''')
        supervisor = r'''import ctypes, json, os, pathlib, signal, subprocess, sys, time
profile = pathlib.Path(sys.argv[1]); helper = sys.argv[2]
libc = ctypes.CDLL(None); assert libc.prctl(36, 1, 0, 0, 0) == 0
def wait_retired(owner):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (profile / 'retired').exists():
            assert (profile / 'retired').read_text() == str(owner)
            return
        assert not (profile / 'closed').exists(), 'pending socket was closed on parent loss'
        time.sleep(.01)
    raise AssertionError('owner did not observe parent loss')
parent_code = """import json, os, pathlib, signal, subprocess, sys, time
p = pathlib.Path(sys.argv[1])
child = subprocess.Popen([sys.executable, '-I', '-B', str(p / 'owner.py'), str(os.getpid()), sys.argv[2]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=dict(GREETD_SOCK='fixture-only'))
(p / 'supervisor-pid').write_text(str(child.pid))
child.stdin.write((json.dumps(dict(op='begin', user='fixture-user', attempt=1))+'\\n').encode()); child.stdin.flush()
assert json.loads(child.stdout.readline())['event']=='hello'
assert json.loads(child.stdout.readline())['event']=='prompt'
child.stdin.write((json.dumps(dict(op='respond', password='fixture-éЖ🔒'))+'\\n').encode()); child.stdin.flush()
while True: signal.pause()
"""
for mode in ('parent-kill', 'parent-crash', 'process-destructor'):
    for file in ('wire', 'pending', 'release', 'closed', 'retired', 'supervisor-pid'):
        (profile / file).unlink(missing_ok=True)
    parent = subprocess.Popen([sys.executable, '-I', '-B', '-c', parent_code, str(profile), helper])
    owner = managed = None
    try:
        deadline = time.monotonic() + 3
        while not (profile / 'pending').exists() and time.monotonic() < deadline: time.sleep(.01)
        assert (profile / 'pending').exists(), 'no outstanding request'
        owner = int((profile / 'pending').read_text()); managed = int((profile / 'supervisor-pid').read_text())
        if mode == 'process-destructor': os.kill(managed, signal.SIGKILL)
        else:
            os.kill(parent.pid, signal.SIGKILL if mode == 'parent-kill' else signal.SIGSEGV)
        # A crashing parent may still be dumping core. Release the verdict only
        # after the real owner observes retirement, not after a scheduling delay.
        wait_retired(owner)
        os.kill(owner, 0)
        assert not (profile / 'closed').exists(), 'pending socket was closed on parent loss'
        # A replacement greeter now owns a fresh global slot. The late auth_error
        # must be consumed without a cancel that could erase that replacement.
        (profile / 'release').write_text('replacement owns the slot')
        if parent.poll() is None: parent.kill()
        parent.wait(timeout=30)
        deadline = time.monotonic() + 3; seen_owner = False
        while time.monotonic() < deadline:
            try: pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError: break
            if pid == owner:
                assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (mode, status)
                seen_owner = True
            if pid == 0: time.sleep(.01)
        assert seen_owner and (profile / 'closed').exists(), 'guardian did not drain, exit and reap'
        events = [json.loads(line)['type'] for line in (profile / 'wire').read_text().splitlines()]
        assert events == ['create_session', 'post_auth_message_response'], events
    finally:
        # Never release into a live attempt during failure cleanup: it may still
        # legitimately cancel its own slot before parent loss reaches the owner.
        if parent.poll() is None: parent.kill()
        parent.wait(timeout=30)
        if owner is not None and not (profile / 'closed').exists(): wait_retired(owner)
        (profile / 'release').write_text('release fixture')
print('guardian-ok')
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', supervisor, str(profile), str(ROOT / 'shell/helpers/greeter-auth.py')], capture_output=True, text=True, timeout=120, preexec_fn=core_limit)
        assert result.returncode == 0 and result.stdout.strip() == 'guardian-ok' and not result.stderr, result
    print('PASS: parent SIGKILL/SIGSEGV and QProcess-style supervisor kill preserve pending socket; retired owner drains late auth_error without cancelling replacement, exits and reaps')


def acknowledge_cancel(server):
    cancellation = server.request('cancel_session')
    assert cancellation['connection'] == server.auth_connection, 'cleanup left the attempt connection'
    if 'automatic_error' not in cancellation:
        server.success(connection=cancellation['connection'])
    return cancellation


def dead_client_socket_negative():
    with tempfile.TemporaryDirectory(prefix='dead-', dir=CACHE) as name:
        server = server_module.GreetdServer(runtime_path(name) / 'g.sock')
        expected_panic = False
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(server.path)
            def send(value):
                raw = json.dumps(value).encode()
                client.sendall(struct.pack('=i', len(raw)) + raw)
            def receive(count):
                data = bytearray()
                while len(data) < count:
                    part = client.recv(count - len(data))
                    assert part
                    data.extend(part)
                return data
            send(dict(type='create_session', username='fixture-user'))
            server.request('create_session'); server.prompt()
            size, = struct.unpack('=i', receive(4)); receive(size)
            send(dict(type='post_auth_message_response', response='fixture-éЖ🔒'))
            assert server.request('post_auth_message_response')['response_matches']
            client.close()
            deadline = time.monotonic() + 2
            while 0 not in server.closed and time.monotonic() < deadline: time.sleep(.005)
            assert 0 in server.closed
            try:
                server.error('auth_error', 'late wrong verdict')
            except AssertionError as error:
                assert 'greetd abort' in str(error) and server.panicked
                expected_panic = True
            else:
                raise AssertionError('real socket close-before-auth_error was silently accepted')
        finally:
            client.close()
            server.close(expected_panic=expected_panic)
    print('PASS: actual AF_UNIX close-before-send auth_error ordering hard-fails the greetd panic negative control')


def socket_scenarios():
    with harness() as (action, state, wait, server, proc, wait_log):
        assert action('password'); action('submit'); assert state()['queued']
        action('ready'); first = server.request('create_session'); answer(server); server.error()
        rejected = wait(lambda v: v['failures'] == 1)
        assert rejected['message'] == 'Wrong password' and rejected['wrongAt'] >= 0
        cancellation = acknowledge_cancel(server)
        assert 'automatic_error' in cancellation, 'exited PAM cancellation must return error'
        wait(lambda v: not v['recovering'])
        begin(action, server); answer(server); action('autoLaunch'); accepted_at = time.monotonic(); server.success()
        start = server.request('start_session')
        assert start['connection'] != first['connection'] and 0 <= start['received_at'] - accepted_at < 1.15
        accepted = wait(lambda v: v['successes'] == 1)
        assert accepted['succeeded'] and accepted['handoffs'] == 1 and accepted['phase'] == 'handoff'
        action('cancel'); action('disable'); assert state()['attempt'] == accepted['attempt']
        assert start['cmd'] == ['/usr/bin/niri-session', '--fixture']
        assert start['env'] == ['XDG_SESSION_TYPE=wayland', 'EMAKI_LOGIN_HANDOFF=' + json.dumps(FIXTURE_MARKER, separators=(',', ':'))]
        server.success(); assert proc.wait(timeout=3) == 0 and server.events.empty() and not server.panicked
    print('PASS: wrong verdict → same-connection exited-worker cancel error → correct fresh attempt; launch gate/marker/exit intact')

    with harness() as (action, state, wait, server, proc, wait_log):
        begin(action, server); server.prompt('error', 'Account locked for 10 minutes')
        assert not server.request('post_auth_message_response')['has_response']
        server.error(); wait(lambda v: v['failures'] == 1); acknowledge_cancel(server)
        wait(lambda v: not v['recovering']); time.sleep(2.1)
        assert state()['message'] == 'Account locked for 10 minutes'
    print('PASS: faillock text retained through same-connection cancellation error')

    with harness() as (action, state, wait, server, proc, wait_log):
        begin(action, server)
        server.prompt('info', 'The account is locked due to 3 failed logins.')
        assert not server.request('post_auth_message_response')['has_response']
        server.prompt('info', '(1 minute left to unlock)')
        assert not server.request('post_auth_message_response')['has_response']
        server.error(); wait(lambda v: v['failures'] == 1); acknowledge_cancel(server)
        current = wait(lambda v: not v['recovering'])
        assert current['lockoutRemaining'] in range(57, 61), current
        assert current['displayMessage'] == ('Too many wrong passwords. Try again in about '
                                           + str(current['lockoutRemaining'] // 60) + ':'
                                           + str(current['lockoutRemaining'] % 60).zfill(2) + '.'), current
        time.sleep(2.1)
        assert state()['lockoutRemaining'] < current['lockoutRemaining']
        action('expireLockout')
        assert state()['displayMessage'] == 'Wait time has ended. Try again.'
        assert state()['successes'] == 0 and not state()['checking']
        action('cancel')
        assert state()['lockoutRemaining'] == 0 and state()['displayMessage'] == ''
    print('PASS: rounded PAM duration counts down without automatic authentication; expiry and cancellation clear safely')

    for before_prompt in (True, False):
        for late in ('success', 'auth_error', 'error', 'prompt'):
            with harness() as (action, state, wait, server, proc, wait_log):
                begin(action, server); old = server.auth_connection
                if not before_prompt: answer(server)
                previous = state()['attempt']; action('cancel')
                current = state()
                assert not current['checking'] and current['attempt'] > previous
                assert current['pendingLength'] == current['bufferLength'] == current['dotCount'] == 0
                assert action('password'); action('submit')
                assert state()['queued'] and not state()['checking'] and server.events.empty()
                # The ordering that crashed greetd with SIGKILL must now be safe.
                assert old not in server.closed
                if late == 'success': server.success(connection=old)
                elif late == 'prompt': server.prompt(connection=old)
                else: server.error(late, 'late verdict', connection=old, worker_exited=True)
                acknowledge_cancel(server)
                fresh = server.request('create_session')
                assert fresh['connection'] != old and not server.panicked
                answer(server); server.success()
                accepted = wait(lambda v: v['successes'] == 1)
                assert accepted['fatals'] == accepted['failures'] == 0 and server.events.empty()
    print('PASS: eight Escape/late-reply orderings keep the outstanding socket alive; same-connection success/error ACK permits queued retry; no late login or greetd panic')

    with harness() as (action, state, wait, server, proc, wait_log):
        begin(action, server); answer(server); server.error('error', 'PAM worker exited', worker_exited=True)
        cancellation = acknowledge_cancel(server)
        assert 'automatic_error' in cancellation
        failed = wait(lambda v: v['fatals'] == 1)
        assert failed['technicalFailures'] == 1 and not failed['checking'] and not server.panicked
    print('PASS: generic technical error followed by exited-worker cancel error immediately requests ReGreet')

    with harness() as (action, state, wait, server, proc, wait_log):
        for count in (1, 2, 3):
            begin(action, server)
            server.error('error', 'a session is already being configured')
            current = wait(lambda v: v['technicalFailures'] == count)
            assert current['recovering'] and current['pendingLength'] == 0
            cancellation = server.request('cancel_session')
            assert 'automatic_error' not in cancellation
            assert action('password')
            server.success(connection=cancellation['connection'])
            if count < 3:
                recovered = wait(lambda v: not v['recovering'])
                assert recovered['fatals'] == 0 and recovered['bufferLength'] == 11
            else:
                assert wait(lambda v: v['fatals'] == 1)['technicalFailures'] == 3
        assert not server.panicked
    print('PASS: live-worker cleanup success permits two retries; the third consecutive technical error falls back')

    for outcome in ('rejected', 'technical', 'cancel'):
        with harness() as (action, state, wait, server, proc, wait_log):
            begin(action, server); answer(server); action('shortWatchdog')
            if outcome == 'cancel':
                action('cancel'); server.success()
            elif outcome == 'rejected':
                server.defer_cancel_error = True
                server.error('auth_error')
            else:
                server.error('error', 'live worker')
            cancellation = server.request('cancel_session')
            assert cancellation['connection'] == server.auth_connection
            wait(lambda v: v['fatals'] == 1)
            assert server.auth_connection not in server.closed, 'UI watchdog killed an outstanding cancel socket'
            if outcome == 'rejected':
                server.error('error', 'worker exited', connection=cancellation['connection'])
            else:
                server.success(connection=cancellation['connection'])
            assert not server.panicked
    print('PASS: pending cleanup ACK has a bounded UI watchdog; fallback retires without killing the reply socket')

    for loss in ('malformed', 'hangup'):
        with harness() as (action, state, wait, server, proc, wait_log):
            begin(action, server); answer(server); action('cancel'); server.success()
            cancellation = server.request('cancel_session')
            assert action('password')
            if loss == 'malformed': server.send({'type': 'unexpected'}, connection=cancellation['connection'])
            else: server.connections[cancellation['connection']].shutdown(socket.SHUT_RDWR)
            failed = wait(lambda v: not v['recovering'])
            assert failed['fatals'] == 0 and failed['bufferLength'] == 11 and failed['kind'] == 'technical'
            action('submit')
            retry_cleanup = server.request('cancel_session')
            assert retry_cleanup['connection'] != cancellation['connection']
            assert server.events.empty(), 'retry bypassed cleanup-only acknowledgement'
            server.success(connection=retry_cleanup['connection'])
            server.request('create_session'); answer(server); server.success()
            assert wait(lambda v: v['successes'] == 1)['fatals'] == 0 and not server.panicked
    print('PASS: malformed/lost Escape cleanup ACK preserves typing; next Enter uses acknowledged cleanup-only worker before fresh authentication')

    with harness() as (action, state, wait, server, proc, wait_log):
        begin(action, server); answer(server); action('cancel'); server.success()
        cancellation = server.request('cancel_session')
        server.send({'type': 'unexpected'}, connection=cancellation['connection'])
        wait(lambda v: not v['recovering']); assert action('password'); action('breakStart')
        started = time.monotonic(); action('submit')
        wait(lambda v: v['fatals'] == 1)
        assert time.monotonic() - started < 1.5 and server.events.empty()
    print('PASS: cleanup-only worker FailedToStart is bounded by the same short startup guard')

    with harness() as (action, state, wait, server, proc, wait_log):
        begin(action, server); answer(server); old = server.auth_connection
        proc.kill(); proc.wait(timeout=3)
        time.sleep(.1)
        assert old not in server.closed, 'parent death dropped the pending socket'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
            replacement.connect(server.path)
            def replace_request(value):
                raw = json.dumps(value).encode()
                replacement.sendall(struct.pack('=i', len(raw)) + raw)
            def replace_reply():
                def exact(count):
                    data = bytearray()
                    while len(data) < count:
                        chunk = replacement.recv(count - len(data)); assert chunk; data.extend(chunk)
                    return data
                size, = struct.unpack('=i', exact(4))
                return json.loads(exact(size))
            replace_request(dict(type='cancel_session'))
            cancelled = server.request('cancel_session'); server.success(connection=cancelled['connection']); replace_reply()
            replace_request(dict(type='create_session', username='replacement-user'))
            fresh = server.request('create_session'); server.prompt(connection=fresh['connection']); replace_reply()
            server.error('auth_error', 'old delayed verdict', connection=old)
            deadline = time.monotonic() + 3
            while old not in server.closed and time.monotonic() < deadline: time.sleep(.01)
            assert old in server.closed and server.events.empty() and not server.panicked
            assert server.configuring['user'] == 'replacement-user' and server.configuring['alive']
    print('PASS: retired real-socket owner drains its late auth_error without cancelling or launching over the replacement greeter global slot')

    for launch_failure in ('error', 'hang'):
        with harness() as (action, state, wait, server, proc, wait_log):
            begin(action, server); answer(server); server.success(); wait(lambda v: v['handoffs'] == 1)
            action('shortWatchdog'); action('noMarker'); action('launch'); start = server.request('start_session')
            assert start['env'] == ['XDG_SESSION_TYPE=wayland']
            if launch_failure == 'error': server.error('error', 'session launch failed')
            wait(lambda v: v['fatals'] == 1)
            if launch_failure == 'hang':
                assert server.auth_connection not in server.closed
                server.success()
            assert server.events.empty() and not server.panicked
    print('PASS: launch error/watchdog never cancels an accepted or scheduled session and drains outstanding launch ACK')




session_contract()
with harness('scripted-queued') as (action, state, wait, server, proc, wait_log):
    action('ready')
    assert action('password')
    action('submit')
    first = wait(lambda v: v['checking'] and v['pendingLength'] == 0)
    action('wait-verdict')
    action('cancel')
    cancelled = state()
    assert not cancelled['checking'] and cancelled['attempt'] > first['attempt']
    assert cancelled['pendingLength'] == cancelled['bufferLength'] == cancelled['dotCount'] == 0
    assert action('password')
    action('submit')
    held = state()
    assert held['queued'] and not held['checking'] and held['bufferLength'] == 11, held
    action('release-verdict')
    final = wait(lambda v: v['successes'] == 1)
    assert final['fatals'] == final['failures'] == 0 and final['succeeded']
    action('launch')
    assert proc.wait(timeout=3) == 0
print('PASS: real parent/worker anonymous pipes and framing; Escape drains and cancels the delayed correct verdict, preserves queued typing and launches only the fresh attempt')
with harness('scripted-rejection') as (action, state, wait, server, proc, wait_log):
    action('ready')
    assert action('password')
    action('submit')
    wait(lambda v: v['failures'] == 1)
    time.sleep(6.2)
    assert state()['message'] == 'Wrong password', 'the rejection must remain until the next attempt'
    assert action('password')
    after_cleanup = wait(lambda v: not v['recovering'])
    assert after_cleanup['message'] == 'Wrong password' and after_cleanup['kind'] == 'wrong'
    assert after_cleanup['technicalFailures'] == after_cleanup['fatals'] == 0
    assert after_cleanup['bufferLength'] == 11
    action('submit')
    assert state()['message'] == '', 'the next submitted attempt clears the old rejection'
    assert wait(lambda v: v['successes'] == 1)['fatals'] == 0
print('PASS: production worker rejection plus failed cleanup ACK keeps message/typing/count and permits a fresh correct attempt')
with harness('absent') as (action, state, wait, server, proc, wait_log):
    action('ready'); action('breakStart'); assert action('password')
    started = time.monotonic(); action('submit')
    failed = wait(lambda v: v['fatals'] == 1)
    assert time.monotonic() - started < 1.5 and failed['pendingLength'] == failed['bufferLength'] == 0
print('PASS: real QProcess FailedToStart exits checking promptly without waiting 60 seconds')
with harness('no-hello') as (action, state, wait, server, proc, wait_log):
    action('ready'); assert action('password'); started = time.monotonic(); action('submit')
    failed = wait(lambda v: v['fatals'] == 1)
    assert time.monotonic() - started < 1.8 and failed['pendingLength'] == failed['bufferLength'] == 0
print('PASS: started helper without a child hello is bounded by the one-second handshake guard')
isolated_auth_protocol_checks()
worker_lifetime_checks()
no_socket = '--no-socket' in sys.argv
if not no_socket:
    # The managed worker forbids even private AF_UNIX binds. Only an explicitly
    # requested restricted subset may skip this; normal acceptance fails here.
    with tempfile.TemporaryDirectory(prefix='gp-', dir=CACHE) as probe_dir:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(str(runtime_path(probe_dir) / 'g.sock'))
            except PermissionError:
                if os.environ.get('EMAKI_TEST_SANDBOX') != '1':
                    raise
                no_socket = True
if not no_socket:
    dead_client_socket_negative()
    socket_scenarios()
else:
    print('BLOCKED: explicit restricted socket-free subset; framed socket scenarios require an environment permitting AF_UNIX bind')

for socket_mode in (('missing', 'absent') if no_socket else ('missing', 'absent', 'server')):
    with harness(socket_mode) as (action, state, wait, server, proc, wait_log):
        action('ready')
        # Leave scheduler headroom while distinguishing immediate detection from
        # a watchdog expiry. Missing GREETD_SOCK is known synchronously by the controller.
        action('slowWatchdog' if socket_mode == 'missing' else 'shortWatchdog')
        assert action('password')
        before = time.monotonic()
        action('submit')
        if server:
            server.request('create_session')
            server.hangup()
        final = wait(lambda value: value['fatals'] == 1)
        assert final['kind'] == 'technical' and final['bufferLength'] == 0
        assert final['message'] == 'Authentication error. Opening another login screen.'
        assert final['pendingLength'] == 0
        assert not final['checking'] and final['successes'] == 0
        if socket_mode == 'missing':
            assert time.monotonic() - before < 1, 'missing GREETD_SOCK waited for watchdog'
print('PASS: missing GREETD_SOCK immediately technical; isolated worker connection failure clears and falls back' if no_socket else
      'PASS: missing GREETD_SOCK immediately technical; nonexistent/disconnected socket watchdog clears and falls back')

with harness('missing') as (action, state, wait, server, proc, wait_log):
    action('ready')
    assert action('cap')
    assert not action('excess')
    assert state()['message'] == 'Password is too long (maximum 1024 characters)'
    assert not action('nul')
    assert state()['message'] == 'This character cannot be used in a password'
    assert state()['bufferLength'] == 1024 and state()['kind'] == 'input'
    action('cancel')
    assert not state()['bufferLength'] and not state()['fatals']
    for _ in range(8):
        action('cancel')
        current = state()
        assert current['user'] == 'fixture-user' and current['prompt'] == 'Password'
        assert not current['usernameMode'] and current['userSelections'] == current['fatals'] == 0
    action('usernameStep')  # Initial account discovery, never an Escape action.
    assert state()['usernameMode'] and state()['prompt'] == 'Username'
    for _ in range(8):
        action('cancel')
        current = state()
        assert current['user'] == '' and current['prompt'] == 'Username'
        assert current['usernameMode'] and current['userSelections'] == current['fatals'] == 0
    action('unknownUser')
    assert state()['message'] == 'Unknown user'
    assert action('username')
    assert state()['message'] == state()['kind'] == ''
    before_selection = state()['attempt']
    action('submit')
    selected = state()
    assert selected['selectedUser'] == 'fixture-user' and not selected['usernameMode']
    assert selected['bufferLength'] == selected['dotCount'] == 0
    assert selected['attempt'] == before_selection
    # Escape while account discovery is busy also only clears the input.
    action('selectionPending')
    selections = state()['userSelections']
    action('cancel')
    assert state()['userSelections'] == selections and not state()['usernameMode']
    action('selectionReady')
    action('cancel')
    assert not state()['usernameMode'] and state()['user'] == 'fixture-user'
    assert state()['userSelections'] == selections and state()['fatals'] == 0
    assert not state()['checking'], 'username selection must not create a greetd session'
    for internal in ('reset', 'suspend', 'disable', 'enable'):
        action(internal)
        assert not state()['usernameMode'] and state()['fatals'] == 0
        assert state()['user'] == 'fixture-user' and state()['userSelections'] == selections
    action('usernameStep')
    assert action('username')
    action('cancel')
    assert state()['bufferLength'] == state()['fatals'] == 0, 'Escape clears nonempty username input'
    action('unknownUser')
    action('cancel')
    final = state()
    assert final['message'] == final['kind'] == ''
    assert final['usernameMode'] and final['user'] == '' and final['fatals'] == 0
    assert final['userSelections'] == selections
    assert not final['checking'] and final['pendingLength'] == final['bufferLength'] == final['dotCount'] == 0
print('PASS: exact password input errors and initial username submission; repeated idle Escape only clears input, preserving the account and field mode')

with harness('missing') as (action, state, wait, server, proc, wait_log):
    action('ready'); action('lockoutNotice')
    current = state()
    assert current['lockoutRemaining'] in range(418, 421), current
    assert current['displayMessage'] == ('Too many wrong passwords. Try again in about '
                                           + str(current['lockoutRemaining'] // 60) + ':'
                                           + str(current['lockoutRemaining'] % 60).zfill(2) + '.'), current
    time.sleep(2.1)
    assert state()['lockoutRemaining'] < current['lockoutRemaining']
    action('expireLockout')
    assert state()['displayMessage'] == 'Wait time has ended. Try again.'
    assert state()['successes'] == 0 and not state()['checking']
    action('cancel')
    assert state()['displayMessage'] == '' and state()['lockoutRemaining'] == 0
print('PASS: greeter PAM-message countdown, expiry and cancellation without socket dependencies')
