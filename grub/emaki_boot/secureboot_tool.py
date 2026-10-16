# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""emaki-secureboot: the machine side of Secure Boot.

  sign [PATH...]   (root) sign both kernels and fwupd's loader with the machine key; with
                   paths, sign only those kernels in /boot (the mkinitcpio post hook)
  check            (root) prove the key and sbsigntools can sign before pacman replaces a
                   kernel (PreTransaction hook with AbortOnFail)
  status [--json] [--write]
                   {secure_boot, key, enrollment, signed}; root computes it, everyone else
                   reads the copy root wrote to /run at boot
  enroll [--json]  (root, through pkexec) queue a new confirmation of the key for MokManager
                   and print its one-time password

Without the machine key every command leaves the machine exactly as it is: the key is what
turns signing on (secureboot.enabled), never the firmware switch.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from . import secureboot

# Written by root (the boot unit, sign and enroll), readable by the session: the key itself
# stays in the root-only state directory, so the session never computes this on its own.
STATUS = 'run/emaki-secureboot.json'
BOOT = 'boot'
KERNEL_PREFIX = 'vmlinuz-'
SIGNING_SUFFIX = '.emaki-signing'
# A machine set up for signing whose private key is gone: its firmware boots through shim, so
# going on as if it had never been set up would leave unsigned kernels it refuses.
KEY_MISSING = ('The Secure Boot machine key is missing from /var/lib/emaki/secureboot, '
               'but this computer was set up to sign its boot files.')


class CommandError(RuntimeError):
    def __init__(self, argv, output):
        super().__init__(f'{argv[0]} failed: {output.strip()[-300:]}')
        self.output = output


def run(argv, input=None):
    """The runner secureboot.py expects: stdout, or CommandError with the program's output."""
    try:
        result = subprocess.run(list(argv), input=input, text=True, capture_output=True)
    except FileNotFoundError as error:
        raise CommandError(argv, f'{argv[0]} is not installed') from error
    if result.returncode:
        raise CommandError(argv, result.stdout + result.stderr)
    return result.stdout


class Machine:
    """Every path the tool touches, below one root (/ on a machine, a scratch tree in tests)."""

    def __init__(self, root=Path('/')):
        self.root = Path(root)
        self.state = self.root / secureboot.STATE.relative_to('/')
        self.boot = self.root / BOOT
        self.kernels = tuple(self.root / Path(kernel).relative_to('/') for kernel in secureboot.KERNELS)
        self.fwupd_efi = self.root / secureboot.FWUPD_EFI.relative_to('/')
        self.fwupd_signed = self.root / secureboot.FWUPD_SIGNED.relative_to('/')
        self.status = self.root / STATUS

    def shown(self, path):
        """A path as the machine names it, without the scratch root."""
        return '/' + str(Path(path).relative_to(self.root))

    def has_key(self):
        try:
            return secureboot.enabled(self.root)
        except OSError:
            return False

    def key_missing(self):
        try:
            return secureboot.damaged(self.root)
        except OSError:
            return True


def replace_signed(runner, machine, source, target):
    """Sign source into target through a temporary file next to it, then verify target."""
    temporary = target.with_name(target.name + SIGNING_SUFFIX)
    temporary.unlink(missing_ok=True)
    try:
        secureboot.sign(runner, source, temporary, machine.state)
        try:
            os.chmod(temporary, source.stat().st_mode & 0o7777)
        except PermissionError:
            pass  # a FAT /boot has no modes to keep
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if not secureboot.signed(runner, target, machine.state):
        raise secureboot.SecureBootError('it does not verify with the machine key after signing')


def explicit_kernel(machine, value):
    """The kernel a hook named, if it is one of /boot's vmlinuz-* files; None to skip it."""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
        boot = machine.boot.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent != boot or not resolved.name.startswith(KERNEL_PREFIX) or not resolved.is_file():
        return None
    return resolved


def sign(runner, machine, paths=(), say=print):
    """Sign what needs signing; the list of failures, each naming its file."""
    if machine.key_missing():
        return [KEY_MISSING]
    if not machine.has_key():
        return []
    failures = []
    if paths:
        kernels = []
        for value in paths:
            kernel = explicit_kernel(machine, value)
            if kernel is None:
                say(f'Not signing {value}: only kernels in /boot are signed.')
            elif kernel not in kernels:
                kernels.append(kernel)
    else:
        kernels = [kernel for kernel in machine.kernels if kernel.is_file()]
    for kernel in kernels:
        name = machine.shown(kernel)
        try:
            if secureboot.signed(runner, kernel, machine.state):
                continue
            replace_signed(runner, machine, kernel, kernel)
            say(f'Signed {name} with the Secure Boot machine key.')
        except (OSError, RuntimeError) as error:
            failures.append(f'Could not sign {name}: {error}')
    # fwupd starts its capsule loader through shim from the .signed copy. The copy is made
    # again on every full run: the loader is small, and an older copy would still verify.
    if not paths and machine.fwupd_efi.is_file():
        name = machine.shown(machine.fwupd_signed)
        try:
            replace_signed(runner, machine, machine.fwupd_efi, machine.fwupd_signed)
            say(f'Signed {name} with the Secure Boot machine key.')
        except (OSError, RuntimeError) as error:
            failures.append(f'Could not sign {name}: {error}')
    return failures


