#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve Rust dependency notices from locked, locally available sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib


NOTICE = re.compile(r"^(licen[cs]e|copying|copyright|notice|authors)(?:[._-].*)?$", re.I)
LICENCE = re.compile(r"^(licen[cs]e|copying)(?:[._-].*)?$", re.I)


def manifest(path):
    with path.open('rb') as stream:
        return tomllib.load(stream)


def source_files(root, vendor=None):
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders
                            if name not in {'.git', '.cache', 'target'}
                            and (vendor is None or (Path(directory) / name).resolve() != vendor.resolve()))
        for name in sorted(files):
            path = Path(directory) / name
            if name != 'THIRD-PARTY-NOTICES' and path.is_file():
                yield path


def standard_texts(index):
    """Find complete standard terms, retaining the donor's notices unchanged."""
    found = {}
    for roots in index.values():
        for root in roots:
            metadata = manifest(root / 'Cargo.toml')['package']
            for path in source_files(root):
                if not LICENCE.match(path.name):
                    continue
                text = path.read_text(encoding='utf-8')
                kind = None
                if ('mit' in path.name.lower() or metadata.get('license') == 'MIT') and all(
                        part in text for part in ('Permission is hereby granted', 'THE SOFTWARE IS PROVIDED', 'WITHOUT WARRANTY')):
                    kind = 'MIT'
                if ('apache' in path.name.lower() or metadata.get('license') == 'Apache-2.0') and all(
                        part in text for part in ('Apache License', 'Version 2.0', 'END OF TERMS AND CONDITIONS')):
                    kind = 'Apache-2.0'
                if kind and kind not in found:
                    found[kind] = (root.name, path.relative_to(root), text)
    return found


def host_sources(metadata, packages):
    """Select normal/build dependencies from Cargo's host-filtered resolution.

    These source paths come from locked cargo fetch/metadata, which verify registry
    checksums and git revisions. Unlike cargo vendor, metadata supports a target.
    """
    nodes = {node['id']: node for node in metadata['resolve']['nodes']}
    pending = list(metadata['workspace_default_members'])
    selected = set()
    while pending:
        identifier = pending.pop()
        if identifier in selected:
            continue
        selected.add(identifier)
        pending.extend(dep['pkg'] for dep in nodes[identifier]['deps']
                       if any(kind['kind'] != 'dev' for kind in dep['dep_kinds']))
    locked = {(p['name'], p['version'], p.get('source')) for p in packages}
    roots = {}
    for package in metadata['packages']:
        if package['id'] not in selected:
            continue
        key = (package['name'], package['version'], package.get('source'))
        if key not in locked:
            raise ValueError(f'{package["name"]} {package["version"]}: resolved source not in Cargo.lock')
        roots[key] = Path(package['manifest_path']).parent
        selected.remove(package['id'])
    if selected:
        raise ValueError('missing resolved package metadata')
    return roots


