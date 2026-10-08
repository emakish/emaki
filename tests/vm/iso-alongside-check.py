#!/usr/bin/env python3
"""Synthetic Windows preservation, encrypted install, both EFI targets and resume."""
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
spec = importlib.util.spec_from_file_location('monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
spec = importlib.util.spec_from_file_location('encrypt_check', HERE / 'iso-encrypt-check.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


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


# Neither a pass nor a failure: the image does not offer the mode under test.
# The same status the installer UI tests use for a check that cannot run here.
NOT_APPLICABLE = 77


def mode_refused(output):
    """True when the worker's plan reply refuses the alongside mode itself."""
    for line in output.decode(errors='replace').splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get('type') == 'plan_ack' and any(
                error.get('code') == 'unsupported_mode' for error in msg.get('errors') or []):
            return True
    return False


def main():
    fs, name = sys.argv[1:3]
    assert fs in ('btrfs', 'ext4') and re.fullmatch('[a-z0-9-]+', name)
    iso, iso_record = checked_iso()
    base = Path(os.environ['VMDIR']).resolve()
    vms = (Path.home() / 'VMs').resolve()
    assert base != vms and base.is_relative_to(vms), f'VMDIR must be a directory under {vms}'
    os.umask(0o077)
    vm = base / (fs + '-' + name)
    vm.mkdir()
    (vm / 'iso.txt').write_text(iso_record)
    env = dict(os.environ, EMAKI_ISO_VM_DIR=str(vm), EMAKI_ISO_SSH_PORT='2241')
    qemu = None
    handles = []
    fixture = json.loads((HERE / 'fixtures/plan-alongside.json').read_text())
    fixture.update(fs=fs, encryption='account' if fs == 'btrfs' else 'separate', hibernation=True)
    if fs == 'ext4':
        fixture['disk_password'] = 'disk-vm-test-only'
    user = fixture['user']['login']
    password = fixture['user']['password']
    disk_password = fixture.get('disk_password', password)

    def run(script, *args, data=None, timeout=600, check=True):
        result = subprocess.run(['bash', str(HERE / script), '--dir', str(vm), '--ssh-port', '2241', *args],
                                input=data, capture_output=True, env=env, timeout=timeout)
        if check and result.returncode:
            (vm / 'command-failure.log').write_bytes(result.stdout + result.stderr)
            result.check_returncode()
        return result

    def ssh(command, data=None, account='live', timeout=600):
        return run('iso-ssh.sh', '--user', account, command, data=data, timeout=timeout).stdout

    def sudo(command, extra=b''):
        return ssh("sudo -S -p '' bash -c " + shlex.quote(command),
                   data=password.encode() + b'\n' + extra, account=user)

    def start(live):
        nonlocal qemu
        output = (vm / f'qemu-{time.time_ns()}.log').open('wb')
        handles.append(output)
        args = ['--iso', str(iso)] if live else ['--no-cd']
        qemu = subprocess.Popen(['bash', str(HERE / 'run-iso.sh'), '--dir', str(vm), '--ssh-port', '2241', *args],
                                env=env, stdout=output, stderr=subprocess.STDOUT)

    def stop():
        if qemu and qemu.poll() is None:
            monitor.command(vm, 'quit')
            qemu.wait(timeout=60)
        result = run('iso-stop.sh', check=False)
        print(result.stdout.decode(), end='', flush=True)

    def capture(label):
        frame = resume.capture_frame(vm, label)
        print(f'SHOT: {frame} (not judged); HUMAN REVIEW REQUIRED', flush=True)
        return frame

    def type_text(value):
        keys = {' ': 'spc', '-': 'minus', '/': 'slash', '.': 'dot', '=': 'equal',
                ',': 'comma', ':': 'shift-semicolon', '_': 'shift-minus',
                '@': 'shift-2', '"': 'shift-apostrophe', "'": 'apostrophe',
                '$': 'shift-4', '(': 'shift-9', ')': 'shift-0', '{': 'shift-bracket_left',
                '}': 'shift-bracket_right', ';': 'semicolon'}
        for char in value:
            key = keys.get(char, 'shift-' + char.lower() if char.isupper() else char)
            assert re.fullmatch('[a-z0-9-]+', key)
            monitor.command(vm, 'sendkey ' + key + ' 30')
            time.sleep(.07)

    def boot(label, windows=False):
        start(False)
        time.sleep(15)
        capture(label + '-unlock')
        type_text(disk_password)
        monitor.command(vm, 'sendkey ret')
        # Stop the countdown throughout firmware Argon2 decryption.
        for _ in range(300):
            monitor.command(vm, 'sendkey up')
            time.sleep(.2)
        capture(label + '-menu')
        if windows:
            monitor.command(vm, 'sendkey home')
            for _ in range(windows_index):
                monitor.command(vm, 'sendkey down')
                time.sleep(.2)
            capture(label + '-selected')
        else:
            monitor.command(vm, 'sendkey home')
        monitor.command(vm, 'sendkey ret')
        if windows:
            for _ in range(60):
                if 'EMAKI_SYNTHETIC_WINDOWS_EFI_OK' in (vm / 'serial.log').read_text(errors='replace'):
                    capture(label + '-target')
                    print('OK: GRUB Windows entry executed the preserved diagnostic EFI target', flush=True)
                    return
                time.sleep(1)
            capture(label + '-failure')
            raise RuntimeError('Windows EFI target marker absent')
        result = run('iso-wait-ssh.sh', '--user', user, check=False)
        (vm / (label + '-ssh.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            capture(label + '-failure')
        assert result.returncode == 0, 'Emaki target did not boot'

    try:
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(vm / 'target.qcow2'), '96G'], check=True)
        start(True)
        print(run('iso-wait-ssh.sh').stdout.decode(), flush=True)
        ssh('test -d /run/archiso/bootmnt && grep -qw emaki.test=1 /proc/cmdline')
        # This release ships with alongside Windows switched off in the installer core.
        # Ask the packaged worker for a plan only (no --yes, nothing is written):
        # a refusal of the mode itself means this scenario does not apply.
        ssh('for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
        ssh('umask 077; cat > /tmp/alongside-offer.json', data=json.dumps(fixture).encode())
        offer = run('iso-ssh.sh', 'emaki-install-cli --plan /tmp/alongside-offer.json', check=False)
        (vm / 'offer.ndjson').write_bytes(offer.stdout + offer.stderr)
        if mode_refused(offer.stdout):
            (vm / 'NOT-APPLICABLE').write_text(
                'The installer in this image does not offer install alongside Windows.\n' + iso_record)
            print('NOT APPLICABLE: the installer in this ISO refuses install alongside Windows '
                  '(off in this release); nothing was installed or checked', flush=True)
            return NOT_APPLICABLE
        (vm / 'capabilities.txt').write_bytes(ssh('pacman -Q ntfs-3g ntfsprogs gptfdisk os-prober grub archinstall cryptsetup'))
        ssh('cat > /tmp/windows-efi.cfg', data=b'serial --unit=0 --speed=115200\nterminal_output console serial\necho EMAKI_SYNTHETIC_WINDOWS_EFI_OK\nsleep 3600\n')
        ssh("grub-mkstandalone -O x86_64-efi --locales='' --fonts='' -o /tmp/windows-fixture.efi boot/grub/grub.cfg=/tmp/windows-efi.cfg")
        ssh('cat > /tmp/windows-disk.py', data=(HERE / 'fixtures/windows-disk.py').read_bytes())
        baseline = ssh('sudo python3 /tmp/windows-disk.py prepare')
        json.loads(baseline)
        (vm / 'windows-before.json').write_bytes(baseline)
        ssh('for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
        ssh('cat > /tmp/windows-refusals.py', data=(HERE / 'fixtures/windows-refusals.py').read_bytes())
        ssh('sudo systemctl stop emaki-installerd')
        refusals = ssh('sudo python3 /tmp/windows-refusals.py', data=json.dumps(fixture).encode())
        (vm / 'refusals.txt').write_bytes(refusals)
        print(refusals.decode(), flush=True)
        ssh('sudo systemctl start emaki-installerd')
        ssh('for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
        probe = ssh('emaki-install-cli --probe')
        (vm / 'probe.ndjson').write_bytes(probe)
        (vm / 'plan.json').write_text(json.dumps(fixture))
        ssh('umask 077; cat > /tmp/alongside-plan.json', data=json.dumps(fixture).encode())
        print('Installing alongside with encryption and hibernation: ' + fs, flush=True)
        result = run('iso-ssh.sh', 'emaki-install-cli --plan /tmp/alongside-plan.json --yes', timeout=5400, check=False)
        (vm / 'install.ndjson').write_bytes(result.stdout)
        (vm / 'install.stderr').write_bytes(result.stderr)
        (vm / 'worker.log').write_bytes(ssh('sudo cat /var/log/emaki-install.log'))
        assert result.returncode == 0, 'Installation failed; see install.ndjson'
        assert password.encode() not in result.stdout and disk_password.encode() not in result.stdout
        evidence = ssh('sudo python3 /tmp/windows-disk.py check --freed ' + str(fixture['shrink_bytes']), data=baseline)
        (vm / 'preservation-live.txt').write_bytes(evidence)
        print(evidence.decode(), flush=True)
        stop()
        boot('emaki')
        checks = r'''set -eu
cat /proc/cmdline
cat /etc/fstab
swapon --show --bytes
cryptsetup luksDump /dev/vda4
test "$(stat -c %s /swap/swapfile)" = 6442450944
test "$(stat -c %a /etc/cryptsetup-keys.d/emaki-root.key)" = 600
test "$(stat -c %a /boot)" = 700
test "$(cat /sys/power/resume_offset)" -gt 0
test "$(busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanHibernate)" = 's "yes"'
test -z "$(systemctl --failed --no-legend)"
grep -q 'GRUB_DISABLE_OS_PROBER=false' /etc/default/grub
grep -q 'Windows Boot Manager' /boot/grub/grub.cfg
if findmnt -n -o FSTYPE / | grep -qx btrfs; then
    test "$(findmnt -n -o FSROOT /swap)" = /@swap
    test "$(btrfs inspect-internal map-swapfile -r /swap/swapfile)" = "$(cat /sys/power/resume_offset)"
    snapper --no-dbus -c root list
fi
echo OK: alongside encrypted root and RAM-sized hibernation file
'''
        (vm / 'installed-checks.txt').write_bytes(sudo(checks))
        grub = sudo('cat /boot/grub/grub.cfg').decode()
        (vm / 'grub.cfg').write_text(grub)
        entries = re.findall(r'^(?:menuentry|submenu) .+', grub, re.M)
        windows_index = next(i for i, entry in enumerate(entries) if 'Windows Boot Manager' in entry)
        ssh('cat > /tmp/windows-disk.py', data=(HERE / 'fixtures/windows-disk.py').read_bytes(), account=user)
        (vm / 'preservation-installed.txt').write_bytes(sudo(
            'python3 /tmp/windows-disk.py check --freed ' + str(fixture['shrink_bytes']), baseline))
        ssh('cat > /tmp/guest-login.py', data=(HERE / 'guest-login.py').read_bytes(), account=user)
        time.sleep(10)
        print(sudo('python3 /tmp/guest-login.py ' + user + ' niri-emaki-session', password.encode()).decode(), flush=True)
        ssh('''export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
for i in $(seq 1 60); do systemctl --user is-active --quiet niri-emaki.service emaki-shell.service && exit 0; sleep 1; done
exit 1''', account=user)
        marker = secrets.token_hex(32)
        ssh('printf %s ' + shlex.quote(marker) + ' > /run/user/$(id -u)/hibernate-proof', account=user)
        def resume_probe():
            return json.loads(ssh(resume.RESUME_PROBE, account=user, timeout=15))
        before = resume_probe()
        (vm / 'pre-hibernate.json').write_text(json.dumps(before, indent=2) + '\n')
        result = run('iso-ssh.sh', '--user', user, "sudo -S -p '' systemctl hibernate",
                     data=password.encode() + b'\n', timeout=120, check=False)
        (vm / 'hibernate.log').write_bytes(result.stdout + result.stderr)
        qemu.wait(timeout=180)
        (vm / 'hibernate-qemu-exit').write_text(str(qemu.returncode) + '\n')
        stop()
        try:
            boot('resume')
            resume.observe_resume(before, resume_probe, vm / 'resume-proof.json')
            capture('resume-after-observation')
            if resume_probe() != before:
                raise RuntimeError('Session changed during post-resume screenshot capture')
        except Exception as error:
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
        print('NOT TESTED: resumed desktop appearance and real-hardware hibernation', flush=True)
        stop()
        boot('windows', windows=True)
        stop()
        (vm / 'PASS').write_text('Resize, NTFS/ESP/MSR preservation, encrypted Emaki boot, 60-second resume continuity and synthetic Windows EFI target passed.\nHUMAN REVIEW REQUIRED: unlock, boot menus, target screens and resumed desktop frames are not judged.\nNOT TESTED: real-hardware hibernation.\n' + iso_record)
    finally:
        stop()
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    sys.exit(main())
