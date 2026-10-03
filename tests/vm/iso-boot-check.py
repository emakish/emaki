#!/usr/bin/env python3
"""Installed-system acceptance; start before run-iso.sh --no-cd to catch GRUB."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('iso_monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


# Exact QEMU hardware limitations and the first raw-greetd login before a
# keyring exists. All other error messages fail acceptance.
JOURNAL_ERROR_ALLOWLIST = tuple(re.compile(re.escape(line)) for line in (
    'i8042: PNP: No PS/2 controller found.',
    'virt/tdx: TDX not supported by the host platform',
    "gkr-pam: couldn't unlock the login keyring.",
))


def journal_errors_ok(output):
    print(output, end='' if output.endswith('\n') else '\n', flush=True)
    return all(not line.strip() or line == '-- No entries --'
               or any(pattern.fullmatch(line) for pattern in JOURNAL_ERROR_ALLOWLIST)
               for line in output.splitlines())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', nargs='?', default='erase-btrfs')
    parser.add_argument('--dir', default=os.environ.get('EMAKI_ISO_VM_DIR', str(Path.home() / 'VMs/iso-vm')))
    parser.add_argument('--ssh-port', type=int)
    parser.add_argument('--user', help='installed account (default: fixture user)')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]+', args.fixture):
        parser.error('invalid fixture name')
    fixture = HERE.parents[1] / 'installer/fixtures' / f'plan-{args.fixture}.json'
    if not fixture.exists():
        fixture = HERE / 'fixtures' / f'plan-{args.fixture}.json'
    last_run = Path(args.dir) / 'last-install-run'
    if last_run.is_file():
        recorded = Path(last_run.read_text().strip()) / 'plan.json'
        if recorded.is_file() and f'-{args.fixture}-' in recorded.parent.name:
            fixture = recorded
    data = json.loads(fixture.read_text())
    config = data.get('config', data)
    user = args.user or config['user']['login']
    if not re.fullmatch('[a-z_][a-z0-9_-]*', user):
        parser.error('invalid installed username')
    password = config['user']['password']
    if '\n' in password or '\r' in password:
        parser.error('fixture password must be a single line')
    vm = Path(args.dir).resolve()
    vm.mkdir(parents=True, exist_ok=True)
    run = vm / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + f'-boot-{os.getpid()}')
    run.mkdir(parents=True)
    failures = []

    def status(ok, label):
        line = ('OK: ' if ok else 'BAD: ') + label
        print(line, flush=True)
        with (run / 'summary.txt').open('a') as stream:
            stream.write(line + '\n')
        if not ok:
            failures.append(label)
        return ok

    # Start the checker BEFORE QEMU. Repeated Up keys span firmware handoff and
    # stop GRUB's five-second countdown as soon as it becomes ready for input.
    deadline = time.monotonic() + 60
    connected = False
    while time.monotonic() < deadline:
        try:
            monitor.command(vm, 'sendkey up', timeout=1)
            connected = True
            break
        except (OSError, RuntimeError):
            time.sleep(.1)
    if not status(connected, 'QEMU monitor available for early GRUB capture'):
        return 1
    capture_ok = False
    start = time.monotonic()
    next_capture = 3
    with (run / 'grub-monitor.log').open('w') as log:
        while time.monotonic() - start < 15:
            log.write(monitor.command(vm, 'sendkey up'))
            elapsed = time.monotonic() - start
            if elapsed >= next_capture:
                output = run / f'grub-{next_capture:02d}.png'
                response = monitor.command(vm, f'screendump {json.dumps(str(output))} -f png')
                log.write(response)
                if output.is_file() and output.read_bytes().startswith(b'\x89PNG'):
                    capture_ok = True
                next_capture += 4
            time.sleep(.25)
        log.write(monitor.command(vm, 'sendkey home'))
        time.sleep(.2)
        log.write(monitor.command(vm, 'sendkey ret'))
    status(capture_ok, 'GRUB monitor frames saved; inspect Emaki title, theme and both kernels'
           if capture_ok else 'GRUB screenshot unavailable; virgl may report no surface (see grub-monitor.log)')
    port_file = vm / 'ssh-port'
    port = args.ssh_port or int(os.environ.get('EMAKI_ISO_SSH_PORT', port_file.read_text().strip() if port_file.exists() else '2223'))
    common = ['--dir', str(vm), '--ssh-port', str(port), '--user', user]
    wait = subprocess.run([str(HERE / 'iso-wait-ssh.sh'), *common], capture_output=True)
    (run / 'wait-ssh.log').write_bytes(wait.stdout + wait.stderr)
    if not status(wait.returncode == 0, f'installed SSH account {user}'):
        return 1
    ssh = [str(HERE / 'iso-ssh.sh'), *common, '--']

    def remote(command, *, privileged=False, input_data=b'', timeout=180):
        if privileged:
            command = 'sudo -k -S -p "" -- ' + command
            input_data = (password + '\n').encode() + input_data
        return subprocess.run(ssh + [command], input=input_data, capture_output=True, timeout=timeout)

    def check(label, command, *, privileged=False, predicate=None, timeout=180):
        result = remote(command, privileged=privileged, timeout=timeout)
        (run / f'{label}.log').write_bytes(result.stdout + result.stderr)
        ok = result.returncode == 0 and (predicate(result.stdout.decode(errors='replace')) if predicate else True)
        status(ok, label)
        return result

    if check('installed-not-live', 'test ! -d /run/archiso/bootmnt && case $(systemd-detect-virt --vm) in qemu|kvm) ;; *) exit 1;; esac').returncode != 0:
        return 1
    check('failed-units', 'systemctl --failed --no-legend --plain', predicate=lambda output: not output.strip())
    check('journal-errors', 'journalctl -p err -b --no-pager -o cat', privileged=True,
          predicate=journal_errors_ok)
    check('emaki-package', 'LC_ALL=C pacman -Qi emaki', predicate=lambda output: bool(re.search(r'^Version\s*:\s*0\.1\.0(?:-|\s)', output, re.M)))
    check('package-ownership', 'pacman -Qo /usr/bin/emaki /usr/share/emaki/shell/shell.qml', predicate=lambda output: len(output.strip().splitlines()) == 2)
    check('network', 'LC_ALL=C nmcli -t general', predicate=lambda output: output.startswith('connected:'))
    check('emaki-repository', 'pacman-conf --repo emaki Server', predicate=lambda output: bool(output.strip()))
    check('package-update', 'pacman -Syu --noconfirm', privileged=True, timeout=1800)
    fs = check('root-filesystem', 'findmnt -n -o FSTYPE /').stdout.decode().strip()
    if fs == 'btrfs':
        check('snapper', 'env LC_ALL=C snapper -c root list', privileged=True, predicate=lambda output: bool(re.search(r'^\s*[1-9][0-9]*\s*\|', output, re.M)))
        check('snapshot-grub', "grep -E 'snapshot|Snapshot' /boot/grub/grub-btrfs.cfg", privileged=True)
    check('grub-emaki', 'grep -c Emaki /boot/grub/grub.cfg', privileged=True,
          predicate=lambda output: output.strip().isdigit() and int(output.strip()) >= 1)
    check('grub-kernels', "sh -c 'grep -q vmlinuz-linux /boot/grub/grub.cfg && grep -q vmlinuz-linux-lts /boot/grub/grub.cfg'", privileged=True)
    check('grub-lts-title', '''grep -Fq "menuentry 'Emaki, with Linux linux-lts'" /boot/grub/grub.cfg''', privileged=True)
    shot = subprocess.run([str(HERE / 'iso-shot.sh'), *common, '--fixture', str(fixture), str(run / 'greeter.png')], capture_output=True)
    (run / 'greeter-shot.log').write_bytes(shot.stdout + shot.stderr)
    status(shot.returncode == 0, 'greeter screenshot (visual review required)')
    upload = remote('umask 077; cat > "$HOME/iso-guest-login.py"', input_data=(HERE / 'guest-login.py').read_bytes())
    if status(upload.returncode == 0, 'guest-login.py uploaded'):
        # sudo consumes its password line; the remaining bytes are the exact PAM
        # password expected by guest-login.py (no trailing newline).
        login = remote(f'python3 /home/{user}/iso-guest-login.py {user} niri-emaki-session',
                       privileged=True, input_data=password.encode(), timeout=60)
        (run / 'login.log').write_bytes(login.stdout + login.stderr)
        status(login.returncode == 0, 'raw greetd login (does not test greeter UI)')
        ready = False
        for _ in range(30):
            session = remote('XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus systemctl --user is-active niri-emaki.service emaki-shell.service')
            if session.returncode == 0:
                ready = True
                break
            time.sleep(1)
        status(ready, 'installed compositor and shell active')
        shot = subprocess.run([str(HERE / 'iso-shot.sh'), *common, '--fixture', str(fixture), str(run / 'desktop.png')], capture_output=True)
        (run / 'desktop-shot.log').write_bytes(shot.stdout + shot.stderr)
        status(shot.returncode == 0, 'desktop screenshot (inspect ring and shell)')
    print(f'Evidence: {run}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f'BAD: boot check: {error}')
