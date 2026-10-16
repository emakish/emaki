#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""No tracked file holds a real private key (docs/signing.md, section 5).

The source archive and the public export are made from tracked files; both refuse a private key with
the same detector, packaging/private_keys.py (tests/test-make-public.py proves the two refusals).
Here: the tracked tree is clean, and the detector finds PEM and DER keys, plain and encrypted, under
any name, while the redaction fixtures' made-up bodies and certificates are no key.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('private_keys', ROOT / 'packaging/private_keys.py')
private_keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(private_keys)


def openssl(*args, data=None):
    return subprocess.run(['openssl', *args], input=data, capture_output=True, check=True).stdout


def tracked():
    # A package build checks an exported source tree with no .git: walk it then, leaving out build output.
    if not (ROOT / '.git').exists():
        files = []
        for directory, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('.cache', 'target')]
            files.extend(Path(directory) / name for name in names)
        return files
    names = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout
    return [ROOT / name for name in names.decode().split('\0') if name]


class NoPrivateKeys(unittest.TestCase):
    def test_no_tracked_file_is_a_private_key(self):
        files = tracked()
        self.assertGreater(len(files), 1000)
        self.assertEqual(private_keys.scan(path for path in files if path.exists()), [])
        named = [str(path.relative_to(ROOT)) for path in files
                 if path.name.startswith('emaki-release.') or path.suffix == '.key']
        self.assertEqual(named, [])

    def test_the_detector_finds_pem_and_der_keys_under_any_name(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plain = openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048')
            ec = openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256')
            files = {
                'notes.txt': b'before\n' + plain + b'after\n',
                'locked.md': openssl('pkcs8', '-topk8', '-v2', 'aes-256-cbc', '-passout', 'pass:x', data=plain),
                'old.pem': openssl('pkey', '-traditional', '-aes256', '-passout', 'pass:x', data=plain),
                'backup.der': openssl('pkey', '-outform', 'DER', data=plain),
                'data.bin': openssl('pkey', '-outform', 'DER', '-traditional', data=ec),
                'archive.dat': openssl('pkcs8', '-topk8', '-v2', 'aes-256-cbc', '-passout', 'pass:x', '-outform',
                                       'DER', data=plain),
                # Not keys: a made-up fixture body, a certificate (PEM and DER), a public key.
                'fixture.txt': b'-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAzorkSYNTHbody1234\n'
                               b'-----END RSA PRIVATE KEY-----\n',
                'public.pem': openssl('pkey', '-pubout', data=plain),
            }
            subprocess.run(['openssl', 'req', '-x509', '-key', '/dev/stdin', '-subj', '/CN=t', '-days', '1',
                            '-out', str(base / 'cert.pem')], input=plain, check=True, capture_output=True)
            files['cert.der'] = openssl('x509', '-in', str(base / 'cert.pem'), '-outform', 'DER')
            for name, data in files.items():
                (base / name).write_bytes(data)
            found = {Path(path).name for path, _ in private_keys.scan([base])}
            self.assertEqual(found, {'notes.txt', 'locked.md', 'old.pem', 'backup.der', 'data.bin', 'archive.dat'})
            result = subprocess.run(['python3', '-I', str(ROOT / 'packaging/private_keys.py'), str(base)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('backup.der: DER private key', result.stdout)
            self.assertIn('archive.dat: DER private key', result.stdout)

    def test_loosely_written_pem_short_der_and_other_tools_keys_are_found(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plain = openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048')
            lines = plain.decode().splitlines()
            files = {
                # OpenSSL still reads these: blanks after the delimiters, CRLF, tabs, an indented body.
                'space.txt': ('\n'.join([lines[0] + ' '] + lines[1:]) + '\n').encode(),
                'crlf.txt': ('\r\n'.join(lines) + '\r\n').encode(),
                'tabs.txt': ('\n'.join([lines[0] + '\t'] + lines[1:-1] + [lines[-1] + '  ']) + '\n').encode(),
                # Ed25519 and X25519 PKCS#8 DER keys are 48 bytes.
                'ed.bin': openssl('genpkey', '-algorithm', 'ED25519', '-outform', 'DER'),
                'x.bin': openssl('genpkey', '-algorithm', 'X25519', '-outform', 'DER'),
            }
            self.assertEqual(len(files['ed.bin']), 48)
            expected = set(files)
            if shutil.which('ssh-keygen'):
                for name, passphrase in (('id_test', ''), ('id_locked', 'x')):
                    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', passphrase, '-C', '', '-f',
                                    str(base / name)], check=True, capture_output=True)
                    (base / f'{name}.pub').unlink()
                    expected.add(name)
                    # R9K5: the same key after an editor or a paste: CRLF, trailing spaces, an indent.
                    key = (base / name).read_bytes()
                    for suffix, changed in (('crlf.txt', key.replace(b'\n', b'\r\n')),
                                            ('spaces.txt', key.replace(b'\n', b'  \n')),
                                            ('indent.md', b'\n'.join(b'    ' + line for line in key.split(b'\n')))):
                        files[f'{name}-{suffix}'] = changed
                        expected.add(f'{name}-{suffix}')
            if shutil.which('gpg'):
                home = base / 'gnupg'
                home.mkdir(mode=0o700)
                gpg = ['gpg', '--homedir', str(home), '--batch', '--pinentry-mode', 'loopback', '--passphrase', '']
                subprocess.run([*gpg, '--quick-gen-key', 'Test <test@example.org>', 'ed25519', 'sign', '1d'],
                               check=True, capture_output=True)
                files['secret.asc'] = subprocess.run([*gpg, '--armor', '--export-secret-keys'], check=True,
                                                     capture_output=True).stdout
                expected.add('secret.asc')
                files['public.asc'] = subprocess.run([*gpg, '--armor', '--export'], check=True,
                                                     capture_output=True).stdout
                # The same secret packets under other armor labels, and as binary OpenPGP; gpg decides.
                armored = files['secret.asc']
                for name, label in (('relabelled.asc', b'PGP SECRET KEY BLOCK'),
                                    ('mislabelled.asc', b'PGP PUBLIC KEY BLOCK'), ('message.asc', b'PGP MESSAGE')):
                    files[name] = armored.replace(b'PGP PRIVATE KEY BLOCK', label)
                    expected.add(name)
                files['keyring.gpg'] = subprocess.run([*gpg, '--export-secret-keys'], check=True,
                                                      capture_output=True).stdout
                expected.add('keyring.gpg')
                files['pubring.gpg'] = subprocess.run([*gpg, '--export'], check=True, capture_output=True).stdout
                # The reviewer's constructions (R9K4): gpg still reads the secret packet in each.
                binary = files['keyring.gpg']
                prefixed = {
                    'image.png': b'\x89PNG' + b'\0' * (0x504e - 1) + binary,   # looks like a PNG
                    'trust.gpg': b'\xcc\x00' + binary,                         # an empty trust packet first
                    'tag16.bin': b'\xd0\x00' + binary, 'tag17.bin': b'\xd1\x00' + binary,
                    'tag61.bin': b'\xfd\x00' + binary, 'tag63.bin': b'\xff\x00' + binary,
                }
                public_label = armored.replace(b'PGP PRIVATE KEY BLOCK', b'PGP PUBLIC KEY BLOCK')
                footer_at = public_label.index(b'-----END')
                prefixed.update({
                    'footer.asc': public_label.replace(b'-----END PGP PUBLIC KEY BLOCK-----',
                                                       b'-----END PUBLIC KEY BLOCK-----'),
                    'tab.asc': public_label.replace(b'-----END PGP PUBLIC KEY BLOCK-----',
                                                    b'-----END PGP\tPUBLIC KEY BLOCK-----'),
                    'nofooter.asc': public_label[:footer_at],
                    'trailing.asc': public_label + b'and some text after it\n',
                })
                for name, data in prefixed.items():
                    files[name] = data
                    expected.add(name)
                subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], capture_output=True)
                shutil.rmtree(home)
            for name, data in files.items():
                (base / name).write_bytes(data)
            subprocess.run(['openssl', 'pkey', '-in', str(base / 'space.txt'), '-noout'], check=True,
                           capture_output=True)
            found = {Path(path).name for path, _ in private_keys.scan([base])}
            self.assertEqual(found, expected)


if __name__ == '__main__':
    unittest.main()
