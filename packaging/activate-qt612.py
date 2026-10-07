#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the Qt 6.12 recipe and marker pin for a reviewed host commit."""
from pathlib import Path
import re


def activate(root):
    active = root / 'packaging/quickshell-emaki'
    candidate = active / 'qt-6.12'
    marker = root / 'packaging/emaki/PKGBUILD'
    text = marker.read_text()
    current = (active / 'PKGBUILD').read_text()
    recipe = (candidate / 'PKGBUILD').read_text()
    backport = (candidate / '0003-qt-6.12-moc-includes.patch').read_bytes()

    def field(source, name):
        values = re.findall(rf'^{name}=([0-9.]+)$', source, re.MULTILINE)
        if len(values) != 1:
            raise ValueError(f'Expected one numeric {name} in the recipe')
        return values[0]

    version, release = field(current, 'pkgver'), field(current, 'pkgrel')
    if not release.isdecimal() or version != field(recipe, 'pkgver'):
        raise ValueError('Qt activation requires matching versions and an integer active pkgrel')
    old = f'quickshell-emaki={version}-{release}'
    if text.count(old + "'") != 1:
        raise ValueError('Expected exactly one current Quickshell marker pin')
    # Re-running activation must not allocate another release or overwrite a patch rebuild.
    if "'qt6-base>=6.12." in current and '0003-qt-6.12-moc-includes.patch' in current:
        return
    marker_release = field(text, 'pkgrel')
    if not marker_release.isdecimal():
        raise ValueError('Qt activation requires an integer marker pkgrel')
    text = re.sub(r'^pkgrel=[0-9.]+$', 'pkgrel=' + str(int(marker_release) + 1),
                  text, flags=re.MULTILINE)
    new_release = str(int(release) + 1)
    recipe = re.sub(r'^pkgrel=[0-9.]+$', 'pkgrel=' + new_release, recipe, flags=re.MULTILINE)
    new = f'quickshell-emaki={version}-{new_release}'
    # The dormant recipe has no reserved release; allocate after all patch rebuilds.
    (active / 'PKGBUILD').write_text(recipe)
    (active / '0003-qt-6.12-moc-includes.patch').write_bytes(backport)
    marker.write_text(text.replace(old + "'", new + "'"))


if __name__ == '__main__':
    activate(Path(__file__).resolve().parent.parent)
    print('Qt 6.12 activated. Run packaging tests and commit the changes for review before building.')
