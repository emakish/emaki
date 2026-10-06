#!/usr/bin/env python3
"""Install, unlock, log in, hibernate and resume a disposable encrypted VM."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
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
        monitor.command(vm, f'screendump {vm / (label + ".ppm")}')

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

    def unlock(label, snapshot_commands=None):
        time.sleep(15)
        capture(label + '-prompt')
        # Public disposable fixture, never a user's password. No monitor transcript.
        type_text(disk_password)
        monitor.command(vm, 'sendkey ret')
        if snapshot_commands:
            # GRUB's Argon2 takes a moment before the menu appears.
            # Keep the menu stopped as soon as it appears after unlocking.
            for _ in range(300):
                monitor.command(vm, 'sendkey up')
                time.sleep(.2)
            capture('snapshot-menu')
            monitor.command(vm, 'sendkey c')
            time.sleep(1)
            for command in snapshot_commands:
                type_text(command)
                monitor.command(vm, 'sendkey ret')
                time.sleep(1)
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
        grub = sudo('cat /boot/grub/grub-btrfs.cfg').decode()
        (vm / 'grub-btrfs.cfg').write_text(grub)
        linux = next(line.strip() for line in grub.splitlines()
                     if re.match(r'\s*linux\s', line) and '/vmlinuz-linux ' in line.replace('"', ''))
        remaining = grub[grub.index(linux) + len(linux):]
        initrd = next(line.strip() for line in remaining.splitlines() if re.match(r'\s*initrd\s', line))
        uuid = sudo('findmnt -n -o UUID /').decode().strip()
        commands = ['search --no-floppy --fs-uuid --set=root ' + uuid, linux, initrd, 'boot']
        (vm / 'snapshot-commands.txt').write_text('\n'.join(commands) + '\n')
        sudo('sync')
        stop()
        # The initial snapshot predates sshd's first generated host keys.
        (vm / 'ssh-host-generation').write_text(str(time.time_ns()) + '\n')
        start(False)
        unlock('snapshot', commands)
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
        before = ssh('cat /proc/sys/kernel/random/boot_id', user=user).strip()
        ssh('printf retained > /run/user/$(id -u)/hibernate-proof', user=user)
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
        unlock('resume')
        after = ssh('cat /proc/sys/kernel/random/boot_id; cat /run/user/$(id -u)/hibernate-proof', user=user)
        assert after.splitlines() == [before, b'retained'], 'Cold boot occurred instead of resume'
        (vm / 'resume-proof.json').write_text(json.dumps({
            'before_boot_id': before.decode(), 'after_boot_id': after.splitlines()[0].decode(),
            'volatile_marker': after.splitlines()[1].decode()}) + '\n')
        (vm / 'resume-journal.txt').write_bytes(sudo('journalctl -b --no-pager | grep -iE "hibernat|PM:.*(image|resume|restor)"'))
        ssh('cat > /tmp/guest-shot.py', data=(HERE / 'iso-guest-shot.py').read_bytes(), user=user)
        screenshot = sudo('timeout 30 python3 /tmp/guest-shot.py')
        assert screenshot.startswith(b'\x89PNG'), 'Resumed Wayland session did not render'
        (vm / 'resume-desktop.png').write_bytes(screenshot)
        print('OK: hibernation restored the same boot and volatile session marker', flush=True)
        if fs == 'btrfs':
            snapshot_boot()
        (vm / 'PASS').write_text('Encrypted installation, login, hibernate and resume passed.\n' + iso_record)
    finally:
        stop()
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
