#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe-bound omissions proven absent from an exact binary package."""
import copy
import hashlib

RECIPE_SHA256 = '102d7189f3758d545c542cc1f934475da0b4eeef075d2c64905e55c2857809ee'
WIKI = 'git+https://github.com/streambinder/vpnc.wiki.git'
BINARY_SHA256 = '58258429e0dd018148d22331d6d06825c608e347186e0ab349ffb224a2d5033f'
EVIDENCE = {'source': 'git+https://github.com/streambinder/vpnc.wiki.git',
 'reason': 'Unused legacy wiki: the pinned primary has no gitlinks; install-doc installs '
           'docs/*.md, and the recipe installs .github/README.md. Every binary documentation file '
           'matches these pinned primary files byte for byte. No build or install target reads the '
           'wiki.',
 'binary': 'vpnc-1:0.5.3.r557.r241-1-x86_64.pkg.tar.zst',
 'binary_sha256': '58258429e0dd018148d22331d6d06825c608e347186e0ab349ffb224a2d5033f',
 'primary_source': 'vpnc::git+https://github.com/streambinder/vpnc#commit=95f08ac4434d59752f2447a2c568b325df4c5903',
 'primary_commit': '95f08ac4434d59752f2447a2c568b325df4c5903',
 'primary_makefile_sha256': '1e645512bc5eda96671752169736e5b7bd5e5833cb25f046dc98a71bb856b1c8',
 'primary_gitlinks': [],
 'binary_documentation': {'usr/share/doc/vpnc/README.md': {'source': '.github/README.md',
                                                           'equal': True,
                                                           'sha256': '997268d246f43b129eca550c3e0f5d2407af7563927ce91445bbc9cf03b17389'},
                          'usr/share/doc/vpnc/about.md': {'source': 'docs/about.md',
                                                          'equal': True,
                                                          'sha256': 'b922d0c66e0b5d3bf443b3dc36bf76283f86e3b192d4af931a4b355ba74f8c5a'},
                          'usr/share/doc/vpnc/faq.md': {'source': 'docs/faq.md',
                                                        'equal': True,
                                                        'sha256': 'c660ab3b8d4d97c822e7ebdffa2c875ca5ad79962a97cca1059fd99dc2dab97f'},
                          'usr/share/doc/vpnc/installation.md': {'source': 'docs/installation.md',
                                                                 'equal': True,
                                                                 'sha256': '066d76e1d01660a26f071cc75fb3d700399f4e55a32aa4cf7b4f8b60b5f16dbf'}}}


def expected_evidence(record):
    """Only the reviewed recipe and binary identity permit this omission."""
    if record.get('recipe_sha256') != RECIPE_SHA256:
        return []
    if (record.get('base'), record.get('name'), record.get('version'),
            record.get('binary_sha256')) != (
            'vpnc', 'vpnc', '1:0.5.3.r557.r241-1', BINARY_SHA256):
        raise ValueError('unused-source evidence does not match the binary')
    return [copy.deepcopy(EVIDENCE)]


def filtered_srcinfo(record, srcinfo):
    """Remove only the reviewed source and its corresponding checksum entry."""
    evidence = expected_evidence(record)
    if not evidence:
        return srcinfo, []
    lines = srcinfo.splitlines(keepends=True)
    sources = []
    sums = []
    for number, line in enumerate(lines):
        if ' = ' not in line:
            continue
        key, value = line.strip().split(' = ', 1)
        if key == 'source':
            sources.append((number, value))
        elif key == 'sha512sums':
            sums.append((number, value))
        elif key.startswith('source_') or ('sums' in key and key != 'sha512sums'):
            raise ValueError('unused-source recipe metadata has unexpected arrays')
    expected = [
        EVIDENCE['primary_source'],
        'vpnc-scripts::git+https://gitlab.com/openconnect/vpnc-scripts.git#commit=ce9e961bd0f6b867e1c7c35f78f6fb973f6ff101',
        WIKI, 'vpnc.conf', 'vpnc@.service',
    ]
    if ([value for _, value in sources] != expected or len(sums) != 5
            or [value for _, value in sums[:3]] != ['SKIP'] * 3):
        raise ValueError('unused-source recipe metadata does not match reviewed arrays')
    omitted = {sources[2][0], sums[2][0]}
    return ''.join(line for number, line in enumerate(lines) if number not in omitted), evidence


def effective_recipe(record, recipe, srcinfo):
    """Return a collection-only recipe; archive the original recipe and metadata."""
    filtered, evidence = filtered_srcinfo(record, srcinfo)
    if not evidence:
        return recipe, srcinfo, []
    if hashlib.sha256(recipe).hexdigest() != RECIPE_SHA256:
        raise ValueError('unused-source recipe bytes do not match the reviewed hash')
    # makepkg --allsource does not run prepare. Keep its body unchanged.
    suffix = b'\nunset "source[2]" "sha512sums[2]"\nsource=("${source[@]}")\nsha512sums=("${sha512sums[@]}")\n'
    return recipe + suffix, filtered, evidence


def validate_evidence(record, evidence):
    if evidence != expected_evidence(record):
        raise ValueError('unused-source evidence is missing or changed')
