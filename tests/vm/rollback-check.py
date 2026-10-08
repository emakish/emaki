#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
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
    '/usr/lib/initcpio/hooks/emaki-resume': 'emaki-config',
    '/usr/lib/initcpio/install/emaki-resume': 'emaki-config',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_fixture(base, iso, manifest, candidate):
    if manifest.get('candidate') != candidate or not candidate.strip():
        raise ValueError('Installed fixture does not identify the requested candidate')
    for label, path in [('iso', iso), *([('target.qcow2', base / 'target.qcow2'),
                           ('OVMF_VARS.4m.fd', base / 'OVMF_VARS.4m.fd')] if base else [])]:
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


def verify_candidate(release, candidate):
    commits = [line.partition('=')[2] for line in release.splitlines()
               if line.startswith('EMAKI_COMMIT=')]
    if (len(commits) != 1 or not re.fullmatch(r'[0-9a-f]{40}', commits[0])
            or commits[0] != candidate):
        raise ValueError('Image EMAKI_COMMIT does not identify the requested candidate')
    return release


def verify_installed(remote, evidence, packages, candidate):
    evidence('installed-release', verify_candidate(
        remote('cat /usr/lib/emaki-release'), candidate))
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
    for hook in ('emaki-snapshot-fstab', 'emaki-resume'):
        for directory in ('hooks', 'install'):
            remote('test ! -e /etc/initcpio/' + directory + '/' + hook, privileged=True)


def encrypted_plan():
    fixture = json.loads((ROOT / 'installer/fixtures/plan-erase-btrfs.json').read_text())
    fixture.update(encryption='account', hibernation=True, software='minimal', online_update=False)
    return fixture


def flag_provenance(candidate, iso_sha256, package_flags):
    if not iso_sha256 or not re.fullmatch(r'[a-fA-F0-9]{64}', iso_sha256):
        raise ValueError('--iso-sha256 must be the expected candidate image digest')
    packages = {}
    for item in package_flags:
        name, separator, version = item.partition('=')
        if not separator or not name or not version or name in packages:
            raise ValueError('--package requires a unique NAME=VERSION')
        packages[name] = version
    return {'candidate': candidate, 'sha256': {'iso': iso_sha256.lower()}, 'packages': packages}


def capture_frame(vm, label, stage, frames):
    path = vm / (label + '.png')
    subprocess.run([sys.executable, str(HERE / 'iso-shot.py'), '--dir', str(vm), str(path)],
                   check=True, capture_output=True, timeout=25)
    return frames.assess_frame(path, stage)


def wait_frame(capture, label, stage, timeout, before_capture=None):
    deadline = time.monotonic() + timeout
    while True:
        try:
            if before_capture:
                before_capture()
            return capture(label, stage)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Timed out waiting for {stage}: {error}') from error
            time.sleep(.5)


def supported_plan(config, encrypted_hibernation=False):
    if encrypted_hibernation:
        return (config.get('fs') == 'btrfs' and config.get('encryption') in ('account', 'separate')
                and config.get('hibernation') is True and config.get('mode') == 'erase')
    return (config.get('fs') == 'btrfs' and config.get('encryption') == 'none'
            and config.get('hibernation') is False)



# Emit structured observations so the same fail-closed parser is exercised offline.
RESUME_PROBE = r"""python3 - <<'PYPROBE'
import json
from pathlib import Path
import subprocess

def run(*args):
    return subprocess.check_output(args, text=True).strip()

swap = Path('/swap/swapfile').stat()
# map-swapfile rejects unsupported multi-device layouts and proves the offset.
offset = run('btrfs', 'inspect-internal', 'map-swapfile', '-r', '/swap/swapfile')
# Btrfs st_dev / findmnt MAJ:MIN is anonymous, not the resume block device.
source = run('findmnt', '-n', '-o', 'SOURCE', '-T', '/swap/swapfile').split('[', 1)[0]
device = run('lsblk', '-dn', '-o', 'MAJ:MIN', source)
print(json.dumps({
    'cmdline': Path('/proc/cmdline').read_text(),
    'offset': offset,
    'kernel_offset': Path('/sys/power/resume_offset').read_text().strip(),
    'kernel_resume': Path('/sys/power/resume').read_text().strip(),
    'swap_source': source, 'swap_device': device,
    'swap_uuid': run('findmnt', '-n', '-o', 'UUID', '-T', '/swap/swapfile'),
    'swap_fsroot': run('findmnt', '-n', '-o', 'FSROOT', '-T', '/swap/swapfile'),
    'swap_size': swap.st_size, 'swap_inode': swap.st_ino,
    'swaps': Path('/proc/swaps').read_text(),
    'hooks': Path('/etc/mkinitcpio.conf').read_text(),
    'initramfs': run('lsinitcpio', '/boot/initramfs-linux.img'),
    'crypt': run('cryptsetup', 'status', 'emaki-root'),
    'luks_uuid': run('cryptsetup', 'luksUUID', '/dev/vda2'),
    'crypt_uuid': run('blkid', '-s', 'UUID', '-o', 'value', '/dev/mapper/emaki-root'),
}))
PYPROBE"""


