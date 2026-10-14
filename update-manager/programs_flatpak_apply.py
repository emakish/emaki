#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply one preview-bound app transaction to the default system installation.

  emaki-programs-flatpak install|remove ORIGIN REF TOKEN
  emaki-programs-flatpak undo ORIGIN REF RECEIPT TOKEN
"""
import json
import os
from pathlib import Path
import pwd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programs_flatpak as backend
import programs_receipts as receipts


def emit(event, **fields):
    print(json.dumps(dict(type=event, **fields)), flush=True)


def validate(arguments):
    if len(arguments) == 5 and arguments[0] == 'undo':
        action, origin, ref, identity, token = arguments
        if not receipts.IDENTITY.fullmatch(identity):
            raise ValueError('invalid-receipt')
    elif len(arguments) == 4:
        action, origin, ref, token = arguments
        identity = ''
        if action not in ('install', 'remove'):
            raise ValueError('unsupported-action')
    else:
        raise ValueError('invalid-arguments')
    row = dict(installation='system', origin=origin, ref=ref)
    backend._target(row)
    if not backend.TOKEN.fullmatch(token):
        raise ValueError('invalid-token')
    return action, row, identity, token


def record(row, result):
    """What the install added: the app and every runtime or extension it brought."""
    added = {operation['ref']: operation['commit'] for operation in result.get('operations', [])
             if operation.get('action') == 'install'}
    key = 'flatpak:system:' + row['origin'] + ':' + row['ref']
    return receipts.record('flatpak', key, row['ref'].split('/')[1], row['ref'], {}, added,
                           refs=sorted(added))['id']


def main(arguments):
    try:
        action, row, identity, token = validate(arguments)
        if os.getuid() != 0 or os.geteuid() != 0:
            raise ValueError('authentication-required')
        # No caller-controlled installation, GI module path, home or XDG path
        # may influence the privileged library. Load libflatpak only after this.
        os.environ.clear()
        os.environ.update(PATH=os.defpath, LC_ALL='C', HOME=pwd.getpwuid(0).pw_dir)
        # libflatpak creates the installation's exports and deployments with this umask, and
        # every account must read them to list and run its programs (077 left them root-only).
        # Receipts set their own mode.
        os.umask(0o022)
        os.chdir('/')
        if action == 'undo':
            path = receipts.DIRECTORY / (identity + '.json')
            receipt = receipts.read(path, trusted=True)
            if receipt['key'] != 'flatpak:system:' + row['origin'] + ':' + row['ref']:
                raise ValueError('receipt-mismatch')
            result = backend.undo_execute(row, receipt, token, emit)
            if result.get('ok'):
                receipts.mark_undone(path, receipt)
        else:
            result = backend.execute(action, row, token, emit)
            if result.get('ok') and action == 'install':
                try:
                    result['receipt'] = record(row, result)
                except (OSError, ValueError):
                    result['receipt'] = ''
    except ValueError as error:
        result = dict(ok=False, code=str(error), kind='flatpak', notStarted=True)
    except Exception:
        result = dict(ok=False, code='flatpak-failed', kind='flatpak')
    emit('finished', **result)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
