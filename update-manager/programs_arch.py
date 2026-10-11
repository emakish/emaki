# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read repository metadata and preview full repository installation transactions."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

try:
    from emaki_paths import BINDIR
except ModuleNotFoundError:
    BINDIR = str(Path(os.defpath.split(os.pathsep)[-1]))
ENV = {'PATH': str(BINDIR) + os.pathsep + os.defpath, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9@._+\-]*\Z')


def validate_target(target):
    if not isinstance(target, str) or len(target) > 512:
        raise ValueError('[PLACEHOLDER] Select one repository program.')
    pieces = target.split('/')
    if len(pieces) != 2 or any(not NAME.fullmatch(piece) for piece in pieces):
        raise ValueError('[PLACEHOLDER] Select one repository program.')
    return pieces


def run(arguments, timeout=60):
    result = subprocess.run(arguments, capture_output=True, text=True, cwd='/', env=ENV, timeout=timeout)
    if result.returncode:
        raise RuntimeError('[PLACEHOLDER] Repository package information could not be read.')
    return result.stdout


def parse_versions(text):
    result = {}
    for line in text.splitlines():
        name, version = line.split(maxsplit=1)
        if not NAME.fullmatch(name) or not version:
            raise ValueError('[PLACEHOLDER] Invalid installed package record')
        result[name] = version
    return result


def parse_desc(text, repository):
    fields = {}
    key = None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line[1:-1]
            fields[key] = []
        elif line and key:
            fields[key].append(line)
    def field(name):
        return ' '.join(fields.get(name, []))
    def size(name):
        value = field(name)
        return int(value) if value.isascii() and value.isdigit() else None
    name = field('NAME')
    validate_target(repository + '/' + name)
    if not field('VERSION'):
        raise ValueError('[PLACEHOLDER] Missing package version')
    return {'repository': repository, 'package': name, 'version': field('VERSION'),
            'summary': field('DESC'), 'licence': field('LICENSE'),
            'downloadSize': size('CSIZE'), 'installedSize': size('ISIZE')}


def inventory():
    """Read configured sync archives without refreshing or extracting anything."""
    installed = parse_versions(run(['pacman', '-Q']))
    database = Path(run(['pacman-conf', 'DBPath']).strip())
    if not database.is_absolute():
        raise ValueError('[PLACEHOLDER] Invalid package database path')
    packages = {}
    for repository in run(['pacman-conf', '--repo-list']).splitlines():
        if not NAME.fullmatch(repository):
            raise ValueError('[PLACEHOLDER] Invalid repository name')
        with tarfile.open(database / 'sync' / (repository + '.db'), 'r:*') as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith('/desc'):
                    continue
                if member.size > 1024 * 1024:
                    raise ValueError('[PLACEHOLDER] Oversized package metadata')
                with archive.extractfile(member) as stream:
                    package = parse_desc(stream.read().decode('utf-8'), repository)
                packages[repository + '/' + package['package']] = package
    return packages, installed


def parse_plan(text):
    packages = []
    for line in text.splitlines():
        # Informational replacement messages are not package records.
        if '|' not in line:
            continue
        fields = line.split('|')
        if len(fields) != 4:
            raise ValueError('[PLACEHOLDER] Invalid transaction record')
        repository, name, version, size = fields
        validate_target(repository + '/' + name)
        if not version:
            raise ValueError('[PLACEHOLDER] Missing transaction version')
        packages.append({'repository': repository, 'package': name, 'version': version,
                         'downloadSize': int(size) if size.isascii() and size.isdigit() else None})
    return sorted(packages, key=lambda item: (item['repository'], item['package']))


def install_plan(target):
    validate_target(target)
    packages, installed = inventory()
    if target not in packages:
        return {'ok': False, 'code': 'unavailable', 'action': 'install', 'kind': 'arch', 'target': target,
                'message': '[PLACEHOLDER] This repository program is no longer available.'}
    planned = parse_plan(run(['pacman', '-Sup', '--needed', '--noconfirm',
                              '--print-format', '%r|%n|%v|%s', target]))
    state = {'target': target, 'candidate': packages[target], 'packages': planned, 'installed': installed}
    token = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'ok': True, 'code': 'ready', 'action': 'install', 'kind': 'arch', 'target': target,
            'packages': planned, 'token': token, 'fullUpgrade': True,
            'message': '[PLACEHOLDER] Installing also updates all repository packages. The upgrade checks fresh metadata; the final package list and sizes may change.'}