def minimal_efi():
    """A tiny valid PE32+ EFI application (one .text section returning EFI_SUCCESS)."""
    header = bytearray(0x400)
    header[0:2] = b'MZ'
    header[0x3c:0x40] = (0x80).to_bytes(4, 'little')
    header[0x80:0x84] = b'PE\0\0'
    coff = 0x84
    header[coff:coff + 20] = (0x8664).to_bytes(2, 'little') + (1).to_bytes(2, 'little') + bytes(12) + \
        (240).to_bytes(2, 'little') + (0x22).to_bytes(2, 'little')
    fields = bytearray(240)
    for offset, size, value in ((0, 2, 0x20b), (4, 4, 0x200), (16, 4, 0x1000), (20, 4, 0x1000),
                                (24, 8, 0x10000000), (32, 4, 0x1000), (36, 4, 0x200), (56, 4, 0x2000),
                                (60, 4, 0x400), (68, 2, 10), (108, 4, 16)):
        fields[offset:offset + size] = value.to_bytes(size, 'little')
    header[coff + 20:coff + 260] = fields
    section = coff + 260
    header[section:section + 40] = b'.text\0\0\0' + (0x200).to_bytes(4, 'little') + \
        (0x1000).to_bytes(4, 'little') + (0x200).to_bytes(4, 'little') + (0x400).to_bytes(4, 'little') + \
        bytes(12) + (0x60000020).to_bytes(4, 'little')
    return bytes(header) + b'\x31\xc0\xc3'.ljust(0x200, b'\0')


def check(runner, machine):
    """Problems that would make the next signing fail; signs a scratch image to find them."""
    if machine.key_missing():
        return [KEY_MISSING]
    if not machine.has_key():
        return []
    with tempfile.TemporaryDirectory(prefix='emaki-secureboot-') as scratch:
        image = Path(scratch) / 'probe.efi'
        image.write_bytes(minimal_efi())
        try:
            secureboot.sign(runner, image, Path(scratch) / 'probe.signed.efi', machine.state)
        except (OSError, RuntimeError) as error:
            return [f'The Secure Boot machine key cannot sign boot files: {error}']
    return []


def secure_boot(root):
    """True or False from the firmware's SecureBoot variable; None without UEFI variables."""
    if not (Path(root) / 'sys/firmware/efi/efivars').is_dir():
        return None
    try:
        data = secureboot.efi_variable(root, 'SecureBoot', secureboot.GLOBAL_GUID)
    except (OSError, secureboot.SecureBootError):
        return None
    if data is None:
        return False
    return len(data) >= 1 and data[0] == 1


def compute_status(runner, machine):
    key = machine.has_key()
    status = {'schema_version': 1, 'secure_boot': secure_boot(machine.root), 'key': key,
              'enrollment': None, 'signed': {}}
    if not key:
        return status
    try:
        status['enrollment'] = secureboot.enrollment(runner, machine.state)
    except (OSError, RuntimeError):
        pass
    for kernel in machine.kernels:
        if kernel.is_file():
            status['signed'][machine.shown(kernel)] = secureboot.signed(runner, kernel, machine.state)
    if machine.fwupd_efi.is_file():
        status['signed'][machine.shown(machine.fwupd_signed)] = (
            machine.fwupd_signed.is_file() and secureboot.signed(runner, machine.fwupd_signed, machine.state))
    return status


def write_status(machine, status):
    machine.status.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.emaki-secureboot-', dir=machine.status.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(status, stream, sort_keys=True)
            stream.write('\n')
        os.chmod(temporary, 0o644)
        os.replace(temporary, machine.status)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_status(machine):
    """The status root wrote; unknown key and enrollment when it has not been written yet."""
    unknown = {'schema_version': 1, 'secure_boot': secure_boot(machine.root), 'key': None,
               'enrollment': None, 'signed': {}}
    try:
        status = json.loads(machine.status.read_text())
    except (OSError, ValueError):
        return unknown
    valid = (isinstance(status, dict) and status.get('schema_version') == 1
             and status.get('secure_boot') in (True, False, None)
             and status.get('key') in (True, False)
             and status.get('enrollment') in ('enrolled', 'pending', 'missing', None)
             and isinstance(status.get('signed'), dict)
             and all(isinstance(name, str) and value in (True, False)
                     for name, value in status['signed'].items()))
    return status if valid else unknown


