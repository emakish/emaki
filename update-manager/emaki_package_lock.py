# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only package-lock advice; never remove another operation's lock."""
import json
import os
from pathlib import Path
import stat
import time

LIVE = 'Another package operation is running. Wait for it to finish, then try again.'
UNKNOWN = ('Another program may be installing or updating software. '
           'Do not remove the lock while software changes may be running. '
           'Restart the computer, then try again.')
ABANDONED = ('Software changes are blocked by a lock left by an earlier operation. '
             'Close all software installers and updaters. '
             'An administrator must check that none is running before removing {path}. '
             'Then try again. Nothing was removed automatically.')


def identity(info):
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def lock_message(path, proc=Path('/proc')):
    """Inspect descriptors before offering manual recovery, with bounded work.

    Pacman and Emaki's package guards keep the lock descriptor open throughout
    their work. A complete scan with no holder establishes that the creator has
    gone. Permission errors, disappearing locks and replacements stay uncertain.
    This is advice, never permission for an automated unlink: another process
    can start immediately after the scan.
    """
    path = Path(path)
    deadline = time.monotonic() + 2
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return UNKNOWN
        # Unprivileged procfs may hide entire processes (hidepid), not merely
        # deny their descriptor directories. It cannot prove abandonment.
        uncertain = proc == Path('/proc') and os.geteuid() != 0
        count = 0
        for process in proc.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                for descriptor in (process / 'fd').iterdir():
                    count += 1
                    if count > 100000 or time.monotonic() > deadline:
                        return UNKNOWN
                    try:
                        held = descriptor.stat()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        uncertain = True
                        continue
                    if (held.st_dev, held.st_ino) == (before.st_dev, before.st_ino):
                        return LIVE
            except FileNotFoundError:
                # A process which exited cannot still own this lock.
                continue
            except OSError:
                uncertain = True
            if time.monotonic() > deadline:
                return UNKNOWN
        if uncertain or identity(path.lstat()) != identity(before):
            return UNKNOWN
        return ABANDONED.format(path=json.dumps(str(path)))
    except OSError:
        return UNKNOWN
