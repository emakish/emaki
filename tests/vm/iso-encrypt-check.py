#!/usr/bin/env python3
"""Install, unlock, log in, hibernate and resume a disposable encrypted VM."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def checked_iso():
    """The image under test is always named explicitly, never a silent default."""
    value = os.environ.get('EMAKI_CHECK_ISO', '')
    if not value or not os.path.isfile(value) or not os.access(value, os.R_OK):
        sys.exit(f'EMAKI_CHECK_ISO must name a readable ISO image file (got {value!r})')
    digest = hashlib.sha256()
    with open(value, 'rb') as image:
        for block in iter(lambda: image.read(1 << 20), b''):
            digest.update(block)
    iso = Path(value).resolve()
    return iso, f'ISO: {iso}\nsha256: {digest.hexdigest()}\n'


def luks_keyslots(dump):
    """The keyslots of `cryptsetup luksDump` (LUKS2) as {number: {field: value}}.

    Only the Keyslots section counts: the Digests section after it repeats field names such
    as Iterations.
    """
    slots, slot, inside = {}, None, False
    for line in dump.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            inside, slot = line == 'Keyslots:', None
        elif inside and (match := re.fullmatch(r' +(\d+): \S+', line)):
            slot = slots.setdefault(int(match[1]), {})
        elif slot is not None and (match := re.fullmatch(r'\t([A-Za-z][A-Za-z ]*):\s*(.*)', line)):
            slot[match[1]] = match[2].strip()
    return slots


def check_keyslots(dump):
    """Keyslot 0 holds the passphrase with the installer's Argon2id; keyslot 1 the keyfile with
    PBKDF2 at cryptsetup's minimum, so a wrong passphrase costs GRUB one Argon2id run, not two."""
    slots = luks_keyslots(dump)
    assert sorted(slots) == [0, 1], f'expected keyslots 0 and 1, found {sorted(slots)}'
    assert slots[0].get('PBKDF') == 'argon2id', f"keyslot 0 PBKDF is {slots[0].get('PBKDF')}, not argon2id"
    found = (slots[1].get('PBKDF'), slots[1].get('Iterations'))
    assert found == ('pbkdf2', '1000'), f'keyslot 1 PBKDF and iterations are {found}, not pbkdf2 at 1000'


RESUME_OBSERVATION_SECONDS = 60
RESUME_PROBE = r'''export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
python3 - <<'PY'
import json
import os
from pathlib import Path
import subprocess
identity = {
    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'volatile_marker': (Path(os.environ['XDG_RUNTIME_DIR']) / 'hibernate-proof').read_text(),
    'services': {},
}
for unit in ('niri-emaki.service', 'emaki-shell.service'):
    subprocess.run(['systemctl', '--user', 'is-active', '--quiet', unit], check=True)
    pid = int(subprocess.check_output(['systemctl', '--user', 'show', '--value',
                                      '--property=MainPID', unit]))
    assert pid > 0, unit + ' has no running process'
    # Field 22 is the start time; split after comm, which may contain spaces.
    started = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    identity['services'][unit] = {'pid': pid, 'started': started}
print(json.dumps(identity))
PY'''


def observe_resume(before, probe, evidence, *, clock=time.monotonic, sleep=time.sleep):
    """Persist every sample, including failed probes; never retry away a lost session."""
    started = clock()
    record = {'before': before, 'required_seconds': RESUME_OBSERVATION_SECONDS,
              'samples': [], 'status': 'NOT TESTED'}
    evidence.write_text(json.dumps(record, indent=2) + '\n')
    try:
        while True:
            sample = {'elapsed_seconds': clock() - started}
            record['samples'].append(sample)
            after = probe()
            sample['identity'] = after
            if after != before:
                raise RuntimeError('Boot, volatile marker or desktop process changed after resume')
            sample['elapsed_seconds'] = clock() - started
            evidence.write_text(json.dumps(record, indent=2) + '\n')
            if sample['elapsed_seconds'] >= RESUME_OBSERVATION_SECONDS:
                break
            sleep(min(5, RESUME_OBSERVATION_SECONDS - sample['elapsed_seconds']))
        record['status'] = 'PASS'
    except Exception as error:
        record['status'] = 'FAIL'
        record['error'] = str(error)
        raise
    finally:
        evidence.write_text(json.dumps(record, indent=2) + '\n')


def capture_frame(vm, label):
    """Require a fresh decoded host frame; guest desktop buffers are not scan-out."""
    from argparse import Namespace
    spec = importlib.util.spec_from_file_location('iso_shot', HERE / 'iso-shot.py')
    shot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shot)
    output = vm / (label + '.png')
    if not shot.screenshot(Namespace(dir=str(vm), output=str(output), monitor=False)):
        raise RuntimeError('Host display capture failed: ' + str(output))
    return output


