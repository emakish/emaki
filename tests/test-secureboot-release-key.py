#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Secure Boot release key with throwaway keys and passphrases (docs/signing.md).

scripts/new-secureboot-release-key.sh makes the key once; iso/secureboot-key-to-vm.sh lends it to
the build VM for one build. Both get the passphrase only through OpenSSL's own prompt, so these
tests type it into a pseudo-terminal. The "VM" is a local directory: the ssh command is `bash -c`,
and sudo, findmnt and swapon are stand-ins; openssl is real.
"""
import os
from pathlib import Path
import pty
import re
import select
import shutil
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAKE = ROOT / 'scripts/new-secureboot-release-key.sh'
LEND = ROOT / 'iso/secureboot-key-to-vm.sh'
PASSPHRASE = 'throwaway passphrase for a test'


def in_terminal(args, answers, env=None, timeout=120):
    """Run args with a pseudo-terminal as controlling terminal; type each answer after its prompt."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(args[0], args, env or os.environ)
    output, pending, deadline = b'', list(answers), time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        # OpenSSL writes its prompt before disabling echo. Seeing the prompt alone
        # does not mean it is safe to type a passphrase into the terminal yet.
        if (pending and re.search(pending[0][0], output.decode(errors='replace').split('\x00')[-1])
                and not termios.tcgetattr(fd)[3] & termios.ECHO):
            prompt, answer = pending.pop(0)
            output += b'\n<typed>\n'
            os.write(fd, answer.encode() + b'\n')
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), output.decode(errors='replace').replace('\r', '')


SWAPS_HEADER = 'Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n'
CORE_PATTERN = '|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h %d %F %I\n'


def fake_proc(base, swaps=SWAPS_HEADER, core_pattern=CORE_PATTERN, qemu_core='0', qemu_args='qemu-system-x86_64 -m 12G'):
    proc = base / 'proc'
    (proc / 'sys/kernel').mkdir(parents=True, exist_ok=True)
    (proc / 'sys/kernel/core_pattern').write_text(core_pattern)
    (proc / 'swaps').unlink(missing_ok=True)
    if swaps is not None:
        (proc / 'swaps').write_text(swaps)
    (proc / '4242').mkdir(exist_ok=True)
    (proc / '4242/cmdline').write_bytes(qemu_args.replace(' ', '\0').encode() + b'\0')
    (proc / '4242/limits').write_text('Limit                     Soft Limit           Hard Limit           Units\n'
                                      f'Max core file size        {qemu_core}                    unlimited            bytes\n')
    return proc


def fake_tools(bin_directory):
    """busctl answers ListInhibitors: by default this user's sleep block owned by process 4242 (in fake_proc);
    STUB_INHIBIT=none|delay|dead|fail for the refusals. head records the core limits it runs with when it
    reads a .key file (into STUB_HEAD_LOG)."""
    bin_directory.mkdir(exist_ok=True)
    uid = os.getuid()
    real_head = shutil.which('head')
    tools = {
        'busctl': 'case ${STUB_INHIBIT:-block} in\n'
                  '  fail) exit 1 ;;\n'
                  '  none) echo \'{"type":"a(ssssuu)","data":[[]]}\' ;;\n'
                  f'  delay) echo \'{{"type":"a(ssssuu)","data":[[["sleep","Emaki","Emaki release key","delay",{uid},4242]]]}}\' ;;\n'
                  f'  dead) echo \'{{"type":"a(ssssuu)","data":[[["sleep","Emaki","Emaki release key","block",{uid},999999]]]}}\' ;;\n'
                  f'  *) echo \'{{"type":"a(ssssuu)","data":[[["sleep:idle:handle-lid-switch","Emaki","Emaki release key","block",{uid},4242]]]}}\' ;;\n'
                  'esac',
        'head': 'for a; do case $a in *.key) [ -n "$STUB_HEAD_LOG" ] && echo "$(ulimit -S -c) $(ulimit -H -c)" >>"$STUB_HEAD_LOG" ;; esac; done\n'
                f'exec {real_head} "$@"',
    }
    for name, text in tools.items():
        (bin_directory / name).write_text(f'#!/bin/sh\n{text}\n')
        (bin_directory / name).chmod(0o755)
    return bin_directory