def validate_resume(state, baseline=None):
    tokens = shlex.split(state['cmdline'])
    def argument(name):
        values = [t.split('=', 1)[1] for t in tokens if t.startswith(name + '=')]
        if len(values) != 1:
            raise ValueError('Missing or duplicate kernel argument: ' + name)
        return values[0]
    offset = str(state['offset'])
    if not offset.isdigit() or int(offset) <= 0:
        raise ValueError('Invalid mapped swap offset')
    if argument('resume_offset') != offset or str(state['kernel_offset']) != offset:
        raise ValueError('Swap offset differs from kernel resume settings')
    if not state['swap_uuid'] or argument('resume') != 'UUID=' + state['swap_uuid']:
        raise ValueError('Resume UUID differs from swap filesystem')
    if state['crypt_uuid'] != state['swap_uuid'] or argument('root') != 'UUID=' + state['crypt_uuid']:
        raise ValueError('Root and swap do not share the encrypted filesystem')
    if (not re.fullmatch(r'[1-9][0-9]*:[0-9]+', state['swap_device'])
            or state['kernel_resume'] != state['swap_device']):
        raise ValueError('Kernel resume device differs from swap filesystem')
    if (not re.fullmatch(r'[A-Fa-f0-9-]+', state['luks_uuid'])
            or argument('cryptdevice') != 'UUID=' + state['luks_uuid'] + ':emaki-root'):
        raise ValueError('Root encryption kernel argument is missing')
    if argument('cryptkey') != 'rootfs:/etc/cryptsetup-keys.d/emaki-root.key':
        raise ValueError('Encrypted root keyfile argument is missing')
    if not re.search(r'^\s*type:\s+LUKS2\s*$', state['crypt'], re.M):
        raise ValueError('Root mapper is not LUKS2')
    if state['swap_fsroot'] != '/@swap' or state['swap_size'] != 6 * 1024**3:
        raise ValueError('Shared RAM-sized swap file is missing')
    rows = [line.split() for line in state['swaps'].splitlines()[1:]]
    if sum(bool(row) and row[0] == '/swap/swapfile' for row in rows) != 1:
        raise ValueError('Swap file is not active exactly once')
    hook_rows = re.findall(r'^HOOKS=\(([^)]*)\)\s*$', state['hooks'], re.M)
    if len(hook_rows) != 1:
        raise ValueError('Ambiguous initramfs hooks')
    hooks = shlex.split(hook_rows[0])
    if not all(hooks.count(h) == 1 for h in ('encrypt', 'emaki-resume', 'filesystems')):
        raise ValueError('Missing or duplicate encryption/resume hook')
    if not hooks.index('encrypt') < hooks.index('emaki-resume') < hooks.index('filesystems'):
        raise ValueError('Wrong encryption/resume hook order')
    files = {line.strip().removeprefix('./') for line in state['initramfs'].splitlines()}
    if not {'hooks/emaki-resume', 'hooks/emaki-resume-upstream',
            'etc/cryptsetup-keys.d/emaki-root.key'} <= files:
        raise ValueError('Built initramfs lacks resume hook or root keyfile')
    if baseline:
        for key in ('offset', 'swap_uuid', 'swap_fsroot', 'swap_size', 'swap_inode'):
            if state[key] != baseline[key]:
                raise ValueError('Rollback changed swap identity: ' + key)
    return state

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
        path = vm / (name + '.png')
        path.write_bytes(image)
        print(f'SHOT: {path} (not judged); HUMAN REVIEW REQUIRED', flush=True)

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
    parser.add_argument('--install-encrypted-hibernation', action='store_true',
                        help='Install the checked encrypted btrfs plan on a fresh disk before rollback')
    parser.add_argument('--base', type=Path, help='Installed candidate fixture directory')
    parser.add_argument('--iso', required=True, type=Path, help='Image used to install the fixture')
    parser.add_argument('--provenance', type=Path, help='JSON: candidate, sha256, packages')
    parser.add_argument('--iso-sha256', help='Expected ISO digest, instead of --provenance')
    parser.add_argument('--package', action='append', default=[], metavar='NAME=VERSION',
                        help='Expected candidate package version; repeat for each package')
    parser.add_argument('--candidate', required=True, help='Expected full EMAKI_COMMIT from the candidate image')
    parser.add_argument('--identity', required=True, type=Path, help='Disposable guest SSH identity')
    parser.add_argument('--plan', type=Path, help='Installation plan used for this fixture')
    parser.add_argument('--work-root', type=Path, default=Path.home() / 'VMs')
    args = parser.parse_args()
    frame_spec = importlib.util.spec_from_file_location('frame_assessment', HERE / 'frame_assessment.py')
    frames = importlib.util.module_from_spec(frame_spec)
    frame_spec.loader.exec_module(frames)
    frames.require_tools()
    area = Path(os.environ['VMDIR']).resolve()
    base = args.base.resolve() if args.base else None
    if not args.install_encrypted_hibernation and base is None:
        parser.error('--base is required unless installing a fresh encrypted fixture')
    if args.install_encrypted_hibernation and base is not None:
        parser.error('--base cannot be used with --install-encrypted-hibernation')
    if base:
        validate_area(area, base, args.work_root.resolve())
    elif not area.is_relative_to(args.work_root.resolve()) or area == args.work_root.resolve():
        raise ValueError('VMDIR must be strictly inside the chosen VM work root')
    if args.provenance and (args.iso_sha256 or args.package):
        parser.error('Use either --provenance or --iso-sha256/--package')
    if not args.install_encrypted_hibernation and (not args.plan or not args.provenance):
        parser.error('Installed fixtures require --plan and --provenance')
    manifest = (json.loads(args.provenance.read_text()) if args.provenance else
                flag_provenance(args.candidate, args.iso_sha256, args.package))
    packages = validate_fixture(base, args.iso.resolve(), manifest, args.candidate)
    if args.plan:
        if manifest.get('sha256', {}).get('plan') != digest(args.plan):
            raise ValueError('Candidate fixture checksum mismatch: plan')
        fixture = json.loads(args.plan.read_text())
    else:
        fixture = encrypted_plan()
    config = fixture.get('config', fixture)
    if not supported_plan(config, args.install_encrypted_hibernation):
        print('NOT TESTED: plan does not match the selected rollback mode', flush=True)
        return 77
    if not args.install_encrypted_hibernation:
        print('NOT TESTED: encrypted rollback and swap/resume preservation with hibernation', flush=True)
    area.mkdir(parents=True, exist_ok=True)
    vm = Path(tempfile.mkdtemp(prefix='rollback-', dir=area))
    (vm / 'candidate.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in (('target.qcow2', 'OVMF_VARS.4m.fd') if base else ()):
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

    env.update(EMAKI_ISO_SSH_KEY=str(key))

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

    baseline_resume = None

    def resume_state(label):
        state = json.loads(remote(RESUME_PROBE, privileged=True))
        evidence(label + '-resume', json.dumps(state, indent=2))
        return validate_resume(state, baseline_resume)

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

    def assess_capture(label, stage):
        return capture_frame(vm, label, stage, frames)

    def grub(indices, label):
        if args.install_encrypted_hibernation:
            wait_frame(assess_capture, label + '-prompt', 'prompt', 90)
            # One submission only. A second prompt must time out rather than be
            # answered, so SSH readiness proves the root keyfile unlock worked.
            disk_password = config.get('disk_password', password)
            if not re.fullmatch('[a-zA-Z0-9_-]+', disk_password):
                raise ValueError('Fixture disk password needs an additional key mapping')
            for char in disk_password:
                key = ('minus' if char == '-' else 'shift-minus' if char == '_'
                       else 'shift-' + char.lower() if char.isupper() else char)
                monitor.command(vm, 'sendkey ' + key)
                time.sleep(.07)
            monitor.command(vm, 'sendkey ret')
            evidence(label + '-unlock', json.dumps({'assessed_prompt': True, 'password_submissions': 1}))
        # Early frames can still be firmware or the old guest. Keep stopping the
        # countdown until the actual menu appears, including unencrypted boots.
        wait_frame(assess_capture, label + '-grub', 'menu', 90,
                   before_capture=lambda: monitor.command(vm, 'sendkey up', timeout=1))
        for level, index in enumerate(indices):
            wait_frame(assess_capture, f'{label}-menu-{level}', 'menu', 30)
            select_menu(index)
        wait_ssh()
        wait_frame(assess_capture, label + '-after-unlock', 'after-unlock', 60)

    def reboot(indices, label):
        before = remote('cat /proc/sys/kernel/random/boot_id').strip()
        remote('systemctl reboot', privileged=True)
        grub(indices, label)
        after = remote('cat /proc/sys/kernel/random/boot_id').strip()
        assert before != after, 'reboot did not change boot ID'
        state = json.loads(remote('emaki-rollback status --json'))
        assert state['mode'] == ('snapshot' if label == 'snapshot' else 'normal'), state
        if args.install_encrypted_hibernation:
            resume_state(label)
        evidence(label + '-boot', remote('cat /proc/cmdline; findmnt /; emaki-rollback status --json'))

    if args.install_encrypted_hibernation:
        (vm / 'plan.json').write_text(json.dumps(fixture, indent=2) + '\n')
        live_log = (vm / 'qemu-live.log').open('w')
        live = subprocess.Popen([str(HERE / 'run-iso.sh'), '--iso', str(args.iso.resolve()), *common],
                                stdout=live_log, stderr=subprocess.STDOUT, env=env)
        def live_run(script, *commands, data=None, timeout=600):
            result = subprocess.run([str(HERE / script), *common, '--user', 'live', *commands],
                                    input=data, capture_output=True, env=env, timeout=timeout)
            if result.returncode:
                evidence('install-failure', result.stdout.decode() + result.stderr.decode())
                result.check_returncode()
            return result.stdout
        try:
            live_run('iso-wait-ssh.sh')
            live_run('iso-ssh.sh', 'test -d /run/archiso/bootmnt && grep -qw emaki.test=1 /proc/cmdline')
            evidence('image-release', verify_candidate(
                live_run('iso-ssh.sh', 'cat /usr/lib/emaki-release').decode(), args.candidate))
            live_run('iso-ssh.sh', 'for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
            live_run('iso-ssh.sh', 'umask 077; cat > /tmp/rollback-plan.json', data=json.dumps(fixture).encode())
            evidence('install', live_run('iso-ssh.sh', 'emaki-install-cli --plan /tmp/rollback-plan.json --yes',
                                         timeout=5400).decode())
            evidence('install-worker', live_run('iso-ssh.sh', 'sudo cat /var/log/emaki-install.log').decode())
        finally:
            if live.poll() is None:
                monitor.command(vm, 'quit')
            live.wait(timeout=30)
            live_log.close()
        # Record the fresh fixture before the installed boot changes its disk.
        evidence('installed-fixture', json.dumps({'candidate': args.candidate, 'packages': packages,
                 'sha256': {name: digest(path) for name, path in (
                     ('iso', args.iso), ('plan', vm / 'plan.json'), ('target.qcow2', vm / 'target.qcow2'),
                     ('OVMF_VARS.4m.fd', vm / 'OVMF_VARS.4m.fd'))}}, indent=2))

    log = (vm / 'qemu.log').open('w')
    qemu = subprocess.Popen([str(HERE / 'run-iso.sh'), '--no-cd', *common], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        grub([0], 'initial')
        out = remote("sh -ec 'cat /proc/cmdline; findmnt -o TARGET,SOURCE,FSTYPE,FSROOT,UUID; cat /etc/fstab /etc/default/grub; cat /boot/grub/grub.cfg; btrfs subvolume list /; snapper --no-dbus -c root list; systemctl --failed'", privileged=True)
        (vm / 'baseline.log').write_text(out)
        print(out, flush=True)
        verify_installed(remote, evidence, packages, args.candidate)
        if args.install_encrypted_hibernation:
            baseline_resume = resume_state('initial')
        if args.inspect:
            print('NOT TESTED: rollback actions (--inspect only)', flush=True)
            return 77
        acceptance(remote, evidence, reboot, vm, user, password, ssh)
        print('HUMAN REVIEW REQUIRED: recovery and authorization dialog appearance in saved desktop frames', flush=True)
        if args.install_encrypted_hibernation:
            print('PASS: encrypted rollback preserves shared swap, resume offset/device, kernel arguments and built resume hook; one password submission reaches each boot', flush=True)
            print('NOT TESTED: actual hibernation and resume in this rollback run', flush=True)
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