def grub_entries(config):
    """Read generated menu blocks, retaining sibling indices and boot commands."""
    root = {'children': [], 'commands': []}
    stack = [root]
    for line in config.splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if words[0] in ('menuentry', 'submenu'):
            if '{' not in words:
                raise ValueError('Unsupported menu block [GRUB]')
            node = {'children': [], 'commands': [], 'title': words[1]}
            stack[-1]['children'].append(node)
        else:
            node = {'children': [], 'commands': []}
            stack[-1]['commands'].append(words)
        # Parameter expansions remain a single word, not a structural brace.
        for word in words:
            if word == '{':
                stack.append(node)
            elif word == '}':
                if len(stack) == 1:
                    raise ValueError('Unbalanced menu block [GRUB]')
                stack.pop()
    if len(stack) != 1:
        raise ValueError('Unclosed menu block [GRUB]')
    return root['children']


def snapshot_selection(main_config, snapshot_config):
    """Resolve the actual generated menu path and its expected snapshot identity."""
    def walk(entries, path=()):
        for index, entry in enumerate(entries):
            current = (*path, index)
            yield current, entry
            yield from walk(entry['children'], current)

    menus = [(path, entry) for path, entry in walk(grub_entries(main_config))
             if any(command[0] in ('source', 'configfile') and
                    any('grub-btrfs.cfg' in word for word in command[1:])
                    for command in entry['commands'])]
    if len(menus) != 1:
        raise ValueError('Expected one snapshot menu [GRUB]')
    for path, entry in walk(grub_entries(snapshot_config)):
        for command in entry['commands']:
            if command[0] != 'linux' or Path(command[1]).name != 'vmlinuz-linux':
                continue
            flags = [word[10:] for word in command[2:] if word.startswith('rootflags=')]
            subvols = [part[7:] for flags_value in flags for part in flags_value.split(',')
                       if part.startswith('subvol=')]
            if len(subvols) != 1:
                continue
            match = re.fullmatch(r'/?@snapshots/(\d+)/snapshot', subvols[0])
            if match and any(row[0] == 'initrd' for row in entry['commands']):
                return {'indices': list(menus[0][0] + path), 'snapshot': match[1],
                        'title': entry['title']}
    raise ValueError('No bootable snapshot entry [GRUB]')


def select_snapshot_menu(selection, send, capture, sleep=time.sleep):
    for level, index in enumerate(selection['indices']):
        send('sendkey home')
        sleep(.3)
        for _ in range(index):
            send('sendkey down')
            sleep(.2)
        capture('snapshot-menu-' + str(level))
        send('sendkey ret')
        sleep(1)


def verify_snapshot(selection, status):
    if status.get('mode') != 'snapshot' or status.get('snapshot') != selection['snapshot']:
        raise RuntimeError('The selected snapshot did not boot [GRUB]: ' + repr(status))


