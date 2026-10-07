#!/usr/bin/env python3
"""HOST ONLY: disposable QEMU C9 UI/fallback acceptance, never the host session.

Run after run-test.sh installs a committed revision, with --greeter-only there:
  python3 tests/vm/check-greeter.py --output /path/to/evidence
Use --fixture for the release account and transport; without it, development
defaults apply. Passwords reach guest-keys.py through stdin only.
"""
import argparse
import concurrent.futures
import json
import os
import re
from pathlib import Path
import shlex
import socket
import subprocess
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suite_target import Target, add_arguments

HERE = Path(__file__).resolve().parent
TARGET = Target()
VM = TARGET.vm
OUT = None
FAILURES = []
CAPTURE_NOTES = []
LAST_RESTART = 0


def configure(args):
    global TARGET, VM
    TARGET = Target.from_args(args)
    VM = TARGET.vm


def remote(command, *, data=None, timeout=35, privileged=False):
    return TARGET.remote(command, data=data, timeout=timeout, privileged=privileged)


def checked_remote(command, **kwargs):
    result = remote(command, **kwargs)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def guest(op, **values):
    global LAST_RESTART
    if op == 'restart-greeter':
        # Deliberate compositor exits can make greetd itself restart. Its Arch
        # unit has StartLimitBurst=5 / StartLimitInterval=30; do not make fixture
        # setup/cleanup exhaust that independent daemon recovery budget.
        remaining = LAST_RESTART + 8 - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        LAST_RESTART = time.monotonic()
    payload = json.dumps(dict(op=op, user=TARGET.user, **values), separators=(',', ':'))
    return json.loads(checked_remote('python3 /tmp/c9-guest-greeter.py ' + shlex.quote(payload), privileged=True))


def wait(predicate, timeout=35):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(.3)
    raise AssertionError('guest condition deadline; observation=' + repr(last))


def inspect():
    return guest('inspect')


def greeter():
    state = inspect()
    assert healthy(state), state
    return state['qs'][0]


def healthy(state):
    return (len(state['qs']) == 1 and not state['regreet'] and not state['qs_stopped']
            and len(state['greeter_compositors']) == 1 and not state['user_compositors']
            and state['greetd'] == 'active' and state['active_tty'] == 'tty1')


def wait_greeter(timeout=45):
    identity, healthy_at = None, None
    def ready():
        nonlocal identity, healthy_at
        state = inspect()
        if not healthy(state):
            identity, healthy_at = None, None
            return None
        if state['qs'][0] != identity:
            identity, healthy_at = state['qs'][0], time.monotonic()
        # Allow the helper, first frame and password-drop pour to settle. A
        # load-error process that briefly exists cannot pass this health gate.
        # The state helper has a five-second timeout; observe beyond that too.
        return state if time.monotonic() - healthy_at >= 5.5 else None
    return wait(ready, timeout)


def recover(*, force=False):
    restored = guest('restore-fixtures')
    state = inspect()
    if state['active_tty'] != 'tty1':
        keys('chord:Control_L+Alt_L+F1')
        wait(lambda: inspect()['active_tty'] == 'tty1')
    if state['user_compositors']:
        guest('logout')
    # greetd Restart=always has a real restart interval after an intentional
    # greeter compositor exit. Do not start the next scenario inside that gap.
    def present():
        observed = inspect()
        return observed if observed['greetd'] == 'active' and observed['greeter_compositors'] and (observed['qs'] or observed['regreet']) else None
    state = wait(present, 45)
    if force or state['regreet'] or restored['restored']:
        guest('restart-greeter')
    return wait_greeter()


def journal_mark():
    return guest('journal-mark')['since']


def journal_since(mark):
    return guest('journal-since', since=mark)['journal']


def authentication_failed(mark):
    journal = journal_since(mark)
    lines = journal.splitlines()
    return journal if any('pam_unix(emaki-greetd:auth): authentication failure' in line
                          and re.search(r'\buser=' + re.escape(TARGET.user) + r'(?:\s|$)', line) for line in lines) else None


def keys(*steps, text=''):
    checked_remote('python3 /tmp/c9-guest-keys.py ' + shlex.join(steps), data=text, privileged=True)


def hmp_response(connection):
    deadline = time.monotonic() + 3
    data = bytearray()
    while time.monotonic() < deadline:
        connection.settimeout(max(.01, deadline - time.monotonic()))
        part = connection.recv(65536)
        if not part:
            raise OSError('QEMU monitor disconnected before its prompt')
        data.extend(part)
        if data.rstrip().endswith(b'(qemu)'):
            return data.decode(errors='replace')
        if len(data) > 1024 * 1024:
            raise OSError('QEMU monitor response exceeded its bound')
    raise OSError('QEMU monitor did not complete the command')


