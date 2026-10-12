#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable alongside only in a disposable test image's staged live root."""
import argparse
import ast
from pathlib import Path


def enable(root):
    root = Path(root)
    if not (root / 'etc/emaki-test/authorized_keys').is_file():
        raise ValueError('alongside override requires a staged test image')
    matches = list(root.glob('usr/lib/python*/site-packages/emaki_installer/constants.py'))
    if len(matches) != 1:
        raise ValueError('expected exactly one installed installer constants module')
    path = matches[0]
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('installer constants must stay inside the staged root')
    source = path.read_text()
    bindings = [node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Name) and node.id == 'ALONGSIDE'
                and isinstance(node.ctx, ast.Store)]
    if len(bindings) != 1 or source.splitlines().count('ALONGSIDE = False') != 1:
        raise ValueError('test override requires the shipped literal ALONGSIDE = False')
    # Keep a second marker independent of SSH/test boot configuration. Image
    # verification refuses it in release mode, even after hardware acceptance.
    (root / 'etc/emaki-test-alongside').write_text('test-only\n')
    path.write_text(''.join('ALONGSIDE = True\n' if line.rstrip('\r\n') == 'ALONGSIDE = False'
                            else line for line in source.splitlines(keepends=True)))
    # Packaged bytecode may carry timestamp/size metadata; never reuse it here.
    for bytecode in path.parent.glob('__pycache__/constants.*.pyc'):
        bytecode.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    try:
        enable(args.root)
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, f'ERROR: {error}\n')
