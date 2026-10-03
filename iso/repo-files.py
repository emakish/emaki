#!/usr/bin/env python3
"""Check an input repository and materialize detached signatures from sync DBs.

pacman can verify the database's embedded PGPSIG without leaving a .sig file in
its cache. repo-add -s signs a database, not its packages; never use it as a
substitute for carrying each package's original detached signature.
"""
import argparse
import base64
from pathlib import Path
import subprocess
import tarfile


def records(database):
    with tarfile.open(database, 'r:*') as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('/desc'):
                fields = archive.extractfile(member).read().decode().split('\n\n')
                yield {lines[0].strip('%'): lines[1:] for field in fields
                       if (lines := field.strip().splitlines()) and len(lines) > 1}


def check_input(repo, names):
    database = repo / 'emaki.db'
    if not database.is_file():
        raise ValueError(f'missing {database}')
    entries = {record['NAME'][0]: record for record in records(database)}
    for name in names.read_text().split():
        if name not in entries:
            raise ValueError(f'{name} absent from emaki.db')
        filename = entries[name]['FILENAME'][0]
        if Path(filename).name != filename or not filename.endswith('.pkg.tar.zst'):
            raise ValueError(f'unsupported repository filename: {filename}')
        package = repo / filename
        if not package.is_file() or not Path(str(package) + '.sig').is_file():
            raise ValueError(f'missing package or detached signature: {package}')
        subprocess.run(['pacman-key', '--verify', str(package) + '.sig', str(package)], check=True)


def signatures(cache, db):
    entries = {}
    for database in sorted((db / 'sync').glob('*.db')):
        for record in records(database):
            if 'FILENAME' in record:
                entries[record['FILENAME'][0]] = record
    packages = sorted(cache.glob('*.pkg.tar.zst'))
    if not packages:
        raise ValueError('offline closure is empty')
    for package in packages:
        signature = Path(str(package) + '.sig')
        if not signature.is_file():
            record = entries.get(package.name, {})
            if not record.get('PGPSIG'):
                raise ValueError(f'no detached or embedded package signature: {package.name}')
            signature.write_bytes(base64.b64decode(''.join(record['PGPSIG']), validate=True))
        subprocess.run(['pacman-key', '--verify', str(signature), str(package)], check=True)
    print(f'OK: {len(packages)} signed offline packages')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    source = sub.add_parser('check-input')
    source.add_argument('repo', type=Path)
    source.add_argument('names', type=Path)
    sigs = sub.add_parser('signatures')
    sigs.add_argument('cache', type=Path)
    sigs.add_argument('db', type=Path)
    args = parser.parse_args()
    if args.action == 'check-input':
        check_input(args.repo, args.names)
    else:
        signatures(args.cache, args.db)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'ERROR: {error}')
