#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find real private keys in a tree or in files: the one detector the tests and both public
exports (packaging/source_archive.py checkout, scripts/make-public.sh) refuse on.

Scope: a second line of defence against a key that got into the tree by accident (the release key
lives outside every checkout, and the key scripts refuse a directory inside one). It does not guess
formats; the real readers decide on every candidate:
- every `-----BEGIN` line of a file starts a candidate that runs to the end of the file (any label,
  any or no footer, trailing text allowed); it goes, as written and with CR line ends and blanks
  around every line undone, to OpenSSL, ssh-keygen and gpg;
- every file that is not valid UTF-8 text goes whole to gpg --list-packets and to OpenSSL as DER;
  a file that starts like DER (0x30) does too, even when it happens to be valid UTF-8.
A candidate is a key when OpenSSL or ssh-keygen read a private key (a passphrase prompt counts) or
gpg lists a secret key or secret subkey packet, also after errors in earlier packets. Not covered:
a key inside an encrypted container (an encrypted OpenPGP message, an archive), a DER key embedded
in the middle of another file, and formats none of these tools read.

Usage: private_keys.py PATH...   prints `PATH: what` per key and exits 1 when any is found.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

LOCKED = ('bad decrypt', 'maybe wrong password', 'unable to get passphrase', 'interrupted or cancelled',
          'bad password read')
BEGIN = b'-----BEGIN'


def openssl_key(data, form):
    """True when OpenSSL reads data as a private key, or as one that needs a passphrase."""
    result = subprocess.run(['openssl', 'pkey', '-inform', form, '-noout', '-passin', 'pass:'], input=data,
                            capture_output=True, timeout=60)
    if result.returncode == 0:
        return True
    errors = result.stderr.decode(errors='replace')
    return any(word in errors for word in LOCKED)


def with_file(data, check):
    with tempfile.TemporaryDirectory(prefix='emaki-key-scan-') as directory:
        path = Path(directory) / 'candidate'
        path.write_bytes(data)
        path.chmod(0o600)
        return check(Path(directory), path)


def ssh_key(data):
    """True when ssh-keygen reads a private key (a passphrase prompt counts)."""
    if not shutil.which('ssh-keygen'):
        return False

    def check(_, path):
        result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(path)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        return result.returncode == 0 or b'incorrect passphrase' in result.stderr
    return with_file(data, check)


def gpg_secret(data):
    """True when gpg lists a secret key or secret subkey packet in data (armored or binary)."""
    if not shutil.which('gpg'):
        return False

    def check(directory, path):
        home = directory / 'gnupg'
        home.mkdir(mode=0o700)
        result = subprocess.run(['gpg', '--homedir', str(home), '--batch', '--no-tty', '--no-autostart',
                                 '--pinentry-mode', 'cancel', '--list-packets', str(path)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=120, env=dict(os.environ, LC_ALL='C'))
        return b':secret key packet:' in result.stdout or b':secret sub key packet:' in result.stdout
    return with_file(data, check)


def trimmed(data):
    """The same text after the usual accidents are undone: CR line ends (CRLF or lone CR) become LF, and
    blanks (spaces, tabs) before and after every line go. Every tool sees this form as well."""
    lines = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n').split(b'\n')
    return b'\n'.join(line.strip(b' \t\f\v') for line in lines)


def keys_in(data):
    found = []
    try:
        data.decode('utf-8')
        text = True
    except UnicodeDecodeError:
        text = False
    if not text or data[:1] == b'\x30':
        if gpg_secret(data):
            found.append('OpenPGP secret key')
        if openssl_key(data, 'DER'):
            found.append('DER private key')
    start = data.find(BEGIN)
    while start != -1:
        candidate = data[start:]
        label = candidate.split(b'\n', 1)[0].strip().decode(errors='replace')
        variants = [candidate]
        if trimmed(candidate) != candidate:
            variants.append(trimmed(candidate))
        if any(openssl_key(variant, 'PEM') or ssh_key(variant) or gpg_secret(variant) for variant in variants):
            found.append(f'key in the block starting "{label}"')
        start = data.find(BEGIN, start + len(BEGIN))
    return found


def files_of(paths):
    for path in map(Path, paths):
        if path.is_dir():
            yield from sorted(p for p in path.rglob('*') if '.git' not in p.relative_to(path).parts
                              and p.is_file() and not p.is_symlink())
        elif path.is_file() and not path.is_symlink():
            yield path


def inspect(file):
    try:
        data = file.read_bytes()
    except OSError as error:
        return [(str(file), f'unreadable: {error.strerror}')]
    return [(str(file), what) for what in keys_in(data)]


def scan(paths):
    """[(path, what)] for every private key in the given files and directory trees (links not followed)."""
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        return [finding for findings in pool.map(inspect, files_of(paths)) for finding in findings]


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: private_keys.py PATH...')
    findings = scan(sys.argv[1:])
    for path, what in findings:
        print(f'{path}: {what}')
    sys.exit(1 if findings else 0)


if __name__ == '__main__':
    main()
