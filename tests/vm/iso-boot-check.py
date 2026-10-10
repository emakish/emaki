#!/usr/bin/env python3
"""Installed-system acceptance; start before run-iso.sh --no-cd to catch GRUB."""
import argparse
import importlib.machinery
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
spec = importlib.util.spec_from_file_location('iso_shot', HERE / 'iso-shot.py')
shot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shot)
loader = importlib.machinery.SourceFileLoader('system_map', str(HERE.parents[1] / 'scripts/render-system-map'))
system_map = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(system_map)
# The installed page, so the check proves what shipped rather than this checkout.
SYSTEM_MAP = '/usr/share/emaki/system-map.md'
SESSION_ENV = 'XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus'


def map_state_commands(page):
    """(component, command) for every state the map promises; "none yet" entries are skipped."""
    entries, problems = system_map.components(page, SYSTEM_MAP)
    if problems or not entries:
        raise ValueError('; '.join(problems) or f'no system-map blocks in {SYSTEM_MAP}')
    return [(entry['component'], command) for entry in entries
            if (command := system_map.state_command(entry))]


def json_answer(output):
    try:
        json.loads(output)
    except ValueError:
        return False
    return True


# Exact QEMU hardware limitations, the duplicate packaged activation names
# shadowed by emaki-wallet-start's runtime overrides, and the denial that the
# sudo-user check itself provokes. All other errors fail acceptance.
JOURNAL_ERROR_ALLOWLIST = tuple(re.compile(re.escape(line)) for line in (
    'i8042: PNP: No PS/2 controller found.',
    'virt/tdx: TDX not supported by the host platform',
    *(f"Ignoring duplicate name '{name}' in service file '/usr/share/dbus-1/services/{name}.service'"
      for name in ('org.freedesktop.secrets', 'org.freedesktop.impl.portal.desktop.kwallet',
                   'org.kde.secretservicecompat')),
)) + (
    re.compile(r' *([a-z_][a-z0-9_-]*) : a password is required ; PWD=/home/\1 ; USER=root ; COMMAND=/usr/bin/true'),
)


def journal_errors_ok(output):
    print(output, end='' if output.endswith('\n') else '\n', flush=True)
    return all(not line.strip() or line == '-- No entries --'
               or any(pattern.fullmatch(line) for pattern in JOURNAL_ERROR_ALLOWLIST)
               for line in output.splitlines())


