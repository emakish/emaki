#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Print the GRUB modules a Secure Boot image must carry for the given configuration files.

Under Secure Boot GRUB (lockdown) refuses every module it would load from disk; only the image
itself and its own memdisk are trusted (kern/verifiers.c). So each command the configurations run
and each module they insmod must be in the image: the image preloads them and its memdisk holds
the same set, nothing else. The module of a command comes from GRUB's own command.lst. A command that
neither GRUB's core, its script keywords nor command.lst knows fails the audit: a guess here
would only show up as a dead menu on a machine with Secure Boot on.

  secureboot-grub-modules.py --grub-lib /usr/lib/grub/x86_64-efi --base 'normal linux ...' CFG...
"""
import argparse
from pathlib import Path
import re
import sys

# Commands of GRUB's kernel (kern/corecmd.c) and words of its script language.
CORE = {'set', 'unset', 'ls', 'insmod'}
KEYWORDS = {'if', 'then', 'elif', 'else', 'fi', 'for', 'in', 'while', 'until', 'do', 'done', 'function',
            '!', '{', '}'}


def statements(text):
    """Word lists of the statements: split at unquoted newlines, ';', '{' and '}' (not '${...}')."""
    words, word, quote, brace, comment = [], None, '', 0, False
    previous = ''
    for char in text + '\n':
        if comment:
            comment = char != '\n'
            if comment:
                continue
        if quote:
            word += char
            if char == quote and not (quote == '"' and previous == '\\'):
                quote = ''
        elif brace:
            word += char
            brace += {'{': 1, '}': -1}.get(char, 0)
        elif char in '\'"':
            word = (word or '') + char
            quote = char
        elif char == '{' and previous == '$' and word is not None:
            word += char
            brace = 1
        elif char == '#' and word is None:
            comment = True
        elif char.isspace() or char in ';{}':
            if word is not None:
                words.append(word)
                word = None
            if char in '\n;{}':
                if words:
                    yield words
                words = []
        else:
            word = (word or '') + char
        previous = char


def commands(text):
    """(command, first argument) of every statement of a GRUB configuration."""
    for words in statements(text):
        while words and words[0] in KEYWORDS:
            words = words[1:]
        if not words:
            continue
        word = words[0]
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', word) or word.startswith('$'):
            continue  # an assignment, or a command named by a variable (none in our files)
        yield word, words[1] if len(words) > 1 else ''


def modules(grub_lib, base, configs):
    table = {}
    for line in (grub_lib / 'command.lst').read_text().splitlines():
        name, _, module = line.partition(':')
        table[name.lstrip('*').strip()] = module.strip()
    available = {path.stem for path in grub_lib.glob('*.mod')}
    wanted = list(dict.fromkeys(base))
    errors = []
    for config in configs:
        for command, argument in commands(config.read_text()):
            if command == 'insmod':
                needed = argument
            elif command in CORE:
                continue
            elif command in table:
                needed = table[command]
            else:
                errors.append(f'{config}: no GRUB module provides the command {command!r}')
                continue
            if needed not in wanted:
                wanted.append(needed)
    errors += [f'missing GRUB module: {name}.mod ({grub_lib})' for name in wanted if name not in available]
    if errors:
        raise ValueError('\n'.join(errors))
    return wanted


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--grub-lib', type=Path, required=True)
    parser.add_argument('--base', default='', help='modules the image carries in any case (space-separated)')
    parser.add_argument('configs', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        print(' '.join(modules(args.grub_lib, args.base.split(), args.configs)))
    except (OSError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
