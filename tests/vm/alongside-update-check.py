#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package updates on an alongside install whose ESP is shared with a synthetic Windows.

Phases, run in order against one VM directory strictly inside ~/VMs:

  live              boot the alongside test ISO on a new empty 96 GiB disk
  prepare           create the synthetic Windows disk (--esp-mib 100|260|512) and copy
                    package files for the update from the ISO's offline repository
  live-installer    put this checkout's installer modules into the live worker (the
                    test image predates them), keeping the image's test-only switch
  install           install alongside Windows (btrfs, no encryption) and power off
  boot              boot the installed disk and wait for the installed user's SSH
  overlay           install the candidate boot files from this checkout over the
                    packaged ones (the test image predates them; nothing is signed)
  manifest LABEL    record the shared ESP's full file manifest and firmware entries
  order-check LABEL record them and require Emaki first with Windows' entry kept
  hibernate/wake    put/remove a Fast Startup record on the synthetic Windows volume
  update LABEL      reinstall the copied packages with pacman; --expect-refusal
                    requires the shared-ESP refusal and an unchanged ESP and package set
  hibernate-attempt start systemd-hibernate.service (no swap: sleep fails) and require
                    the drop-in to restore a read-only /efi and the update counters
  windows-grub      reboot, choose Windows Boot Manager in GRUB, require its marker
  windows-first     imitate a Windows update putting its boot manager first
  next-boot LABEL   set BootNext to a firmware entry (a one-time firmware menu choice)
  reboot            reboot and, unless --windows, wait for Emaki's SSH again
  shot LABEL        capture the VM's display
  stop              power off

