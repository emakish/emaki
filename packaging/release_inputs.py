#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check release prerequisites and selective published package source records."""
import argparse
import ast
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys

from source_archive import PACKAGES, public_excluded, public_policy, run


def check_alongside(repo):
    """Read the committed build inputs; uncommitted acceptance cannot authorize a build."""
    source = run('git', '-C', str(repo), 'show',
                 'HEAD:installer/emaki_installer/constants.py')
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError('alongside release switch is not valid Python') from error
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'ALONGSIDE'
                           for target in node.targets)]
    bindings = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                and node.id == 'ALONGSIDE' and isinstance(node.ctx, ast.Store)]
    if (len(assignments) != 1 or len(bindings) != 1
            or not isinstance(assignments[0].value, ast.Constant)
            or type(assignments[0].value.value) is not bool):
        raise ValueError('alongside release switch must be a literal boolean')
    if assignments[0].value.value is False:
        return
    try:
        acceptance = run('git', '-C', str(repo), 'show', 'HEAD:docs/hardware-acceptance.md')
    except subprocess.CalledProcessError as error:
        raise ValueError('alongside requires recorded real-Windows acceptance') from error
    rows = [line.strip().strip('|').split('|') for line in acceptance.splitlines()
            if line.strip().startswith('| H-W-ALONGSIDE |')]
    if len(rows) == 1 and len(rows[0]) == 6:
        _, result, machine, day, iso, evidence = [cell.strip() for cell in rows[0]]
        try:
            recorded = date.fromisoformat(day)
        except ValueError:
            recorded = None
        pending = {'', 'pending', 'not tested', 'none', 'n/a', 'tbd', '—', '-'}
        if (result == 'PASS' and recorded and recorded.isoformat() == day
                and recorded <= date.today()
                and re.fullmatch(r'[0-9a-f]{64}', iso) and len(set(iso)) > 1
                and machine.lower() not in pending and evidence.lower() not in pending
                and not re.search(r'placeholder|synthetic|\bvm\b', machine + ' ' + evidence, re.I)):
            return
    raise ValueError('alongside requires a PASS real-Windows acceptance row '
                     '(H-W-ALONGSIDE) in docs/hardware-acceptance.md')


def changed_inputs(repo, name, old, new):
    changed = run('git', '-C', str(repo), 'diff', '--no-renames', '--name-only',
                  '-z', old, new, '--').split('\0')
    recipe = f'packaging/{name}/'
    if name == 'emaki-config':
        policies = [public_policy(repo, commit)[:2] for commit in (old, new)]
        return any(path and (path.startswith(recipe) or path == 'scripts/make-public.sh'
                   or path.startswith('scripts/public/')
                   or any(not public_excluded(path, *policy) for policy in policies))
                   for path in changed)
    # Keep shared payload inputs in step with each package's prepare() archive.
    shared = {
        'emaki-nvidia': {'LICENSE', 'installer/emaki_installer/graphics.py'},
        'emaki-installer': {'LICENSE'},
        'emaki-sudo-key': {'scripts/emaki-sudo-turn', 'sudo/turn-reporting.md'},
    }.get(name, set())
    return any(path.startswith(recipe) or path in shared or
               (name == 'emaki-installer' and path.startswith('installer/')) or
               (name == 'emaki-sudo-key' and path.startswith('sudo/integrations/'))
               for path in changed if path)


def recipe_version(repo, name):
    fields = {}
    for line in run('makepkg', '--printsrcinfo', cwd=repo / 'packaging' / name).splitlines():
        key, separator, value = line.strip().partition(' = ')
        if separator and key in ('epoch', 'pkgver', 'pkgrel'):
            fields[key] = value
    if not fields.get('pkgver') or not fields.get('pkgrel'):
        raise ValueError(f'{name}: recipe has no version')
    epoch = fields.get('epoch', '0')
    return (epoch + ':' if epoch != '0' else '') + fields['pkgver'] + '-' + fields['pkgrel']


def check(repo, metadata, selected):
    check_alongside(repo)
    if not selected:
        raise ValueError('--published-sources requires explicit --only selections')
    if any(name not in PACKAGES for name in selected):
        raise ValueError('unknown selected package')
    if not isinstance(metadata, dict):
        raise ValueError('invalid published SOURCES.json')
    records = metadata.get('packages')
    if metadata.get('format') != 1 or not isinstance(records, dict):
        raise ValueError('invalid published SOURCES.json')
    if not all(name in records for name in ('emaki-config', 'emaki-installer')):
        raise ValueError('published SOURCES.json lacks checkout package records')
    head = run('git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD^{commit}').strip()
    errors = []
    for name, record in sorted(records.items()):
        if name not in PACKAGES or not isinstance(record, dict):
            raise ValueError('invalid published package source record')
        # Merged snapshots retain each package's own build commit. Build manifests also
        # support a common commit, but it must never override a per-package record.
        old = record.get('commit', metadata.get('commit'))
        if not isinstance(old, str) or not re.fullmatch('[0-9a-f]{40}', old):
            raise ValueError(f'{name}: missing or invalid published source commit')
        run('git', '-C', str(repo), 'cat-file', '-e', old + '^{commit}')
        version = record.get('version')
        if not isinstance(version, str) or not version:
            raise ValueError(f'{name}: missing published version')
        changed = changed_inputs(repo, name, old, head)
        if changed and name not in selected:
            errors.append(f'{name}: archived inputs changed; add --only {name}')
        if changed or name in selected:
            current = recipe_version(repo, name)
            if int(run('vercmp', current, version).strip()) <= 0:
                errors.append(f'{name}: recipe version {current} has already shipped ({version}); '
                              'increase pkgrel before building')
    if errors:
        raise ValueError('\n'.join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--published-sources', type=Path)
    parser.add_argument('--only', action='append', default=[])
    args = parser.parse_args()
    try:
        if args.published_sources:
            check(args.repo, json.loads(args.published_sources.read_text()), set(args.only))
        else:
            check_alongside(args.repo)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'ERROR: release input check: {error}\n')
    print('Release input check passed.')


if __name__ == '__main__':
    main()
