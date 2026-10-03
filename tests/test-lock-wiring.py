#!/usr/bin/env python3
"""Real system helper sleep ordering, using a blocking lock fake and no D-Bus."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import importlib.util
import contextlib
import io
import itertools
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='lw-', dir=ROOT / '.cache') as folder:
    profile = Path(folder)
    locker = profile / 'emaki-lock'
    locker.write_text('''#!/usr/bin/env python3
import os,sys,time
from pathlib import Path
p=Path(os.environ['LOCK_WIRING_FIXTURE'])
assert sys.argv[1:]==[os.environ['LOCK_WIRING_MODE']]
(p/'started').write_text('1')
deadline=time.monotonic()+3
while not (p/'release').exists():
 if time.monotonic()>deadline:sys.exit(1)
 time.sleep(.01)
if (p/'fail').exists():sys.exit(1)
(p/'confirmed').write_text('1')
''')
    system = profile / 'systemctl'
    system.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
p=Path(os.environ['LOCK_WIRING_FIXTURE'])
assert sys.argv[1:]==['suspend'] and (p/'confirmed').exists()
(p/'slept').write_text('1')
''')
    locker.chmod(0o700)
    system.chmod(0o700)
    env = dict(os.environ, EMAKI_LOCK=str(locker), EMAKI_SYSTEMCTL=str(system),
               LOCK_WIRING_FIXTURE=str(profile), PYTHONDONTWRITEBYTECODE='1',
               NIRI_SOCKET='', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'none'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'none-system'))
    for operation, fail in itertools.product(('lock', 'suspend'), (True, False)):
        env['LOCK_WIRING_MODE'] = '--confirm' if operation == 'lock' else '--wait'
        if fail:
            (profile / 'fail').touch()
        else:
            (profile / 'fail').unlink()
        proc = subprocess.Popen([sys.executable, '-B', str(ROOT / 'shell/helpers/system-tools.py')],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env)
        try:
            proc.stdin.write(json.dumps(dict(op='lock') if operation == 'lock' else dict(op='session', value='suspend', confirmed=True)) + '\n')
            proc.stdin.flush()
            deadline = time.monotonic() + 2
            while not (profile / 'started').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert (profile / 'started').exists() and proc.poll() is None
            time.sleep(.1)
            assert not (profile / 'slept').exists(), 'alive but unconfirmed must never suspend'
            (profile / 'release').touch()
            output, error = proc.communicate(timeout=5)
            assert proc.returncode == 0 and not error, (output, error)
            state = json.loads(output)['state']
            assert state == ('lock_failed' if fail else 'locked' if operation == 'lock' else 'requested'), state
            assert (profile / 'slept').exists() == (operation == 'suspend' and not fail)
            print('PASS system helper:', operation, 'fails closed' if fail else 'waits for delayed confirmation')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        for name in ('started', 'release', 'confirmed', 'slept'):
            (profile / name).unlink(missing_ok=True)

# VM scenario scheduling is testable without a VM: a failure must clean up only
# its own lock through authentication and still record/run the next scenario.
spec = importlib.util.spec_from_file_location('lock_vm_fixture', ROOT / 'tests/vm/check-lock.py')
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)
with tempfile.TemporaryDirectory(prefix='lv-', dir=ROOT / '.cache') as folder:
    vm.OUT = Path(folder)
    class Owner:
        alive = True
        def poll(self):
            return None if self.alive else 0
    owned = Owner()
    cleaned = []
    def authenticated_cleanup(owner):
        assert owner is owned
        owner.alive = False
        cleaned.append(owner)
    vm.unlock = authenticated_cleanup
    vm.run = lambda argv: None
    def failing():
        vm.LOCK_OWNERS.append(owned)
        raise AssertionError('intentional scenario failure')
    with contextlib.redirect_stdout(io.StringIO()):
        vm.scenario('intentional failure', failing)
        vm.scenario('continues after failure', lambda: 'next evidence')
    records = [json.loads(line) for line in (vm.OUT / 'results.jsonl').read_text().splitlines()]
    assert [row['passed'] for row in records] == [False, True]
    assert cleaned == [owned] and vm.FAILURES == ['intentional failure']
    print('PASS: VM scenarios preserve evidence, authenticate owned cleanup, and continue after failure')
    # Materialize the VM panel wrappers but forbid starting a GUI/process. Their
    # exact generated sources, including returned helper JSON evidence, must parse.
    with patch.object(vm.subprocess, 'Popen', side_effect=RuntimeError('fixture stops before GUI start')):
        try:
            vm.panel_sleep()
            raise AssertionError('panel fixture must not start a process')
        except RuntimeError as error:
            assert str(error) == 'fixture stops before GUI start'
    for script in (vm.OUT / 'panel-lock', vm.OUT / 'systemctl', vm.OUT / 'panel/helpers/system-tools.py'):
        compile(script.read_text(), str(script), 'exec')
    print('PASS: VM panel evidence wrappers compile and retain lock result, sleep timestamp and helper JSON')

# A successful fallback must never count as a successful Quickshell scenario.
locked_qs = dict(state='locked', backend='quickshell', secure=True, poured=True)
locked_fallback = dict(locked_qs, backend='hyprlock')
fake_owner = SimpleNamespace(pid=321, poll=lambda: None)
for preserve_capture in (False, True):
    with patch.object(vm, 'status', side_effect=[dict(state='unavailable'), locked_fallback]), \
            patch.object(vm, 'run', return_value=SimpleNamespace(returncode=0)), \
            patch.object(vm, 'wait'), patch.object(vm.subprocess, 'Popen', return_value=fake_owner), \
            patch.object(vm, 'owned_quickshell') as child_lookup:
        try:
            vm.start(preserve_capture=preserve_capture)
            raise AssertionError('hyprlock fallback must fail a Quickshell scenario')
        except AssertionError as error:
            assert 'expected backend quickshell, got hyprlock' in str(error), error
            assert '"backend": "hyprlock"' in str(error), error
        child_lookup.assert_not_called()
with patch.object(vm, 'status', return_value=locked_qs), \
        patch.object(vm, 'owned_quickshell', return_value=654) as child_lookup:
    observed, pid = vm.require_quickshell(321, expected_pid=654)
    assert observed == locked_qs and pid == 654
    child_lookup.assert_called_once_with(321)
    try:
        vm.require_quickshell(321, expected_pid=655)
        raise AssertionError('a replacement child must fail identity verification')
    except AssertionError as error:
        assert 'owned Quickshell child changed' in str(error) and 'status=' in str(error)
print('PASS: both lock start paths reject confirmed hyprlock; QS confirmation requires its exact owned child')

with patch.object(vm, 'status', return_value=locked_qs), \
        patch.object(Path, 'read_text', return_value='654 655'):
    # A substring match would accept either the gate's Python command text or a
    # different file whose name happens to contain lock.qml.
    for impostor in (b'python3\0-c\0exec lock.qml\0', b'qs\0-p\0/tmp/unlock.qml\0'):
        with patch.object(Path, 'read_bytes', side_effect=[
                b'/usr/bin/quickshell\0-p\0/usr/share/emaki/shell/lock.qml\0--no-color\0', impostor]):
            assert vm.owned_quickshell(321) == 654
    with patch.object(Path, 'read_bytes', return_value=b'hyprlock\0--grace\00\0'):
        try:
            vm.owned_quickshell(321)
            raise AssertionError('hyprlock is not an owned lock.qml child')
        except AssertionError as error:
            assert 'expected one owned lock.qml child' in str(error) and 'status=' in str(error)
print('PASS: VM child lookup matches direct-child executable and QML argv, with status in failure evidence')

# niri can accept a missing head's configuration without connecting that head.
# Exercise absent, visible-but-disabled, and newly connected Virtual-2 outputs.
first = {'Virtual-1': dict(current_mode=0)}
for topology in ('absent', 'disabled', 'connects'):
    commands = []
    current = dict(first)
    if topology == 'disabled':
        current['Virtual-2'] = dict(current_mode=None)
    def fake_niri(argv):
        commands.append(argv)
        if argv[-1] == 'outputs':
            return SimpleNamespace(returncode=0, stdout=json.dumps(current), stderr='')
        if argv[-2:] == ['mode', 'auto'] and topology != 'absent':
            current['Virtual-2'] = dict(current_mode=0)
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            'OutputWasMissing' if topology == 'absent' else 'Applied'), stderr='')
    evidence = dict(commands=[], observations=[])
    with patch.object(vm, 'run', side_effect=fake_niri), \
            patch.object(vm.time, 'monotonic', side_effect=itertools.count()), \
            patch.object(vm.time, 'sleep'):
        data = vm.prepare_outputs(True, evidence)
    assert ['niri', 'msg', '--json', 'output', 'Virtual-2', 'on'] in commands
    assert ['niri', 'msg', '--json', 'output', 'Virtual-2', 'mode', 'auto'] in commands
    assert len(vm.active_outputs(data)) == (1 if topology == 'absent' else 2)
    assert evidence['commands'] and evidence['observations']
print('PASS: VM output probing enables disabled/new heads and does not mistake OutputWasMissing for connection')

with tempfile.TemporaryDirectory(prefix='lt-', dir=ROOT / '.cache') as folder:
    vm.OUT = Path(folder)
    vm.SKIPS.clear()
    with patch.object(vm, 'prepare_outputs', return_value=first), \
            patch.object(vm, 'query_outputs', return_value=first), \
            patch.object(vm, 'start', return_value=fake_owner), \
            patch.object(vm, 'output_command', return_value=SimpleNamespace(returncode=0, stderr='')) as output_action, \
            patch.object(vm, 'require_quickshell', return_value=(locked_qs, 654)), \
            patch.object(vm, 'status', return_value=locked_qs), \
            patch.object(vm, 'shot'), patch.object(vm, 'unlock') as authenticate, \
            patch.object(vm, 'wait', side_effect=lambda predicate: predicate()), \
            patch.object(vm.time, 'sleep'), patch.object(Path, 'glob', return_value=[]), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        vm.scenario('output off/on and topology', lambda: vm.outputs(True))
    report = json.loads((vm.OUT / 'output-topology.json').read_text())
    row = json.loads((vm.OUT / 'results.jsonl').read_text())
    assert report['off_on_passed'] and report['two_output_coverage'] == 'unavailable'
    assert len(report['off_on']) == 1 and row['passed'] is None
    assert output_action.call_args_list[0].args[1:] == ('output', 'Virtual-1', 'off')
    assert output_action.call_args_list[1].args[1:] == ('output', 'Virtual-1', 'on')
    authenticate.assert_called_once_with(fake_owner, 'quickshell')
    assert vm.SKIPS == ['output off/on and topology']
    assert 'SKIP' in output.getvalue() and 'off/on PASS' in output.getvalue()
print('PASS: one-head guest still exercises off/on and authenticates; unavailable two-head coverage is SKIP, never PASS')
