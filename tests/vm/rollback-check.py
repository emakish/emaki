#!/usr/bin/env python3
"""Permanent rollback acceptance on a fresh copy of the installed btrfs fixture."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import re
import shlex
import sys
import subprocess
import time
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


# The release runner supplies this manifest from its installed candidate fixture.
# No acceptance run may repair the production payload it is about to test.
PAYLOAD = {
    '/usr/bin/emaki-rollback': 'emaki-config',
    '/usr/share/polkit-1/actions/org.emaki.rollback.policy': 'emaki-config',
    '/usr/share/emaki/shell/SnapshotRecovery.qml': 'emaki-config',
    '/usr/share/emaki/shell/SnapshotPrompt.qml': 'emaki-config',
    '/usr/share/emaki/shell/shell.qml': 'emaki-config',
    '/usr/share/emaki/shell/qmldir': 'emaki-config',
    '/usr/lib/initcpio/hooks/emaki-snapshot-fstab': 'emaki-config',
    '/usr/lib/initcpio/install/emaki-snapshot-fstab': 'emaki-config',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_fixture(base, iso, manifest, candidate):
    if manifest.get('candidate') != candidate or not candidate.strip():
        raise ValueError('Installed fixture does not identify the requested candidate')
    for label, path in [('iso', iso), ('target.qcow2', base / 'target.qcow2'),
                        ('OVMF_VARS.4m.fd', base / 'OVMF_VARS.4m.fd')]:
        if manifest.get('sha256', {}).get(label) != digest(path):
            raise ValueError('Candidate fixture checksum mismatch: ' + label)
    packages = manifest.get('packages', {})
    if not {'emaki', 'emaki-config', 'emaki-desktop'} <= packages.keys():
        raise ValueError('Candidate manifest lacks required installed packages')
    for name, version in packages.items():
        if not re.fullmatch(r'[a-zA-Z0-9@_.+:-]+', name) or not isinstance(version, str) or not version:
            raise ValueError('Invalid candidate package identity')
    return packages


def validate_area(area, base, work_root):
    if not area.is_relative_to(work_root) or area == work_root:
        raise ValueError('VMDIR must be strictly inside the chosen VM work root')
    if area == base or area.is_relative_to(base) or base.is_relative_to(area):
        raise ValueError('Candidate fixture and disposable run area must be separate')


def verify_installed(remote, evidence, packages):
    for name, version in packages.items():
        actual = remote('pacman -Q ' + shlex.quote(name)).strip()
        if actual != name + ' ' + version:
            raise ValueError('Installed candidate version mismatch: ' + actual)
        evidence('package-' + name, actual)
        # A nonzero integrity result fails before snapshots or test mutations.
        evidence('integrity-' + name, remote('pacman -Qkk ' + shlex.quote(name), privileged=True))
    for path, owner in PAYLOAD.items():
        actual = remote('pacman -Qqo ' + shlex.quote(path)).strip()
        if actual != owner:
            raise ValueError('Unexpected payload owner: ' + path + ': ' + actual)
    evidence('installed-payload', remote('sha256sum ' + ' '.join(map(shlex.quote, PAYLOAD))))
    # An old override would shadow the installed package while passing its checksum.
    remote('test ! -e /etc/initcpio/hooks/emaki-snapshot-fstab; '
           'test ! -e /etc/initcpio/install/emaki-snapshot-fstab', privileged=True)


def supported_plan(config):
    return (config.get('fs') == 'btrfs' and config.get('encryption') == 'none'
            and config.get('hibernation') is False)


def acceptance(remote, evidence, reboot, vm, user, password, ssh):
    evidence('normal-status', remote('emaki-rollback status --json'))
    remote("sh -ec 'printf good > /etc/emaki-rollback-marker; printf shared > /home/rollback-home-marker'", privileged=True)
    # A real pre snapshot with pacman's lock present exercises snap-pac's shape.
    number = remote("sh -ec 'touch /var/lib/pacman/db.lck; snapper --no-dbus -c root create --type pre --description rollback-test-good --print-number; rm /var/lib/pacman/db.lck'", privileged=True).strip()
    assert number.isdigit(), number
    evidence('snapshot', number)
    evidence('snapshot-grub-generation', remote('grub-mkconfig -o /boot/grub/grub.cfg', privileged=True))
    main_menu = remote('cat /boot/grub/grub.cfg', privileged=True)
    snapshot_menu = remote('cat /boot/grub/grub-btrfs.cfg', privileged=True)
    (vm / 'grub.cfg').write_text(main_menu)
    (vm / 'grub-btrfs.cfg').write_text(snapshot_menu)
    menu = re.findall(r"^\s*(?:menuentry|submenu) ['\"](.*?)['\"]", main_menu, re.M)
    main_index = next(i for i, title in enumerate(menu) if 'snapshots' in title.lower())
    snapshots = re.findall(r"^(?:menuentry|submenu) ['\"](.*?)['\"]", snapshot_menu, re.M)
    snapshot_index = next(i for i, title in enumerate(snapshots) if 'rollback-test-good' in title)
    remote("sh -ec 'printf broken > /etc/emaki-rollback-marker; printf home-kept > /home/rollback-home-marker'", privileged=True)
    broken = b'[Unit]\nDescription=Rollback test failure\n[Service]\nType=oneshot\nExecStart=/usr/bin/false\n[Install]\nWantedBy=multi-user.target\n'
    remote('install -Dm644 /dev/stdin /etc/systemd/system/rollback-test-broken.service', broken, privileged=True)
    remote('systemctl enable rollback-test-broken.service', privileged=True)
    evidence('grub-selection', json.dumps({'main': main_index, 'snapshot': snapshot_index, 'kernel': 1}))
    reboot([main_index, snapshot_index, 1], 'snapshot')
    status = json.loads(remote('emaki-rollback status --json'))
    assert status['mode'] == 'snapshot' and status['snapshot'] == number, status
    evidence('snapshot-contents', remote("sh -ec 'test $(cat /etc/emaki-rollback-marker) = good; test $(cat /home/rollback-home-marker) = home-kept; test ! -f /etc/systemd/system/rollback-test-broken.service; findmnt -n -o FSTYPE /'"))
    remote('touch /etc/emaki-recovery-only', privileged=True)
    # Log in to the real compositor so the shell displays the recovery offer.
    remote('cat > /tmp/rollback-guest-login.py', (HERE / 'guest-login.py').read_bytes())
    evidence('snapshot-login', remote('python3 /tmp/rollback-guest-login.py ' + user + ' niri-emaki-session', password.encode(), privileged=True))
    time.sleep(10)
    session = 'XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus '
    evidence('snapshot-shell', remote(session + 'systemctl --user is-active emaki-shell.service'))
    desktop_env = "export XDG_RUNTIME_DIR=/run/user/1000; export NIRI_SOCKET=$(find /run/user/1000 -maxdepth 1 -type s -name 'niri.*.sock' | head -1); export WAYLAND_DISPLAY=$(basename $(find /run/user/1000 -maxdepth 1 -type s -name 'wayland-*' | head -1)); "
    def desktop(command):
        return remote('sh -ec ' + shlex.quote(desktop_env + command))

    def capture(name):
        desktop('grim /tmp/rollback-desktop.png')
        image = subprocess.run(ssh + ['cat /tmp/rollback-desktop.png'], capture_output=True, check=True).stdout
        (vm / (name + '.png')).write_bytes(image)

    def windows():
        return json.loads(desktop('niri msg -j windows'))

    visible = windows()
    prompt = next(w for w in visible if w['title'] == 'Recovery snapshot')
    desktop('niri msg action focus-window --id ' + str(prompt['id']))
    capture('snapshot-desktop')
    monitor.command(vm, 'sendkey tab')
    time.sleep(.2)
    monitor.command(vm, 'sendkey ret')
    # Only send the disposable administrator password after the actual polkit
    # authentication window is observed and focused in the active desktop.
    authentication = None
    for _ in range(30):
        visible = windows()
        authentication = next((w for w in visible if 'polkit' in (w.get('app_id') or '').lower()
                               or 'authentication' in (w.get('title') or '').lower()), None)
        if authentication:
            break
        time.sleep(1)
    evidence('polkit-window', json.dumps(visible))
    assert authentication, 'No administrator-password dialog appeared'
    desktop('niri msg action focus-window --id ' + str(authentication['id']))
    capture('polkit-password')
    assert re.fullmatch('[a-zA-Z0-9_-]+', password), 'Fixture password needs an additional key mapping'
    for char in password:
        key = 'minus' if char == '-' else 'shift-minus' if char == '_' else 'shift-' + char.lower() if char.isupper() else char
        monitor.command(vm, 'sendkey ' + key)
        time.sleep(.05)
    monitor.command(vm, 'sendkey ret')
    rows = ''
    for _ in range(60):
        result = remote('emaki-rollback list', privileged=True, check=False)
        rows = result.stdout.decode()
        if result.returncode == 0 and '\tkept' in rows:
            break
        time.sleep(1)
    capture('snapshot-kept')
    evidence('polkit-keep', rows + remote('journalctl -b -u polkit --no-pager -o cat', privileged=True))
    kept_names = [line.split()[0] for line in rows.splitlines() if line.endswith('\tkept')]
    assert len(kept_names) == 1, rows
    kept = kept_names[0]
    reboot([0], 'kept-normal')
    evidence('kept-state', remote("sh -ec 'test $(cat /etc/emaki-rollback-marker) = good; test $(cat /home/rollback-home-marker) = home-kept; test ! -e /etc/emaki-recovery-only; test ! -e /var/lib/pacman/db.lck; test ! -e /etc/systemd/system/rollback-test-broken.service; emaki-rollback status --json'"))
    new = remote('snapper --no-dbus -c root create --description rollback-after-keep --print-number', privileged=True).strip()
    assert new.isdigit()
    time.sleep(8)
    evidence('snapper-after-keep', remote('snapper --no-dbus -c root list', privileged=True))
    evidence('automatic-grub-entry', remote('grep -F rollback-after-keep /boot/grub/grub-btrfs.cfg', privileged=True))
    evidence('undo', remote('emaki-rollback restore ' + kept, privileged=True))
    reboot([0], 'undo-normal')
    evidence('undo-state', remote("sh -ec 'test $(cat /etc/emaki-rollback-marker) = broken; test $(cat /home/rollback-home-marker) = home-kept; test -e /etc/systemd/system/rollback-test-broken.service; emaki-rollback status --json'"))
    evidence('normal-rollback', remote('emaki-rollback snapshot ' + number, privileged=True))
    reboot([0], 'second-normal')
    evidence('second-state', remote("sh -ec 'test $(cat /etc/emaki-rollback-marker) = good; emaki-rollback status --json'"))
    kept_rows = remote('emaki-rollback list', privileged=True)
    evidence('kept-list', kept_rows)
    kept_names = [line.split()[0] for line in kept_rows.splitlines() if line.endswith('\tkept')]
    assert len(kept_names) == 3, kept_rows
    refusal = remote('emaki-rollback delete ' + kept_names[-1] + ' --yes', privileged=True, check=False)
    assert refusal.returncode != 0 and b'newest kept root' in refusal.stderr
    evidence('cleanup-refusal', refusal.stderr.decode())
    evidence('cleanup', remote('emaki-rollback delete ' + kept_names[0] + ' --yes', privileged=True))
    evidence('final-snapper', remote('snapper --no-dbus -c root create --description rollback-final --print-number', privileged=True))
    evidence('final-root', remote('findmnt -n -o FSTYPE,FSROOT,OPTIONS /; test -z "$(systemctl --failed --no-legend)"; emaki-rollback list; btrfs property get -ts / ro', privileged=True))
    time.sleep(8)
    evidence('final-grub-entry', remote('grep -F rollback-final /boot/grub/grub-btrfs.cfg', privileged=True))
    print('PASS: functional rollback, snapshot boot, polkit authentication, normal reboot, snapper, GRUB, undo and cleanup', flush=True)
    print('NOT TESTED: visual recovery and authentication appearance; captured frames need review at owner screen sizes', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--base', required=True, type=Path, help='Installed candidate fixture directory')
    parser.add_argument('--iso', required=True, type=Path, help='Image used to install the fixture')
    parser.add_argument('--provenance', required=True, type=Path, help='JSON: candidate, sha256, packages')
    parser.add_argument('--candidate', required=True, help='Expected candidate build identity')
    parser.add_argument('--identity', required=True, type=Path, help='Disposable guest SSH identity')
    parser.add_argument('--plan', required=True, type=Path, help='Installation plan used for this fixture')
    parser.add_argument('--work-root', type=Path, default=Path.home() / 'VMs')
    args = parser.parse_args()
    area = Path(os.environ['VMDIR']).resolve()
    base = args.base.resolve()
    validate_area(area, base, args.work_root.resolve())
    manifest = json.loads(args.provenance.read_text())
    packages = validate_fixture(base, args.iso.resolve(), manifest, args.candidate)
    if manifest.get('sha256', {}).get('plan') != digest(args.plan):
        raise ValueError('Candidate fixture checksum mismatch: plan')
    fixture = json.loads(args.plan.read_text())
    config = fixture.get('config', fixture)
    if not supported_plan(config):
        print('NOT TESTED: rollback requires an unencrypted btrfs fixture without hibernation', flush=True)
        return 77
    print('NOT TESTED: encrypted rollback and swap/resume preservation with hibernation', flush=True)
    area.mkdir(parents=True, exist_ok=True)
    vm = Path(tempfile.mkdtemp(prefix='rollback-', dir=area))
    (vm / 'candidate.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in ('target.qcow2', 'OVMF_VARS.4m.fd'):
        subprocess.run(['cp', '--reflink=auto', '--sparse=always', str(base / name), str(vm / name)], check=True)
        if digest(vm / name) != manifest['sha256'][name]:
            raise ValueError('Candidate changed while copying: ' + name)
    user = config['user']['login']
    password = config['user']['password']
    common = ['--dir', str(vm), '--ssh-port', '2251', '--user', user]
    env = dict(os.environ, EMAKI_ISO_VM_DIR=str(vm), EMAKI_ISO_SSH_PORT='2251')
    # The queue's user namespace cannot trust host-owned SSH configuration.
    # Use only the explicit test identity and a job-owned known_hosts file.
    key = vm / 'id_vm'
    shutil.copyfile(args.identity, key)
    key.chmod(0o600)
    ssh = ['ssh', '-F', '/dev/null', '-p', '2251', '-i', str(key),
           '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
           '-o', 'StrictHostKeyChecking=accept-new',
           '-o', 'UserKnownHostsFile=' + str(vm / 'known_hosts'),
           '-o', 'ConnectTimeout=3', user + '@127.0.0.1']

    def remote(command, data=b'', privileged=False, timeout=900, check=True):
        if privileged:
            command = 'sudo -k -S -p "" -- sh -ec ' + shlex.quote(command)
            data = (password + '\n').encode() + data
        p = subprocess.run(ssh + [command], input=data, capture_output=True, timeout=timeout, env=env)
        if check and p.returncode:
            raise RuntimeError(f'{command}: {p.returncode}\n{p.stdout.decode()}\n{p.stderr.decode()}')
        return p.stdout.decode() if check else p

    def evidence(name, text):
        (vm / (name + '.log')).write_text(text)
        print(f'EVIDENCE: {name}', flush=True)

    def wait_ssh():
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            check = subprocess.run(ssh + ['true'], capture_output=True)
            if check.returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError(check.stderr.decode())

    def select_menu(index):
        monitor.command(vm, 'sendkey home')
        time.sleep(.3)
        for _ in range(index):
            monitor.command(vm, 'sendkey down')
            time.sleep(.15)
        monitor.command(vm, 'sendkey ret')
        time.sleep(.5)

    def grub(indices, label):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                monitor.command(vm, 'sendkey up', timeout=1)
                break
            except (OSError, RuntimeError):
                time.sleep(.2)
        for _ in range(60):
            monitor.command(vm, 'sendkey up')
            time.sleep(.25)
        monitor.command(vm, 'screendump ' + str(vm / (label + '-grub.png')) + ' -f png')
        for level, index in enumerate(indices):
            select_menu(index)
            monitor.command(vm, 'screendump ' + str(vm / f'{label}-menu-{level}.png') + ' -f png')
        wait_ssh()

    def reboot(indices, label):
        before = remote('cat /proc/sys/kernel/random/boot_id').strip()
        remote('systemctl reboot', privileged=True)
        time.sleep(2)
        grub(indices, label)
        after = remote('cat /proc/sys/kernel/random/boot_id').strip()
        assert before != after, 'reboot did not change boot ID'
        state = json.loads(remote('emaki-rollback status --json'))
        assert state['mode'] == ('snapshot' if label == 'snapshot' else 'normal'), state
        evidence(label + '-boot', remote('cat /proc/cmdline; findmnt /; emaki-rollback status --json'))

    log = (vm / 'qemu.log').open('w')
    qemu = subprocess.Popen([str(HERE / 'run-iso.sh'), '--no-cd', *common], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        grub([0], 'initial')
        out = remote("sh -ec 'cat /proc/cmdline; findmnt -o TARGET,SOURCE,FSTYPE,FSROOT,UUID; cat /etc/fstab /etc/default/grub; cat /boot/grub/grub.cfg; btrfs subvolume list /; snapper --no-dbus -c root list; systemctl --failed'", privileged=True)
        (vm / 'baseline.log').write_text(out)
        print(out, flush=True)
        verify_installed(remote, evidence, packages)
        if args.inspect:
            print('NOT TESTED: rollback actions (--inspect only)', flush=True)
            return 77
        acceptance(remote, evidence, reboot, vm, user, password, ssh)
        print(f'EVIDENCE: rollback run: {vm}', flush=True)
        return 0
    finally:
        if qemu.poll() is None:
            monitor.command(vm, 'quit')
        qemu.wait(timeout=30)
        (vm / 'qemu.pid').unlink(missing_ok=True)
        (vm / 'mon.sock').unlink(missing_ok=True)
        print('INFO: test VM stopped', flush=True)
        log.close()


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