def refresh_status(runner, machine):
    """Rewrite the session's copy after a change; a failure here never fails the change."""
    try:
        write_status(machine, compute_status(runner, machine))
    except OSError:
        pass


def enroll(runner, machine):
    """(exit code, answer): a new MokManager request with a new one-time password."""
    if not machine.has_key():
        return 1, {'schema_version': 1, 'status': 'error', 'message': 'This computer has no Secure Boot key.'}
    try:
        state = secureboot.enrollment(runner, machine.state)
        if state == 'enrolled':
            return 1, {'schema_version': 1, 'status': 'error', 'enrollment': state,
                       'message': 'The Secure Boot key is already confirmed.'}
        if state == 'pending':
            # The earlier password may be lost; mokutil refuses a second request for the same
            # key, so the old one goes first (MokNew and MokAuth, no password asked).
            runner(['mokutil', '--revoke-import'])
        password = secureboot.one_time_password()
        secureboot.request_enrollment(runner, password, machine.state)
        if secureboot.enrollment(runner, machine.state) != 'pending':
            raise secureboot.SecureBootError('mokutil did not keep the request')
    except (OSError, RuntimeError) as error:
        refresh_status(runner, machine)
        return 1, {'schema_version': 1, 'status': 'error',
                   'message': f'The new confirmation could not be queued: {error}'}
    refresh_status(runner, machine)
    return 0, {'schema_version': 1, 'status': 'queued', 'enrollment': 'pending', 'password': password}


def not_signed(problems, has_key, startup=True):
    """The block a failed signing prints. libalpm ignores a PostTransaction hook's exit status,
    so the transaction ends as usual and this text is all the person sees in it. startup is
    False when only fwupd's loader failed: that stops firmware updates, not Emaki."""
    rule = 'emaki-secureboot: ' + '=' * 62
    advice = ('Free some disk space or fix the problem named above, then open',
              'Settings > Privacy > Secure Boot and choose "Sign again",',
              'or run: sudo emaki-secureboot sign') if has_key and startup else (
              'Fix the problem named above, then run: sudo emaki-secureboot sign',)
    lines = ('SOME STARTUP FILES ARE NOT SIGNED.' if startup else 'THE FIRMWARE UPDATE LOADER IS NOT SIGNED.',
             *problems,
             'Emaki will not start with Secure Boot on until this is fixed.' if startup
             else 'Firmware updates will not start with Secure Boot on until this is fixed.', *advice)
    return '\n'.join([rule, *(f'emaki-secureboot: {line}' for line in lines), rule])


def main(argv=None, machine=None, runner=run, euid=None):
    parser = argparse.ArgumentParser(prog='emaki-secureboot', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    signing = commands.add_parser('sign')
    signing.add_argument('paths', nargs='*')
    commands.add_parser('check')
    status = commands.add_parser('status')
    status.add_argument('--json', action='store_true')
    status.add_argument('--write', action='store_true')
    enrolling = commands.add_parser('enroll')
    enrolling.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    machine = machine or Machine()
    root = (os.geteuid() if euid is None else euid) == 0

    if args.command == 'status':
        if args.write and not root:
            print('emaki-secureboot: status --write must run as root.', file=sys.stderr)
            return 2
        answer = compute_status(runner, machine) if root else read_status(machine)
        if args.write:
            write_status(machine, answer)
        print(json.dumps(answer, sort_keys=True))
        return 0
    if not root:
        print(f'emaki-secureboot: {args.command} must run as root.', file=sys.stderr)
        return 2
    if args.command == 'enroll':
        code, answer = enroll(runner, machine)
        print(json.dumps(answer, sort_keys=True))
        return code
    if args.command == 'check':
        problems = check(runner, machine)
        for problem in problems:
            print(f'emaki-secureboot: {problem}', file=sys.stderr)
        if problems:
            print('emaki-secureboot: the package changes were stopped before any boot file changed. '
                  'Install sbsigntools or repair the key, then try again.', file=sys.stderr)
        return 1 if problems else 0
    problems = sign(runner, machine, args.paths)
    if problems:
        loader = f'Could not sign {machine.shown(machine.fwupd_signed)}:'
        startup = any(not problem.startswith(loader) for problem in problems)
        print(not_signed(problems, machine.has_key(), startup), file=sys.stderr, flush=True)
    # The session's copy lives in /run (tmpfs): it is written even when a full disk stopped the
    # signing, so the next login can tell which files are not signed.
    if machine.has_key():
        refresh_status(runner, machine)
    return 1 if problems else 0
