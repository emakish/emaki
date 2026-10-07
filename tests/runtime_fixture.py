# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private socket directories independent of evidence paths and inherited TMPDIR."""
import atexit
import os
from pathlib import Path
import signal
import tempfile


def short_runtime():
    """Return a context-managed private directory with room for Unix socket names."""
    return tempfile.TemporaryDirectory(prefix='em-', dir='/tmp')


_directories = {}


def _cleanup():
    for (pid, _), directory in list(_directories.items()):
        if pid == os.getpid():
            directory.cleanup()


def _terminate(signum, frame):
    try:
        _cleanup()
    finally:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)


def runtime_path(profile):
    """Keep one short runtime per profile until this test process exits."""
    key = (os.getpid(), str(Path(profile).absolute()))
    if key not in _directories:
        if not _directories:
            signal.signal(signal.SIGTERM, _terminate)
            atexit.register(_cleanup)
        directory = short_runtime()
        _directories[key] = directory
    return Path(_directories[key].name)