def openssl(*args, data=None):
    return subprocess.run(['openssl', *args], input=data, capture_output=True, check=True).stdout


class MakeKey(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.dir = self.home / 'config/emaki-secureboot'
        self.proc = fake_proc(self.home)
        self.bin = fake_tools(self.home / 'bin')
        for name, text in (('findmnt', 'echo /dev/mapper/root'), ('lsblk', 'printf "%s\\n" ${STUB_LSBLK:-part disk}')):
            (self.bin / name).write_text(f'#!/bin/sh\n{text}\n')
            (self.bin / name).chmod(0o755)

    def env(self, **extra):
        return dict(os.environ, EMAKI_PROC_ROOT=str(self.proc), PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                    **extra)

    def make(self, first=PASSPHRASE, second=PASSPHRASE, directory=None, third=None, shell=('bash',), env=None):
        return in_terminal([*shell, str(MAKE), '--dir', str(directory or self.dir)],
                           [('Enter Encryption Password:', first), ('Verifying - Enter Encryption Password:', second),
                            ('Enter pass phrase for', third if third is not None else first)], env or self.env())

    def test_makes_an_encrypted_rsa_2048_code_signing_key_once(self):
        code, output = self.make()
        self.assertEqual(code, 0, output)
        key, crt, cer = (self.dir / f'emaki-release.{suffix}' for suffix in ('key', 'crt', 'cer'))
        self.assertEqual(oct(self.dir.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct(key.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(crt.stat().st_mode & 0o777), '0o644')
        # Encrypted at rest: PKCS#8 PBES2, PBKDF2-HMAC-SHA256 with 600000 iterations, AES-256-CBC.
        self.assertEqual(key.read_text().splitlines()[0], '-----BEGIN ENCRYPTED PRIVATE KEY-----')
        structure = openssl('asn1parse', '-in', str(key)).decode()
        for part in (':PBES2', ':PBKDF2', ':0927C0', ':hmacWithSHA256', ':aes-256-cbc'):
            self.assertIn(part, structure)
        with self.assertRaises(subprocess.CalledProcessError):
            openssl('pkey', '-in', str(key), '-passin', 'pass:', '-noout')
        plain = openssl('pkey', '-in', str(key), '-passin', f'pass:{PASSPHRASE}')
        self.assertEqual(openssl('pkey', '-pubout', data=plain), openssl('x509', '-in', str(crt), '-noout', '-pubkey'))
        text = openssl('x509', '-in', str(crt), '-noout', '-text').decode()
        year = time.gmtime().tm_year
        self.assertIn(f'Subject: CN=Emaki Secure Boot release key {year}', text)
        self.assertIn(f'Issuer: CN=Emaki Secure Boot release key {year}', text)
        self.assertIn('Public-Key: (2048 bit)', text)
        self.assertIn('Signature Algorithm: sha256WithRSAEncryption', text)
        self.assertRegex(text, r'X509v3 Basic Constraints: critical\n\s+CA:FALSE')
        self.assertRegex(text, r'X509v3 Key Usage: critical\n\s+Digital Signature\n')
        self.assertRegex(text, r'X509v3 Extended Key Usage: \n\s+Code Signing\n')
        self.assertNotIn('1.3.6.1.4.1.2312.16.1.2', text)
        not_after = openssl('x509', '-in', str(crt), '-noout', '-enddate').decode()
        self.assertGreaterEqual(int(not_after.split()[-2]), year + 99)
        openssl('verify', '-CAfile', str(crt), '-partial_chain', str(crt))
        self.assertEqual(cer.read_bytes(), openssl('x509', '-in', str(crt), '-outform', 'DER'))
        for digest, label in (('sha256', 'SHA-256 fingerprint: '), ('sha1', 'SHA-1 fingerprint (MokManager shows this one): ')):
            expected = openssl('x509', '-in', str(crt), '-noout', '-fingerprint', f'-{digest}').decode().split('=')[1].strip()
            self.assertIn(label + expected, output)
        # The passphrase was typed, never shown, never in the files' public halves.
        self.assertNotIn(PASSPHRASE, output.replace('<typed>', ''))
        # Once: a second run refuses and changes nothing.
        before = {path.name: path.read_bytes() for path in self.dir.iterdir()}
        code, output = self.make()
        self.assertNotEqual(code, 0)
        self.assertIn('already exists; the release key is made once and never overwritten', output)
        self.assertEqual({path.name: path.read_bytes() for path in self.dir.iterdir()}, before)

    def test_shell_tracing_is_refused_before_anything_secret(self):
        startup = self.home / 'startup.sh'
        startup.write_text("trap 'echo \"$BASH_COMMAND\" >&2' DEBUG\n")
        for shell, env, phrase in (
                (('bash', '-x'), self.env(), 'shell tracing (set -x or -v)'),
                (('bash', '-v'), self.env(), 'shell tracing (set -x or -v)'),
                (('bash',), self.env(SHELLOPTS='xtrace'), 'shell tracing (set -x or -v)'),
                (('bash', '-T'), self.env(), 'function tracing (bash -T or functrace)'),
                (('bash',), self.env(SHELLOPTS='functrace'), 'function tracing (bash -T or functrace)'),
                (('bash',), self.env(BASH_ENV=str(startup)), 'a shell startup file (BASH_ENV or ENV) ran'),
                (('bash',), self.env(ENV=str(startup)), 'a shell startup file (BASH_ENV or ENV) ran'),
                (('bash',), self.env(BASH_ENV='/dev/null'), 'a shell startup file (BASH_ENV or ENV) ran')):
            with self.subTest(shell=shell, env={k: env.get(k) for k in ('SHELLOPTS', 'BASH_ENV')}):
                code, output = self.make(shell=shell, env=env)
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertNotIn('PRIVATE KEY', output)
                self.assertNotIn('Enter Encryption Password', output)
                self.assertFalse(self.dir.exists())

    def test_the_plain_key_never_passes_through_a_shell_variable_or_argument(self):
        text = MAKE.read_text()
        self.assertNotRegex(text, r'=\$\(openssl (genpkey|pkey|pkcs8)')
        self.assertNotRegex(text, r'printf [^\n]*\$plain|<<<')
        self.assertIn('openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null \\\n    | openssl pkcs8', text)

    def test_unencrypted_swap_or_a_foreign_core_handler_on_this_machine_refuses_first(self):
        swap = SWAPS_HEADER + '/swap/swapfile                          file\t\t67108860\t1\t\t-1\n'
        for swaps, pattern, phrase in (
                (swap, CORE_PATTERN, 'swap /swap/swapfile on this machine is on and not encrypted'),
                (None, CORE_PATTERN, 'cannot inspect the swap of this machine'),
                ('nonsense\n', CORE_PATTERN, 'cannot inspect the swap of this machine'),
                (SWAPS_HEADER, '|/usr/local/bin/catch-dumps %p\n', "core dumps on this machine go to '/usr/local/bin/catch-dumps")):
            with self.subTest(phrase=phrase):
                fake_proc(self.home, swaps=swaps, core_pattern=pattern)
                code, output = self.make()
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertNotIn('Enter Encryption Password', output)
                self.assertFalse(self.dir.exists())
        # zram that writes pages back to a disk is refused; without a backing device it is fine.
        sys_root = self.home / 'sys'
        (sys_root / 'block/zram0').mkdir(parents=True)
        fake_proc(self.home, swaps=SWAPS_HEADER + '/dev/zram0                              partition\t8388604\t0\t\t100\n')
        (sys_root / 'block/zram0/backing_dev').write_text('/dev/sda3\n')
        code, output = self.make(env=self.env(EMAKI_SYS_ROOT=str(sys_root)))
        self.assertNotEqual(code, 0)
        self.assertIn('swap /dev/zram0 on this machine is on and not encrypted', output)
        self.assertFalse(self.dir.exists())
        (sys_root / 'block/zram0/backing_dev').write_text('none\n')
        code, output = self.make(env=self.env(EMAKI_SYS_ROOT=str(sys_root)))
        self.assertEqual(code, 0, output)
        shutil.rmtree(self.dir)
        # Encrypted swap (dm-crypt under the swap file) is fine.
        fake_proc(self.home, swaps=swap)
        code, output = self.make(env=self.env(STUB_LSBLK='crypt part disk'))
        self.assertEqual(code, 0, output)

    def test_a_held_sleep_block_and_an_exact_dump_handler_are_required(self):
        for env, pattern, phrase in (
                ({'STUB_INHIBIT': 'none'}, CORE_PATTERN, 'no sleep block is held'),
                ({'STUB_INHIBIT': 'delay'}, CORE_PATTERN, 'no sleep block is held'),
                ({'STUB_INHIBIT': 'dead'}, CORE_PATTERN, 'no sleep block is held'),
                ({'STUB_INHIBIT': 'fail'}, CORE_PATTERN, 'cannot ask logind which sleep inhibitors are held'),
                ({}, '|/tmp/catch-dumps /usr/lib/systemd/systemd-coredump %P %u %g %s %t %c\n',
                 "go to '/tmp/catch-dumps /usr/lib/systemd/systemd-coredump"),
                ({}, '|/usr/lib/systemd/systemd-coredump %P %u %g %s %t 999999999 %h\n',
                 "go to '/usr/lib/systemd/systemd-coredump %P %u %g %s %t 999999999 %h'"),
                ({}, '|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c 999\n', "unexpected argument '999'"),
                ({}, '@/tmp/catch-dumps.sock\n', "go to the socket '/tmp/catch-dumps.sock'"),
                ({}, '@@/tmp/catch-dumps.sock\n', "go to the socket '@/tmp/catch-dumps.sock'")):
            with self.subTest(env=env, pattern=pattern):
                fake_proc(self.home, core_pattern=pattern)
                code, output = self.make(env=self.env(**env))
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertNotIn('Enter Encryption Password', output)
                self.assertFalse(self.dir.exists())
        for pattern in ('', 'core\n', '|/usr/lib/systemd/systemd-coredump %p %u %g %s %t %c %h\n'):
            with self.subTest(pattern=pattern):
                fake_proc(self.home, core_pattern=pattern)
                code, output = self.make()
                self.assertEqual(code, 0, output)
                shutil.rmtree(self.dir)

    def test_zram_that_cannot_be_inspected_or_says_nothing_is_refused(self):
        sys_root = self.home / 'sys'
        fake_proc(self.home, swaps=SWAPS_HEADER + '/dev/zram0                              partition\t8388604\t0\t\t100\n')
        code, output = self.make(env=self.env(EMAKI_SYS_ROOT=str(sys_root)))
        self.assertNotEqual(code, 0)
        self.assertIn('swap /dev/zram0 on this machine is on and not encrypted', output)
        (sys_root / 'block/zram0').mkdir(parents=True)
        (sys_root / 'block/zram0/backing_dev').write_text('')
        code, output = self.make(env=self.env(EMAKI_SYS_ROOT=str(sys_root)))
        self.assertNotEqual(code, 0)
        (sys_root / 'block/zram0/backing_dev').unlink()
        code, output = self.make(env=self.env(EMAKI_SYS_ROOT=str(sys_root)))
        self.assertEqual(code, 0, output)

    def test_differing_or_empty_passphrases_keep_nothing(self):
        for first, second, phrase in (('one passphrase', 'another one', 'the two passphrases differ'),
                                      ('', '', 'the passphrase is empty')):
            with self.subTest(phrase=phrase):
                code, output = self.make(first, second)
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertEqual(list(self.dir.iterdir()), [])

    def test_never_inside_a_git_work_tree(self):
        checkout = self.home / 'checkout'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        code, output = self.make(directory=checkout / '.cache/key')
        self.assertNotEqual(code, 0)
        self.assertIn('is inside a git work tree; keep the release key outside every checkout', output)
        self.assertFalse((checkout / '.cache').exists())

    def test_needs_a_terminal_for_the_passphrase(self):
        result = subprocess.run(['bash', str(MAKE), '--dir', str(self.dir)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, start_new_session=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('run this in a terminal: the passphrase is typed into it', result.stderr)
        self.assertFalse(self.dir.exists())

    def test_no_passphrase_ever_passes_through_an_argument_variable_or_file(self):
        for script in (MAKE, LEND):
            with self.subTest(script=script.name):
                text = script.read_text()
                uses = re.findall(r'-pass(?:in|out)\s+\S+', text)
                self.assertTrue(all(use.split()[1] == 'pass:' for use in uses), uses)
                self.assertNotRegex(text, r'\bread\s+-[a-z]*s')


class LendKey(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = self.base = Path(self.temp.name)
        self.prefix = base / 'host/emaki-release'
        self.prefix.parent.mkdir(mode=0o700)
        plain = openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048')
        self.plain_pub = openssl('pkey', '-pubout', data=plain)
        self.key_bytes = plain
        subprocess.run(['openssl', 'req', '-new', '-x509', '-key', '/dev/stdin', '-subj', '/CN=Test release',
                        '-days', '2', '-addext', 'extendedKeyUsage=codeSigning', '-out', f'{self.prefix}.crt'],
                       input=plain, check=True, capture_output=True)
        subprocess.run(['openssl', 'pkcs8', '-topk8', '-v2', 'aes-256-cbc', '-in', '/dev/stdin', '-out',
                        f'{self.prefix}.key', '-passout', f'pass:{PASSPHRASE}'], input=plain, check=True,
                       capture_output=True)
        os.chmod(f'{self.prefix}.key', 0o600)
        self.pinned = base / 'pinned.crt'
        shutil.copy(f'{self.prefix}.crt', self.pinned)
        self.remote = base / 'vm/run/emaki-secureboot'
        self.remote.parent.mkdir(parents=True)
        self.bin = fake_tools(base / 'bin')
        real_stat = shutil.which('stat')
        for name, text in (
                # sudo: as the caller; STUB_RM_FAIL makes removal fail.
                ('sudo', 'if [ "$1" = rm ] && [ -n "$STUB_RM_FAIL" ]; then exit 1; fi\nexec "$@"'),
                ('findmnt', 'echo "${STUB_FS:-tmpfs}"'),
                # stat: STUB_VM_MODE answers for the key inside the VM.
                ('stat', 'for a; do last=$a; done\n'
                         'case $last in */vm/*) [ -n "$STUB_VM_MODE" ] && { echo "$STUB_VM_MODE"; exit 0; } ;; esac\n'
                         f'exec {real_stat} "$@"'),
                # The VM: one remote shell command, with its /proc/swaps a fixture file.
                ('fake-ssh', 'printf "%s\\n" "$1" >>"$FAKE_VM_LOG"\n'
                             'cmd=$(printf "%s" "$1" | sed "s|/proc/swaps|$FAKE_VM_SWAPS|g; '
                             's|/proc/sys/kernel/core_pattern|$FAKE_VM_CORE|g")\nexec bash -c "$cmd"')):
            (self.bin / name).write_text(f'#!/bin/sh\n{text}\n')
            (self.bin / name).chmod(0o755)
        self.proc = fake_proc(base)
        self.vm_swaps = base / 'vm-swaps'
        self.vm_swaps.write_text(SWAPS_HEADER)
        self.vm_core = base / 'vm-core-pattern'
        self.vm_core.write_text(CORE_PATTERN)
        self.vm_log = base / 'vm-commands.log'

    def lend(self, action, answers=(), ssh=None, vm_pid='4242', **env):
        environment = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                           EMAKI_PROC_ROOT=str(self.proc), FAKE_VM_SWAPS=str(self.vm_swaps),
                           FAKE_VM_CORE=str(self.vm_core), FAKE_VM_LOG=str(self.vm_log))
        environment.update(env)
        args = ['bash', str(LEND), action, '--key', str(self.prefix), '--remote-dir', str(self.remote),
                '--release-cert', str(self.pinned), *(['--vm-pid', vm_pid] if vm_pid else []), '--',
                *(ssh or ['fake-ssh'])]
        return in_terminal(args, answers, environment)

    def secret_material(self):
        der = openssl('pkey', '-outform', 'DER', data=self.key_bytes)
        return [b'PRIVATE KEY', der[-64:], PASSPHRASE.encode()]

    def test_send_unlocks_once_into_memory_proves_it_and_remove_takes_it_back(self):
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)])
        self.assertEqual(code, 0, output)
        key = self.remote / 'emaki-release.key'
        self.assertEqual(oct(self.remote.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct(key.stat().st_mode & 0o777), '0o600')
        self.assertEqual(key.read_text().splitlines()[0], '-----BEGIN PRIVATE KEY-----')
        self.assertEqual(openssl('pkey', '-in', str(key), '-pubout'), self.plain_pub)
        self.assertEqual((self.remote / 'emaki-release.crt').read_bytes(), Path(f'{self.prefix}.crt').read_bytes())
        self.assertIn(f'--secure-boot-key {self.remote}/emaki-release', output)
        self.assertEqual(output.count('Enter pass phrase for'), 1, 'the passphrase is asked once')
        for needle in self.secret_material():
            self.assertNotIn(needle.decode(errors='replace'), output.replace('<typed>', ''))
        self.assertIn('present:', self.lend('status')[1])
        code, output = self.lend('remove')
        self.assertEqual(code, 0, output)
        self.assertIn('is gone from the build VM', output)
        self.assertFalse(self.remote.exists())
        self.assertIn('absent:', self.lend('status')[1])

    def test_status_and_remove_never_guess(self):
        code, output = self.lend('status', ssh=['false'])
        self.assertEqual(code, 2, output)
        self.assertIn('unknown: the build VM could not be asked', output)
        self.remote.mkdir()
        (self.remote / 'emaki-release.key').write_text('stand-in')
        code, output = self.lend('remove', STUB_RM_FAIL='1')
        self.assertNotEqual(code, 0)
        self.assertNotIn('Removed', output)
        self.assertIn('could not be confirmed gone', output)
        self.assertTrue(self.remote.exists())
        code, output = self.lend('remove', ssh=['false'])
        self.assertNotEqual(code, 0)
        self.assertNotIn('Removed', output)

    def test_a_failed_check_after_the_transfer_says_whether_the_key_is_still_there(self):
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)], STUB_VM_MODE='644')
        self.assertNotEqual(code, 0)
        self.assertIn('the key in the build VM has mode 644; nothing was left in the build VM', output)
        self.assertFalse(self.remote.exists())
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)], STUB_VM_MODE='644', STUB_RM_FAIL='1')
        self.assertNotEqual(code, 0)
        self.assertIn('could NOT be confirmed gone from the build VM', output)
        self.assertNotIn('nothing was left', output)
        self.assertTrue((self.remote / 'emaki-release.key').exists())

    def test_the_vm_host_and_the_vm_must_keep_the_key_off_disks(self):
        swap = SWAPS_HEADER + '/swap/swapfile file 67108860 1 -1\n'
        cases = (
            ({}, 'no-pid', "give --vm-pid PID: the build VM's QEMU process"),
            ({}, 'core', 'could write its memory, the key included, to a core dump; first run: prlimit --pid 4242'),
            ({}, 'host-swap', 'swap /swap/swapfile on this machine is on and not encrypted'),
            ({}, 'vm-swap', 'swap /swap/swapfile is on in the build VM'),
            ({}, 'vm-swap-unreadable', 'cannot inspect the swap of the build VM'),
            ({}, 'host-swap-unreadable', 'cannot inspect the swap of this machine'),
        )
        for env, case, phrase in cases:
            with self.subTest(case=case):
                fake_proc(self.proc.parent, swaps=swap if case == 'host-swap' else (None if case == 'host-swap-unreadable' else SWAPS_HEADER),
                          qemu_core='unlimited' if case == 'core' else '0')
                self.vm_swaps.unlink(missing_ok=True)
                if case != 'vm-swap-unreadable':
                    self.vm_swaps.write_text(swap if case == 'vm-swap' else SWAPS_HEADER)
                code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)],
                                         vm_pid='' if case == 'no-pid' else '4242', **env)
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertNotIn('Enter pass phrase', output)
                self.assertFalse(self.remote.exists())
        # QEMU started with dump-guest-core=off needs no core limit.
        fake_proc(self.proc.parent, qemu_core='unlimited', qemu_args='qemu-system-x86_64 -machine q35,dump-guest-core=off')
        self.vm_swaps.write_text(SWAPS_HEADER)
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)])
        self.assertEqual(code, 0, output)

    def test_every_vm_process_that_holds_the_key_runs_without_core_dumps(self):
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)])
        self.assertEqual(code, 0, output)
        holders = [line for line in self.vm_log.read_text().splitlines()
                   if 'emaki-release.key' in line and ('cat >' in line or 'openssl' in line)]
        self.assertEqual(len(holders), 2, holders)
        for line in holders:
            self.assertIn("sudo sh -c 'ulimit -S -c 0 && ulimit -H -c 0 && ", line)
        code, output = self.lend('remove')
        self.assertIn('Keep this machine\'s swap off until the build VM is powered off and its QEMU process is gone',
                      output)

    def test_a_vm_socket_dump_handler_is_refused(self):
        for pattern in ('@/tmp/catch-dumps.sock\n', '@@/tmp/catch-dumps.sock\n'):
            with self.subTest(pattern=pattern):
                self.vm_core.write_text(pattern)
                code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)])
                self.assertNotEqual(code, 0)
                self.assertIn('core dumps on the build VM go to the socket', output)
                self.assertNotIn('Enter pass phrase', output)
                self.assertFalse(self.remote.exists())

    def test_a_vm_dump_handler_that_ignores_the_limit_is_refused(self):
        self.vm_core.write_text('|/usr/local/bin/catch-dumps %p\n')
        code, output = self.lend('send', [('Enter pass phrase for', PASSPHRASE)])
        self.assertNotEqual(code, 0)
        self.assertIn("core dumps on the build VM go to '/usr/local/bin/catch-dumps", output)
        self.assertNotIn('Enter pass phrase', output)
        self.assertFalse(self.remote.exists())

    def test_core_dumps_are_off_before_the_host_key_is_first_read(self):
        Path(f'{self.prefix}.key').write_bytes(self.key_bytes)
        log = self.base / 'head.log'
        code, output = self.lend('send', STUB_HEAD_LOG=str(log))
        self.assertNotEqual(code, 0)
        self.assertIn('is not stored encrypted', output)
        self.assertEqual(set(log.read_text().splitlines()), {'0 0'})

    def test_shell_tracing_is_refused(self):
        code, output = in_terminal(['bash', '-x', str(LEND), 'send', '--', 'false'], [])
        self.assertNotEqual(code, 0)
        self.assertIn('shell tracing (set -x or -v) is on and could print secrets', output)

    def test_a_wrong_passphrase_leaves_nothing_in_the_vm(self):
        code, output = self.lend('send', [('Enter pass phrase for', 'not the passphrase')])
        self.assertNotEqual(code, 0)
        self.assertIn('the key was not unlocked (wrong passphrase?); nothing was left in the build VM', output)
        self.assertFalse(self.remote.exists())

    def test_ctrl_c_at_the_prompt_leaves_nothing_in_the_vm(self):
        code, output = self.lend('send', [('Enter pass phrase for', '\x03')])
        self.assertNotEqual(code, 0)
        self.assertFalse(self.remote.exists(), output)

    def test_refuses_before_asking_when_the_vm_could_keep_the_key_on_a_disk(self):
        for env, phrase in (({'STUB_FS': 'ext4'}, 'is not a RAM file system (tmpfs)'),
                            ({'FAKE_VM_SWAPS': '/nonexistent'}, 'cannot inspect the swap of the build VM')):
            with self.subTest(phrase=phrase):
                code, output = self.lend('send', **env)
                self.assertNotEqual(code, 0)
                self.assertIn(phrase, output)
                self.assertNotIn('Enter pass phrase', output)
                self.assertFalse(self.remote.exists())
        self.remote.mkdir()
        code, output = self.lend('send')
        self.assertNotEqual(code, 0)
        self.assertIn('already exists in the build VM', output)

    def test_only_the_pinned_key_stored_encrypted_is_lent(self):
        other = self.base / 'other.crt'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Other', '-days', '2',
                        '-keyout', str(self.base / 'other.key'), '-out', str(other)], check=True, capture_output=True)
        shutil.copy(other, self.pinned)
        code, output = self.lend('send')
        self.assertIn('is not the release certificate pinned in', output)
        shutil.copy(f'{self.prefix}.crt', self.pinned)
        Path(f'{self.prefix}.key').write_bytes(self.key_bytes)
        code, output = self.lend('send')
        self.assertNotEqual(code, 0)
        self.assertIn('is not stored encrypted', output)
        self.assertFalse(self.remote.exists())

    def test_the_remote_directory_is_a_plain_path(self):
        for remote in ('/', '/run', "/run/x';rm -rf ~;'", '/run/../etc', 'run/relative'):
            with self.subTest(remote=remote):
                result = subprocess.run(['bash', str(LEND), 'remove', '--remote-dir', remote, '--', 'false'],
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unsafe remote directory', result.stderr)


class BackupCheck(unittest.TestCase):
    """scripts/check-secureboot-key-backup.sh: PASS only for the pinned key's encrypted copy that opens."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = self.base = Path(self.temp.name)
        self.proc = fake_proc(base)
        self.bin = fake_tools(base / 'bin')
        plain = openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048')
        subprocess.run(['openssl', 'req', '-new', '-x509', '-key', '/dev/stdin', '-subj', '/CN=Test release',
                        '-days', '2', '-out', str(base / 'pinned.crt')], input=plain, check=True, capture_output=True)
        self.backup = base / 'backup.key'
        self.backup.write_bytes(openssl('pkcs8', '-topk8', '-v2', 'aes-256-cbc', '-passout', f'pass:{PASSPHRASE}',
                                        data=plain))
        self.plain = base / 'plain.key'
        self.plain.write_bytes(plain)

    def check(self, *args, answer=PASSPHRASE, **env):
        script = ROOT / 'scripts/check-secureboot-key-backup.sh'
        return in_terminal(['bash', str(script), *map(str, args)], [('Enter pass phrase for', answer)],
                           dict(os.environ, EMAKI_PROC_ROOT=str(self.proc),
                                PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', **env))

    def test_core_dumps_are_off_before_the_first_read_of_a_candidate_key(self):
        log = self.base / 'head.log'
        code, output = self.check(self.plain, self.base / 'pinned.crt', STUB_HEAD_LOG=str(log))
        self.assertNotEqual(code, 0)
        self.assertIn('is not stored encrypted', output)
        self.assertEqual(set(log.read_text().splitlines()), {'0 0'})

    def test_pass_only_when_everything_succeeded(self):
        code, output = self.check(self.backup, self.base / 'pinned.crt')
        self.assertEqual(code, 0, output)
        self.assertIn('PASS: the backup opens with the passphrase', output)
        other = self.base / 'other.crt'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Other', '-days', '2',
                        '-keyout', str(self.base / 'other.key'), '-out', str(other)], check=True, capture_output=True)
        for args, answer, phrase in (
                ((self.base / 'missing.key', self.base / 'pinned.crt'), PASSPHRASE, 'no backup file'),
                ((self.backup, self.base / 'missing.crt'), PASSPHRASE, 'no certificate'),
                ((self.base / 'missing.key', self.base / 'missing.crt'), PASSPHRASE, 'no backup file'),
                ((self.backup, self.base / 'pinned.crt'), 'wrong passphrase', 'the backup did not open'),
                ((self.backup, other), PASSPHRASE, 'the backup is not the private key of'),
                ((self.plain, self.base / 'pinned.crt'), PASSPHRASE, 'is not stored encrypted')):
            with self.subTest(phrase=phrase, args=[Path(a).name for a in args]):
                code, output = self.check(*args, answer=answer)
                self.assertNotEqual(code, 0, output)
                self.assertIn(phrase, output)
                self.assertNotIn('PASS', output)

    def test_the_backup_check_names_the_certificate_it_compares_with(self):
        code, output = self.check(self.backup, self.base / 'pinned.crt')
        self.assertEqual(code, 0, output)
        self.assertIn(f"is the key of {self.base / 'pinned.crt'}", output)

    @unittest.skipUnless((ROOT / 'docs/signing.md').exists(), 'docs/signing.md is not part of the public export')
    def test_signing_md_uses_the_checked_command(self):
        text = ' '.join((ROOT / 'docs/signing.md').read_text().split())
        self.assertIn('scripts/check-secureboot-key-backup.sh', text)
        self.assertNotIn('cmp - <(openssl x509', text)
        # Rotation proves the new backup against the new certificate, not the pinned old one.
        rotation = text.split('### Rotate it', 1)[1]
        self.assertIn('scripts/check-secureboot-key-backup.sh <copy>/emaki-release.key '
                      '~/.config/emaki-secureboot-<year>/emaki-release.crt', rotation)
        # Swap comes back only after the build VM's QEMU is gone.
        self.assertIn('only after the build VM is powered off and its QEMU process has exited', text)
        self.assertNotIn('then swap on again', text)


if __name__ == '__main__':
    unittest.main()
