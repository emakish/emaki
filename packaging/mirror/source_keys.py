# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retrieve recipe-authorized full public keys into a disposable verification ring."""
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile


SERVERS = ('https://keys.openpgp.org/vks/v1/by-fingerprint/{fingerprint}',
           'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x{fingerprint}')


def public_fingerprints(text):
    """Map each full primary and subkey fingerprint to its public primary."""
    result = {}
    primary = None
    pending = None
    for line in text.splitlines():
        parts = line.split(':')
        if parts[0] in ('sec', 'ssb'):
            raise ValueError('secret key material is not a source verification key')
        if parts[0] in ('pub', 'sub'):
            if pending is not None:
                raise ValueError('public key fingerprint is missing')
            pending = parts[0]
            if pending == 'pub':
                primary = None
            elif primary is None:
                raise ValueError('public subkey has no primary fingerprint')
        elif parts[0] == 'fpr' and pending is not None:
            if len(parts) < 10 or not re.fullmatch(r'[0-9A-F]{40}|[0-9A-F]{64}', parts[9]):
                raise ValueError('invalid public key fingerprint')
            fingerprint = parts[9]
            if pending == 'pub':
                primary = fingerprint
            if fingerprint in result and result[fingerprint] != primary:
                raise ValueError('public key fingerprint has conflicting primaries')
            result[fingerprint] = primary
            pending = None
    if pending is not None:
        raise ValueError('public key fingerprint is missing')
    return result


def primary_fingerprints(text):
    return set(public_fingerprints(text).values())


def prepare(fingerprints, recipe, env, run, *, refresh=False):
    """Never query short IDs or import a fetched key before inspecting its full key identity."""
    home = Path(env['GNUPGHOME'])
    if not home.is_dir() or home.resolve() == (Path.home() / '.gnupg').resolve():
        raise ValueError('source keys require a disposable verification keyring')
    allowed = set(fingerprints)
    if any(not re.fullmatch(r'[0-9A-F]{40}|[0-9A-F]{64}', key) for key in allowed):
        raise ValueError('validpgpkeys requires full uppercase fingerprints')
    if not allowed:
        return []
    command = ('gpg', '--batch', '--no-autostart', '--homedir', str(home),
               '--with-subkey-fingerprint')
    present = set(public_fingerprints(run(*command, '--with-colons', '--list-keys', env=env)))
    evidence = []
    for fingerprint in sorted(allowed if refresh else allowed - present):
        target = home / (fingerprint + '.asc')
        failures = []
        for server in SERVERS:
            url = server.format(fingerprint=fingerprint)
            try:
                effective_url = run('curl', '-q', '--fail', '--location', '--silent', '--show-error',
                    '--connect-timeout', '10', '--max-time', '60', '--max-filesize', '5242880',
                    '--user-agent', 'emaki-publish', '--write-out', '%{url_effective}',
                    '--output', str(target), url).strip()
                inspected = public_fingerprints(run(
                    *command, '--with-colons', '--import-options', 'show-only',
                    '--import', str(target), env=env))
                if fingerprint not in inspected or len(set(inspected.values())) != 1:
                    raise ValueError('retrieved public key primary fingerprint differs from recipe or subkey is absent')
                run(*command, '--import', str(target), env=env)
                imported = public_fingerprints(run(*command, '--with-colons', '--list-keys', env=env))
                if fingerprint not in imported:
                    raise ValueError('retrieved public key was not usable after import')
            except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                failures.append(str(error))
                target.unlink(missing_ok=True)
                continue
            original = Path(recipe) / 'keys' / 'pgp' / (fingerprint + '.asc')
            keyfile = original.with_name(fingerprint + '.refreshed.asc')
            keyfile.parent.mkdir(parents=True, exist_ok=True)
            keyfile.write_bytes(target.read_bytes())
            item = {'fingerprint': fingerprint, 'url': url, 'effective_url': effective_url,
                    'file': keyfile.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
            if original.exists():
                item['recipe_key_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
            evidence.append(item)
            target.unlink()
            break
        else:
            raise ValueError('cannot retrieve exact source key ' + fingerprint + ': ' + '; '.join(failures))
    return evidence


def add_keys(source, target, recipe, base, evidence):
    """Makepkg includes only <fingerprint>.asc; retain verification supplements too."""
    additions = {base + '/keys/pgp/' + item['file']:
                 Path(recipe) / 'keys/pgp' / item['file'] for item in evidence}
    with tarfile.open(source, 'r:*') as incoming, tarfile.open(target, 'w') as outgoing:
        for member in incoming:
            if member.name in additions:
                raise ValueError('source archive already contains a verification supplement')
            outgoing.addfile(member, incoming.extractfile(member) if member.isfile() else None)
        for name, path in sorted(additions.items()):
            outgoing.add(path, arcname=name, recursive=False)


def validate(archive, record, info):
    for item in record.get('verification_keys', []):
        fingerprint = item.get('fingerprint', '')
        if (fingerprint not in info.get('validpgpkeys', []) or
                not re.fullmatch(r'[0-9A-F]{40}|[0-9A-F]{64}', fingerprint) or
                item.get('url') not in [server.format(fingerprint=fingerprint) for server in SERVERS]):
            raise ValueError('unreviewed verification key evidence')
        # Legacy evidence used the recipe filename; new retrievals always use a
        # separate file, including when the recipe did not ship a bundled key.
        filename = item.get('file', fingerprint + '.asc')
        if filename not in (fingerprint + '.asc', fingerprint + '.refreshed.asc'):
            raise ValueError('unreviewed verification key filename')
        name = record['base'] + '/keys/pgp/' + filename
        members = [member for member in archive.getmembers() if member.name == name]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('retrieved verification key missing from source archive')
        if hashlib.sha256(archive.extractfile(members[0]).read()).hexdigest() != item.get('sha256'):
            raise ValueError('retrieved verification key bytes changed')
        if 'recipe_key_sha256' in item:
            name = record['base'] + '/keys/pgp/' + fingerprint + '.asc'
            originals = [member for member in archive.getmembers() if member.name == name]
            if len(originals) != 1 or not originals[0].isfile():
                raise ValueError('original recipe key missing from source archive')
            if hashlib.sha256(archive.extractfile(originals[0]).read()).hexdigest() != item['recipe_key_sha256']:
                raise ValueError('original recipe key bytes changed')
