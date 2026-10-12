#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-signed exceptions for release gates (docs/mirror.md, "Exceptions").

Every refusal of publish.py carries a stable gate id. A gate in EXCEPTABLE refuses unless an
exception record names it, covers exactly the artifacts the gate looks at (file names and
SHA-256), has not expired or been revoked, and carries a detached signature by the release key
that verifies against the public keyring in git. Every other gate is in NEVER: no record passes
it. A record is created with `publish.sh exception new`; gpg asks for the key's passphrase.
"""
import datetime
import json
from pathlib import Path
import re
import secrets

FORMAT = 1
MAX_DAYS = 7
CLOCK_SKEW = datetime.timedelta(minutes=5)

# Checks of evidence that a release is ready. The owner may take the risk for one exact set of
# artifacts, for at most MAX_DAYS. The artifacts are what the gate's refusal lists.
EXCEPTABLE = {
    'build.alongside-acceptance': 'alongside lacks real-Windows acceptance (artifact: installer/emaki_installer/constants.py)',
    'publish.tree-clean': 'publish from a source tree that is not clean (artifacts: the candidate packages)',
    'release.transactions': 'installer Minimal/Rich install or full upgrade does not resolve '
                            '(artifacts: the candidate packages, or the MANIFEST being promoted or copied)',
    'stamp.missing': 'no acceptance stamp (artifacts: the MANIFEST, or the image)',
    'stamp.manifest': 'the acceptance stamp names another MANIFEST (artifacts: the MANIFEST, or the image)',
    'stamp.runs': 'the acceptance stamp lacks a required upgrade run (artifacts: the MANIFEST, or the image)',
    'stamp.bridge': 'the bridge stamp lacks T2 by the old address (artifacts: the MANIFEST)',
    'stamp.untimed': 'the acceptance stamp has no run time (artifacts: the MANIFEST, or the image)',
    'stamp.age': 'the acceptance stamp is older than 24 hours (artifacts: the MANIFEST, or the image)',
    'iso.transactions': "the image's offline repository does not resolve (artifacts: the image)",
    'iso.stable-packages': 'stable does not serve some Emaki packages of the image (artifacts: the image)',
    'iso.emaki-source-record': 'stable has no source record for an Emaki package of the image '
                               '(artifacts: the image)',
    'github.restore-sources': 'restore a saved GitHub database without verified sources '
                              '(artifacts: the saved emaki.db)',
}

# Never passed by an exception, with the reason.
NEVER = {
    'signature.package': 'package signatures by a key in packaging/emaki-keyring',
    'signature.database': 'database signatures, and pacman syncing with SigLevel = Required',
    'signature.image': 'the image signature by a key in packaging/emaki-keyring',
    'signing.stored-passphrase': 'the release key passphrase is typed, never stored',
    'immutable.name': 'a published file name is never replaced: machines keep what they have',
    'immutable.version': 'a released package version never gets other bytes: pacman decides by name and version',
    'immutable.iso': 'a published image name is never replaced',
    'publish.downgrade': 'repo-add --prevent-downgrade refuses a lower version as well',
    'promote.first-spent': 'promote --first exists once; the stamped promote takes exceptions',
    'state.pointer': 'the bucket state is not what the publisher can work from',
    'state.pointer-moved': 'another publisher moved the pointer during this run',
    'state.journal': 'an interrupted run must be finished with its own inputs',
    'state.lock': 'another publisher holds the lock',
    'state.no-snapshot': 'there is nothing to work from',
    'snapshot.missing': 'a snapshot file is missing',
    'snapshot.manifest': 'a snapshot file differs from its MANIFEST',
    'snapshot.build': 'the built database differs from the packages',
    'snapshot.package': 'a mirror package differs from its MANIFEST',
    'verify.package-download': 'read-back: the public download differs from the upload',
    'verify.snapshot': 'read-back: the snapshot pacman reads differs from the one built',
    'verify.channel': 'read-back: the channel does not serve what was published',
    'verify.iso': 'read-back: the published image differs from the local one',
    'verify.iso-sources': 'read-back: the published image sources differ',
    'sources.upload': 'a source archive differs from its record',
    'sources.snapshot': 'a source archive named by the snapshot is not verified or not public',
    'iso.sources': 'image source coverage; uncollected sources are declared in MISSING-SOURCES.json',
    'iso.closure': 'the image package list is malformed or inconsistent',
    'iso.checksum': 'the image checksum file differs from the image',
    'iso.download-worker': 'without the download Worker a browser cannot resume the image',
    'iso-sources.published': 'the image is not published',
    'iso-sources.validate': 'image source additions must keep every published record',
    'iso-sources.route': 'without the source pointer route the addition is not atomic',
    'input.packages': 'the candidate directory is malformed',
    'provenance.buildinfo': 'BUILDINFO binds the build to a commit and its hashes',
    'provenance.sources': 'SOURCES.json binds every package to its source archive',
    'acceptance.machines': 'the machines file says which upgrade starts exist',
    'stamp-write.results': 'a stamp is only written from green, recent, real results',
    'withdraw.target': 'withdraw returns only to a snapshot the channel served',
    'github.restore-none': 'there is no saved database to restore',
    'github.db-signature': 'the old address never serves a database signature',
    'release.arch-sync': 'the Arch databases for the release check could not be read',
    'net.get': 'network error', 'net.head': 'network error', 'net.range': 'network error',
    'net.etag': 'network error',
    'cli.flat-layout': 'command line', 'cli.backend': 'command line', 'cli.arch-dbs': 'command line',
    'cli.publish-channel': 'command line', 'cli.github-tag': 'command line', 'cli.iso': 'command line',
    'exception.invalid': 'exception records themselves',
}

ID_RE = re.compile(r'^\d{8}T\d{6}Z-[a-z0-9.-]+-[0-9a-f]{4}$')
FINGERPRINT = re.compile('[0-9A-F]{40}')
FIELDS = ('format', 'id', 'gate', 'scope', 'reason', 'created', 'expires', 'who')


def stamp(moment):
    return moment.astimezone(datetime.timezone.utc).isoformat(timespec='seconds')


def new_record(gate, scope, reason, who, days, now):
    if gate not in EXCEPTABLE:
        raise ValueError(f'gate {gate} can never be passed by an exception'
                         if gate in NEVER else f'unknown gate {gate}')
    if not reason.strip():
        raise ValueError('an exception needs a --reason')
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f'an exception lasts 1 to {MAX_DAYS} days')
    if not scope:
        raise ValueError('an exception covers at least one artifact')
    if not FINGERPRINT.fullmatch(who):
        raise ValueError('the signing key must be named by its full fingerprint (--key)')
    return {'format': FORMAT, 'id': f'{now:%Y%m%dT%H%M%SZ}-{gate}-{secrets.token_hex(2)}', 'gate': gate,
            'scope': dict(sorted(scope.items())), 'reason': reason.strip(), 'created': stamp(now),
            'expires': stamp(now + datetime.timedelta(days=days)), 'who': who}


def encode(record):
    return (json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + '\n').encode()


def paths(directory, exception_id):
    base = Path(directory) / exception_id
    return Path(f'{base}.json'), Path(f'{base}.json.sig'), Path(f'{base}.revoked')


def load(directory):
    """(id, record or None, why it cannot be read) for every record file, oldest first."""
    found = []
    for path in sorted(Path(directory).glob('*.json')) if Path(directory).is_dir() else ():
        exception_id = path.name[:-len('.json')]
        try:
            record = json.loads(path.read_bytes())
            if not isinstance(record, dict) or sorted(record) != sorted(FIELDS):
                raise ValueError('wrong fields')
            if record['format'] != FORMAT or record['id'] != exception_id or not ID_RE.match(exception_id):
                raise ValueError('wrong format or id')
            if not all(isinstance(record[key], str) for key in ('gate', 'reason', 'created', 'expires')):
                raise ValueError('gate, reason and dates must be strings')
            # A failed verification names no signer (None): `who` must be a fingerprint it can never equal.
            if not isinstance(record['who'], str) or not FINGERPRINT.fullmatch(record['who']):
                raise ValueError('who must be the full fingerprint of the signing key')
            if not isinstance(record['scope'], dict) or not all(
                    isinstance(name, str) and isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest)
                    for name, digest in record['scope'].items()):
                raise ValueError('scope must map file names to SHA-256')
            for key in ('created', 'expires'):
                record[key + '_at'] = datetime.datetime.fromisoformat(record[key])
            found.append((exception_id, record, None))
        except (OSError, ValueError, KeyError, TypeError) as error:
            found.append((exception_id, None, f'unreadable record ({error})'))
    return found


def problem(directory, record, now, signed_by):
    """Why this record passes nothing now, or None. signed_by(data, signature) returns the
    trusted, unrevoked primary fingerprint behind a good signature, or None."""
    record_path, signature, revoked = paths(directory, record['id'])
    if record['gate'] not in EXCEPTABLE:
        return f'gate {record["gate"]} can never be passed by an exception'
    if not signature.is_file():
        return 'unsigned (no .sig)'
    signer = signed_by(record_path, signature)
    if signer is None or signer != record['who']:
        return 'its signature does not verify against the release keyring (tampered, or another key)'
    if revoked.exists():
        return 'revoked'
    created, expires = record['created_at'], record['expires_at']
    if expires - created > datetime.timedelta(days=MAX_DAYS) or expires <= created:
        return f'its lifetime is not 1 to {MAX_DAYS} days'
    if created > now + CLOCK_SKEW:
        return 'created in the future'
    if now >= expires:
        return f'expired {record["expires"]}'
    return None


def scope_problem(record, scope):
    """Why the record's exact scope differs from the artifacts at the gate, or None."""
    wanted, have = record['scope'], scope
    notes = [f'{name}: not at this gate' for name in sorted(set(wanted) - set(have))]
    notes += [f'{name}: not covered' for name in sorted(set(have) - set(wanted))]
    notes += [f'{name}: sha256 {have[name][:12]}…, the exception covers {wanted[name][:12]}…'
              for name in sorted(set(wanted) & set(have)) if wanted[name] != have[name]]
    return '; '.join(notes) or None


def find(directory, gate, scope, now, signed_by):
    """The valid record for this gate and exactly these artifacts, and the notes on records
    for this gate that do not apply (shown with the refusal)."""
    notes = []
    for exception_id, record, error in load(directory):
        if record is None:
            notes.append(f'{exception_id}: {error}')
            continue
        if record['gate'] != gate:
            continue
        why = scope_problem(record, scope) or problem(directory, record, now, signed_by)
        if why is None:
            return record, notes
        notes.append(f'{exception_id}: {why}')
    return None, notes
