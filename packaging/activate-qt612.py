#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the Qt 6.12 recipe and marker pin for a reviewed host commit."""
from pathlib import Path


def activate(root):
    active = root / 'packaging/quickshell-emaki'
    candidate = active / 'qt-6.12'
    marker = root / 'packaging/emaki/PKGBUILD'
    text = marker.read_text()
    old, new = 'quickshell-emaki=0.3.1-2', 'quickshell-emaki=0.3.1-3'
    if text.count(old) + text.count(new) != 1:
        raise ValueError('Expected exactly one Quickshell 0.3.1 marker pin')
    # Read both inputs before changing any files.
    recipe = (candidate / 'PKGBUILD').read_bytes()
    backport = (candidate / '0003-qt-6.12-moc-includes.patch').read_bytes()
    (active / 'PKGBUILD').write_bytes(recipe)
    (active / '0003-qt-6.12-moc-includes.patch').write_bytes(backport)
    marker.write_text(text.replace(old, new))


if __name__ == '__main__':
    activate(Path(__file__).resolve().parent.parent)
    print('Qt 6.12 activated. Run packaging tests and commit the changes for review before building.')
