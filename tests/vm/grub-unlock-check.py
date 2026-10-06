#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rootless encrypted-disk prompt check using the checkout's GRUB renderer.

Needs QEMU, OVMF, cryptsetup, dosfstools, mtools, gptfdisk, GRUB 2.16,
libgcrypt, e2fsprogs, Pillow and tesseract. --argon2-iterations 4 shortens a
TCG functional run; --real-calibration (the default) uses the installer's
fixed Argon2id pass count. Use --accel kvm to measure GRUB's cost on this
machine; TCG timings are not a hardware measurement.
--grub-root may name an unpacked package's usr directory. Nothing is installed
or downloaded; all writes stay in a new --output directory.

The fixture builds an encrypted ext4 filesystem entirely in userspace and
loads /boot/grub/grub.cfg through the production cryptouuid prefix. Its menu
configuration reproduces the generated header's font and terminal startup;
there is no installed kernel or initramfs. --case missing-menu-assets omits
the target's unicode font; all menus omit a background line.

Four wrong attempts (configurable), Escape and empty input precede the right
password. A fixture-only serial mirror counts completed attempts. Queued c/e
keys check the keyboard drain. Review all PNGs too. --video-size changes the
fixture's GRUB preference and verifies the actual GOP size; it does not remove
modes from the firmware list. The missing UUID check fails while the
non-password error loop remains.
"""

import argparse
import ctypes
import ctypes.util
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer import render  # noqa: E402
from emaki_installer.arch_backend import (  # noqa: E402
    GRUB_ARGON2_PASSES,
)
from emaki_installer.grub_screen import ARTWORK, VARIANT, assets  # noqa: E402


def run(*args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stderr.strip()}')
    return result.stdout


def tool(name, usr):
    local = usr / 'bin' / name
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f'Missing prerequisite: {name}')
    return found


def append_archive(data, files, remove=()):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r') as original:
        contents = {member.name: original.extractfile(member).read() for member in original}
    contents.update(files)
    for name in remove:
        contents.pop(name, None)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, content in contents.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(content), 0o644, 0
            archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def encrypt_filesystem(source, target, key, offset):
    """Write aes-xts-plain64 sectors without a privileged device-mapper mapping."""
    library = ctypes.util.find_library('gcrypt')
    if not library:
        raise RuntimeError('Missing prerequisite: libgcrypt')
    crypto = ctypes.CDLL(library)
    pointer, size = ctypes.c_void_p, ctypes.c_size_t
    crypto.gcry_check_version.argtypes = [ctypes.c_char_p]
    crypto.gcry_check_version.restype = ctypes.c_char_p
    crypto.gcry_check_version(None)
    crypto.gcry_cipher_open.argtypes = [ctypes.POINTER(pointer), ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint]
    crypto.gcry_cipher_setkey.argtypes = [pointer, pointer, size]
    crypto.gcry_cipher_setiv.argtypes = [pointer, pointer, size]
    crypto.gcry_cipher_encrypt.argtypes = [pointer, pointer, size, pointer, size]
    crypto.gcry_cipher_close.argtypes = [pointer]
    handle = pointer()

    def checked(result):
        if result:
            raise RuntimeError(f'Offline fixture encryption failed: {result}')

    checked(crypto.gcry_cipher_open(ctypes.byref(handle), 9, 13, 0))
    try:
        checked(crypto.gcry_cipher_setkey(handle, key, len(key)))
        encrypted = ctypes.create_string_buffer(512)
        with source.open('rb') as plain, target.open('r+b') as disk:
            disk.seek(offset)
            for sector in range(source.stat().st_size // 512):
                block = plain.read(512)
                checked(crypto.gcry_cipher_setiv(handle, sector.to_bytes(16, 'little'), 16))
                checked(crypto.gcry_cipher_encrypt(handle, encrypted, 512, block, 512))
                disk.write(encrypted.raw)
    finally:
        crypto.gcry_cipher_close(handle)


def build(args, out):
    usr = args.grub_root.resolve()
    if args.video_size != '1024x768':
        render.GRUB_GFXMODE = args.video_size + ',auto'
    version = run(tool('grub-mkimage', usr), '--version').strip()
    if not re.search(r'\b2\.16\b', version):
        raise RuntimeError(f'This check requires GRUB 2.16, found: {version}')
    (out / 'grub-version.txt').write_text(version + '\n')
    disk_uuid = str(uuid.uuid4())
    grub_uuid = (str(uuid.uuid4()) if args.case == 'missing-uuid' else disk_uuid).replace('-', '')
    prefix = f'(cryptouuid/{grub_uuid})/boot/grub'
    load_cfg = f'cryptomount -u {grub_uuid}\nset prefix={prefix}\n'
    (out / 'early.cfg').write_text(render.grub_early_config(load_cfg, grub_uuid))
    shutil.copyfile(ROOT / 'installer/assets/grub/unlock-24.pf2', out / 'visible.pf2')
    font = (out / 'visible.pf2').read_bytes()
    memdisk = render.grub_unlock_memdisk(font, load_cfg, grub_uuid)
    script = render.grub_unlock_script(load_cfg, grub_uuid)
    setup = 'while true; do\n'
    if script.count(setup) != 1:
        raise RuntimeError('Expected one retry setup for the fixture serial mirror')
    # Console fallback resets terminal_output on every retry. Reattach the mirror
    # at loop entry so every cryptomount, including the successful one, is traced.
    traced = script.replace(setup, 'serial --unit=0 --speed=115200\n' + setup +
                            '  terminal_output --append serial\n', 1)
    (out / 'production-unlock.cfg').write_text(script)
    (out / 'traced-unlock.cfg').write_text(traced)
    menu = ('set timeout=-1\n'
            'if loadfont $prefix/fonts/unicode.pf2; then\n'
            '  set gfxmode=auto\n'
            'fi\nterminal_output gfxterm\n'
            'menuentry "Emaki unlock check passed" { halt; }\n')
    root_tree = out / 'root'
    (root_tree / 'boot/grub/fonts').mkdir(parents=True)
    (root_tree / 'boot/grub/grub.cfg').write_text(menu)
    if args.case != 'missing-menu-assets':
        shutil.copyfile(usr / 'share/grub/unicode.pf2', root_tree / 'boot/grub/fonts/unicode.pf2')
    # The disk config deliberately has no background line, as when the target image is missing.
    remove = ([name for name in assets(args.variant) if name.endswith('.png')]
              if args.case == 'missing-background' else
              [name for name in assets(args.variant) if name.startswith('wrong-')]
              if args.case == 'missing-retry-background' else [])
    (out / 'memdisk.tar').write_bytes(append_archive(memdisk, {
        **assets(args.variant), 'unlock.cfg': traced.encode()}, remove=remove))
    modules = list(render.GRUB_EARLY_MODULES)
    if args.case == 'console':
        modules = [module for module in modules if module not in
                   ('gfxterm', 'gfxterm_background', 'efi_gop')]
    run(tool('grub-mkimage', usr), '--directory=' + str(usr / 'lib/grub/x86_64-efi'),
        '--format=x86_64-efi', '--memdisk=' + str(out / 'memdisk.tar'),
        '--prefix=' + prefix,
        '--config=' + str(out / 'early.cfg'), '--output=' + str(out / 'BOOTX64.EFI'),
        *modules, 'halt', 'serial')
    password = out / 'password'
    keyfile = out / 'keyfile'
    volume_key = out / 'volume-key'
    volume_key.write_bytes(os.urandom(64))
    volume_key.chmod(0o600)
    password.write_bytes(b'emaki-test')
    keyfile.write_bytes(os.urandom(64))
    password.chmod(0o600)
    keyfile.chmod(0o600)
    luks = out / 'luks.img'
    with luks.open('wb') as target:
        target.truncate(330 * 1024 * 1024)
    kdf_cost = ['--pbkdf-force-iterations', str(args.argon2_iterations or GRUB_ARGON2_PASSES)]
    (out / 'fixture.json').write_text(json.dumps({
        'primary_cost_arguments': kdf_cost, 'secondary_pbkdf2_iterations': 1000,
        'primary_memory_kib': 65536, 'primary_parallel': 1,
        'installer_argon2_passes': GRUB_ARGON2_PASSES,
        'calibration': 'fixed iterations' if args.argon2_iterations else 'installer cost',
        'accel': args.accel,
        'scope': 'unlock and encrypted ext4 /boot/grub/grub.cfg; no kernel boot',
        'case': args.case, 'video_size': args.video_size, 'variant': args.variant,
        'gfxmode_preference': render.GRUB_GFXMODE,
        'wrong_attempts': args.wrong_attempts, 'serial_mirror': True}, indent=2) + '\n')
    try:
        run('cryptsetup', 'luksFormat', '--batch-mode', '--type', 'luks2',
            '--pbkdf', 'argon2id', '--pbkdf-memory', '65536', '--pbkdf-parallel', '1',
            *kdf_cost, '--uuid', disk_uuid, '--key-file', password,
            '--volume-key-file', volume_key, '--cipher', 'aes-xts-plain64', '--key-size', '512',
            '--sector-size', '512', luks)
        run('cryptsetup', 'luksAddKey', '--batch-mode', '--pbkdf', 'pbkdf2',
            '--pbkdf-force-iterations', '1000', '--key-file', password, luks, keyfile)
        (out / 'luksDump.txt').write_text(run('cryptsetup', 'luksDump', luks))
        metadata = json.loads(run('cryptsetup', 'luksDump', '--dump-json-metadata', luks))
        offset = int(metadata['segments']['0']['offset'])
        filesystem = out / 'root.ext4'
        with filesystem.open('wb') as image:
            image.truncate(luks.stat().st_size - offset)
        run('mkfs.ext4', '-F', '-q', '-E', 'nodiscard', '-d', root_tree, filesystem)
        encrypt_filesystem(filesystem, luks, volume_key.read_bytes(), offset)
        filesystem.unlink()
    finally:
        password.unlink(missing_ok=True)
        keyfile.unlink(missing_ok=True)
        volume_key.unlink(missing_ok=True)
    esp = out / 'esp.img'
    run('mkfs.fat', '-F', '32', '-n', 'EMAKI', '-C', esp, '65536')
    run(tool('mmd', usr), '-i', esp, '::/EFI', '::/EFI/BOOT')
    run(tool('mcopy', usr), '-i', esp, out / 'BOOTX64.EFI', '::/EFI/BOOT/BOOTX64.EFI')
    disk = out / 'disk.img'
    with disk.open('wb') as target:
        target.truncate(396 * 1024 * 1024)
    partitioner = tool('sgdisk', usr)
    run(partitioner, '--clear', '-n', '1:2048:+64M', '-t', '1:ef00',
        '-n', '2:0:+330M', '-t', '2:8300', disk)
    for number, source in ((1, esp), (2, luks)):
        info = run(partitioner, '-i', number, disk)
        sector = int(re.search(r'First sector: (\d+)', info).group(1))
        with disk.open('r+b') as target, source.open('rb') as part:
            target.seek(sector * 512)
            shutil.copyfileobj(part, target)
        source.unlink()
    return disk


class Monitor:
    def __init__(self, process):
        self.process = process
        self.lines = queue.Queue()
        self.serial = 0
        threading.Thread(target=self.read, daemon=True).start()
        self.command('qmp_capabilities')

    def read(self):
        for line in self.process.stdout:
            self.lines.put(json.loads(line))
        self.lines.put({'error': {'desc': 'QEMU exited'}})

    def command(self, execute, arguments=None):
        self.serial += 1
        self.process.stdin.write(json.dumps({'execute': execute, 'arguments': arguments or {},
                                            'id': self.serial}) + '\n')
        self.process.stdin.flush()
        while True:
            message = self.lines.get(timeout=30)
            if 'error' in message:
                raise RuntimeError(message['error'])
            if message.get('id') == self.serial:
                return message.get('return')

    def key(self, name):
        self.command('human-monitor-command', {'command-line': f'sendkey {name} 50'})
        time.sleep(0.09)

    def password(self, value, before_enter=None):
        for char in value:
            self.key('minus' if char == '-' else char)
        if before_enter:
            before_enter()
        self.key('ret')

    def capture(self, path, ocr=True):
        from PIL import Image
        ppm = path.with_suffix('.ppm')
        self.command('screendump', {'filename': str(ppm)})
        with Image.open(ppm) as frame:
            frame.save(path)
        ppm.unlink()
        if not ocr:
            return ''
        words = run('tesseract', path, 'stdout', '--psm', '6')
        path.with_suffix('.txt').write_text(words)
        return ' '.join(words.lower().replace('_', ' ').replace('passuord', 'password').split())

    def wait_initial(self, out, timeout, allow_errors=False):
        end, previous, number = time.monotonic() + timeout, None, 0
        while time.monotonic() < end:
            path = out / 'boot-current.png'
            self.capture(path, ocr=False)
            current = path.read_bytes()
            if current != previous:
                (out / f'boot-{number:04d}.png').write_bytes(current)
                previous, number = current, number + 1
            trace = (out / 'serial.log').read_text(errors='replace')
            if 'Enter passphrase for ' in trace:
                if 'error:' in trace.split('Enter passphrase for ', 1)[0] and not allow_errors:
                    raise RuntimeError('GRUB error before first password prompt; inspect boot frames')
                return self.capture(out / '01-unlock.png')
            time.sleep(0.05)
        raise RuntimeError('Timed out waiting for initial prompt; inspect boot frames')

    def wait_text(self, out, label, wanted, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            words = self.capture(out / (label + '.png'))
            if wanted in words:
                return words
            time.sleep(2)
        raise RuntimeError(f'Timed out waiting for {label}; inspect its PNG and qemu.log')


class UnlockClock:
    """Measure serial slot-open detection independently of screenshot/OCR work."""
    def __init__(self, out):
        self.trace = out / 'serial.log'
        self.stopped = threading.Event()
        self.thread = None
        self.entered_at = None
        self.opened_at = None

    def start(self):
        self.entered_at = time.monotonic()
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()

    def watch(self):
        while not self.stopped.is_set():
            if 'Slot "0" opened' in self.trace.read_text(errors='replace'):
                self.opened_at = time.monotonic()
                return
            self.stopped.wait(0.01)

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join()

    def seconds(self):
        if self.opened_at is None:
            raise RuntimeError('No timestamp for successful slot unlock')
        return self.opened_at - self.entered_at


def wait_prompt(out, count, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        trace = out / 'serial.log'
        text = trace.read_text(errors='replace') if trace.exists() else ''
        if text.count('Enter passphrase for ') >= count:
            return
        time.sleep(1)
    raise RuntimeError(f'Timed out waiting for password prompt {count}; inspect serial.log')


def check_background(out, label, state, variant):
    from PIL import Image, ImageChops
    with Image.open(out / (label + '.png')) as actual:
        name = f'{state}-{actual.width}x{actual.height}.png'
        with tarfile.open(out / 'memdisk.tar') as archive:
            committed = ARTWORK / variant / name
            if archive.extractfile(name).read() != committed.read_bytes():
                raise RuntimeError(f'{label} does not carry variant {variant} artwork')
            with Image.open(committed) as expected:
                if ImageChops.difference(actual.convert('RGB'), expected.convert('RGB')).getbbox():
                    raise RuntimeError(f'{label} differs from the exact unlock artwork')


def check_words(words):
    for forbidden in ('enter passphrase', 'attempting to decrypt', 'invalid passphrase',
                      'error:', 'grub rescue', 'hd0,'):
        if forbidden in words:
            raise RuntimeError(f'Unexpected visible GRUB text: {forbidden}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True, type=Path, help='new directory for all mutable files')
    parser.add_argument('--grub-root', type=Path, default=Path('/usr'))
    parser.add_argument('--variant', choices=('A', 'B', 'C'), default=VARIANT)
    parser.add_argument('--case', choices=('normal', 'console', 'missing-background',
                                         'missing-uuid', 'missing-menu-assets',
                                         'missing-retry-background'), default='normal')
    parser.add_argument('--video-size', choices=('1024x768', '800x600', '640x480'),
                        default='1024x768',
                        help='fixture GRUB preference; actual GOP size is verified from the screenshot')
    parser.add_argument('--wrong-attempts', type=int, default=4)
    parser.add_argument('--ovmf-code', type=Path, default=Path('/usr/share/edk2/x64/OVMF_CODE.4m.fd'))
    parser.add_argument('--ovmf-vars', type=Path, default=Path('/usr/share/edk2/x64/OVMF_VARS.4m.fd'))
    calibration = parser.add_mutually_exclusive_group()
    calibration.add_argument('--argon2-iterations', type=int, default=0,
                             help='fixed primary Argon2 time cost for TCG; 0 uses the installer cost')
    calibration.add_argument('--real-calibration', action='store_true',
                             help="use the installer's pass count (default); measure with KVM")
    parser.add_argument('--accel', choices=('tcg', 'kvm'), default='tcg')
    parser.add_argument('--timeout', type=float, default=600, help='seconds to wait for initial/error/menu text')
    args = parser.parse_args()
    if args.argon2_iterations < 0 or 0 < args.argon2_iterations < 4:
        parser.error('Argon2 iterations must be 0 (installer cost) or at least 4')
    if args.wrong_attempts < 0:
        parser.error('wrong attempts must be non-negative')
    if args.timeout <= 0:
        parser.error('timeouts must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    disk = build(args, out)
    shutil.copyfile(args.ovmf_vars, out / 'vars.fd')
    width, height = args.video_size.split('x')
    command = ['qemu-system-x86_64', '-machine', 'q35', '-accel', args.accel,
               '-m', '1024', '-display', 'none', '-serial', f'file:{out / "serial.log"}', '-qmp', 'stdio',
               '-no-reboot', '-net', 'none', '-vga', 'none',
               '-device', f'VGA,xres={width},yres={height}',
               '-drive', f'if=pflash,format=raw,readonly=on,file={args.ovmf_code.resolve()}',
               '-drive', f'if=pflash,format=raw,file={out / "vars.fd"}',
               '-drive', f'format=raw,file={disk}']
    (out / 'qemu-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'qemu.log').open('w') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, text=True)
        unlock_clock = UnlockClock(out)
        try:
            monitor = Monitor(process)
            if args.case == 'missing-uuid':
                time.sleep(10)
                words = monitor.capture(out / '01-missing-uuid.png')
                trace = (out / 'serial.log').read_text(errors='replace')
                attempts = trace.count('no such cryptodisk')
                if attempts > 1 or 'wrong password' in words:
                    raise RuntimeError('Missing UUID repeats as wrong password instead of stopping')
                if attempts != 1 or not words or 'Enter passphrase for ' in trace:
                    raise RuntimeError('Missing UUID lacks a visible non-password failure')
                (out / 'RESULT.txt').write_text('PASS: missing UUID stopped with visible text.\n')
                print(f'PASS: evidence in {out}', flush=True)
                return
            words = monitor.wait_initial(out, args.timeout,
                                         allow_errors=args.case in ('console', 'missing-background'))
            graphical = args.case not in ('console', 'missing-background')

            def check_screen(label, state, words):
                if graphical and not (args.case == 'missing-retry-background' and state == 'wrong'):
                    check_words(words)
                    check_background(out, label, state, args.variant)
                else:
                    for wanted in ("disk is encrypted", "type your disk password",
                                   "letters do not appear", "after enter, wait a few seconds"):
                        if wanted not in words:
                            raise RuntimeError(f'Console fallback is missing: {wanted}')
                    if any(text in words for text in ('error:', 'invalid passphrase', 'no video mode')):
                        raise RuntimeError('Console fallback retains errors from the previous attempt')
                    if 'enter passphrase' not in words:
                        raise RuntimeError('Console fallback has no visible native password prompt')

            if graphical:
                from PIL import Image
                with Image.open(out / '01-unlock.png') as initial:
                    if initial.size != (int(width), int(height)):
                        raise RuntimeError('Actual GOP mode differs from the requested fixture size')
            check_screen('01-unlock', 'unlock', words)
            prompt_factor = (out / 'serial.log').read_text(errors='replace').count('Enter passphrase for ')
            if prompt_factor not in (1, 2):
                raise RuntimeError('Unexpected initial serial prompt count')
            wait_prompt(out, prompt_factor, args.timeout)
            # OVMF mirrors console output to serial as well. A failed retry image
            # switches from gfxterm (one copy) to console (two copies).
            retry_factor = 2 if args.case == 'missing-retry-background' else prompt_factor
            for attempt in range(1, args.wrong_attempts + 1):
                monitor.password('wrong-password')
                print(f'Waiting for wrong attempt {attempt} to finish', flush=True)
                wait_prompt(out, prompt_factor + attempt * retry_factor, args.timeout)
                words = monitor.capture(out / f'02-wrong-{attempt}.png')
                check_screen(f'02-wrong-{attempt}', 'wrong', words)
                if 'password. try again' not in words:
                    raise RuntimeError('Error screen missing after wrong submission')
            for count, (label, key) in enumerate((('03-escape', 'esc'), ('04-empty', 'ret')),
                                               args.wrong_attempts + 2):
                monitor.key(key)
                wait_prompt(out, prompt_factor + (count - 1) * retry_factor, args.timeout)
                time.sleep(1)
                words = monitor.capture(out / f'{label}.png')
                check_screen(label, 'wrong', words)
                if 'password. try again' not in words:
                    raise RuntimeError(f'Error screen missing after {label}')
            monitor.password('emaki-test', before_enter=unlock_clock.start)
            # Deliberately queue menu shortcuts during Argon2; cleanup must discard them.
            time.sleep(0.5)
            checking_words = monitor.capture(out / '05-during-check.png')
            checking_state = 'PRESENT' if 'checking the password' in checking_words else 'ABSENT'
            (out / 'checking-state.txt').write_text(checking_state + '\n')
            monitor.key('c')
            monitor.key('e')
            menu_words = monitor.wait_text(out, '06-menu', 'unlock check passed', args.timeout)
            if 'wrong password' in menu_words or 'disk is encrypted' in menu_words:
                raise RuntimeError('Unlock artwork remains behind the installed disk menu')
            unlock_clock.stop()
            unlock_seconds = unlock_clock.seconds()
            timing = {
                'enter_to_unlock_seconds': unlock_seconds,
                'start': 'host monotonic clock immediately before sending Enter through QMP',
                'end': 'first serial observation of Slot 0 opened',
                'poll_interval_seconds': 0.01,
                'accel': args.accel,
                'calibration': 'fixed iterations' if args.argon2_iterations else 'installer cost',
            }
            (out / 'unlock-timing.json').write_text(json.dumps(timing, indent=2) + '\n')
            print(f'Enter to unlock: {unlock_seconds:.3f} s ({timing["calibration"]}, {args.accel})',
                  flush=True)
            trace = (out / 'serial.log').read_text(errors='replace')
            if 'no video mode activated' in trace:
                raise RuntimeError('Console handoff attempted a background image without graphics')
            expected_prompts = prompt_factor + (args.wrong_attempts + 2) * retry_factor
            expected_errors = (prompt_factor + (args.wrong_attempts - 1) * retry_factor
                               if args.wrong_attempts else 0)
            if (trace.count('Enter passphrase for ') != expected_prompts or
                    trace.count('Invalid passphrase.') != expected_errors or
                    'Slot "0" opened' not in trace or 'grub rescue>' in trace):
                raise RuntimeError('Serial attempt counts or successful slot do not match the scenario')
            (out / 'RESULT.txt').write_text(
                'PASS: initial screen, retries and encrypted ext4 disk menu detected; inspect PNGs.\n'
                f'{args.wrong_attempts} wrong attempts, Escape and empty input before correct password.\n'
                'Queued c/e keys during checking were discarded before the menu.\n'
                f'Checking state: {checking_state}; see 05-during-check.png.\n'
                f'Enter to unlock: {unlock_seconds:.3f} s; see unlock-timing.json.\n'
                'Each next prompt was counted through a fixture-only serial output mirror.\n'
                'Inspect production-unlock.cfg and traced-unlock.cfg for the instrumentation.\n'
                'The disk grub.cfg reproduces menu startup; no installed kernel/initramfs is tested.\n')
            print(f'PASS: evidence in {out}', flush=True)
        finally:
            unlock_clock.stop()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
