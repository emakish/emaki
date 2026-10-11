# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refuse package and repository changes while the running root is not the next boot's root.

After a restore is prepared (mode `pending`) or while a recovery snapshot runs (`snapshot`),
changes land in a root that the next restart discards. The update, program removal and the
update channel switch share this check.
"""
import json
from pathlib import Path

try:
    from emaki_paths import BINDIR
    ROLLBACK = str(Path(BINDIR) / 'emaki-rollback')
except ModuleNotFoundError:
    ROLLBACK = 'emaki-rollback'


def require_update_root(run, pending, unknown):
    """Raise RuntimeError(pending) on a restored or snapshot root, RuntimeError(unknown) if unsure.

    run: the caller's runner, called with an argument list; returns returncode and stdout.
    """
    result = run([ROLLBACK, 'status', '--json'])
    try:
        mode = json.loads(result.stdout)['mode'] if result.returncode == 0 else ''
    except (ValueError, KeyError, TypeError):
        mode = ''
    if mode in ('pending', 'snapshot'):
        raise RuntimeError(pending)
    if mode not in ('normal', 'unsupported'):
        raise RuntimeError(unknown)