Every phase leaves its output in the VM directory. This tests chainloading of a
synthetic Windows loader, not Windows; it is not hardware or real-Windows acceptance.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location('monitor', HERE / 'iso-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
spec = importlib.util.spec_from_file_location('encrypt_check', HERE / 'iso-encrypt-check.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)

PLAN = json.loads((ROOT / 'installer/fixtures/plan-alongside.json').read_text())
PLAN.update(fs='btrfs', encryption='none', hibernation=False, software='minimal')
USER, PASSWORD = PLAN['user']['login'], PLAN['user']['password']
PACKAGES = ('grub', 'linux')
WINDOWS_MARKER = 'EMAKI_SYNTHETIC_WINDOWS_EFI_OK'
OVERLAY = {
    'boot/': [ROOT / 'grub/emaki_boot' / name for name in
              ('__init__.py', 'refresh.py', 'shared.py', 'update.py', 'update_menu.py', 'usable.py')]
             + [ROOT / 'installer/emaki_installer' / (name + '.py') for name in ('boot', 'errors', 'grub_screen')],
    'bin/': [ROOT / 'scripts/emaki-boot-order'],
    'hooks/': [ROOT / 'grub' / name for name in
               ('00-emaki-alongside.hook', '04-emaki-update-boot.hook', '06-emaki-update-boot.hook')],
}


class Guest:
    def __init__(self, vm, port):
        self.vm, self.port = vm, port

    def script(self, name, *args, user=None, data=None, timeout=900, check=True):
        result = subprocess.run(['bash', str(HERE / name), '--dir', str(self.vm), '--ssh-port', str(self.port),
                                 *(['--user', user] if user else []), *args],
                                input=data, capture_output=True, timeout=timeout)
        if check and result.returncode:
            (self.vm / 'command-failure.log').write_bytes(result.stdout + result.stderr)
            raise RuntimeError(f'{name} {args[:1]} failed ({result.returncode}): '
                               + (result.stdout + result.stderr).decode(errors='replace')[-2000:])
        return result

    def ssh(self, command, user='live', data=None, timeout=900, check=True):
        return self.script('iso-ssh.sh', command, user=user, data=data, timeout=timeout, check=check)

    def sudo(self, command, data=b'', timeout=900, check=True):
        return self.ssh("sudo -S -p '' bash -c " + shlex.quote(command), user=USER,
                        data=PASSWORD.encode() + b'\n' + data, timeout=timeout, check=check)

    def put(self, path, data, user=USER):
        self.ssh('umask 077; cat > ' + shlex.quote(path), user=user, data=data)

    def wait(self, user, seconds=600):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.script('iso-ssh.sh', 'true', user=user, timeout=20, check=False).returncode == 0:
                return
            time.sleep(4)
        raise RuntimeError('guest SSH did not become ready for ' + user)

    def monitor(self, text):
        return monitor.command(self.vm, text)

    def start(self, live=False, iso=None):
        args = ['--iso', str(iso)] if live else ['--no-cd']
        log = (self.vm / f'qemu-{time.time_ns()}.log').open('wb')
        subprocess.Popen(['setsid', 'bash', str(HERE / 'run-iso.sh'), '--dir', str(self.vm),
                          '--ssh-port', str(self.port), *args],
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            if (self.vm / 'mon.sock').exists():
                return
            time.sleep(0.2)
        raise RuntimeError('QEMU monitor did not appear')

    def stop(self, force=False):
        return self.script('iso-stop.sh', *(['--force'] if force else []), check=False).stdout.decode()

    def shot(self, label):
        frame = resume.capture_frame(self.vm, label)
        print(f'SHOT: {frame} (not judged); HUMAN REVIEW REQUIRED', flush=True)
        return frame

    def serial_marker(self, seconds=120):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if WINDOWS_MARKER in (self.vm / 'serial.log').read_text(errors='replace'):
                return True
            time.sleep(1)
        return False


def record(vm, name, data):
    path = vm / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    print('saved ' + str(path), flush=True)
    return path


def overlay_archive():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for prefix, paths in OVERLAY.items():
            for path in paths:
                data = path.read_bytes()
                member = tarfile.TarInfo(prefix + path.name)
                member.size, member.mode, member.mtime = len(data), 0o644, 0
                archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def manifest(guest, label):
    guest.put('/tmp/alongside-update-guest.py', (HERE / 'fixtures/alongside-update-guest.py').read_bytes())
    text = guest.sudo('python3 /tmp/alongside-update-guest.py manifest').stdout
    value = json.loads(text)
    record(guest.vm, f'esp-{label}.json', json.dumps(value, indent=2) + '\n')
    record(guest.vm, f'efibootmgr-{label}.txt', value['efibootmgr'])
    return value


def guest_step(guest, action, *args):
    guest.put('/tmp/alongside-update-guest.py', (HERE / 'fixtures/alongside-update-guest.py').read_bytes())
    result = guest.sudo('python3 /tmp/alongside-update-guest.py ' + ' '.join(map(shlex.quote, (action, *args))))
    suffix = '-' + re.sub(r'[^A-Za-z0-9_.-]+', '_', Path(args[0]).name) if args else ''
    record(guest.vm, f'guest-{action}{suffix}.json', result.stdout)
    return json.loads(result.stdout)


def update(guest, label, expect_refusal):
    vm = guest.vm
    before = manifest(guest, label + '-before')
    names = sorted(path.name for path in (vm / 'pkgs').iterdir() if path.name.endswith('.pkg.tar.zst'))
    for name in names:
        guest.put('/tmp/' + name, (vm / 'pkgs' / name).read_bytes())
    installed = guest.ssh('pacman -Q ' + ' '.join(PACKAGES), user=USER).stdout
    command = 'pacman -U --noconfirm ' + ' '.join('/tmp/' + name for name in names)
    result = guest.sudo(command + '; echo "pacman exit $?"', timeout=1800)
    output = (result.stdout + result.stderr).decode(errors='replace')
    record(vm, f'pacman-{label}.log', output)
    status = int(re.search(r'pacman exit (\d+)', output)[1])
    after = manifest(guest, label + '-after')
    if after['windows'] != before['windows']:
        raise RuntimeError('FAIL: files outside EFI/Emaki changed')
    options = after['mount']['options'].split(',')
    if 'ro' not in options:
        raise RuntimeError('FAIL: the shared ESP was left writable: ' + after['mount']['options'])
    if expect_refusal:
        if status == 0 or 'Windows is hibernated' not in output:
            raise RuntimeError('FAIL: the unsafe update was not refused')
        if after['files'] != before['files']:
            raise RuntimeError('FAIL: the refused update changed the ESP')
        if guest.ssh('pacman -Q ' + ' '.join(PACKAGES), user=USER).stdout != installed:
            raise RuntimeError('FAIL: the refused update changed packages')
        print('PASS: update refused while Windows is hibernated; ESP and packages unchanged', flush=True)
    else:
        if status != 0:
            raise RuntimeError('FAIL: the update did not succeed')
        changed = sorted(name for name in set(before['files']) | set(after['files'])
                         if before['files'].get(name) != after['files'].get(name))
        record(vm, f'esp-changes-{label}.txt', '\n'.join(changed) + '\n')
        if any(name.casefold() != 'efi/emaki' and not name.casefold().startswith('efi/emaki/')
               for name in changed):
            raise RuntimeError('FAIL: a path outside EFI/Emaki changed: ' + repr(changed))
        print('PASS: update succeeded; only EFI/Emaki changed; Windows files identical; ESP read-only: '
              + ', '.join(changed), flush=True)
    return output


# Boot jobs the evidence waits for, in groups. Each group's budget starts when the
# group before it has finished. The snapshot menu runs after boot completion and the
# boot loader retry (up to five minutes) and has its own five-minute timeout; it
# rewrites grub.cfg, so the boot journal and menu are read only after it ends.
BOOT_SERVICES = ((('emaki-update-boot.service', 'emaki-boot-complete.service'), 420),
                 (('emaki-snapshot-menu.service',), 630))


def wait_boot_services(guest, groups=BOOT_SERVICES):
    """Observe the boot jobs finishing; SSH can precede the usable-session trial.

    A never-started inactive unit is not completion. A recorded condition skip is
    valid on boots without a loader candidate or pending update. Do not start the
    units here: that could repeat acceptance after the original boot job finished.
    """
    outcomes = {}
    for units, seconds in groups:
        end = time.monotonic() + seconds
        for unit in units:
            outcomes[unit] = wait_boot_service(guest, unit, end)
    return outcomes


def wait_boot_service(guest, unit, end):
    """'completed' or 'skipped'; a failed, missing or unfinished unit raises."""
    properties = ('LoadState,ActiveState,Result,ExecMainStatus,'
                  'ExecMainExitTimestampMonotonic,ConditionResult,ConditionTimestampMonotonic')
    state = {}
    while time.monotonic() < end:
        result = guest.sudo('systemctl show ' + unit + ' --property=' + properties,
                            timeout=min(20, max(1, end - time.monotonic())))
        state = dict(line.split('=', 1) for line in result.stdout.decode().splitlines()
                     if '=' in line)
        if state.get('LoadState') != 'loaded' or state.get('ActiveState') == 'failed':
            raise RuntimeError(f'boot service {unit} failed: {state}')
        if state.get('ActiveState') == 'inactive':
            skipped = (state.get('ConditionResult') == 'no'
                       and int(state.get('ConditionTimestampMonotonic', '0')) > 0)
            exited = int(state.get('ExecMainExitTimestampMonotonic', '0')) > 0
            if skipped:
                return 'skipped'
            if exited:
                if state.get('Result') != 'success' or state.get('ExecMainStatus') != '0':
                    raise RuntimeError(f'boot service {unit} failed: {state}')
                return 'completed'
        time.sleep(min(1, max(0, end - time.monotonic())))
    raise RuntimeError(f'timed out waiting for boot service {unit}: {state}')


def boot_status(guest):
    return guest.sudo('cat /proc/cmdline; findmnt -no OPTIONS /efi; systemctl --failed --no-legend; '
                      'journalctl -b -o cat -u emaki-update-boot -u emaki-boot-complete '
                      '-u emaki-boot-order -u emaki-snapshot-menu -u emaki-boot-refresh --no-pager',
                      check=False)


def acceptance_state(outcome, journal):
    """What `emaki-update-boot accept` decided: it exits 0 whether or not it accepted."""
    if outcome == 'skipped':
        return 'not_required'
    # Its own messages carry a prefix; the last decision of the boot counts.
    for line in reversed(journal.splitlines()):
        message = line.removeprefix('Update boot protection: ')
        if message == 'The updated boot was accepted.':
            return 'accepted'
        if message.startswith('Acceptance timed out'):
            return 'timed_out'
        if message.startswith('Acceptance deferred'):
            return 'deferred'
    return 'unconfirmed'


def wait_boot(guest, label):
    guest.wait(USER, 600)
    try:
        outcomes = wait_boot_services(guest)
    except Exception as error:
        # Keep the boot journal of a failed wait, even if the guest stops answering.
        evidence = (str(error) + '\n').encode()
        try:
            status = boot_status(guest)
            evidence += status.stdout + status.stderr
        except Exception as capture_error:
            evidence += f'Boot journal capture failed: {capture_error}\n'.encode()
        record(guest.vm, f'boot-{label}-failed.txt', evidence)
        raise
    trial = guest.sudo('journalctl -b -o cat -u emaki-update-boot --no-pager', check=False)
    record(guest.vm, f'boot-{label}-update.txt', trial.stdout + trial.stderr)
    acceptance = acceptance_state(outcomes['emaki-update-boot.service'],
                                  trial.stdout.decode(errors='replace') if trial.returncode == 0 else '')
    record(guest.vm, f'boot-{label}-acceptance.json', json.dumps({'state': acceptance}) + '\n')
    print('Updated boot acceptance: ' + acceptance, flush=True)
    guest.shot(label)
    status = boot_status(guest)
    record(guest.vm, f'boot-{label}.txt', status.stdout + status.stderr)
    # windows-grub picks its entry from the menu of the latest boot.
    record(guest.vm, 'grub.cfg', guest.sudo('cat /boot/grub/grub.cfg').stdout)
    if acceptance == 'timed_out':
        # The next boot would return to the previous system.
        raise RuntimeError('FAIL: the updated boot was not accepted: no session stayed usable')


def menu_lines(frame):
    """The GRUB menu's entry lines, top to bottom, read with tesseract."""
    text = subprocess.run(['tesseract', str(frame), '-', '--psm', '6'], capture_output=True,
                          text=True, check=True).stdout
    lines = [line.strip(' *|') for line in text.splitlines() if line.strip()]
    body = []
    for line in lines:
        if line.startswith('Use the'):
            break
        if 'GNU GRUB' not in line and line.strip('_-=| '):
            body.append(line)
    return body


def select_windows_in_grub(guest):
    guest.start()
    for _ in range(250):
        guest.monitor('sendkey up')
        time.sleep(0.2)
    lines = menu_lines(guest.shot('grub-menu'))
    # After an unfinished boot GRUB defaults to the all-drivers entry inside the
    # Advanced options submenu. Esc leaves that submenu; at the top level it would
    # open GRUB's command line instead, so send it only when the submenu is shown.
    if not any('Boot Manager' in line for line in lines) and any('all drivers' in line for line in lines):
        guest.monitor('sendkey esc')
        time.sleep(0.5)
        lines = menu_lines(guest.shot('grub-menu-top'))
    record(guest.vm, 'grub-menu-ocr.txt', '\n'.join(lines) + '\n')
    # OCR can misread 'Windows' over the theme background ('Hindous'); the rest is stable.
    index = next(i for i, line in enumerate(lines) if 'Boot Manager' in line)
    guest.monitor('sendkey home')
    for _ in range(index):
        guest.monitor('sendkey down')
        time.sleep(0.2)
    guest.shot('grub-windows-selected')
    guest.monitor('sendkey ret')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--ssh-port', type=int, required=True)
    parser.add_argument('--iso')
    parser.add_argument('--esp-mib', type=int, choices=(100, 260, 512), default=100)
    parser.add_argument('--expect-refusal', action='store_true')
    parser.add_argument('--windows', action='store_true')
    parser.add_argument('phase')
    parser.add_argument('label', nargs='?')
    args = parser.parse_args()
    vm = Path(args.dir).resolve()
    home = (Path.home() / 'VMs').resolve()
    assert vm != home and vm.is_relative_to(home), 'the VM directory must be strictly inside ~/VMs'
    os.umask(0o077)
    guest = Guest(vm, args.ssh_port)
    if args.phase == 'live':
        vm.mkdir()
        iso = Path(args.iso).resolve()
        digest = hashlib.sha256()
        with iso.open('rb') as image:
            for block in iter(lambda: image.read(1 << 20), b''):
                digest.update(block)
        record(vm, 'iso.txt', f'ISO: {iso}\nsha256: {digest.hexdigest()}\n')
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(vm / 'target.qcow2'), '96G'], check=True)
        guest.start(live=True, iso=iso)
        guest.wait('live')
        guest.ssh('test -d /run/archiso/bootmnt && grep -qw emaki.test=1 /proc/cmdline '
                  '&& test -e /run/archiso/bootmnt/emaki/TEST-ALONGSIDE')
    elif args.phase == 'prepare':
        efi = b'serial --unit=0 --speed=115200\nterminal_output console serial\necho ' + WINDOWS_MARKER.encode() + b'\nsleep 3600\n'
        guest.put('/tmp/windows-efi.cfg', efi, user='live')
        guest.ssh("grub-mkstandalone -O x86_64-efi --locales='' --fonts='' -o /tmp/windows-fixture.efi "
                  'boot/grub/grub.cfg=/tmp/windows-efi.cfg')
        guest.put('/tmp/windows-disk.py', (HERE / 'fixtures/windows-disk.py').read_bytes(), user='live')
        baseline = guest.ssh(f'sudo python3 /tmp/windows-disk.py prepare --esp-mib {args.esp_mib}').stdout
        json.loads(baseline)
        record(vm, 'windows-before.json', baseline)
        (vm / 'pkgs').mkdir()
        listing = guest.ssh('ls /run/archiso/bootmnt/emaki/repo').stdout.decode().split()
        for package in PACKAGES:
            name, = [n for n in listing if re.fullmatch(re.escape(package) + r'-[^-]+-[^-]+-[a-z0-9_]+\.pkg\.tar\.zst', n)]
            data = guest.ssh('cat /run/archiso/bootmnt/emaki/repo/' + name).stdout
            record(vm, 'pkgs/' + name, data)
    elif args.phase == 'live-installer':
        # The test image predates this checkout's installer: put its modules into the
        # disposable live worker, then apply the image's own test-only alongside switch.
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for path in sorted((ROOT / 'installer/emaki_installer').glob('*.py')):
                archive.add(path, arcname=path.name)
        guest.put('/tmp/n8a1-installer.tar', stream.getvalue(), user='live')
        guest.put('/tmp/enable-test-alongside.py', (ROOT / 'iso/enable-test-alongside.py').read_bytes(), user='live')
        site = '$(python3 -c "import emaki_installer, os; print(os.path.dirname(emaki_installer.__file__))")'
        result = guest.ssh('set -e; site=' + site + '; sudo tar -xf /tmp/n8a1-installer.tar -C "$site" '
                           '--no-same-owner; sudo rm -rf "$site/__pycache__" /etc/emaki-test-alongside; '
                           'sudo python3 /tmp/enable-test-alongside.py /; '
                           'sudo systemctl restart emaki-installerd.service; cd "$site"; sha256sum *.py; '
                           'grep -n "^ALONGSIDE" constants.py')
        record(vm, 'live-installer.txt', result.stdout)
    elif args.phase == 'install':
        guest.ssh('for i in $(seq 1 300); do test -S /run/emaki-installer/sock && exit 0; sleep 1; done; exit 1')
        record(vm, 'plan.json', json.dumps(PLAN, indent=2) + '\n')
        guest.put('/tmp/alongside-plan.json', json.dumps(PLAN).encode(), user='live')
        result = guest.ssh('emaki-install-cli --plan /tmp/alongside-plan.json --yes', timeout=5400, check=False)
        record(vm, 'install.ndjson', result.stdout)
        record(vm, 'install.stderr', result.stderr)
        record(vm, 'worker.log', guest.ssh('sudo cat /var/log/emaki-install.log').stdout)
        if result.returncode:
            raise RuntimeError('installation failed; see install.ndjson')
        baseline = (vm / 'windows-before.json').read_bytes()
        record(vm, 'preservation-live.txt',
               guest.ssh('sudo python3 /tmp/windows-disk.py check --freed ' + str(PLAN['shrink_bytes']),
                         data=baseline).stdout)
        print(guest.stop(), flush=True)
    elif args.phase == 'boot':
        guest.start()
        wait_boot(guest, args.label or 'first-boot')
        guest.put('/tmp/windows-disk.py', (HERE / 'fixtures/windows-disk.py').read_bytes())
        record(vm, f'preservation-{args.label or "first-boot"}.txt', guest.sudo(
            'python3 /tmp/windows-disk.py check --freed ' + str(PLAN['shrink_bytes']),
            (vm / 'windows-before.json').read_bytes()).stdout)
    elif args.phase == 'overlay':
        guest.put('/tmp/n8a1-overlay.tar', overlay_archive())
        guest_step(guest, 'overlay', '/tmp/n8a1-overlay.tar')
    elif args.phase == 'manifest':
        manifest(guest, args.label)
    elif args.phase == 'order-check':
        text = manifest(guest, args.label or 'order-check')['efibootmgr']
        labels = dict(re.findall(r'^Boot([0-9A-F]{4})\*? ([^\t]+)\t', text, re.M))
        order = re.search(r'^BootOrder: (\S+)$', text, re.M)[1].split(',')
        if labels.get(order[0], '').strip() != 'Emaki' or 'Windows Boot Manager' not in map(str.strip, labels.values()):
            raise RuntimeError('FAIL: Emaki is not first or the Windows entry is gone: ' + ','.join(order))
        print('PASS: Emaki is first again; the Windows Boot Manager entry is kept: ' + ','.join(order), flush=True)
    elif args.phase in ('hibernate', 'wake'):
        guest_step(guest, args.phase + '-windows')
    elif args.phase == 'update':
        update(guest, args.label, args.expect_refusal)
    elif args.phase == 'hibernate-attempt':
        result = guest_step(guest, 'hibernate-attempt')
        before, after = result['before'], result['after']
        if 'ro' not in after['efi_options'].split(',') or 'rw' in after['efi_options'].split(','):
            raise RuntimeError('FAIL: /efi is not mounted read-only after the hibernate attempt')
        if after['update_env'] != before['update_env']:
            raise RuntimeError('FAIL: the update counters were not restored')
        if before['update_env'] is not None and after['update_env_mtime'] == before['update_env_mtime']:
            raise RuntimeError('FAIL: the drop-in did not write the update counters')
        print('PASS: hibernate drop-in saved and restored the counters through the gate; /efi read-only',
              flush=True)
    elif args.phase == 'windows-grub':
        print(guest.stop(), flush=True)
        (vm / 'serial.log').write_bytes(b'')
        select_windows_in_grub(guest)
        if not guest.serial_marker():
            guest.shot('windows-grub-failure')
            raise RuntimeError('FAIL: the Windows entry did not start the synthetic Windows loader')
        guest.shot('windows-grub-target')
        print('PASS: GRUB chainloaded the preserved Windows loader', flush=True)
        print(guest.stop(force=True), flush=True)
    elif args.phase == 'windows-first':
        guest_step(guest, 'windows-first')
    elif args.phase == 'next-boot':
        guest_step(guest, 'next-boot', args.label)
    elif args.phase == 'reboot':
        print(guest.stop(), flush=True)
        (vm / 'serial.log').write_bytes(b'')
        guest.start()
        if args.windows:
            if not guest.serial_marker(180):
                guest.shot((args.label or 'reboot') + '-failure')
                raise RuntimeError('FAIL: the firmware did not start the synthetic Windows loader')
            guest.shot(args.label or 'windows-firmware')
            print('PASS: firmware started the preserved Windows loader', flush=True)
        else:
            wait_boot(guest, args.label or 'reboot')
    elif args.phase == 'shot':
        guest.shot(args.label)
    elif args.phase == 'stop':
        print(guest.stop(force=bool(args.windows)), flush=True)
    else:
        parser.error('unknown phase ' + args.phase)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        sys.exit(str(error))
