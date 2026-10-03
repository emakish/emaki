#!/usr/bin/env python3
"""VM ONLY: C8 acceptance, run as arch inside the disposable VM checkout.

  python3 tests/vm/check-lock.py
  python3 tests/vm/check-lock.py --two-outputs

Requires installed committed C8, python-evdev, grim and sudo for uinput. Password
is the VM fixture `arch`; it is passed only over stdin to the keyboard injector.
Screenshots are VM evidence only, never production lock captures. This script
never suspends; the panel path uses a fake systemctl that verifies lock status.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
KEYS = ROOT / 'tests/vm/guest-keys.py'
FRAMES = ROOT / 'tests/vm/guest-frames.sh'
OUT = ROOT / '.cache' / ('lock-vm-' + time.strftime('%Y%m%d-%H%M%S'))
FAILURES = []
SKIPS = []
OWNED = []
LOCK_OWNERS = []
AUTH_SUBMITTED = set()
BACKEND_CHECKS = []


class CoverageUnavailable(Exception):
    """The available guest hardware cannot exercise requested coverage."""


def run(argv, **kwargs):
    kwargs.setdefault('timeout', 25)
    return subprocess.run(argv, text=True, capture_output=True, **kwargs)


def status():
    result = run(['emaki-lock', 'status'])
    return json.loads(result.stdout or '{}')


def status_evidence():
    try:
        return json.dumps(status(), sort_keys=True)
    except Exception as error:
        return 'status unavailable: ' + str(error)


def require_quickshell(supervisor_pid, expected_pid=None):
    state = status()
    detail = '; status=' + json.dumps(state, sort_keys=True)
    assert state.get('backend') == 'quickshell', ('expected backend quickshell, got '
            + str(state.get('backend')) + detail)
    assert state.get('secure') and state.get('poured'), 'Quickshell lock is not confirmed' + detail
    pid = owned_quickshell(supervisor_pid)
    assert expected_pid is None or pid == expected_pid, 'owned Quickshell child changed' + detail
    BACKEND_CHECKS.append(dict(supervisor_pid=supervisor_pid, locker_pid=pid, state=state))
    return state, pid


def wait(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.1)
    raise AssertionError('deadline; status=' + json.dumps(status()))


def keys(*steps, text=''):
    result = run(['sudo', '-n', 'python3', str(KEYS), *steps], input=text)
    assert result.returncode == 0, result.stderr


def start(preserve_capture=False, **environment):
    assert status().get('state') == 'unavailable', 'start with no existing VM lock supervisor'
    # Keep the exact owner pid, so crash tests never signal an unrelated locker.
    owner = subprocess.Popen(['emaki-lock', '--supervise'], env=dict(os.environ, **environment),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    OWNED.append(owner)
    LOCK_OWNERS.append(owner)
    wait(lambda: status().get('state') != 'unavailable')
    assert owner.poll() is None, 'test owner did not acquire supervision; status=' + status_evidence()
    if preserve_capture:
        # The panel's confirm-only path retains the image; --wait prepares sleep.
        result = run(['emaki-lock', '--confirm'])
        assert result.returncode == 0, 'capture-preserving confirmation failed; status=' + status_evidence()
    else:
        result = run(['emaki-lock', '--wait'])
        assert result.returncode == 0, 'lock confirmation failed; status=' + status_evidence()
    require_quickshell(owner.pid)
    return owner


def layouts():
    result = run(['niri', 'msg', '--json', 'keyboard-layouts'])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def select_layout(target):
    for _ in range(len(layouts()['names']) + 1):
        before = layouts()['current_idx']
        if before == target: return
        # Production Mod+Space is allowed by niri during session lock.
        keys('chord:Super_L+space')
        wait(lambda: layouts()['current_idx'] != before, timeout=3)
    raise AssertionError('layout shortcut did not reach requested index')


def unlock(owner, expected_backend=None):
    if expected_backend == 'quickshell':
        require_quickshell(owner.pid)
    assert owner not in AUTH_SUBMITTED, 'authentication already submitted; leave coverage for inspection instead of risking faillock'
    current = layouts(); original = current['current_idx']
    english = next(i for i, name in enumerate(current['names']) if 'English' in name)
    select_layout(english)
    AUTH_SUBMITTED.add(owner)
    keys('key:Escape', 'text', 'key:Return', text='arch')
    owner.wait(timeout=12)
    wait(lambda: status().get('state') == 'unavailable')
    select_layout(original)


def shot(name):
    target = OUT / (name + '.png')
    result = run(['grim', str(target)])
    assert result.returncode == 0, result.stderr
    return target


def owned_quickshell(supervisor_pid):
    candidates = []
    try:
        children = Path(f'/proc/{supervisor_pid}/task/{supervisor_pid}/children').read_text().split()
        for pid in children:
            try:
                argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            # Match argv entries, not a substring in a shell wrapper or another QML path.
            if (Path(os.fsdecode(argv[0])).name in ('qs', 'quickshell')
                    and len(argv) > 2 and argv[1] == b'-p'
                    and Path(os.fsdecode(argv[2])).name == 'lock.qml'):
                candidates.append(int(pid))
    except OSError as error:
        raise AssertionError('cannot inspect owned Quickshell child: ' + str(error)
                             + '; status=' + status_evidence()) from error
    assert len(candidates) == 1, ('expected one owned lock.qml child, got '
            + str(candidates) + '; status=' + status_evidence())
    return candidates[0]


def child(owner):
    return owned_quickshell(owner.pid)


def burst(name, count=120, cadence='.05'):
    archive = (OUT / (name + '.tar')).open('wb')
    process = subprocess.Popen(['bash', str(FRAMES), str(count), 'full', '0', '', cadence], stdout=archive)
    time.sleep(.2)  # Recorder starts before the tested action.
    return process, archive


def end_burst(recording):
    process, archive = recording
    process.wait(timeout=30)
    archive.close()
    assert process.returncode == 0, 'frame recorder failed'


def scenario(name, operation):
    before = len(LOCK_OWNERS)
    checked_before = len(BACKEND_CHECKS)
    evidence = None
    errors = []
    skipped = None
    try:
        evidence = operation()
    except CoverageUnavailable as error:
        skipped = str(error)
    except Exception as error:
        errors.append(str(error) + '; status=' + status_evidence())
    finally:
        for owner in LOCK_OWNERS[before:]:
            if owner.poll() is None:
                try:
                    # Recover only locks started by this scenario, through PAM.
                    # No process kill or compositor unlock bypass is a cleanup step.
                    run(['niri', 'msg', 'action', 'power-on-monitors'])
                    unlock(owner)
                except Exception as error:
                    errors.append('authenticated cleanup failed: ' + str(error))
        result = dict(scenario=name, passed=False if errors else None if skipped else True,
                      evidence=str(evidence or skipped or ''), errors=errors,
                      backend_checks=BACKEND_CHECKS[checked_before:], skipped=skipped)
        with (OUT / 'results.jsonl').open('a') as stream:
            stream.write(json.dumps(result) + '\n')
    if errors:
        FAILURES.append(name)
        print('FAIL', name, '; '.join(errors), flush=True)
    elif skipped:
        SKIPS.append(name)
        print('SKIP', name, skipped, flush=True)
    else:
        print('PASS', name, str(evidence or ''), flush=True)


def basic():
    owner = start(); shot('locked'); unlock(owner, 'quickshell')
    return 'compositor+pour acknowledged; correct PAM password exits owner'


def wrong():
    owner = start()
    recording = burst('wrong-attempt')
    keys('text', 'key:Return', text='wrong-once')
    time.sleep(2.5); shot('wrong-password')
    end_burst(recording)
    assert owner.poll() is None and status()['secure']
    time.sleep(2.1); shot('wrong-cleared')
    unlock(owner, 'quickshell')
    return 'one wrong attempt only, then correct; inspect wrong-password/cleared PNGs'


def caps():
    owner = start()
    keys('key:Caps_Lock')
    try:
        time.sleep(.6)  # The single authoritative LED poll is 500 ms.
        shot('caps-lock')
    finally:
        keys('key:Caps_Lock')
    unlock(owner, 'quickshell')
    return 'Caps toggle remains locked; inspect caps-lock.png indicator'


def russian():
    current = layouts(); names = current['names']
    assert any('Russian' in name for name in names), 'VM niri must configure us,ru'
    original = current['current_idx']; target = next(i for i, name in enumerate(names) if 'Russian' in name)
    owner = start()
    select_layout(target)
    keys('layout:ru', 'text', text='фкср'); shot('russian-input')
    keys('key:Escape')
    unlock(owner, 'quickshell')
    select_layout(original)
    return 'real Mod+Space switches Russian/English while locked; text cleared before correct PAM attempt'


def recovery(sig):
    owner = start(); _, pid = require_quickshell(owner.pid)
    recording = burst('recovery-' + signal.Signals(sig).name)
    os.kill(pid, sig)
    wait(lambda: status().get('backend') == 'hyprlock' and status().get('secure'), timeout=15)
    shot('recovery-' + signal.Signals(sig).name)
    end_burst(recording)
    unlock(owner)
    return 'owned QS pid ' + str(pid) + ' -> compositor-confirmed hyprlock; review coverage frames live'


def flat():
    owner = start(EMAKI_SHELL_SHADER_DIR=str(OUT / 'missing-shaders'))
    shot('flat-missing-shaders'); unlock(owner, 'quickshell')
    return 'missing shader directory still authenticates'


def output_command(evidence, *arguments):
    argv = ['niri', 'msg', '--json', *arguments]
    result = run(argv)
    evidence['commands'].append(dict(argv=argv, returncode=result.returncode,
                                     stdout=result.stdout, stderr=result.stderr))
    return result


def query_outputs(evidence):
    result = output_command(evidence, 'outputs')
    assert result.returncode == 0, 'cannot query guest outputs: ' + result.stderr
    data = json.loads(result.stdout)
    evidence['observations'].append(data)
    return data


def active_outputs(data):
    return [name for name, output in data.items() if output.get('current_mode') is not None]


def prepare_outputs(two, evidence):
    data = query_outputs(evidence)
    if two and len(active_outputs(data)) < 2:
        # niri reports connected outputs, including disabled ones. An unconnected
        # virtio head may be absent entirely; successful IPC only saves its config.
        candidates = [name for name in data if name not in active_outputs(data)]
        if 'Virtual-2' not in data:
            candidates.append('Virtual-2')
        for name in candidates:
            output_command(evidence, 'output', name, 'on')
            output_command(evidence, 'output', name, 'mode', 'auto')
            deadline = time.monotonic() + 2
            while True:
                data = query_outputs(evidence)
                if len(active_outputs(data)) >= 2 or time.monotonic() >= deadline:
                    break
                time.sleep(.2)
            if len(active_outputs(data)) >= 2:
                break
    return data


def outputs(two):
    target = OUT / 'output-topology.json'
    evidence = dict(requested_outputs=2 if two else 1, commands=[], observations=[],
                    drm_connectors={}, off_on=[])
    try:
        for entry in Path('/sys/class/drm').glob('card*-*/status'):
            try:
                evidence['drm_connectors'][entry.parent.name] = entry.read_text().strip()
            except OSError as error:
                evidence['drm_connectors'][entry.parent.name] = str(error)
        data = prepare_outputs(two, evidence)
        names = active_outputs(data)
        assert names, 'niri reports no output with a current mode; observed=' + json.dumps(data)
        evidence['two_output_coverage'] = 'available' if len(names) >= 2 else 'unavailable'
        owner = start()
        for name in names:
            off = output_command(evidence, 'output', name, 'off')
            assert off.returncode == 0, 'output off failed: ' + off.stderr
            try:
                time.sleep(.7)
            finally:
                on = output_command(evidence, 'output', name, 'on')
                assert on.returncode == 0, 'output on failed: ' + on.stderr
            wait(lambda: name in active_outputs(query_outputs(evidence)))
            wait(lambda: status().get('secure') and status().get('poured'))
            state, pid = require_quickshell(owner.pid)
            evidence['off_on'].append(dict(output=name, state=state, locker_pid=pid))
        shot('outputs-restored'); unlock(owner, 'quickshell')
        evidence['off_on_passed'] = True
        if two and len(names) < 2:
            raise CoverageUnavailable('two-output coverage unavailable: niri exposed only '
                    + ', '.join(names) + ' with a mode after activation attempts; '
                    + 'off/on PASS on the available output; evidence=' + str(target))
        return target
    finally:
        target.write_text(json.dumps(evidence, indent=2) + '\n')


def dpms():
    evidence = []
    # Both first acquisition while off and prepare-sleep on an established lock.
    for initial in (True, False):
        owner = None if initial else start()
        assert run(['niri', 'msg', 'action', 'power-off-monitors']).returncode == 0
        try:
            time.sleep(.5)
            if initial:
                owner = start()
            else:
                before = child(owner)
                confirmation = run(['emaki-lock', '--wait'])
                assert confirmation.returncode == 0, confirmation.stdout + confirmation.stderr
                assert child(owner) == before, 'DPMS must not kill a healthy QS locker'
            state, _ = require_quickshell(owner.pid)
            evidence.append(dict(initial=initial, state=state))
            shot('dpms-initial' if initial else 'dpms-wait')
            unlock(owner, 'quickshell')
        finally:
            run(['niri', 'msg', 'action', 'power-on-monitors'])
    target = OUT / 'dpms-confirmation.json'
    target.write_text(json.dumps(evidence, indent=2))
    return target


def panel_sleep():
    # Production SystemBody.session/confirmSession + SystemService, isolated fixture
    # window; the real already-running shell is never restarted or reconfigured.
    directory = OUT / 'panel'; shutil.copytree(ROOT / 'shell', directory)
    shutil.copyfile(ROOT / 'tests/fixtures/ClockTest.qml', directory / 'shell.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemFixture.qml', directory / 'SystemFixture.qml')
    with (directory / 'qmldir').open('a') as handle: handle.write('\nSystemFixture 1.0 SystemFixture.qml\n')
    fake = OUT / 'systemctl'
    evidence = OUT / 'suspend-confirmation.json'
    lock_result = OUT / 'panel-lock-result.json'
    locker = OUT / 'panel-lock'
    locker.write_text('#!/usr/bin/env python3\nimport json,subprocess,sys,time\nfrom pathlib import Path\nassert sys.argv[1:]==["--wait"]\nr=subprocess.run(["emaki-lock","--wait"])\nPath(' + repr(str(lock_result)) + ').write_text(json.dumps(dict(returncode=r.returncode,returned_ns=time.monotonic_ns())))\nsys.exit(r.returncode)\n')
    locker.chmod(0o700)
    fake.write_text('#!/usr/bin/env python3\nimport json,subprocess,sys,time\nfrom pathlib import Path\ncalled=time.monotonic_ns()\nr=subprocess.run(["emaki-lock","status"],capture_output=True,text=True)\ns=json.loads(r.stdout)\nlocked=json.loads(Path(' + repr(str(lock_result)) + ').read_text())\nassert sys.argv[1:]==["suspend"] and s.get("secure") and s.get("poured")\nassert locked["returncode"]==0 and locked["returned_ns"]<called\nPath(' + repr(str(evidence)) + ').write_text(json.dumps(dict(lock=locked,systemctl_ns=called,coverage=s)))\n')
    fake.chmod(0o700)
    original = directory / 'helpers/system-tools-original.py'
    (directory / 'helpers/system-tools.py').rename(original)
    (directory / 'helpers/system-tools.py').write_text(
        'import json,subprocess,sys,time\nfrom pathlib import Path\n'
        'request=sys.stdin.buffer.readline(4097)\n'
        'r=subprocess.run([sys.executable,' + repr(str(original)) + '],input=request,capture_output=True)\n'
        'evidence=Path(' + repr(str(evidence)) + ')\n'
        'if json.loads(request).get("op")=="session" and evidence.exists():\n'
        ' state=json.loads(evidence.read_text());state["helper_response"]=json.loads(r.stdout);state["helper_returned_ns"]=time.monotonic_ns();evidence.write_text(json.dumps(state,indent=2))\n'
        'sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);sys.exit(r.returncode)\n')
    env = dict(os.environ, EMAKI_TEST_SYSTEM='1', EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0',
               EMAKI_SHELL_TRAY='0', EMAKI_SYSTEMCTL=str(fake), EMAKI_LOCK=str(locker))
    panel = subprocess.Popen(['qs', '-p', str(directory)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    OWNED.append(panel)
    def ipc(*arguments):
        result = run(['qs', '-p', str(directory), 'ipc', 'call', 'test', *arguments])
        assert result.returncode == 0, result.stderr
        return result.stdout
    try:
        time.sleep(2)
        ipc('system', 'power'); ipc('session', 'suspend')
        assert not evidence.exists(), 'sleep must wait for human panel confirmation'
        # Establish the production backend first. The panel's suspend --wait still
        # clears the capture and must finish before fake systemctl can be called.
        owner = start(preserve_capture=True)
        ipc('confirmSession')
        wait(evidence.exists, timeout=20)
        def helper_finished():
            try:
                return json.loads(evidence.read_text()).get('helper_response', {}).get('state') == 'requested'
            except (OSError, ValueError):
                return False
        wait(helper_finished, timeout=5)
        require_quickshell(owner.pid)
        unlock(owner, 'quickshell')
    finally:
        panel.terminate(); panel.wait(timeout=5)
    return evidence


def frames():
    recording = burst('pour', count=80, cadence='.025')
    owner = start(preserve_capture=True)
    end_burst(recording)
    select_layout(next(i for i, name in enumerate(layouts()['names']) if 'English' in name))
    require_quickshell(owner.pid)
    recording = burst('drain', count=120, cadence='.035')
    AUTH_SUBMITTED.add(owner)
    keys('text', 'key:Return', text='arch')
    end_burst(recording)
    owner.wait(timeout=15)
    return 'pour.tar/drain.tar; visual edge/coverage judgement required'


def scope_isolation():
    result = run([sys.executable, str(ROOT / 'tests/vm/check-lock-scope.py')], timeout=90)
    evidence = OUT / 'scope-check.log'
    evidence.write_text(result.stdout + result.stderr)
    assert result.returncode == 0, ('scope check failed; evidence: ' + str(evidence)
                                    + '; ' + result.stdout.strip()
                                    + '; status=' + status_evidence())
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--two-outputs', action='store_true')
    args = parser.parse_args()
    assert os.environ.get('USER') == 'arch', 'disposable VM user arch required'
    assert os.environ.get('WAYLAND_DISPLAY') and os.environ.get('NIRI_SOCKET'), 'run in VM niri session'
    OUT.mkdir(parents=True)
    scenario('lock/unlock', basic)
    scenario('real client survives caller service stop', scope_isolation)
    scenario('wrong password then correct', wrong)
    scenario('Caps Lock', caps)
    scenario('Russian layout', russian)
    scenario('SIGKILL recovery', lambda: recovery(signal.SIGKILL))
    scenario('SIGSTOP recovery', lambda: recovery(signal.SIGSTOP))
    scenario('flat shader fallback', flat)
    scenario('output off/on and topology', lambda: outputs(args.two_outputs))
    scenario('DPMS initial lock and wait confirmation', dpms)
    scenario('panel sleep waits; fake systemctl only', panel_sleep)
    scenario('pour/drain frame evidence', frames)
    print(('FAIL' if FAILURES else 'INCOMPLETE' if SKIPS else 'PASS'),
          'acceptance commands complete; visually review', OUT)
    return 1 if FAILURES else 2 if SKIPS else 0


if __name__ == '__main__':
    raise SystemExit(main())
