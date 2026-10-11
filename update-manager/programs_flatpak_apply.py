#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply one preview-bound app transaction to the default system installation."""
import json
import os
from pathlib import Path
import pwd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programs_flatpak as backend


def emit(event, **fields):
    print(json.dumps(dict(type=event, **fields)), flush=True)


def validate(arguments):
    if len(arguments) != 4:
        raise ValueError('invalid-arguments')
    action, origin, ref, token = arguments
    if action not in ('install', 'remove'):
        raise ValueError('unsupported-action')
    row = dict(installation='system', origin=origin, ref=ref)
    backend._target(row)
    if not backend.TOKEN.fullmatch(token):
        raise ValueError('invalid-token')
    return action, row, token


def main(arguments):
    try:
        action, row, token = validate(arguments)
        if os.getuid() != 0 or os.geteuid() != 0:
            raise ValueError('authentication-required')
        # No caller-controlled installation, GI module path, home or XDG path
        # may influence the privileged library. Load libflatpak only after this.
        os.environ.clear()
        os.environ.update(PATH=os.defpath, LC_ALL='C', HOME=pwd.getpwuid(0).pw_dir)
        os.umask(0o077)
        os.chdir('/')
        result = backend.execute(action, row, token, emit)
    except ValueError as error:
        result = dict(ok=False, code=str(error), kind='flatpak', notStarted=True)
    except Exception:
        result = dict(ok=False, code='flatpak-failed', kind='flatpak')
    emit('finished', **result)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
