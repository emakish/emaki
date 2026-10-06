#!/usr/bin/env python3
"""Lock controller + real PAM in isolated offscreen profiles; no live session.

The real PAM harness copies production LockPam and changes its literal service
in the COPY only. No production switch, IPC, factory, or environment variable
can select a different PAM stack. permit/deny are never installed.
"""
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import resource
import pwd
import socket
import shutil
import subprocess
import tempfile
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
QS = shutil.which('qs')
assert QS, 'qs is required'


def core_limit():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def production_path():
    """The unchanged production component must agree with install and packaging."""
    with tempfile.TemporaryDirectory(prefix='lp-', dir=CACHE) as work:
        profile = Path(work)
        for name in ('r', 'cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        shutil.copy(ROOT / 'shell/LockPam.qml', profile)
        shutil.copy(ROOT / 'tests/fixtures/lock/LockPamProductionTest.qml', profile / 'check.qml')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(profile / 'r'),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_STATE_HOME=str(profile / 'state'),
                   TMPDIR=str(profile / 'tmp'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(name, None)
        result = subprocess.run([QS, '-p', str(profile / 'check.qml'), '--no-color'],
                                env=env, text=True, capture_output=True, timeout=10,
                                preexec_fn=core_limit)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'LOCK_PAM_PRODUCTION_PATH /etc/pam.d/emaki-lock' in result.stdout + result.stderr
    make = (ROOT / 'Makefile').read_text()
    recipes = '\n'.join(line for line in make.splitlines() if line.startswith('\t'))
    assert 'pam.d/emaki-lock' not in recipes and 'PAM_ETC_DIR' not in make
    source = ROOT / 'packaging/emaki-desktop/emaki-lock.pam'
    assert source.is_file() and not source.is_symlink()
    assert not (ROOT / 'lock/pam').exists()
    package = (ROOT / 'packaging/emaki-desktop/PKGBUILD').read_text()
    assert "backup=('etc/pam.d/emaki-lock')" in package
    assert '"$pkgdir/etc/pam.d/emaki-lock"' in package
    assert hashlib.sha256(source.read_bytes()).hexdigest() in package
    print('PASS: unchanged production PamContext default /etc/pam.d/emaki-lock agrees with its sole owner: the pacman package and backup')


production_path()


@contextmanager
def harness(service=None):
    profile = Path(tempfile.mkdtemp(prefix='la-', dir=CACHE))
    for part in ('r', 'cache', 'config', 'data', 'state', 'tmp', 'shell', 'pam'):
        (profile / part).mkdir(mode=0o700)
    shell = profile / 'shell'
    shutil.copy(ROOT / 'shell/AuthController.qml', shell)
    shutil.copy(ROOT / 'shell/LockAuth.qml', shell)
    shutil.copy(ROOT / 'tests/fixtures/lock/LockAuthTest.qml', shell / 'lock-auth.qml')
    if service:
        source = (ROOT / 'shell/LockPam.qml').read_text()
        assert source.count('config: "emaki-lock"') == 1
        assert 'configDirectory:' not in source
        # Fixture-only separate PamContext, with production signal mapping.
        source = source.replace('config: "emaki-lock"',
                                f'config: "fixture"\n        configDirectory: {json.dumps(str(profile / "pam"))}')
        (shell / 'LockPam.qml').write_text(source)
        (profile / 'pam/fixture').write_text(f'auth required {service}\n')
    else:
        shutil.copy(ROOT / 'tests/fixtures/lock/LockPam.qml', shell)
    (shell / 'qmldir').write_text('AuthController 1.0 AuthController.qml\nLockAuth 1.0 LockAuth.qml\nLockPam 1.0 LockPam.qml\n')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(profile / 'r'),
               XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_STATE_HOME=str(profile / 'state'),
               TMPDIR=str(profile / 'tmp'), LC_ALL='C', USER='emaki-lock-not-a-user', LOGNAME='emaki-lock-not-a-user', EMAKI_AUTH_FIXTURE=str(profile))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(key, None)
    log_path = profile / 'qs.log'
    with log_path.open('w') as log:
        proc = subprocess.Popen([QS, '-p', str(shell / 'lock-auth.qml'), '--no-color'],
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
                assert proc.poll() is None, log_path.read_text()
                try:
                    response = json.loads((profile / 'reply.json').read_text())
                    if response['id'] == serial:
                        result = response['value']
                        return json.loads(result) if method in ('state', 'answerStats') else result
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                time.sleep(.01)
            raise AssertionError(('fixture reply timeout', method, log_path.read_text()))
        # Cold PAM/module loading can exceed five seconds on shared CI runners.
        # Keep a bounded deadline and the latest state/log in the failure report.
        def wait(predicate, timeout=20):
            deadline = time.monotonic() + timeout
            latest = None
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text()
                try:
                    latest = call('state')
                except AssertionError:
                    time.sleep(.025)
                    continue
                if predicate(latest):
                    return latest
                time.sleep(.025)
            raise AssertionError(('state timeout', latest, log_path.read_text()))
        try:
            initial = wait(lambda s: s['attempt'] == 0)
            # This is deliberately qs -p <singlefile.qml>, NOT a directory.
            assert Path(initial['shellPath']) == shell, initial
            assert initial['shaderPath'] == str(shell / 'shaders'), initial
            yield call, wait
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
    output = log_path.read_text()
    if service:
        assert f'user "{pwd.getpwuid(os.getuid()).pw_name}"' in output, output
    assert 'fixture-éЖ' not in output and 'bad' not in output
    output = '\n'.join(line for line in output.splitlines() if 'quickshell.ipc: Failed to start IPC server' not in line)
    assert 'ERROR' not in output and 'WARN' not in output, output


with harness() as (call, wait):
    def action(name):
        return call('action', name)
    def state():
        return call('state')
    def attempt():
        action('early')
        action('submit')
        return state()['attempt']

    assert action('early')
    assert state()['bufferLength'] == state()['dotCount'] == 11
    action('submit')
    assert state()['queued'] and not state()['checking']
    action('ready')
    assert state()['checking'] and not state()['queued']
    first = state()['attempt']
    action('submit')
    assert state()['attempt'] == first  # one conversation at a time
    assert not action('early')
    call('challenge', 'Password: ', False)
    assert state()['bufferLength'] == 0 and state()['dotCount'] == 11
    assert call('answerStats') == {'responses': 1, 'length': 12}
    call('finish', 'rejected')
    assert state()['kind'] == 'wrong' and state()['message'] == 'Wrong password'
    assert state()['dotCount'] == 0 and state()['failures'] == 1
    time.sleep(1.1)
    assert state()['kind'] == 'wrong'
    wait(lambda s: s['message'] == '', 2)
    assert action('cap') and state()['bufferLength'] == 1024
    assert not action('excess') and state()['bufferLength'] == 1024
    assert state()['kind'] == 'input'
    assert not action('nul') and state()['bufferLength'] == 1024
    action('cancel')
    assert not state()['bufferLength'] and not state()['queued']

    # Do not supply the pretyped password to visible/OTP or repeated prompts.
    attempt()
    call('challenge', 'Verification code:', False)
    assert state()['awaiting'] and state()['prompt'] == 'Verification code:'
    assert not state()['bufferLength'] and call('answerStats')['responses'] == 0
    assert action('early')
    action('submit')
    assert not state()['awaiting'] and not state()['bufferLength']
    assert call('answerStats')['responses'] == 1
    call('challenge', 'Password:', False)
    assert state()['awaiting'] and call('answerStats')['responses'] == 1
    action('submit')
    assert not state()['awaiting'] and not state()['bufferLength']
    assert call('answerStats') == {'responses': 2, 'length': 0}
    action('cancel')
    attempt()
    call('challenge', 'Password:', True)
    assert state()['awaiting'] and call('answerStats')['responses'] == 0
    action('cancel')

    attempt()
    call('notice', 'Account locked for 10 minutes', True)
    call('finish', 'rejected')
    assert state()['kind'] == 'pam-error' and state()['message'] == 'Account locked for 10 minutes'
    attempt()
    call('finish', 'technical')
    assert state()['kind'] == 'technical' and 'Wrong password' not in state()['message']
    call('finish', 'success')  # duplicate error/completed must never authenticate
    assert state()['successes'] == 0
    attempt()
    action('save')
    action('suspend')
    assert not state()['checking'] and not state()['bufferLength']
    action('lateSuccess')
    assert state()['successes'] == 0
    attempt()
    action('lateSuccess')  # old success during a new attempt
    assert state()['checking'] and state()['successes'] == 0
    action('timeout')
    assert state()['kind'] == 'technical' and not state()['checking']
    attempt()
    action('disable')
    assert not state()['checking'] and not state()['bufferLength']
    assert not action('early')
    action('enable')
    attempt()
    call('finish', 'success')
    assert state()['successes'] == 1 and not state()['bufferLength']
    assert state()['dotCount'] == 11  # visual fade keeps count, never the secret
    call('finish', 'success')
    assert state()['successes'] == 1 and not action('early')
    successful_id = state()['attempt']
    assert state()['succeeded']
    action('disable')  # authenticated melt/drain preserves successful identity
    assert state()['succeeded'] and state()['attempt'] == successful_id
    assert state()['dotCount'] == 11 and not state()['bufferLength']
    action('cancel')  # explicit cancellation revokes even an already successful attempt
    assert not state()['succeeded'] and state()['attempt'] > successful_id
    assert not state()['dotCount'] and not state()['bufferLength']
    action('enable')
    action('reset')  # fresh lock: old attempt no longer useful
    assert action('early')
    action('submit')
    call('finish', 'success')
    assert state()['successes'] == 2
    action('suspend')  # suspend revokes a success before its drain completes
    assert action('early')
    action('submit')
    call('finish', 'success')
    assert state()['successes'] == 3
    action('reset')
    action('notReady')
    action('early')
    action('submit')
    action('cancel')
    action('ready')  # Escape cancels an early queued Enter too
    assert not state()['checking'] and not state()['queued']
print('LockAuth: early Unicode, cap, queued Enter, wrong 2 s, prompts, error, cancel, suspend, stale callbacks, timeout, relock: OK')

# Linux-PAM audits even pam_permit. The restricted worker denies audit socket
# creation, and libpam returns PAM_SYSTEM_ERR (4) before reporting the result.
# Still exercise production technical-error handling here; the same harness
# automatically checks permit/deny/missing-module outcomes outside the sandbox.
audit_available = True
try:
    audit = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 9)  # NETLINK_AUDIT
    audit.close()
except PermissionError:
    audit_available = False
    if os.environ.get('EMAKI_TEST_SANDBOX') != '1':
        raise AssertionError('NETLINK_AUDIT denied: real permit/deny not exercised. '
                             'EMAKI_TEST_SANDBOX=1 explicitly permits the restricted subset; '
                             'it is not the full acceptance gate.')
for module, kind in [('pam_permit.so', ''), ('pam_deny.so', 'wrong'),
                     ('/definitely-missing-lock-test-module.so', 'technical')]:
    with harness(module) as (call, wait):
        call('action', 'early')
        call('action', 'ready')
        call('action', 'submit')
        final = wait(lambda s: not s['checking'] and s['attempt'] > 0)
        expected = kind if audit_available else 'technical'
        assert final['kind'] == expected, (module, final)
        assert final['successes'] == (1 if expected == '' else 0), final
        assert not final['bufferLength'], final
        assert final['dotCount'] == (11 if expected == '' else 0), final
    print(f'Real isolated PAM {module}: {expected or "success"}: OK')
if not audit_available:
    print('BLOCKED: sandbox denies NETLINK_AUDIT; libpam returns PAM_SYSTEM_ERR even for pam_permit. Re-run tests/test-lock-auth.py outside sandbox to verify real permit/deny outcomes.')
print('qs -p single file retains shellPath directory + shaders sibling: OK; PamContext defaults to real uid (ignores USER/LOGNAME); no live Wayland/D-Bus/niri used')
