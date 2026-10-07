#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read or deliberately advance the image's Arch build inputs."""
import argparse
from datetime import date
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
HEADER = '''# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
# Build inputs only; installed systems continue following current Arch mirrors.
'''


def validate(values):
    if set(values) != {'date', 'archiso', 'archinstall'}:
        raise ValueError('Record the date and both build tool versions [ARCH-SNAPSHOT].')
    if date.fromisoformat(values['date']).isoformat() != values['date']:
        raise ValueError('Use a calendar date [YYYY-MM-DD].')
    if values['archiso'] != '91':
        raise ValueError('Review image staging before changing the build tool [archiso 91].')
    if not re.fullmatch(r'[0-9][0-9A-Za-z.+_:]*-[0-9]+', values['archinstall']):
        raise ValueError('Use a full package version [archinstall].')
    return values


def read(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key in values:
            raise ValueError('Remove the repeated field [ARCH-SNAPSHOT].')
        values[key] = value
    return validate(values)


def advance(path, values):
    validate(values)
    if date.fromisoformat(values['date']) > date.today():
        raise ValueError('Choose a snapshot date that is not in the future [ARCH-SNAPSHOT].')
    if values['date'] <= read(path)['date']:
        raise ValueError('Choose a later snapshot date [ARCH-SNAPSHOT].')
    temporary = path.with_suffix('.new')
    temporary.write_text(HEADER + ''.join(f'{key}={values[key]}\n' for key in ('date', 'archiso', 'archinstall')))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    move = commands.add_parser('advance')
    move.add_argument('date')
    move.add_argument('--archiso', required=True)
    move.add_argument('--archinstall', required=True)
    args = parser.parse_args()
    path = HERE / 'ARCH-SNAPSHOT'
    try:
        if args.command == 'advance':
            advance(path, {key: getattr(args, key) for key in ('date', 'archiso', 'archinstall')})
        else:
            values = read(path)
            print(values['date'], values['archiso'], values['archinstall'])
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
