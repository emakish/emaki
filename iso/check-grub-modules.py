#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check profile insmod commands against an installed or extracted GRUB module set."""
import argparse
from pathlib import Path
import re
import shlex


def check(profile: Path, modules: Path) -> int:
    configs = sorted(profile.rglob('*.cfg'))
    if not configs:
        raise ValueError(f'no GRUB configuration files: {profile}')
    if not modules.is_dir() or not any(modules.glob('*.mod')):
        raise ValueError(f'no installed GRUB modules: {modules}')
    errors = []
    count = 0
    for config in configs:
        # Tokenizing also handles comments, quoted names and semicolon-separated
        # commands. Dynamic module names cannot be verified statically.
        lexer = shlex.shlex(config.read_text(), posix=True, punctuation_chars=';{}')
        lexer.whitespace_split = True
        tokens = list(lexer)
        for index, token in enumerate(tokens):
            if token != 'insmod':
                continue
            count += 1
            name = tokens[index + 1] if index + 1 < len(tokens) else ''
            if not re.fullmatch(r'[A-Za-z0-9_]+', name):
                errors.append(f'{config}: insmod requires a literal module name: {name!r}')
            elif not (modules / f'{name}.mod').is_file():
                errors.append(f'{config}: missing GRUB module: {name}.mod ({modules})')
    if errors:
        raise ValueError('\n'.join(errors))
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path, help='profile GRUB configuration directory')
    parser.add_argument('modules', type=Path, help='build GRUB platform module directory')
    args = parser.parse_args()
    try:
        count = check(args.profile, args.modules)
    except (OSError, ValueError) as error:
        parser.exit(1, f'ERROR: {error}\n')
    print(f'OK: {count} profile insmod commands exist in {args.modules}')


if __name__ == '__main__':
    main()
