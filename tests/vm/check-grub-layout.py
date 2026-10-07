#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture a synthetic GRUB component fixture and enforce each requested mode.

This fixture does not boot the candidate ISO or prove any menu's readability.
Pass --grub-root with an unpacked Arch grub package, and --out under a temporary
or workspace directory. Exit 3 means actual ISO content/readability NOT TESTED.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import socket_runtime
import time

ROOT = Path(__file__).resolve().parents[2]
SIZES = ((1366, 768), (1920, 1080), (3840, 2160), (1280, 800), (2560, 1600), (1024, 768))


def require_fixture_mode(size, requested):
    if size != requested:
        raise RuntimeError(f'GRUB did not select the requested mode {requested}: observed {size}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grub-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fat = args.out / 'fat'
    for directory in ('EFI/BOOT', 'boot/grub'):
        (fat / directory).mkdir(parents=True, exist_ok=True)
    config = (ROOT / 'iso/profile/grub/grub.cfg').read_text()
    config = config.replace('%ARCH%', 'x86_64').replace('%INSTALL_DIR%', 'emaki')
    config = config.replace('%ARCHISO_UUID%', 'fixture').replace('%KERNEL_PARAMS%', '')
    config = config.replace('timeout=15', 'timeout=-1')
    (fat / 'boot/grub/grub.cfg').write_text(config)
    shutil.copy(ROOT / 'art/grub/background.png', fat / 'boot/grub/background.png')
    shutil.copy(ROOT / 'iso/profile/grub/menu.pf2', fat / 'boot/grub/menu.pf2')
    subprocess.run([str(args.grub_root / 'usr/bin/grub-mkstandalone'), '-O', 'x86_64-efi',
                    '-d', str(args.grub_root / 'usr/lib/grub/x86_64-efi'), '--fonts=', '--themes=',
                    '--locales=', '--disable-shim-lock', '-o', str(fat / 'EFI/BOOT/BOOTX64.EFI'),
                    'boot/grub/grub.cfg=' + str(fat / 'boot/grub/grub.cfg'),
                    'boot/grub/background.png=' + str(fat / 'boot/grub/background.png'),
                    'boot/grub/menu.pf2=' + str(fat / 'boot/grub/menu.pf2'),
                    'boot/grub/fonts/unicode.pf2=' + str(args.grub_root / 'usr/share/grub/unicode.pf2')],
                   check=True)
    evidence = {'fixture': 'synthetic GRUB', 'judgment': 'NOT TESTED', 'frames': []}
    (args.out / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    for width, height in SIZES:
        name = f'{width}x{height}'
        proc = None
        monitor = socket_runtime.runtime(args.out) / 'mon.sock'
        try:
            monitor.unlink(missing_ok=True)
            variables = args.out / (name + '-vars.fd')
            shutil.copy('/usr/share/edk2/x64/OVMF_VARS.4m.fd', variables)
            with (args.out / (name + '.log')).open('w') as log:
                proc = subprocess.Popen(['qemu-system-x86_64', '-accel', 'tcg', '-machine', 'q35',
                                         '-m', '256', '-nodefaults', '-device', f'virtio-gpu-pci,xres={width},yres={height}',
                                         '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd',
                                         '-drive', f'if=pflash,format=raw,file={variables}',
                                         '-drive', f'format=raw,file=fat:rw:{fat}', '-display', 'none',
                                         '-monitor', f'unix:{monitor},server=on,wait=off', '-net', 'none'],
                                        stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, TMPDIR=str(args.out.resolve())))
                deadline = time.monotonic() + 45
                while not monitor.exists():
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('QEMU monitor did not start')
                    time.sleep(.1)
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.connect(str(monitor))
                    connection.settimeout(5)
                    connection.recv(4096)
                    time.sleep(15)
                    path = args.out / (name + '.ppm')
                    connection.sendall(f'screendump {path}\n'.encode())
                    time.sleep(1)
                    response = connection.recv(16384).decode(errors='replace')
                    if not path.exists():
                        raise RuntimeError(response)
                    from PIL import Image
                    with Image.open(path) as frame:
                        frame.save(args.out / (name + '.png'))
                        evidence['frames'].append(dict(file=name + '.png', firmware='uefi',
                                                       advertised_size=[width, height],
                                                       observed_mode=f'{frame.width}x{frame.height}',
                                                       width=frame.width, height=frame.height))
                        (args.out / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
                        require_fixture_mode(frame.size, (width, height))
                        print(f'COMPONENT SHOT: synthetic GRUB, {name} advertised output: captured {frame.size} (not judged)')
                    connection.sendall(b'quit\n')
                    proc.wait(timeout=5)
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            socket_runtime.cleanup(args.out)
    print('NOT TESTED: this synthetic GRUB fixture does not prove the actual ISO menu or readability.')
    return 3


if __name__ == '__main__':
    sys.exit(main())
