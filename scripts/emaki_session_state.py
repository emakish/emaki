# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Root, boot and process identity shared by the session and the system hooks."""
import json
import os
from pathlib import Path
import subprocess

PROC = Path('/proc')
ROOT = Path('/')
LIVE = Path('/etc/emaki-live/greetd.toml')


def root_id(path=ROOT):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def boot_id():
    return (PROC / 'sys/kernel/random/boot_id').read_text().strip()


def foreign_root():
    # The installer shares /run with the target. Neither marker writes nor user
    # bus access are appropriate there. Fail closed if root identity is unknown.
    try:
        if root_id(PROC / '1/root') != root_id(ROOT):
            return True
        result = subprocess.run(['systemd-detect-virt', '--chroot'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=2, check=False)
        return result.returncode != 1
    except (OSError, subprocess.SubprocessError):
        return True


def process_start(pid):
    try:
        fields = (PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] not in ('Z', 'X') else None
    except (OSError, IndexError):
        return None


def readonly_root():
    return bool(os.statvfs(ROOT).f_flag & os.ST_RDONLY)


def marker_record(path):
    """The update record when it was written on this root during this boot, else None."""
    try:
        value = json.loads(path.read_text())
        if (value['root'] == root_id(ROOT) and value['boot'] == boot_id()
                and isinstance(value['transaction'], str)):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def marker_value(path):
    value = marker_record(path)
    return None if value is None else value['transaction']
