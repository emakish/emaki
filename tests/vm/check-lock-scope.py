#!/usr/bin/env python3
"""VM ONLY: prove the real emaki-lock client escapes a shell-like service cgroup.

Run as the fixture account in the disposable guest's niri session:
  python3 tests/vm/check-lock-scope.py

Creates/stops only a uniquely named dummy service, never emaki-shell.service.
The dummy calls the installed emaki-lock --wait through its normal systemd-run
client path. Cleanup authenticates only this test's tagged locker, using the VM
password through guest-keys.py stdin. It never kills a locker or bypasses PAM.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
TOKEN_KEY = 'EMAKI_VM_SCOPE_TEST'


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def process(pid):
    proc = Path('/proc') / str(pid)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] != 'Z', 'process is already a zombie'
    assert proc.stat().st_uid == os.getuid(), 'process belongs to another uid'
    groups = (proc / 'cgroup').read_text().splitlines()
    cgroup = next(line.partition('::')[2] for line in groups if line.startswith('0::'))
    return dict(pid=pid, parent=int(fields[1]), start=fields[19], cgroup=cgroup)


def identity_alive(value):
    try:
        return process(value['pid'])['start'] == value['start']
    except (OSError, AssertionError):
        return False


def tagged(pid, token):
    # Inspect only the boolean membership; never store the process environment.
    try:
        return (TOKEN_KEY + '=' + token).encode() in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    except OSError:
        return False


def worker(directory):
    configuration = json.loads((directory / 'worker.json').read_text())
    write_json(directory / 'worker-start.json', process(os.getpid()))
    started = time.monotonic_ns()
    try:
        result = subprocess.run([configuration['locker'], '--wait'],
                                env=dict(os.environ, **configuration['environment']),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=22)
        code = result.returncode
    except (OSError, subprocess.TimeoutExpired):
        code = 125
    write_json(directory / 'client-result.json', dict(returncode=code, started_ns=started,
                                                    returned_ns=time.monotonic_ns()))
    while True:
        time.sleep(60)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--user', default='arch')
    parser.add_argument('--credentials-stdin', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.service_worker:
        worker(args.service_worker)
        return 0

    password = json.load(sys.stdin)['password'] if args.credentials_stdin else 'arch'
    token = uuid.uuid4().hex
    output = args.output or ROOT / '.cache' / ('lock-scope-vm-' + time.strftime('%Y%m%d-%H%M%S') + '-' + token[:8])
    output.mkdir(mode=0o700, parents=True)
    evidence = dict(scenario='real client survives caller service stop', passed=False, observations=[])
    errors = []
    unit = 'emaki-lock-caller-test-' + token + '.service'
    created = stopped = False
    directory = None
    helper = None
    original_layout = None
    confirmed_ownership = False

    def command(argv):
        return subprocess.run(argv, text=True, capture_output=True, timeout=25)

    def owned_child():
        assert directory is not None, 'no runtime directory established by this test'
        record = json.loads((directory / 'child.json').read_text())
        child = process(record['pid'])
        assert child['start'] == record['identity']['start'], 'locker PID was reused'
        assert record['identity']['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'stale boot identity'
        assert tagged(child['pid'], token), 'locker was not launched by this test service'
        parent = process(child['parent'])
        assert tagged(parent['pid'], token), 'supervisor was not launched by this test service'
        assert b'--supervise' in Path(f"/proc/{parent['pid']}/cmdline").read_bytes().split(b'\0'), 'parent is not the expected supervisor'
        return record, child, parent

    def authenticate_owned():
        assert not evidence.get('authentication_submitted'), 'previous authentication did not complete; leave coverage rather than repeat toward faillock'
        record, child, parent = owned_child()
        state = helper.status()
        assert state.get('secure'), 'owned locker has not confirmed coverage; leave it for inspection'
        current = helper.layouts()
        english = next(i for i, name in enumerate(current['names']) if 'English' in name)
        helper.select_layout(english)
        # Recheck ownership immediately before injecting the fixture password.
        now_record, now_child, _ = owned_child()
        assert now_child['pid'] == child['pid'] and now_record['identity'] == record['identity'], 'locker changed before authentication'
        evidence['authentication_submitted'] = True
        helper.keys('key:Escape', 'text', 'key:Return', text=password)
        helper.wait(lambda: helper.status().get('state') == 'unavailable', timeout=15)
        helper.wait(lambda: not identity_alive(parent), timeout=3)
        evidence['authenticated_cleanup'] = True

    try:
        assert pwd.getpwuid(os.getuid()).pw_name == args.user, 'fixture guest account required'
        assert command(['systemd-detect-virt', '--vm']).stdout.strip() in ('qemu', 'kvm'), 'disposable QEMU guest required'
        assert os.environ.get('WAYLAND_DISPLAY') and os.environ.get('NIRI_SOCKET'), 'run in the disposable VM niri session'
        helper = load_module('vm_lock_helpers', ROOT / 'tests/vm/check-lock.py')
        helper.USER = args.user
        helper.PASSWORD = password
        assert helper.status().get('state') == 'unavailable', 'refusing to touch an existing lock supervisor'
        # SourceFileLoader is needed because the production script has no .py suffix.
        from importlib.machinery import SourceFileLoader
        locker = '/usr/bin/emaki-lock'
        assert Path(locker).is_file(), 'installed emaki-lock is required'
        loader = SourceFileLoader('scope_lock_protocol', str(Path(locker).resolve()))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        protocol = importlib.util.module_from_spec(spec)
        loader.exec_module(protocol)
        directory = protocol.runtime_directory()
        assert not (directory / 'child.json').exists(), 'refusing to touch a recorded orphan or another test lock'
        original_layout = helper.layouts()['current_idx']
        environment = {key: os.environ[key] for key in
                       ('PATH', 'HOME', 'USER', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY', 'NIRI_SOCKET',
                        'DBUS_SESSION_BUS_ADDRESS', 'LANG', 'LC_CTYPE', 'LC_MESSAGES') if key in os.environ}
        environment[TOKEN_KEY] = token
        write_json(output / 'worker.json', dict(locker=locker, environment=environment))
        result = command(['systemd-run', '--user', '--quiet', '--collect', '--unit=' + unit,
                          '--property=Type=exec', '--property=KillMode=control-group',
                          '--property=TimeoutStopSec=3s', '--property=Restart=no',
                          sys.executable, str(Path(__file__).resolve()), '--service-worker', str(output)])
        assert result.returncode == 0, 'cannot create dummy service: ' + result.stderr.strip()
        created = True
        helper.wait(lambda: (output / 'client-result.json').exists(), timeout=25)
        evidence['client'] = json.loads((output / 'client-result.json').read_text())
        record, child, supervisor = owned_child()
        confirmed_ownership = True
        before, _ = helper.require_quickshell(supervisor['pid'], expected_pid=child['pid'])
        assert evidence['client']['returncode'] == 0, 'normal client did not confirm lock and full pour'
        assert record['backend'] == 'quickshell', ('expected recorded backend quickshell, got '
                + str(record['backend']) + '; status=' + helper.status_evidence())
        worker_process = json.loads((output / 'worker-start.json').read_text())
        assert identity_alive(worker_process), 'dummy service ended before the stop check'
        control = command(['systemctl', '--user', 'show', unit, '--property=ControlGroup', '--value'])
        assert control.returncode == 0
        group = control.stdout.strip()
        assert group and group.endswith('/' + unit) and worker_process['cgroup'] == group
        assert supervisor['cgroup'] == child['cgroup'], 'locker and supervisor must share their independent scope'
        assert supervisor['cgroup'] != group and not supervisor['cgroup'].startswith(group + '/'), 'lock is still in caller service cgroup'
        scope = supervisor['cgroup'].rsplit('/', 1)[-1]
        assert scope.startswith(directory.name + '-') and scope.endswith('.scope'), 'expected independent emaki-lock scope'
        evidence.update(dummy_service=dict(unit=unit, process=worker_process, cgroup=group),
                        locker=child, supervisor=supervisor, scope=scope)
        before, _ = helper.require_quickshell(supervisor['pid'], expected_pid=child['pid'])
        evidence['observations'].append(dict(moment='before caller stop', state=before))
        result = command(['systemctl', '--user', 'stop', unit])
        assert result.returncode == 0, 'failed to stop the owned dummy service'
        stopped = True
        assert not identity_alive(worker_process), 'dummy service process survived stop'
        for index in range(4):
            time.sleep(1)
            after_record, after_child, after_supervisor = owned_child()
            assert after_record['identity'] == record['identity'] and after_child == child and after_supervisor == supervisor, 'caller stop replaced or killed the lock stack'
            state, _ = helper.require_quickshell(supervisor['pid'], expected_pid=child['pid'])
            evidence['observations'].append(dict(moment='caller stopped +' + str(index + 1) + 's', state=state))
        authenticate_owned()
    except Exception as error:
        observed = helper.status_evidence() if helper is not None else 'helper not loaded'
        evidence['failure_status'] = observed
        errors.append(type(error).__name__ + ': ' + str(error) + '; status=' + observed)
    finally:
        # A launcher timeout can race a successfully executed worker. Its private
        # marker still proves this uniquely named service was created by the test.
        created = created or (output / 'worker-start.json').exists()
        if created and not stopped:
            try:
                result = command(['systemctl', '--user', 'stop', unit])
                assert result.returncode == 0, 'failed to stop owned dummy service during cleanup'
                stopped = True
            except Exception as error:
                errors.append('dummy service cleanup: ' + str(error))
        if created and helper is not None and not evidence.get('authenticated_cleanup'):
            try:
                if helper.status().get('state') != 'unavailable':
                    owned_child()  # A tag and live recorded identity are mandatory even after a failure.
                    confirmed_ownership = True
                    authenticate_owned()
            except Exception as error:
                errors.append('authenticated cleanup: ' + str(error))
        if helper is not None and original_layout is not None and evidence.get('authenticated_cleanup'):
            try:
                helper.select_layout(original_layout)
            except Exception as error:
                errors.append('layout restore: ' + str(error))
        evidence.update(passed=not errors, errors=errors, dummy_service_stopped=stopped,
                        ownership_verified=confirmed_ownership)
        write_json(output / 'result.json', evidence)
    print('PASS' if not errors else 'FAIL', evidence['scenario'], output / 'result.json', flush=True)
    for error in errors:
        print('FAIL', error, flush=True)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