def shot(name):
    """Prefer the host's QEMU display, record virgl limitations explicitly."""
    ppm = OUT / (name + '.ppm')
    ppm.unlink(missing_ok=True)
    try:
        with socket.socket(socket.AF_UNIX) as monitor:
            monitor.settimeout(2)
            monitor.connect(str((VM / 'mon.sock').resolve()))
            hmp_response(monitor)
            monitor.sendall(('screendump ' + json.dumps(str(ppm)) + '\n').encode())
            response = hmp_response(monitor)
        if ppm.exists() and ppm.stat().st_size > 0:
            from PIL import Image
            with Image.open(ppm) as frame:
                frame.save(OUT / (name + '.png'))
            ppm.unlink()
            return 'host-screendump'
    except (OSError, ValueError) as error:
        response = str(error)
    # Existing virgl VM can report "no surface" for HMP screendump. Preserve that
    # fact and use the shared host display capture helper for review.
    (OUT / 'host-screendump-unavailable.txt').write_text(response)
    if 'direct screendump unavailable; host display capture helper used' not in CAPTURE_NOTES:
        CAPTURE_NOTES.append('direct screendump unavailable; host display capture helper used')
    result = subprocess.run(TARGET.shot_argv(OUT / (name + '.png')),
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return 'host-display-fallback'


def frames(name, action, *, after=1.6):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        future = pool.submit(action)
        sources = []
        completed_at = None
        index = 0
        # guest-keys needs 1.5 s for device discovery before typing. Sample until
        # the operation ends AND its animation settles, not for a fixed burst
        # that might finish before the Return key is delivered.
        while time.monotonic() - started < 45:
            if future.done() and completed_at is None:
                future.result()
                completed_at = time.monotonic()
            if completed_at is not None and time.monotonic() - completed_at >= after:
                break
            captured_at = time.monotonic() - started
            try:
                source = shot(f'{name}-{index:03d}')
            except (AssertionError, subprocess.TimeoutExpired) as error:
                # A compositor handoff can briefly have no capturable Wayland
                # socket. Preserve the gap, then keep sampling the new screen.
                source = 'unavailable during handoff: ' + str(error)
            sources.append(dict(at=round(captured_at, 3), source=source))
            index += 1
            time.sleep(.06)
        future.result(timeout=40)
    (OUT / (name + '-sources.json')).write_text(json.dumps(sources, indent=2))
    assert any(row['source'].startswith(('host-screendump', 'host-display-fallback', 'guest-grim')) for row in sources), 'no frames captured for ' + name


def pam_session_opened(journal):
    return bool(re.search(r'pam_unix\(emaki-greetd:session\): session opened for user '
                          + re.escape(TARGET.user) + r'(?=\s|\(|$)', journal))


def session(expected, mark):
    def ready():
        state = inspect()
        if state['services'].get(expected) != 'active' or state['services'].get('emaki-shell.service') != 'active':
            return None
        journal = journal_since(mark)
        if not pam_session_opened(journal):
            return None
        state['journal'] = journal
        return state
    state = wait(ready)
    assert not state['qs'] and not state['regreet'], state
    return state


def logout():
    guest('logout')
    return wait_greeter()


def restart():
    previous = greeter()
    guest('restart-greeter')
    state = wait_greeter()
    assert state['qs'][0] != previous, 'compositor restart did not replace the greeter'
    return state


def ui_login():
    guest('select-emaki')
    frames('initial-pour', restart, after=.3)
    before = greeter()
    shot('greeter-before-wrong')
    wrong_mark = journal_mark()
    frames('wrong-password', lambda: keys('text', 'key:Return', text='c9-wrong-password'), after=3)
    wrong_journal = wait(lambda: authentication_failed(wrong_mark))
    (OUT / 'wrong-password-journal.txt').write_text(wrong_journal)
    assert greeter() == before, 'wrong password replaced the Emaki greeter'
    correct_mark = journal_mark()
    frames('drain', lambda: keys('text', 'key:Return', text=TARGET.password))
    state = session('niri-emaki.service', correct_mark)
    (OUT / 'login-journal.txt').write_text(state['journal'])
    # A deliberate early logout would trigger the specified crash heuristic.
    time.sleep(31)
    frames('logout-pour', logout, after=.3)
    shot('greeter-after-logout')
    return 'uinput wrong attempt proved by its fresh PAM failure; correct attempt by fresh session-open and services; logout recovered'


def escape_check():
    guest('select-emaki')
    restart()
    before = inspect()
    assert healthy(before), before
    assert before.get('greetd_identity'), 'greetd identity unavailable before cancellation'
    wrong_mark = journal_mark()
    # All steps share ONE uinput device: its 1.5 s setup precedes Return, so
    # wait:1.8 is inside the submitted attempt rather than a second-device delay.
    frames('escape-during-wrong', lambda: keys('text', 'key:Return', 'wait:1.8', 'key:Escape',
                                             text='c9-wrong-password'), after=3.5)
    wrong_journal = wait(lambda: authentication_failed(wrong_mark))
    after = inspect()
    (OUT / 'escape-wrong-journal.txt').write_text(wrong_journal)
    (OUT / 'escape-identities.json').write_text(json.dumps(dict(before=before, after=after), indent=2))
    assert healthy(after) and after['qs'] == before['qs'], 'Escape replaced or left the Emaki greeter'
    assert after.get('greetd_identity') == before['greetd_identity'], 'greetd restarted during the canceled check'
    assert not re.search(r'unable to cancel session|signal=ABRT|status=6/ABRT|start-limit-hit', wrong_journal), wrong_journal
    assert not pam_session_opened(wrong_journal), 'canceled check opened a session'
    # Idle and repeated Escape must keep the selected password screen in place.
    keys(*(['key:Escape'] * 8))
    idle = inspect()
    (OUT / 'escape-idle-identity.json').write_text(json.dumps(idle, indent=2))
    assert healthy(idle) and idle['qs'] == before['qs'], 'idle Escape replaced the greeter'
    assert idle.get('greetd_identity') == before['greetd_identity'], 'greetd changed after idle Escape'
    shot('escape-cleared-and-idle')
    correct_mark = journal_mark()
    keys('text', 'key:Return', text=TARGET.password)
    logged_in = session('niri-emaki.service', correct_mark)
    (OUT / 'escape-next-login-journal.txt').write_text(logged_in['journal'])
    assert logged_in.get('greetd_identity') == before['greetd_identity'], 'greetd changed before the next successful login'
    time.sleep(31)
    logout()
    return ('one wrong-password attempt plus delayed Escape preserved QS/greetd identity and fresh PAM failure; '
            'idle/repeated Escape stayed; next correct login opened session/services and logout recovered; '
            'random PAM delay does not prove hitting the narrow verdict/close race')


def fallback_after_signal(sig, name):
    before = greeter()
    if sig in ('SIGSEGV', 'SIGABRT'):
        assert inspect()['qs_crash_handler_disabled'], 'QS crash handler is not disabled in the installed greeter environment'
    guest('kill', pid=before, signal=sig)
    def fallback():
        state = inspect()
        return state if len(state['regreet']) == 1 and not state['qs'] else None
    wait(fallback)
    shot('regreet-after-' + name)
    return sig + ' targeted exact greeter qs; ReGreet appeared; its UI login still requires manual acceptance'


def killed():
    return fallback_after_signal('SIGKILL', 'sigkill')


def crashed():
    return fallback_after_signal('SIGSEGV', 'sigsegv')


def broken():
    guest('break-qml')
    guest('restart-greeter')
    def fallback():
        state = inspect()
        return state if len(state['regreet']) == 1 and not state['qs'] else None
    wait(fallback)
    shot('regreet-after-qml-load-error')
    # The common scenario cleanup restores original bytes even on failure.
    return 'broken installed QML reached ReGreet'


def early_exit():
    guest('break-session')
    guest('select-emaki')
    restart()
    before = greeter()
    first_mark = journal_mark()
    keys('text', 'key:Return', text=TARGET.password)
    def returned():
        observed = inspect()
        return observed if healthy(observed) and observed['qs'][0] != before else None
    wait(returned)
    wait_greeter()
    first_journal = journal_since(first_mark)
    assert pam_session_opened(first_journal), first_journal
    (OUT / 'early-exit-first-login-journal.txt').write_text(first_journal)
    state = inspect()['memory']
    assert state['users'][TARGET.user]['failed_session'] == 'niri-emaki.desktop', state
    shot('after-immediate-session-death')
    # Restore the real Emaki command before the recovery login. That login must
    # still use stock Niri exactly once, without overwriting remembered Emaki.
    guest('restore-session')
    stock_mark = journal_mark()
    keys('text', 'key:Return', text=TARGET.password)
    stock = session('niri.service', stock_mark)
    (OUT / 'early-exit-stock-journal.txt').write_text(stock['journal'])
    state = inspect()['memory']
    assert state['users'][TARGET.user]['last_session'] == 'niri-emaki.desktop', state
    shot('stock-niri-after-early-death')
    logout()
    retry_mark = journal_mark()
    keys('text', 'key:Return', text=TARGET.password)
    retried = session('niri-emaki.service', retry_mark)
    (OUT / 'early-exit-emaki-return-journal.txt').write_text(retried['journal'])
    shot('emaki-after-one-stock-login')
    time.sleep(31)
    logout()
    return 'immediate Emaki exit; exactly one stock login; following UI login returned to remembered Emaki'


def tty_proof(name):
    keys('chord:Control_L+Alt_L+F2')
    def prompt():
        state = inspect()
        return state if (state['active_tty'] == 'tty2' and state['getty'] == 'active'
                         and re.search(r'\blogin:\s*', state['tty2_text'], re.IGNORECASE)) else None
    state = wait(prompt)
    # /dev/vcs2 is the tty2 text buffer, unlike grim which captures Wayland even
    # when another VT is active. Save the prompt alongside active-VT/getty state.
    (OUT / (name + '.txt')).write_text(state['tty2_text'])
    (OUT / (name + '-state.json')).write_text(json.dumps(dict(active_tty=state['active_tty'], getty=state['getty']), indent=2))


def tty():
    try:
        tty_proof('tty2-getty')
    finally:
        keys('chord:Control_L+Alt_L+F1')
    wait_greeter()
    return 'Ctrl+Alt+F2 selected active tty2/getty and /dev/vcs2 contained its login prompt'


def hung():
    pid = greeter()
    guest('kill', pid=pid, signal='SIGSTOP')
    try:
        tty_proof('tty2-with-hung-greeter')
    finally:
        guest('kill', pid=pid, signal='SIGCONT')
        keys('chord:Control_L+Alt_L+F1')
    wait_greeter()
    return 'SIGSTOP greeter left Ctrl+Alt+F2/getty usable; tty2 prompt captured from /dev/vcs2'


def scenario(name, operation):
    started = time.time()
    row = dict(scenario=name, passed=False)
    recovered = False
    try:
        recover()
        row['detail'] = operation()
        row['passed'] = True
    except Exception as error:
        row['detail'] = str(error)
    finally:
        try:
            # Failed input/auth steps may leave a healthy-looking process with
            # a partial secret or conversation. Replace it during recovery.
            recover(force=not row['passed'])
            recovered = True
        except Exception as error:
            row['passed'] = False
            row['cleanup_error'] = str(error)
            row['detail'] += '; greeter recovery failed; later scenarios will not run'
    if not row['passed']:
        FAILURES.append(name)
    row['elapsed'] = round(time.time() - started, 3)
    with (OUT / 'results.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    print(('PASS ' if row['passed'] else 'FAIL ') + name + ': ' + row['detail'], flush=True)
    return recovered


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', action='append', choices=['ui', 'escape', 'kill', 'crash', 'broken-qml', 'early-exit', 'tty', 'hang'])
    args = parser.parse_args()
    configure(args)
    OUT = args.output.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'results.jsonl').exists():
        parser.error('--output already contains a run; choose a fresh directory to preserve evidence')
    # The configured guest transport is the only route to session/root commands. Require QEMU before
    # uploading or modifying anything; there is no host fallback.
    assert checked_remote('systemd-detect-virt --vm').strip() in ('qemu', 'kvm'), 'requires disposable QEMU/KVM VM'
    for source, destination in [('guest-greeter.py', '/tmp/c9-guest-greeter.py'), ('guest-keys.py', '/tmp/c9-guest-keys.py')]:
        checked_remote('cat > ' + shlex.quote(destination), data=(HERE / source).read_text())
    operations = {'ui': ui_login, 'escape': escape_check, 'kill': killed, 'crash': crashed, 'broken-qml': broken, 'early-exit': early_exit, 'tty': tty, 'hang': hung}
    for name in args.scenario or operations:
        if not scenario(name, operations[name]):
            print('STOP: cleanup could not establish a healthy greeter; restore the disposable VM before continuing.')
            break
    print('VM evidence: ' + str(OUT))
    print('Manual review required: pour/drain/wrong text/cleared field and ReGreet UI login.')
    if CAPTURE_NOTES:
        print('Capture notes: ' + '; '.join(CAPTURE_NOTES) + '. TTY proof uses /dev/vcs2.')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    raise SystemExit(main())
