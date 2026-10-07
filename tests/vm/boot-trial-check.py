#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise bounded loader trials with actual GRUB, OVMF and FAT ESPs.

Rootless and offline; all disk writes remain in the evidence directory. This
checks GRUB path selection and persistent counters, not Linux or promotion.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_boot import refresh

TAG = 'a' * 32
ESP_UUID = 'E0A1-0001'
OFFSET = 2048 * 512


def run(*args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stderr}')
    return result.stdout


def tool(name, package):
    packaged = package / 'usr/bin' / name
    found = str(packaged) if packaged.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f'Missing prerequisite: {name}')
    return found


def read_state(args, disk, out, label):
    target = out / f'{label}.env'
    run(tool('mcopy', args.mtools_root), '-o', '-i', f'{disk}@@{OFFSET}',
        '::/EFI/Emaki/trial.env', target)
    data = target.read_bytes()
    assert len(data) == 1024 and data.startswith(b'# GRUB Environment Block\n')
    return dict(line.split('=', 1) for line in data.decode('ascii').splitlines()
                if '=' in line and not line.startswith('#'))


def boot_vm(args, disk, out, label, marker, *, readonly=False):
    serial = out / f'{label}.serial.log'
    variables = out / f'{label}.vars.fd'
    shutil.copyfile(args.ovmf_vars, variables)
    command = [tool('qemu-system-x86_64', Path('/')), '-machine', 'q35',
               '-accel', args.accel, '-m', '256', '-display', 'none',
               '-serial', f'file:{serial}', '-monitor', 'none', '-no-reboot',
               '-net', 'none', '-drive',
               f'if=pflash,format=raw,readonly=on,file={args.ovmf_code.resolve()}',
               '-drive', f'if=pflash,format=raw,file={variables}',
               '-drive', f'if=none,id=esp,format=raw,file={disk},readonly={"on" if readonly else "off"}',
               '-device', 'virtio-blk-pci,drive=esp']
    (out / f'{label}.command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / f'{label}.qemu.log').open('w') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                text = serial.read_text(errors='replace') if serial.exists() else ''
                if marker in text:
                    break
                if process.poll() is not None:
                    raise RuntimeError(f'{label}: QEMU exited before {marker}; see {serial}')
                time.sleep(0.1)
            else:
                raise RuntimeError(f'{label}: timed out before {marker}; see {serial}')
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return serial.read_text(errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence/t5g4-trial-vm')
    parser.add_argument('--grub-root', type=Path,
                        default=Path(os.environ.get('EMAKI_GRUB_TOOLS', '/')))
    parser.add_argument('--mtools-root', type=Path, default=Path('/'))
    parser.add_argument('--ovmf-code', type=Path,
                        default=Path('/usr/share/edk2/x64/OVMF_CODE.4m.fd'))
    parser.add_argument('--ovmf-vars', type=Path,
                        default=Path('/usr/share/edk2/x64/OVMF_VARS.4m.fd'))
    parser.add_argument('--accel', choices=('tcg', 'kvm'), default='tcg')
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    modules = args.grub_root / 'usr/lib/grub/x86_64-efi'
    standalone = tool('grub-mkstandalone', args.grub_root)
    (out / 'grub-version.txt').write_text(run(standalone, '--version'))
    disk = out / 'esp.raw'
    with disk.open('wb') as stream:
        stream.truncate(129 * 1024 * 1024)
        mbr = bytearray(512)
        mbr[446:462] = struct.pack('<B3sB3sII', 0x80, b'\0\x02\0', 0xef,
                                  b'\xfe\xff\xff', 2048, 128 * 2048)
        mbr[510:512] = b'\x55\xaa'
        stream.write(mbr)
    run('mkfs.fat', '-F', '32', '-i', ESP_UUID.replace('-', ''),
        '--offset=2048', disk)
    mcopy = tool('mcopy', args.mtools_root)
    run(tool('mmd', args.mtools_root), '-i', f'{disk}@@{OFFSET}',
        '::/EFI', '::/EFI/BOOT', '::/EFI/Emaki')

    def put(source, destination):
        run(mcopy, '-o', '-i', f'{disk}@@{OFFSET}', source, '::' + destination)

    for name, generation in (('old', 'old'), ('candidate', TAG)):
        config = out / f'{name}.cfg'
        config.write_text('serial --unit=0 --speed=115200\n'
                          'terminal_output serial\nterminal_input serial\n'
                          f'set emaki_generation={generation}\n'
                          'export emaki_generation\n'
                          f'search --fs-uuid --set=root {ESP_UUID}\n'
                          'configfile /menu.cfg\n')
        run(standalone, '-O', 'x86_64-efi', '-d', modules, '--fonts=',
            '--modules=part_msdos fat search search_fs_uuid normal configfile chain loadenv serial',
            '--themes=', '--locales=', '-o', out / f'{name}.efi',
            f'boot/grub/grub.cfg={config}')
    menu = out / 'menu.cfg'
    normal_menu = ('set default=0\nset timeout=0\n'
                    "menuentry 'Normal entry' {\n"
                    f'  if [ "$emaki_generation" = "{TAG}" ]; then\n'
                    '    echo EMAKI_TRIAL_NEW_NORMAL\n'
                    '  else\n    echo EMAKI_TRIAL_OLD_NORMAL\n  fi\n'
                    '  sleep 120\n}\n')
    trial_menu = refresh.trial_menu(TAG, ESP_UUID, normal_menu)
    assert re.findall(r'^menuentry .*', trial_menu, re.M) == ["menuentry 'Normal entry' {"]
    assert trial_menu.count('EMAKI_TRIAL_OLD_NORMAL') == 1
    assert trial_menu.count('EMAKI_TRIAL_NEW_NORMAL') == 1
    menu.write_text(trial_menu)
    put(menu, '/menu.cfg')
    put(out / 'old.efi', '/EFI/BOOT/BOOTX64.EFI')
    environment = out / 'initial.env'
    environment.write_bytes(refresh.trial_block({'emaki_candidate': TAG, 'emaki_trial': '0'}))
    put(environment, '/EFI/Emaki/trial.env')
    corrupt = out / 'corrupt.efi'
    corrupt.write_bytes(b'Invalid executable\n')
    candidate_path = f'/EFI/Emaki/loader-{TAG}.efi'
    put(corrupt, candidate_path)
    results = []
    for attempt, expected in enumerate(('1', '2', '2'), 1):
        label = f'corrupt-{attempt}'
        trace = boot_vm(args, disk, out, label, 'EMAKI_TRIAL_OLD_NORMAL')
        state = read_state(args, disk, out, label)
        assert state['emaki_trial'] == expected, (label, state)
        assert '/EFI/BOOT' in state['emaki_trial_source'].upper(), (label, state)
        assert 'EMAKI_TRIAL_NEW_NORMAL' not in trace
        results.append({'boot': label, 'state': state, 'entry': 'old'})
        print(f'PASS {label}: normal old entry; persistent count {expected}', flush=True)
    put(environment, '/EFI/Emaki/trial.env')
    put(out / 'candidate.efi', candidate_path)
    trace = boot_vm(args, disk, out, 'valid', 'EMAKI_TRIAL_NEW_NORMAL')
    state = read_state(args, disk, out, 'valid')
    assert state['emaki_trial'] == '1', state
    assert '/EFI/BOOT' in state['emaki_trial_source'].upper(), state
    assert 'EMAKI_TRIAL_OLD_NORMAL' not in trace
    results.append({'boot': 'valid', 'state': state, 'entry': 'new'})
    print('PASS valid: normal new entry; persistent count 1', flush=True)
    for label, data in (
        ('missing-env', None),
        ('corrupt-env', b'Invalid environment\n' + b'#' * (1024 - 20)),
        ('truncated-env', environment.read_bytes()[:32]),
        ('readonly-env', environment.read_bytes()),
    ):
        if data is None:
            run(tool('mdel', args.mtools_root), '-i', f'{disk}@@{OFFSET}',
                '::/EFI/Emaki/trial.env')
        else:
            fixture = out / f'{label}-input.env'
            fixture.write_bytes(data)
            put(fixture, '/EFI/Emaki/trial.env')
        trace = boot_vm(args, disk, out, label, 'EMAKI_TRIAL_OLD_NORMAL',
                        readonly=label == 'readonly-env')
        assert 'EMAKI_TRIAL_NEW_NORMAL' not in trace, label
        if label == 'readonly-env':
            state = read_state(args, disk, out, label)
            assert state['emaki_trial'] == '0', state
        else:
            state = None
        results.append({'boot': label, 'state': state, 'entry': 'old'})
        print(f'PASS {label}: normal old entry', flush=True)
    # A disarmed menu must stop touching the unusable trial store entirely.
    # This supplies the normal menu directly; installed mark_good is separate.
    menu.write_text(normal_menu)
    put(menu, '/menu.cfg')
    trace = boot_vm(args, disk, out, 'disarmed-readonly', 'EMAKI_TRIAL_OLD_NORMAL',
                    readonly=True)
    assert 'EMAKI_TRIAL_NEW_NORMAL' not in trace
    assert 'error:' not in trace.lower(), trace
    assert read_state(args, disk, out, 'disarmed-readonly')['emaki_trial'] == '0'
    results.append({'boot': 'disarmed-readonly', 'entry': 'old', 'errors': False})
    print('PASS disarmed-readonly: normal old entry without errors', flush=True)
    (out / 'results.json').write_text(json.dumps({
        'result': 'PASS', 'boots': results,
        'scope': 'Actual GRUB/OVMF on writable and read-only FAT; no Linux boot or promotion tested.',
    }, indent=2) + '\n')
    print('GRUB path mechanics only; Linux boot and promotion remain separate checks.')


if __name__ == '__main__':
    main()