def valid_boot_frame(output, log):
    try:
        shot.frame_metrics(output)
    except (OSError, ValueError) as error:
        log.write(f'Invalid GRUB frame: {error}\n')
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', nargs='?', default='erase-btrfs')
    parser.add_argument('--dir', default=os.environ.get('EMAKI_ISO_VM_DIR', str(Path.home() / 'VMs/iso-vm')))
    parser.add_argument('--ssh-port', type=int)
    parser.add_argument('--user', help='installed account (default: fixture user)')
    parser.add_argument('--session', choices=('niri-session', 'niri-emaki-session'),
                        default='niri-emaki-session', help='installed desktop session')
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

    def picture(saved, path, what, inspect, failure_hint=''):
        # A saved picture is evidence for a person to judge, not a passed check.
        if not saved:
            return status(False, f'{what} screenshot capture failed{failure_hint}')
        line = f'SHOT: {path} (not judged); inspect {inspect}'
        print(line, flush=True)
        with (run / 'summary.txt').open('a') as stream:
            stream.write(line + '\n')
        return True

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
                if valid_boot_frame(output, log):
                    capture_ok = True
                next_capture += 4
            time.sleep(.25)
        log.write(monitor.command(vm, 'sendkey home'))
        time.sleep(.2)
        log.write(monitor.command(vm, 'sendkey ret'))
    picture(capture_ok, run / 'grub-*.png', 'GRUB', 'Emaki title, theme and both kernels',
            '; virgl may report no surface (see grub-monitor.log)')
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
    defaults_script = (HERE / 'check-installed-defaults.py').read_bytes()

    def defaults_check(name, privileged=False):
        result = remote('python3 - ' + name, privileged=privileged, input_data=defaults_script)
        (run / f'defaults-{name}.log').write_bytes(result.stdout + result.stderr)
        status(result.returncode == 0, 'installed defaults: ' + name)

    # Clear the timestamp and prove denial before any privileged acceptance command.
    defaults_check('sudo-user')
    defaults_check('sudo-files', privileged=True)
    version = (HERE.parent.parent / 'iso/VERSION').read_text().strip()
    check('emaki-package', 'LC_ALL=C pacman -Qi emaki',
          predicate=lambda output: bool(re.search(r'^Version\s*:\s*' + re.escape(version) + r'(?:-|\s)', output, re.M)))
    check('package-ownership', 'pacman -Qo /usr/bin/emaki /usr/share/emaki/shell/shell.qml', predicate=lambda output: len(output.strip().splitlines()) == 2)
    # Compare the pristine installed image, including dependency-provided menu entries.
    # Read its packaged filter; the checkout must not mask a stale image's defects.
    profile = config.get('software', 'rich')
    if profile not in ('minimal', 'rich'):
        raise ValueError('Unknown application profile: ' + str(profile))
    inventory = remote('python3 - --profile ' + profile
                       + ' --catalog /usr/share/emaki/shell/AppCatalog.qml',
                       input_data=(HERE.parent / 'check-launcher-inventory.py').read_bytes())
    (run / 'launcher-inventory.log').write_bytes(inventory.stdout + inventory.stderr)
    status(inventory.returncode == 0, 'launcher-inventory')
    check('network', 'LC_ALL=C nmcli -t general', predicate=lambda output: output.startswith('connected:'))
    check('emaki-repository', 'pacman-conf --repo emaki Server', predicate=lambda output: bool(output.strip()))
    check('package-update', 'pacman -Syu --noconfirm', privileged=True, timeout=1800)
    # After the update: an upgraded systemd or filesystem must leave the identity link alone.
    check('os-release', "sh -c 'readlink /etc/os-release && cat /etc/os-release'",
          predicate=lambda output: output.startswith('../usr/lib/emaki/os-release\n')
          and bool(re.search(r'^PRETTY_NAME="Emaki"$', output, re.M))
          and bool(re.search(r'^ID=arch$', output, re.M)))
    fs = check('root-filesystem', 'findmnt -n -o FSTYPE /',
               predicate=lambda output: output.strip() == config['fs']).stdout.decode().strip()
    if fs == 'btrfs':
        check('snapper', 'env LC_ALL=C snapper -c root list', privileged=True, predicate=lambda output: bool(re.search(r'^\s*[1-9][0-9]*\s*\|', output, re.M)))
        check('snapshot-grub', "grep -E 'snapshot|Snapshot' /boot/grub/grub-btrfs.cfg", privileged=True)
    check('grub-emaki', 'grep -c Emaki /boot/grub/grub.cfg', privileged=True,
          predicate=lambda output: output.strip().isdigit() and int(output.strip()) >= 1)
    check('grub-kernels', "sh -c 'grep -q vmlinuz-linux /boot/grub/grub.cfg && grep -q vmlinuz-linux-lts /boot/grub/grub.cfg'", privileged=True)
    check('grub-lts-title', '''grep -Fq "menuentry 'Emaki, with Linux linux-lts'" /boot/grub/grub.cfg''', privileged=True)
    if config['mode'] == 'alongside':
        check('grub-windows', "grep -E '^menuentry .*Windows Boot Manager' /boot/grub/grub.cfg", privileged=True)
        check('grub-os-prober', 'grep -Fx GRUB_DISABLE_OS_PROBER=false /etc/default/grub', privileged=True)
        check('grub-default-emaki', 'grep -Fx GRUB_DEFAULT=0 /etc/default/grub', privileged=True)
        baseline = vm / 'windows-before.json'
        if status(baseline.is_file(), 'Windows preservation baseline available'):
            uploaded = remote('umask 077; cat > /tmp/emaki-windows-fixture.py',
                              input_data=(HERE / 'fixtures/windows-disk.py').read_bytes())
            if status(uploaded.returncode == 0, 'Windows verifier uploaded'):
                result = remote('python3 /tmp/emaki-windows-fixture.py check --freed ' + str(int(config['shrink_bytes'])),
                                privileged=True, input_data=baseline.read_bytes())
                (run / 'windows-preservation.log').write_bytes(result.stdout + result.stderr)
                status(result.returncode == 0, 'Windows files, partition identity and EFI loaders preserved')
    shot = subprocess.run([str(HERE / 'iso-shot.sh'), *common, '--fixture', str(fixture), str(run / 'greeter.png')], capture_output=True)
    (run / 'greeter-shot.log').write_bytes(shot.stdout + shot.stderr)
    picture(shot.returncode == 0, run / 'greeter.png', 'greeter', 'the greeter')
    upload = remote('umask 077; cat > "$HOME/iso-guest-login.py"', input_data=(HERE / 'guest-login.py').read_bytes())
    if status(upload.returncode == 0, 'guest-login.py uploaded'):
        # sudo consumes its password line; the remaining bytes are the exact PAM
        # password expected by guest-login.py (no trailing newline).
        login = remote(f'python3 /home/{user}/iso-guest-login.py {user} {args.session}',
                       privileged=True, input_data=password.encode(), timeout=60)
        (run / 'login.log').write_bytes(login.stdout + login.stderr)
        status(login.returncode == 0, 'raw greetd login (does not test greeter UI)')
        ready = False
        for _ in range(30):
            unit = 'niri.service' if args.session == 'niri-session' else 'niri-emaki.service'
            session = remote('XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus systemctl --user is-active ' + unit + ' emaki-shell.service')
            if session.returncode == 0:
                ready = True
                break
            time.sleep(1)
        status(ready, 'installed compositor and shell active')
        # The map's truth: every state command it ships answers JSON in the session.
        page = remote('cat ' + SYSTEM_MAP)
        (run / 'map-page.log').write_bytes(page.stdout + page.stderr)
        commands = None
        try:
            if page.returncode == 0:
                commands = map_state_commands(page.stdout.decode(errors='replace'))
        except ValueError as error:
            with (run / 'map-page.log').open('a') as stream:
                stream.write(f'\n{error}\n')
        if status(commands is not None, 'map state commands: installed map readable'):
            for component, command in commands:
                check(f'map-state-{component}', f'{SESSION_ENV} {command}', predicate=json_answer)
        # Query both managers after the session has started, when desktop
        # services and their error messages can actually be observed.
        check('failed-units', 'systemctl --failed --no-legend --plain',
              predicate=lambda output: not output.strip())
        check('failed-user-units', 'XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus systemctl --user --failed --no-legend --plain',
              predicate=lambda output: not output.strip())
        check('journal-errors', 'journalctl -p err -b --no-pager -o cat',
              privileged=True, predicate=journal_errors_ok)
        if ready and args.session == 'niri-session':
            defaults_check('wallpaper')
        shot = subprocess.run([str(HERE / 'iso-shot.sh'), *common, '--fixture', str(fixture), str(run / 'desktop.png')], capture_output=True)
        (run / 'desktop-shot.log').write_bytes(shot.stdout + shot.stderr)
        picture(shot.returncode == 0, run / 'desktop.png', 'desktop', 'ring and shell')
    print(f'Evidence: {run}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f'BAD: boot check: {error}')