def main():
    fs, name = sys.argv[1:3]
    mode = sys.argv[3] if len(sys.argv) > 3 else 'erase'
    snapshot_only = sys.argv[4:] == ['--snapshot-only']
    assert not sys.argv[4:] or snapshot_only
    assert mode in ('erase', 'manual')
    assert fs in ('btrfs', 'ext4') and name.replace('-', '').isalnum()
    assert not snapshot_only or fs == 'btrfs'
    # --snapshot-only boots the installed disk without the CD.
    iso, iso_record = (None, '') if snapshot_only else checked_iso()
    base = Path(os.environ['VMDIR']).resolve()
    vm = base / (fs + '-' + name)
    vm.mkdir(mode=0o700, exist_ok=snapshot_only)
    os.umask(0o077)
    if iso:
        (vm / 'iso.txt').write_text(iso_record)
    env = dict(os.environ, EMAKI_ISO_VM_DIR=str(vm), EMAKI_ISO_SSH_PORT='2231')
    handles = []
    qemu = None

    def run(script, *args, data=None, timeout=600, check=True):
        result = subprocess.run([str(HERE / script), '--dir', str(vm), '--ssh-port', '2231', *args],
                                input=data, capture_output=True, env=env, timeout=timeout)
        if check and result.returncode:
            (vm / 'command-failure.log').write_bytes(result.stdout + result.stderr)
            result.check_returncode()
        return result

    def ssh(command, data=None, user='live', check=True, timeout=600):
        result = run('iso-ssh.sh', '--user', user, command, data=data, check=check, timeout=timeout)
        if check and result.stderr:
            print(result.stderr.decode(errors='replace'), flush=True)
        return result.stdout

    def start(live):
        nonlocal qemu
        log = (vm / ('qemu-live.log' if live else f'qemu-boot-{time.time_ns()}.log')).open('wb')
        handles.append(log)
        args = ['--iso', str(iso)] if live else ['--no-cd']
        qemu = subprocess.Popen([str(HERE / 'run-iso.sh'), '--dir', str(vm), '--ssh-port', '2231', *args],
                                env=env, stdout=log, stderr=subprocess.STDOUT)

    def stop():
        stopper = subprocess.Popen([str(HERE / 'iso-stop.sh'), '--dir', str(vm), '--ssh-port', '2231'],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if qemu:
            qemu.wait(timeout=180)
        output, _ = stopper.communicate(timeout=30)
        print(output.decode(), end='', flush=True)

    def capture(label):
        return capture_frame(vm, label)

    def type_text(text):
        keys = {' ': 'spc', '-': 'minus', '/': 'slash', '.': 'dot', '=': 'equal',
                ',': 'comma', ':': 'shift-semicolon', '_': 'shift-minus',
                '@': 'shift-2', '"': 'shift-apostrophe', "'": 'apostrophe',
                '$': 'shift-4', '(': 'shift-9', ')': 'shift-0'}
        for char in text:
            key = keys.get(char, 'shift-' + char.lower() if char.isupper() else char)
            assert re.fullmatch(r'[a-z0-9-]+', key)
            monitor.command(vm, 'sendkey ' + key + ' 30')
            time.sleep(.07)

    def unlock(label, selection=None):
        time.sleep(15)
        capture(label + '-prompt')
        # Public disposable fixture, never a user's password. No monitor transcript.
        type_text(disk_password)
        monitor.command(vm, 'sendkey ret')
        if selection:
            # GRUB's Argon2 takes a moment before the menu appears.
            # Keep the menu stopped as soon as it appears after unlocking.
            for _ in range(300):
                monitor.command(vm, 'sendkey up')
                time.sleep(.2)
            capture('snapshot-menu')
            select_snapshot_menu(selection, lambda command: monitor.command(vm, command), capture)
        else:
            time.sleep(12)
        capture(label + '-after-unlock')
        result = run('iso-wait-ssh.sh', '--user', user, timeout=600, check=False)
        (vm / (label + '-ssh.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            capture(label + '-failed')
            raise RuntimeError('Installed SSH did not become ready after GRUB unlock')

    fixture = json.loads((ROOT / 'installer/fixtures' / f'plan-erase-{fs}.json').read_text())
    fixture.update(encryption='account' if fs == 'btrfs' else 'separate', hibernation=True,
                   software='minimal', online_update=False)
    if fs == 'ext4':
        fixture['disk_password'] = 'disk-vm-test-only'
    user = fixture['user']['login']
    password = fixture['user']['password']
    disk_password = fixture.get('disk_password', password)

    def sudo(command, extra=b''):
        return ssh("sudo -S -p '' bash -c " + shlex.quote(command),
                   data=password.encode() + b'\n' + extra, user=user)

    def snapshot_boot():
        # The menu path counts the UEFI-only firmware-settings entry; under BIOS every index shifts.
        if sudo('test -d /sys/firmware/efi && echo efi || echo bios').strip() != b'efi':
            raise RuntimeError('snapshot menu navigation is UEFI-only; this guest did not boot through UEFI')
        grub = sudo('cat /boot/grub/grub-btrfs.cfg').decode()
        (vm / 'grub-btrfs.cfg').write_text(grub)
        main_grub = sudo('cat /boot/grub/grub.cfg').decode()
        (vm / 'grub.cfg').write_text(main_grub)
        selection = snapshot_selection(main_grub, grub)
        (vm / 'snapshot-selection.json').write_text(json.dumps(selection, indent=2) + '\n')
        sudo('sync')
        stop()
        # The initial snapshot predates sshd's first generated host keys.
        (vm / 'ssh-host-generation').write_text(str(time.time_ns()) + '\n')
        start(False)
        unlock('snapshot', selection)
        status = json.loads(ssh('emaki-rollback status --json', user=user))
        (vm / 'snapshot-status.json').write_text(json.dumps(status, indent=2) + '\n')
        verify_snapshot(selection, status)
        evidence = sudo('set -eu; findmnt /; test "$(findmnt -n -o FSTYPE /)" = overlay; swapon --show; test -f /etc/cryptsetup-keys.d/emaki-root.key; systemctl --failed --no-pager')
        (vm / 'snapshot-boot.txt').write_bytes(evidence)
        print('OK: generated snapshot entry boots encrypted root with the writable recovery overlay', flush=True)

    try:
        if snapshot_only:
            assert (vm / 'installed-checks.txt').is_file(), 'Requires a previously installed test disk'
            (vm / 'ssh-host-generation').write_text(str(time.time_ns()) + '\n')
            start(False)
            unlock('snapshot-source')
            snapshot_boot()
            return
        start(True)
        print(run('iso-wait-ssh.sh').stdout.decode(), flush=True)
        ssh('test -d /run/archiso/bootmnt && grep -qw emaki.test=1 /proc/cmdline')
        ssh('for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
        if mode == 'manual':
            ssh("sudo sfdisk /dev/vda <<'EOF'\nlabel: gpt\nsize=1G,type=U\ntype=L\nEOF\nsudo udevadm settle; sudo mkfs.fat -F32 /dev/vda1")
            fixture.update(mode='manual', mounts=[
                {'partition_id': fixture['disk_id'] + '-part1', 'mountpoint': '/efi', 'fs': 'vfat', 'format': False},
                {'partition_id': fixture['disk_id'] + '-part2', 'mountpoint': '/', 'fs': fs, 'format': True}])
        (vm / 'capabilities.txt').write_bytes(ssh('pacman -Q grub cryptsetup mkinitcpio; ls /usr/lib/grub/x86_64-efi/{luks2,argon2}.mod'))
        (vm / 'plan.json').write_text(json.dumps(fixture))
        ssh('umask 077; cat > /tmp/encrypt-plan.json', data=json.dumps(fixture).encode())
        print('Installing ' + fs, flush=True)
        result = run('iso-ssh.sh', 'emaki-install-cli --plan /tmp/encrypt-plan.json --yes', timeout=5400, check=False)
        (vm / 'install.ndjson').write_bytes(result.stdout)
        (vm / 'install.stderr').write_bytes(result.stderr)
        (vm / 'worker.log').write_bytes(ssh('sudo cat /var/log/emaki-install.log'))
        assert result.returncode == 0, 'Installation failed; see install.ndjson'
        assert password.encode() not in result.stdout and disk_password.encode() not in result.stdout
        stop()
        start(False)
        unlock('installed')
        ssh('cat > /tmp/guest-login.py', data=(HERE / 'guest-login.py').read_bytes(), user=user)
        time.sleep(10)
        print(sudo('python3 /tmp/guest-login.py ' + user + ' niri-emaki-session', password.encode()).decode(), flush=True)
        ssh('''export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
for i in $(seq 1 60); do systemctl --user is-active --quiet niri-emaki.service emaki-shell.service && exit 0; sleep 1; done
systemctl --user status niri-emaki.service emaki-shell.service --no-pager
exit 1''', user=user)
        checks = r'''set -eu
cat /proc/cmdline
cat /sys/power/state
grep -qw disk /sys/power/state
swapon --show --bytes
test "$(stat -c %a /etc/cryptsetup-keys.d/emaki-root.key)" = 600
test "$(stat -c %a /boot)" = 700
cryptsetup luksDump /dev/vda2
cat /etc/fstab
cat /etc/mkinitcpio.conf
lsinitcpio /boot/initramfs-linux.img | grep etc/cryptsetup-keys.d/emaki-root.key
test "$(busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanHibernate)" = 's "yes"'
python3 - <<'PY'
from pathlib import Path
import subprocess
ram = 6 * 1024**3  # run-iso.sh's physical RAM, including firmware reservations
assert Path('/swap/swapfile').stat().st_size == ram
swaps = [x.split() for x in Path('/proc/swaps').read_text().splitlines()[1:]]
disk = next(x for x in swaps if x[0] == '/swap/swapfile')
zram = next(x for x in swaps if x[0].startswith('/dev/zram'))
assert int(zram[4]) > int(disk[4])
assert int(Path('/sys/power/resume_offset').read_text()) > 0
PY
if findmnt -n -o FSTYPE / | grep -qx btrfs; then
    test "$(btrfs inspect-internal map-swapfile -r /swap/swapfile)" = "$(cat /sys/power/resume_offset)"
    btrfs subvolume list /
    snapper --no-dbus -c root list
    grep -q 'Emaki snapshots' /boot/grub/grub.cfg
    test "$(findmnt -n -o FSROOT /swap)" = /@swap
fi
echo OK: encrypted boot, keyfile, swap, resume offset and CanHibernate
'''
        evidence = sudo(checks)
        (vm / 'installed-checks.txt').write_bytes(evidence)
        print(evidence.decode(), flush=True)
        dump = sudo('cryptsetup luksDump /dev/vda2').decode()
        (vm / 'luks-dump.txt').write_text(dump)
        check_keyslots(dump)
        print('OK: keyslot 0 (passphrase) argon2id, keyslot 1 (keyfile) pbkdf2 at 1000 iterations', flush=True)
        # Polkit challenges remote SSH callers. Query from a child of the active
        # desktop compositor, as the shell does, without changing policy.
        ssh('''for sock in /run/user/$(id -u)/niri.*.sock; do
test -S "$sock" || continue
NIRI_SOCKET="$sock" niri-emaki msg action spawn -- sh -c 'busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanHibernate > "$XDG_RUNTIME_DIR/can-hibernate"'
break
done
for i in $(seq 1 100); do test -s /run/user/$(id -u)/can-hibernate && exit 0; sleep .1; done
exit 1''', user=user)
        capability = ssh('cat /run/user/$(id -u)/can-hibernate', user=user)
        (vm / 'user-can-hibernate.txt').write_bytes(capability)
        assert capability.strip() == b's "yes"', 'Desktop account cannot hibernate'
        marker = secrets.token_hex(32)
        ssh('printf %s ' + shlex.quote(marker) + ' > /run/user/$(id -u)/hibernate-proof', user=user)
        def resume_probe():
            return json.loads(ssh(RESUME_PROBE, user=user, timeout=15))
        before = resume_probe()
        (vm / 'pre-hibernate.json').write_text(json.dumps(before, indent=2) + '\n')
        print('Hibernating', flush=True)
        result = run('iso-ssh.sh', '--user', user, "sudo -S -p '' systemctl hibernate",
                     data=password.encode() + b'\n', check=False, timeout=120)
        (vm / 'hibernate.txt').write_bytes(result.stdout + result.stderr)
        qemu.wait(timeout=180)
        # QEMU's S4 exit status is evidence, not proof of a completed resume.
        # Require the saved kernel boot ID and volatile marker after restarting.
        print(f'QEMU exit after hibernation: {qemu.returncode}', flush=True)
        (vm / 'hibernate-qemu-exit').write_text(str(qemu.returncode) + '\n')
        stop()
        start(False)
        try:
            unlock('resume')
            observe_resume(before, resume_probe, vm / 'resume-proof.json')
            # Framebuffer capture remains available even if the desktop or SSH has died.
            capture('resume-after-observation')
            # A reboot during screenshot collection must also fail the run.
            if resume_probe() != before:
                raise RuntimeError('Session changed during post-resume screenshot capture')
        except Exception as error:
            (vm / 'resume-failure.txt').write_text(str(error) + '\n')
            proof_file = vm / 'resume-proof.json'
            proof = json.loads(proof_file.read_text()) if proof_file.exists() else {'before': before}
            proof.update(status='FAIL', error=str(error))
            proof_file.write_text(json.dumps(proof, indent=2) + '\n')
            try:
                capture('resume-failed')
            except Exception as capture_error:
                (vm / 'resume-capture-failure.txt').write_text(str(capture_error) + '\n')
            raise
        finally:
            # Bound diagnostics and preserve stdout/stderr even after SSH loss or reboot.
            try:
                diagnostic = run('iso-ssh.sh', '--user', user,
                                 "sudo -S -p '' journalctl --no-pager -n 2000",
                                 data=password.encode() + b'\n', check=False, timeout=15)
                (vm / 'resume-journal.txt').write_bytes(diagnostic.stdout)
                (vm / 'resume-journal.stderr').write_bytes(diagnostic.stderr)
                (vm / 'resume-journal-exit').write_text(str(diagnostic.returncode) + '\n')
            except subprocess.TimeoutExpired as error:
                (vm / 'resume-journal.txt').write_bytes(error.stdout or b'')
                (vm / 'resume-journal.stderr').write_bytes((error.stderr or b'') + b'\nSSH timed out\n')
            except Exception as error:
                (vm / 'resume-journal.stderr').write_text(str(error) + '\n')
        print('OK: same boot, volatile marker and desktop processes remained alive for 60 seconds after resume', flush=True)
        print('NOT TESTED: resumed desktop appearance; inspect the saved frames on target resolutions', flush=True)
        if fs == 'btrfs':
            snapshot_boot()
        (vm / 'PASS').write_text('Encrypted installation, login and 60-second resume continuity passed.\n'
                                'NOT TESTED: resumed desktop appearance and real-hardware hibernation.\n' + iso_record)
    finally:
        stop()
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
