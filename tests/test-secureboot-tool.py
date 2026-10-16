#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""emaki-secureboot: signing, the pacman and mkinitcpio hooks, status and the new confirmation.

Runs on a scratch root with fake sbsigntools and mokutil; the real sbsigntools part runs when
they are on PATH or EMAKI_SBSIGNTOOLS names an extracted package root, and is skipped otherwise.
"""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import secureboot
sys.modules['emaki_boot.secureboot'] = secureboot
from emaki_boot import secureboot_tool as tool

MARK = b'\nFAKE-SIGNATURE:'
KERNEL = b'MZ kernel image'
LTS = b'MZ lts kernel image'
FWUPD = b'MZ fwupd capsule loader'


class FakeTools:
    """sbsign appends a mark naming the certificate, sbverify looks for it; mokutil keeps state."""

    def __init__(self, enrollment='missing', fail_sign=(), reject=(), full_disk=()):
        self.calls = []
        self.enrollment = enrollment
        self.fail_sign = fail_sign
        self.reject = reject
        # Output names whose write runs out of space halfway, as on a full /boot.
        self.full_disk = full_disk

    def __call__(self, argv, input=None):
        argv = list(argv)
        self.calls.append((argv, input))
        if argv[0] == 'sbsign':
            cert = Path(argv[argv.index('--cert') + 1]).read_bytes()
            output, source = Path(argv[argv.index('--output') + 1]), Path(argv[-1])
            if source.name in self.fail_sign:
                raise tool.CommandError(argv, 'sbsign: cannot read the key')
            if output.name in self.full_disk:
                output.write_bytes(source.read_bytes()[:4])
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(output))
            output.write_bytes(source.read_bytes() + MARK + cert)
            return ''
        if argv[0] == 'sbverify':
            cert = Path(argv[2]).read_bytes()
            path = Path(argv[3])
            if any(path.name.startswith(name) for name in self.reject) or \
                    not path.read_bytes().endswith(MARK + cert):
                raise tool.CommandError(argv, 'Signature verification failed')
            return 'Signature verification OK\n'
        if argv[0] == 'mokutil':
            if '--test-key' in argv:
                if self.enrollment is None:
                    raise tool.CommandError(argv, 'EFI variables are not supported on this system')
                return {'enrolled': f'{argv[-1]} is already enrolled\n',
                        'pending': f'{argv[-1]} is already in the enrollment request\n',
                        'missing': f'{argv[-1]} is not enrolled\n'}[self.enrollment]
            if argv[1:] == ['--revoke-import']:
                self.enrollment = 'missing'
                return ''
            if argv[1] == '--import':
                password = input.split('\n')[0]
                assert input == f'{password}\n{password}\n', input
                self.enrollment = 'pending'
                return ''
        raise AssertionError(f'unexpected command {argv}')

    def programs(self):
        return [argv[0] for argv, _ in self.calls]


def snapshot(root):
    """Every file below root with its bytes and modification time."""
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


class Scratch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='secureboot-tool-')
        self.root = Path(self.temporary.name)
        self.machine = tool.Machine(self.root)
        (self.root / 'boot').mkdir()
        (self.root / 'boot/vmlinuz-linux').write_bytes(KERNEL)
        (self.root / 'boot/vmlinuz-linux-lts').write_bytes(LTS)
        (self.root / 'boot/initramfs-linux.img').write_bytes(b'initramfs')
        self.machine.fwupd_efi.parent.mkdir(parents=True)
        self.machine.fwupd_efi.write_bytes(FWUPD)
        modules = self.root / 'usr/lib/modules/7.2.7-arch1-1'
        (modules / 'updates/dkms').mkdir(parents=True)
        (modules / 'vmlinuz').write_bytes(KERNEL)
        (modules / 'updates/dkms/nvidia.ko.zst').write_bytes(b'module signed by dkms')
        dkms = self.root / secureboot.DKMS_CONFIG.relative_to('/')
        dkms.parent.mkdir(parents=True)
        dkms.write_text(secureboot.dkms_config())
        efivars = self.root / 'sys/firmware/efi/efivars'
        efivars.mkdir(parents=True)
        self.secure_boot(False)

    def tearDown(self):
        self.temporary.cleanup()

    def secure_boot(self, on):
        path = self.root / 'sys/firmware/efi/efivars' / f'SecureBoot-{secureboot.GLOBAL_GUID}'
        path.write_bytes(b'\x06\0\0\0' + bytes([1 if on else 0]))

    def add_key(self):
        state = self.machine.state
        state.mkdir(parents=True, mode=0o700)
        (state / 'machine.key').write_text('PRIVATE KEY')
        (state / 'machine.crt').write_bytes(b'CERTIFICATE one')
        (state / 'machine.cer').write_bytes(b'DER one')

    def main(self, argv, tools, euid=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tool.main(argv, machine=self.machine, runner=tools, euid=euid)
        return code, out.getvalue(), err.getvalue()


class WithoutKeyTests(Scratch):
    """A machine installed without Secure Boot has no key: nothing at all changes."""

    def test_sign_check_and_status_touch_nothing(self):
        before = snapshot(self.root)
        tools = FakeTools()
        self.assertEqual(self.main(['sign'], tools)[0], 0)
        self.assertEqual(self.main(['sign', str(self.root / 'boot/vmlinuz-linux')], tools)[0], 0)
        self.assertEqual(self.main(['check'], tools)[0], 0)
        code, out, _ = self.main(['status', '--json'], tools)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'schema_version': 1, 'secure_boot': False, 'key': False,
                                           'enrollment': None, 'signed': {}})
        self.assertEqual(tools.calls, [])
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.machine.status.exists())

    def test_enroll_refuses_without_key(self):
        tools = FakeTools()
        code, out, _ = self.main(['enroll', '--json'], tools)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['status'], 'error')
        self.assertEqual(tools.calls, [])

    def test_root_only_commands(self):
        for command in (['sign'], ['check'], ['enroll'], ['status', '--write']):
            self.assertEqual(self.main(command, FakeTools(), euid=1000)[0], 2, command)


class LostKeyTests(Scratch):
    """Set up for signing but the private key is gone: refuse, never pass as unconfigured."""

    def test_check_and_sign_refuse_and_change_nothing(self):
        for leftover in ('state', 'esp', 'slot'):
            with self.subTest(leftover=leftover):
                if leftover == 'state':
                    self.add_key()
                    (self.machine.state / 'machine.key').unlink()
                else:
                    shutil.rmtree(self.machine.state, ignore_errors=True)
                    (self.root / 'efi/EFI/Emaki/emaki-machine-key.cer').unlink(missing_ok=True)
                    cert = self.root / ('efi/EFI/Emaki/emaki-machine-key.cer' if leftover == 'esp'
                                        else 'efi/EFI/Emaki/b/emaki-machine-key.cer')
                    cert.parent.mkdir(parents=True)
                    cert.write_bytes(b'DER one')
                before = snapshot(self.root)
                tools = FakeTools()
                for command in (['check'], ['sign'], ['sign', str(self.root / 'boot/vmlinuz-linux')]):
                    code, _, err = self.main(command, tools)
                    self.assertEqual(code, 1, command)
                    self.assertIn('machine key is missing', err)
                self.assertEqual(tools.calls, [])
                self.assertEqual(snapshot(self.root), before)


class SignTests(Scratch):
    def setUp(self):
        super().setUp()
        self.add_key()

    def test_signs_both_kernels_and_fwupd(self):
        tools = FakeTools()
        code, out, err = self.main(['sign'], tools)
        self.assertEqual((code, err), (0, ''))
        cert = b'CERTIFICATE one'
        self.assertEqual((self.root / 'boot/vmlinuz-linux').read_bytes(), KERNEL + MARK + cert)
        self.assertEqual((self.root / 'boot/vmlinuz-linux-lts').read_bytes(), LTS + MARK + cert)
        self.assertEqual(self.machine.fwupd_signed.read_bytes(), FWUPD + MARK + cert)
        # The loader itself stays the package's file; only its .signed copy is written.
        self.assertEqual(self.machine.fwupd_efi.read_bytes(), FWUPD)
        self.assertIn('Signed /boot/vmlinuz-linux with', out)
        self.assertEqual(oct(self.machine.status.stat().st_mode & 0o777), '0o644')
        status = json.loads(self.machine.status.read_text())
        self.assertEqual(status['signed'], {'/boot/vmlinuz-linux': True, '/boot/vmlinuz-linux-lts': True,
                                            str(secureboot.FWUPD_SIGNED): True})
        self.assertEqual(list(self.root.glob('boot/*' + tool.SIGNING_SUFFIX)), [])

    def test_already_signed_kernels_are_not_signed_again(self):
        self.main(['sign'], FakeTools())
        kernels = {name: (self.root / 'boot' / name).read_bytes() for name in ('vmlinuz-linux', 'vmlinuz-linux-lts')}
        tools = FakeTools()
        self.assertEqual(self.main(['sign'], tools)[0], 0)
        signed = [argv[-1] for argv, _ in tools.calls if argv[0] == 'sbsign']
        self.assertEqual(signed, [str(self.machine.fwupd_efi)])
        for name, data in kernels.items():
            self.assertEqual((self.root / 'boot' / name).read_bytes(), data)

    def test_missing_lts_kernel_is_not_an_error(self):
        (self.root / 'boot/vmlinuz-linux-lts').unlink()
        self.assertEqual(self.main(['sign'], FakeTools())[0], 0)
        self.assertFalse((self.root / 'boot/vmlinuz-linux-lts').exists())

    def test_failure_names_the_file_and_keeps_the_kernel(self):
        tools = FakeTools(fail_sign=('vmlinuz-linux-lts',))
        code, _, err = self.main(['sign'], tools)
        self.assertEqual(code, 1)
        self.assertIn('Could not sign /boot/vmlinuz-linux-lts: sbsign failed', err)
        self.assertIn('emaki-secureboot sign', err)
        self.assertEqual((self.root / 'boot/vmlinuz-linux-lts').read_bytes(), LTS)
        # The other files are still signed: one failure does not stop the rest.
        self.assertTrue((self.root / 'boot/vmlinuz-linux').read_bytes().endswith(MARK + b'CERTIFICATE one'))
        self.assertTrue(self.machine.fwupd_signed.exists())
        self.assertEqual(list(self.root.glob('boot/*' + tool.SIGNING_SUFFIX)), [])

    def test_a_full_disk_after_the_check_is_loud_and_recorded(self):
        """The pre-transaction check passed, then the signed kernel did not fit on the disk."""
        tools = FakeTools(full_disk=('vmlinuz-linux' + tool.SIGNING_SUFFIX,))
        self.assertEqual(self.main(['check'], tools)[0], 0)
        code, _, err = self.main(['sign'], tools)
        self.assertEqual(code, 1)
        lines = err.splitlines()
        rules = [index for index, line in enumerate(lines) if line.endswith('=' * 40)]
        self.assertEqual(len(rules), 2, err)
        block = '\n'.join(lines[rules[0]:rules[1] + 1])
        self.assertIn('Could not sign /boot/vmlinuz-linux: [Errno 28] No space left on device', block)
        self.assertNotIn('vmlinuz-linux-lts', block)
        self.assertIn('Emaki will not start with Secure Boot on until this is fixed.', block)
        self.assertIn('Free some disk space', block)
        self.assertIn('"Sign again"', block)
        self.assertIn('sudo emaki-secureboot sign', block)
        # The kernel is the package's file, whole; no half-written signed copy is left.
        self.assertEqual((self.root / 'boot/vmlinuz-linux').read_bytes(), KERNEL)
        self.assertEqual(list(self.root.glob('boot/*' + tool.SIGNING_SUFFIX)), [])
        # The session's copy names exactly the file that failed; the rest signed after it.
        status = json.loads(self.machine.status.read_text())
        self.assertEqual(status['signed'], {'/boot/vmlinuz-linux': False, '/boot/vmlinuz-linux-lts': True,
                                            str(secureboot.FWUPD_SIGNED): True})
        self.assertTrue((self.root / 'boot/vmlinuz-linux-lts').read_bytes().endswith(MARK + b'CERTIFICATE one'))

    def test_a_full_disk_for_the_firmware_loader_only_does_not_claim_emaki_stops(self):
        tools = FakeTools(full_disk=(secureboot.FWUPD_SIGNED.name + tool.SIGNING_SUFFIX,))
        code, _, err = self.main(['sign'], tools)
        self.assertEqual(code, 1)
        self.assertIn(f'Could not sign {secureboot.FWUPD_SIGNED}:', err)
        self.assertIn('Firmware updates will not start with Secure Boot on until this is fixed.', err)
        self.assertNotIn('Emaki will not start', err)
        status = json.loads(self.machine.status.read_text())
        self.assertEqual(status['signed'], {'/boot/vmlinuz-linux': True, '/boot/vmlinuz-linux-lts': True,
                                            str(secureboot.FWUPD_SIGNED): False})

    def test_a_signature_that_does_not_verify_fails(self):
        tools = FakeTools(reject=('vmlinuz-linux.',))
        code, _, err = self.main(['sign', str(self.root / 'boot/vmlinuz-linux')], tools)
        self.assertEqual(code, 1)
        self.assertIn('Could not sign /boot/vmlinuz-linux:', err)
        self.assertEqual((self.root / 'boot/vmlinuz-linux').read_bytes(), KERNEL)

    def test_explicit_paths_sign_only_kernels_in_boot(self):
        tools = FakeTools()
        module_kernel = self.root / 'usr/lib/modules/7.2.7-arch1-1/vmlinuz'
        code, out, _ = self.main(['sign', str(self.root / 'boot/vmlinuz-linux'), str(module_kernel), '',
                                  'boot/vmlinuz-linux-lts', str(self.root / 'boot/initramfs-linux.img')], tools)
        self.assertEqual(code, 0)
        self.assertTrue((self.root / 'boot/vmlinuz-linux').read_bytes().endswith(MARK + b'CERTIFICATE one'))
        self.assertEqual((self.root / 'boot/vmlinuz-linux-lts').read_bytes(), LTS)
        self.assertEqual(module_kernel.read_bytes(), KERNEL)
        self.assertEqual((self.root / 'boot/initramfs-linux.img').read_bytes(), b'initramfs')
        self.assertFalse(self.machine.fwupd_signed.exists())
        self.assertIn('only kernels in /boot are signed', out)

    def test_signing_leaves_dkms_alone(self):
        """DKMS signs its modules itself (the installer's framework.conf.d file); never this tool."""
        before = snapshot(self.root)
        tools = FakeTools()
        self.assertEqual(self.main(['sign'], tools)[0], 0)
        after = snapshot(self.root)
        changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
        self.assertEqual(changed, {'boot/vmlinuz-linux', 'boot/vmlinuz-linux-lts',
                                   str(secureboot.FWUPD_SIGNED.relative_to('/')), tool.STATUS})
        self.assertEqual(set(tools.programs()), {'sbsign', 'sbverify', 'mokutil'})
        self.assertFalse(any('dkms' in ' '.join(argv) for argv, _ in tools.calls))

    def test_check_signs_a_scratch_image_only(self):
        before = snapshot(self.root)
        tools = FakeTools()
        self.assertEqual(self.main(['check'], tools)[0], 0)
        self.assertEqual(tools.programs(), ['sbsign', 'sbverify'])
        self.assertEqual(snapshot(self.root), before)
        code, _, err = self.main(['check'], FakeTools(fail_sign=('probe.efi',)))
        self.assertEqual(code, 1)
        self.assertIn('cannot sign boot files', err)
        self.assertIn('stopped before any boot file changed', err)


class StatusTests(Scratch):
    def setUp(self):
        super().setUp()
        self.add_key()

    def test_root_computes_and_writes_the_session_copy(self):
        self.secure_boot(True)
        tools = FakeTools(enrollment='enrolled')
        code, out, _ = self.main(['status', '--json', '--write'], tools)
        self.assertEqual(code, 0)
        status = json.loads(out)
        self.assertEqual((status['secure_boot'], status['key'], status['enrollment']), (True, True, 'enrolled'))
        self.assertEqual(status['signed']['/boot/vmlinuz-linux'], False)
        self.assertEqual(json.loads(self.machine.status.read_text()), status)
        # The session reads root's copy and never runs mokutil or sbverify itself.
        session = FakeTools()
        code, out, _ = self.main(['status', '--json'], session, euid=1000)
        self.assertEqual((code, json.loads(out)), (0, status))
        self.assertEqual(session.calls, [])

    def test_session_without_a_copy_knows_nothing_about_the_key(self):
        code, out, _ = self.main(['status', '--json'], FakeTools(), euid=1000)
        self.assertEqual(json.loads(out), {'schema_version': 1, 'secure_boot': False, 'key': None,
                                           'enrollment': None, 'signed': {}})
        self.machine.status.parent.mkdir(parents=True, exist_ok=True)
        for broken in ('{', '[]', '{"schema_version": 1, "key": "yes"}',
                       '{"schema_version": 1, "secure_boot": false, "key": true, "enrollment": "maybe", "signed": {}}'):
            self.machine.status.write_text(broken)
            self.assertIsNone(json.loads(self.main(['status'], FakeTools(), euid=1000)[1])['key'], broken)

    def test_unreadable_mok_state_is_null(self):
        status = tool.compute_status(FakeTools(enrollment=None), self.machine)
        self.assertEqual((status['key'], status['enrollment']), (True, None))

    def test_no_uefi_variables_means_unknown_secure_boot(self):
        shutil.rmtree(self.root / 'sys')
        self.assertIsNone(tool.compute_status(FakeTools(), self.machine)['secure_boot'])


class EnrollTests(Scratch):
    def setUp(self):
        super().setUp()
        self.add_key()

    def test_missing_key_gets_a_new_request_and_password(self):
        tools = FakeTools(enrollment='missing')
        code, out, _ = self.main(['enroll', '--json'], tools)
        answer = json.loads(out)
        self.assertEqual((code, answer['status'], answer['enrollment']), (0, 'queued', 'pending'))
        self.assertRegex(answer['password'], r'^[0-9]{8}$')
        imports = [(argv, data) for argv, data in tools.calls if argv[:2] == ['mokutil', '--import']]
        self.assertEqual(imports, [(['mokutil', '--import', str(self.machine.state / 'machine.cer')],
                                    f"{answer['password']}\n{answer['password']}\n")])
        # Nothing secret on a command line, and the session's copy now says pending.
        self.assertFalse(any(answer['password'] in ' '.join(argv) for argv, _ in tools.calls))
        self.assertEqual(json.loads(self.machine.status.read_text())['enrollment'], 'pending')
        self.assertNotIn(answer['password'], self.machine.status.read_text())

    def test_pending_request_is_replaced(self):
        tools = FakeTools(enrollment='pending')
        code, out, _ = self.main(['enroll'], tools)
        self.assertEqual(code, 0)
        mok = [argv[1] for argv, _ in tools.calls if argv[0] == 'mokutil']
        self.assertEqual(mok[:3], ['--ignore-keyring', '--revoke-import', '--import'])

    def test_enrolled_key_is_left_alone(self):
        tools = FakeTools(enrollment='enrolled')
        code, out, _ = self.main(['enroll'], tools)
        self.assertEqual((code, json.loads(out)['enrollment']), (1, 'enrolled'))
        self.assertFalse(any('--import' in argv for argv, _ in tools.calls))

    def test_mokutil_failure_is_reported(self):
        tools = FakeTools(enrollment=None)
        code, out, _ = self.main(['enroll'], tools)
        self.assertEqual(code, 1)
        self.assertNotIn('password', json.loads(out))


def real_tool(name):
    root = os.environ.get('EMAKI_SBSIGNTOOLS')
    if root:
        path = Path(root) / 'usr/bin' / name
        return str(path) if path.is_file() else None
    return shutil.which(name)


def real_run(argv, input=None):
    argv = list(argv)
    environment = dict(os.environ)
    if argv[0] in ('sbsign', 'sbverify'):
        argv[0] = real_tool(argv[0])
        if os.environ.get('EMAKI_SBSIGNTOOLS'):
            environment['LD_LIBRARY_PATH'] = str(Path(os.environ['EMAKI_SBSIGNTOOLS']) / 'usr/lib')
    result = subprocess.run(argv, input=input, text=True, capture_output=True, env=environment)
    if result.returncode:
        raise tool.CommandError(argv, result.stdout + result.stderr)
    return result.stdout


@unittest.skipUnless(real_tool('sbsign') and real_tool('sbverify') and shutil.which('openssl'),
                     'sbsigntools are not available')
class RealToolsTests(Scratch):
    def test_real_signatures_verify_and_are_not_doubled(self):
        self.machine.state.mkdir(parents=True, mode=0o700)
        secureboot.generate_key(real_run, self.machine.state)
        for path in (self.root / 'boot/vmlinuz-linux', self.root / 'boot/vmlinuz-linux-lts', self.machine.fwupd_efi):
            path.write_bytes(tool.minimal_efi())
        self.assertEqual(tool.check(real_run, self.machine), [])
        self.assertEqual(tool.sign(real_run, self.machine, say=lambda _: None), [])
        for path in (self.root / 'boot/vmlinuz-linux', self.root / 'boot/vmlinuz-linux-lts', self.machine.fwupd_signed):
            self.assertTrue(secureboot.signed(real_run, path, self.machine.state), path)
        self.assertFalse(secureboot.signed(real_run, self.machine.fwupd_efi, self.machine.state))
        first = (self.root / 'boot/vmlinuz-linux').read_bytes()
        self.assertEqual(tool.sign(real_run, self.machine, say=lambda _: None), [])
        self.assertEqual((self.root / 'boot/vmlinuz-linux').read_bytes(), first)
        # A key that does not match its certificate is refused before pacman changes anything.
        other = Path(self.temporary.name) / 'other'
        other.mkdir()
        secureboot.generate_key(real_run, other)
        shutil.copyfile(other / 'machine.crt', self.machine.state / 'machine.crt')
        self.assertTrue(tool.check(real_run, self.machine))


def hook(name):
    sections, current = {}, None
    for line in (ROOT / 'grub' / name).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            current = line
            sections.setdefault(current, []).append({})
            continue
        key, _, value = line.partition('=')
        sections[current][-1].setdefault(key.strip(), []).append(value.strip())
    return sections


class InstalledFilesTests(unittest.TestCase):
    def test_pacman_sign_hook(self):
        sections = hook('94-emaki-secureboot-sign.hook')
        action = sections['[Action]'][0]
        self.assertEqual(action['When'], ['PostTransaction'])
        self.assertEqual(action['Exec'], ['/usr/bin/emaki-secureboot sign'])
        paths = [trigger for trigger in sections['[Trigger]'] if trigger['Type'] == ['Path']][0]
        self.assertEqual(set(paths['Target']), {'usr/lib/modules/*/vmlinuz',
                                                str(secureboot.FWUPD_EFI.relative_to('/'))})
        self.assertEqual(set(paths['Operation']), {'Install', 'Upgrade'})
        # libalpm runs hooks in file-name order: after Arch's kernel copy, before Emaki's refresh.
        self.assertEqual(sorted(['90-mkinitcpio-install.hook', '94-emaki-secureboot-sign.hook',
                                 '95-emaki-boot-refresh.hook']),
                         ['90-mkinitcpio-install.hook', '94-emaki-secureboot-sign.hook', '95-emaki-boot-refresh.hook'])
        self.assertTrue((ROOT / 'grub/95-emaki-boot-refresh.hook').is_file())

    def test_hook_descriptions_say_nothing_of_secure_boot(self):
        # pacman prints them on every kernel, fwupd and emaki-config upgrade, also on machines
        # without the key, where the hooks do nothing (R9I P2 #4).
        for name, expected in (('94-emaki-secureboot-sign.hook', 'Finishing the startup files'),
                               ('03-emaki-secureboot-check.hook', 'Checking the new startup files')):
            with self.subTest(name):
                description, = hook(name)['[Action]'][0]['Description']
                self.assertEqual(description, expected)
                for word in ('secure', 'boot', 'key', 'sign', 'mok'):
                    self.assertNotIn(word, description.lower())

    def test_pacman_check_hook_aborts_before_changes(self):
        sections = hook('03-emaki-secureboot-check.hook')
        action = sections['[Action]'][0]
        self.assertEqual(action['When'], ['PreTransaction'])
        self.assertIn('AbortOnFail', action)
        self.assertEqual(action['Exec'], ['/usr/bin/emaki-secureboot check'])
        self.assertEqual(sections['[Trigger]'][0]['Target'], hook('94-emaki-secureboot-sign.hook')['[Trigger]'][0]['Target'])
        self.assertLess('03-emaki-secureboot-check.hook', '04-emaki-update-boot.hook')
        self.assertLess('03-emaki-secureboot-check.hook', '05-snap-pac-pre.hook')

    def test_mkinitcpio_post_hook(self):
        render = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        with tempfile.TemporaryDirectory(prefix='secureboot-post-') as directory:
            prefix = Path(directory) / 'usr'
            text = render['substitute']((ROOT / 'initcpio/post/emaki-secureboot').read_text(),
                                        render['paths'](str(prefix)))
            self.assertIn(f'exec {prefix}/bin/emaki-secureboot sign "$1"', text)
            hook_path = Path(directory) / 'post-hook'
            hook_path.write_text(text)
            log = Path(directory) / 'calls'
            # mkinitcpio calls: hook KERNELIMAGE IMAGE [UKI]; without the tool the build goes on.
            result = subprocess.run(['sh', str(hook_path), '/boot/vmlinuz-linux', '/boot/initramfs-linux.img'])
            self.assertEqual(result.returncode, 0)
            (prefix / 'bin').mkdir(parents=True)
            fake = prefix / 'bin/emaki-secureboot'
            fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit "${{FAKE_EXIT:-0}}"\n')
            fake.chmod(0o755)
            subprocess.run(['sh', str(hook_path), '', '/boot/initramfs-linux.img'], check=True)
            self.assertFalse(log.exists())
            subprocess.run(['sh', str(hook_path), '/boot/vmlinuz-linux', '/boot/initramfs-linux.img'], check=True)
            self.assertEqual(log.read_text(), 'sign /boot/vmlinuz-linux\n')
            failed = subprocess.run(['sh', str(hook_path), '/boot/vmlinuz-linux', '/x.img'],
                                    env=dict(os.environ, FAKE_EXIT='1'))
            self.assertEqual(failed.returncode, 1)
        self.assertTrue(os.access(ROOT / 'initcpio/post/emaki-secureboot', os.X_OK))

    def test_polkit_policy(self):
        actions = ET.parse(ROOT / 'polkit/org.emaki.secureboot.policy').getroot().findall('action')
        self.assertEqual([action.attrib['id'] for action in actions],
                         ['org.emaki.secureboot.enroll', 'org.emaki.secureboot.sign'])
        for action, command in zip(actions, ('enroll', 'sign')):
            self.assertEqual(action.find('defaults/allow_active').text, 'auth_admin')
            self.assertEqual(action.find('defaults/allow_inactive').text, 'no')
            self.assertEqual(action.find('defaults/allow_any').text, 'no')
            annotations = {item.attrib['key']: item.text for item in action.findall('annotate')}
            self.assertEqual(annotations, {'org.freedesktop.policykit.exec.path': '@EMAKI_BINDIR@/emaki-secureboot',
                                           'org.freedesktop.policykit.exec.argv1': command})

    def test_status_unit_is_written_before_logins(self):
        """A session that starts before root's copy exists would read an unknown key."""
        sections = {}
        current = None
        for line in (ROOT / 'systemd/emaki-secureboot-status.service').read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('['):
                current = sections.setdefault(line, {})
                continue
            key, _, value = line.partition('=')
            current.setdefault(key, []).extend(value.split())
        unit, service = sections['[Unit]'], sections['[Service]']
        # greetd and getty start after systemd-user-sessions, which lifts /run/nologin for the rest.
        self.assertIn('systemd-user-sessions.service', unit.get('Before', []))
        self.assertEqual(service['Type'], ['oneshot'])
        # A oneshot has no start timeout by default: a hung tool must not keep logins closed.
        self.assertEqual(service.get('TimeoutStartSec'), ['30s'])
        # Nothing it waits for may itself wait for logins.
        self.assertFalse({'systemd-user-sessions.service', 'multi-user.target', 'graphical.target',
                          'display-manager.service', 'greetd.service'} & set(unit.get('After', [])))
        self.assertEqual(unit.get('DefaultDependencies', ['yes']), ['yes'])

    def test_status_unit_and_launcher(self):
        unit = (ROOT / 'systemd/emaki-secureboot-status.service').read_text()
        self.assertIn('ExecStart=@EMAKI_BINDIR@/emaki-secureboot status --write\n', unit)
        self.assertIn('WantedBy=multi-user.target', unit)
        launcher = (ROOT / 'grub/emaki-secureboot').read_text()
        self.assertIn('from emaki_boot.secureboot_tool import main', launcher)
        self.assertTrue(os.access(ROOT / 'grub/emaki-secureboot', os.X_OK))
        listing = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        for path in ('/usr/bin/emaki-secureboot', '/usr/lib/emaki/boot/emaki_boot/secureboot_tool.py',
                     '/usr/lib/initcpio/post/emaki-secureboot',
                     '/usr/lib/systemd/system/emaki-secureboot-status.service',
                     '/usr/lib/systemd/system/multi-user.target.wants/emaki-secureboot-status.service',
                     '/usr/share/libalpm/hooks/03-emaki-secureboot-check.hook',
                     '/usr/share/libalpm/hooks/94-emaki-secureboot-sign.hook',
                     '/usr/share/polkit-1/actions/org.emaki.secureboot.policy'):
            self.assertIn(path, listing)


if __name__ == '__main__':
    unittest.main()