def render(lock, vendor, project, metadata=None):
    packages = manifest(lock)['package']
    index = {}
    resolved = host_sources(metadata, packages) if metadata is not None else None
    if resolved is not None:
        packages = [p for p in packages if (p['name'], p['version'], p.get('source')) in resolved]
        for (name, version, _source), root in resolved.items():
            index.setdefault((name, version), []).append(root)
    else:
        for path in sorted(vendor.glob('*/Cargo.toml')):
            package = manifest(path)['package']
            index.setdefault((package['name'], package['version']), []).append(path.parent)
    standards = standard_texts(index)
    local = {}
    manifests = [project / 'Cargo.toml']
    for member in manifest(project / 'Cargo.toml').get('workspace', {}).get('members', []):
        manifests.extend(p / 'Cargo.toml' for p in project.glob(member))
    for path in manifests:
        if vendor is not None and vendor.resolve() in path.resolve().parents:
            continue
        package = manifest(path).get('package', {})
        if package.get('name'):
            local[package['name']] = path.parent
    scope = ('Host normal/build dependencies resolved from Cargo.lock are listed below.'
             if resolved is not None else 'All packages recorded in Cargo.lock are listed below.')
    chunks = ['Third-party Rust notices\n' + scope + '\n']
    for package in sorted(packages, key=lambda p: (p['name'], p['version'], p.get('source', ''))):
        name, version = package['name'], package['version']
        if resolved is not None:
            root = resolved[(name, version, package.get('source'))]
        elif 'source' in package:
            matches = index.get((name, version), [])
            if 'checksum' in package:
                matches = [p for p in matches if json.loads((p / '.cargo-checksum.json').read_text()).get('package') == package['checksum']]
            if len(matches) != 1:
                raise ValueError(f'{name} {version}: expected one matching vendored source, found {len(matches)}')
            root = matches[0]
        else:
            if name not in local:
                raise ValueError(f'{name} {version}: missing local source')
            root = local[name]
        sources = sorted(source_files(root, vendor if 'source' not in package else None))
        files = [p for p in sources if NOTICE.match(p.name)]
        # Workspace members can inherit the project's licence text.
        if 'source' not in package and not any(LICENCE.match(p.name) for p in files):
            files += [p for p in project.iterdir() if p.is_file() and NOTICE.match(p.name)]
        metadata = manifest(root / 'Cargo.toml')['package']
        if 'source' not in package:
            inherited_metadata = manifest(project / 'Cargo.toml').get('workspace', {}).get('package', {})
            metadata = {key: inherited_metadata[key] if value == {'workspace': True} else value
                        for key, value in metadata.items()}
        declared = metadata.get('license-file')
        borrowed = []
        if declared:
            path = (root / declared).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError(f'{name} {version}: invalid license-file')
            files.append(path)
        embedded = []
        if not any(LICENCE.match(p.name) for p in files):
            for path in sources:
                try:
                    text = path.read_text(encoding='utf-8')
                except UnicodeError:
                    continue
                if (all(part in text for part in ('Permission is hereby granted', 'THE SOFTWARE IS PROVIDED', 'WITHOUT WARRANTY'))
                        or all(part in text for part in ('Apache License', 'Version 2.0', 'END OF TERMS AND CONDITIONS'))):
                    embedded.append(path)
            files.extend(embedded)
        if not embedded and not any(LICENCE.match(p.name) or (declared and p == (root / declared).resolve()) for p in files):
            # This published crate omits the workspace GPL text. Its original
            # manifest inherits license.workspace; normalized metadata declares
            # GPL-3.0-or-later. Supply the identical complete GPL, for this exact
            # locked publication only, and retain its upstream author metadata.
            inherited = root / 'Cargo.toml.orig'
            supplement = project / 'LICENSE'
            if (name == 'niri-ipc' and version == '26.4.0'
                    and package.get('checksum') == '6a4adbddf4037ce047854d36a60b5bf80a7990b8db2f0a0b9ede7534b0bae09a'
                    and metadata.get('license') == 'GPL-3.0-or-later'
                    and inherited.is_file()
                    and manifest(inherited).get('package', {}).get('license') == {'workspace': True}
                    and supplement.is_file()
                    and hashlib.sha256(supplement.read_bytes()).hexdigest() == 'fb981668c18a279e285fc4d83fba1e836cc84dd4daa73c9697d3cfd2d8aca6e0'):
                files.append(supplement)
                chunks.append('\nThe following niri-ipc publication omits its workspace GPL text; '
                              'the complete GPL text is supplied from the project licence.\n')
            else:
                # Some crate archives omit repository licence files. Only these
                # standard expressions can reuse complete terms from the vendor
                # set; preserve both alternatives and donor provenance. Authors
                # are attribution evidence, never invented copyright claims.
                expression = metadata.get('license', '')
                terms = {'MIT': ['MIT'], 'Apache-2.0': ['Apache-2.0'],
                         'MIT OR Apache-2.0': ['MIT', 'Apache-2.0'],
                         'MIT/Apache-2.0': ['MIT', 'Apache-2.0'],
                         # Choose the unexceptioned alternatives offered by OR;
                         # do not claim to supply the LLVM exception's terms.
                         'Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT': ['Apache-2.0', 'MIT'],
                         'Apache-2.0 OR MIT': ['Apache-2.0', 'MIT']}.get(expression)
                if not terms or not all(term in standards for term in terms):
                    raise ValueError(f'{name} {version}: no licence text in source')
                borrowed = [(term, standards[term]) for term in terms]
        chunks.append(f'\n===== {name} {version} =====\nSource: {package.get("source", "local workspace")}\nLicence: {metadata.get("license", "see licence text")}\n')
        if metadata.get('authors'):
            chunks.append('Authors: ' + ', '.join(metadata['authors']) + '\n')
        for path in sorted(set(files)):
            text = path.read_text(encoding='utf-8')
            if not text.strip():
                raise ValueError(f'{name} {version}: empty notice {path.name}')
            relative = path.relative_to(root) if path.is_relative_to(root) else path.name
            chunks.append(f'\n--- {relative} ---\n{text.rstrip()}\n')
        for term, (donor, relative, text) in borrowed:
            origin = 'Cargo source' if resolved is not None else 'vendor'
            chunks.append(f'\nDeclared {term} terms (this crate omits its licence file).\n'
                          f'Complete standard text preserved from {origin}/{donor}/{relative}; '
                          'donor attribution below belongs to that source.\n' + text.rstrip() + '\n')
        # Some upstreams put additional copyright holders only in source headers.
        copyrights = set()
        for path in sources:
            if path.is_file() and path not in files:
                try:
                    content = path.read_text(encoding='utf-8')
                except UnicodeError:
                    continue
                copyrights.update(line.strip() for line in content.splitlines() if re.search(r'copyright|©', line, re.I))
        if copyrights:
            chunks.append('\nCopyright lines from source:\n' + '\n'.join(sorted(copyrights)) + '\n')
    return ''.join(chunks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--vendor', type=Path)
    sources.add_argument('--metadata', type=Path,
                         help='locked offline cargo metadata filtered to the build host')
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata.read_text()) if args.metadata else None
        output = render(args.lock, args.vendor, args.project, metadata=metadata)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'Rust notices: {error}\n')
    args.output.write_text(output, encoding='utf-8')


if __name__ == '__main__':
    main()
