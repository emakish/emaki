#!/usr/bin/env python3
"""Capture the live GRUB menu in disposable TCG guests; never boots a target disk.

Pass --grub-root with an unpacked Arch grub package, and --out under a temporary
or workspace directory. OVMF and QEMU must already be available on the host.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
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
subprocess.run([str(args.grub_root / 'usr/bin/grub-mkstandalone'), '-O', 'x86_64-efi',
                '-d', str(args.grub_root / 'usr/lib/grub/x86_64-efi'), '--fonts=', '--themes=',
                '--locales=', '--disable-shim-lock', '-o', str(fat / 'EFI/BOOT/BOOTX64.EFI'),
                'boot/grub/grub.cfg=' + str(fat / 'boot/grub/grub.cfg'),
                'boot/grub/background.png=' + str(fat / 'boot/grub/background.png'),
                'boot/grub/fonts/unicode.pf2=' + str(args.grub_root / 'usr/share/grub/unicode.pf2')],
               check=True)
for width, height in ((1366, 768), (3840, 2160)):
    name = f'{width}x{height}'
    monitor = args.out / (name + '.sock')
    monitor.unlink(missing_ok=True)
    variables = args.out / (name + '-vars.fd')
    shutil.copy('/usr/share/edk2/x64/OVMF_VARS.4m.fd', variables)
    with (args.out / (name + '.log')).open('w') as log:
        proc = subprocess.Popen(['qemu-system-x86_64', '-accel', 'tcg', '-machine', 'q35',
                                 '-m', '256', '-nodefaults', '-device', f'virtio-vga,xres={width},yres={height}',
                                 '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd',
                                 '-drive', f'if=pflash,format=raw,file={variables}',
                                 '-drive', f'format=raw,file=fat:rw:{fat}', '-display', 'none',
                                 '-monitor', f'unix:{monitor},server=on,wait=off', '-net', 'none'],
                                stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, TMPDIR=str(args.out.resolve())))
        try:
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
                    if frame.size != (1024, 768):
                        raise RuntimeError(f'GRUB did not select the expected moderate mode: {frame.size}')
                    print(f'{name} advertised output: captured {frame.size}')
                connection.sendall(b'quit\n')
                proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
