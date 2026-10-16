# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clear the files a disk check saves in the startup partition after a power cut.

The Emaki-only ESP is checked at every boot (fstab pass 2), so systemd-fsck runs
`fsck.fat -a` after an unclean shutdown. When a power cut hits a boot update, the
clusters of a file that was being written or replaced can be allocated in the FAT
without any directory entry (a lost chain). In automatic mode fsck.fat saves each
lost chain as a file FSCKnnnn.REC in the ESP root, and nothing removes it: in one
test, five cuts left 33 MB of such files on a 1 GiB ESP.

A record may hold the only surviving bytes of a damaged file, so one is removed only
when its content is proven to be a lost copy of content Emaki still has and trusts: a
file of a complete slot (its manifest checked), the shim package's files, or a boot
generation's image whose manifest records it (the caller decides; R9S10 #1). Proof
(emaki_copy) needs a known content at least one cluster long (a few bytes such as "MZ"
prove nothing), a record at least one cluster long, a whole number of clusters long, not
one repeated byte value (a chain of zeros proves nothing), and either

  - the whole known file followed by the rest of its last cluster, the rest of the file's
    last logical sector all zero; or
  - a cluster-aligned beginning of the known file (a write cut part way).

A slice from the middle or the end of a known file is not proof and is kept.

The tail: fsck.fat copies the clusters as they are on disk. Checked with dosfstools 4.2
(tests/test-esp-records.py, the real fsck.fat test): a lost chain holding a file plus
other bytes after its end in the last cluster became FSCK0000.REC with the size of the
whole chain, its bytes equal to the clusters as written, tail included. Linux zero-fills
the page that holds the end of a file in memory (fs/mpage.c mpage_write_folio), but writes
only the file's mapped blocks, and a FAT file system's block size is its logical sector
size from the boot sector (fs/fat/inode.c fat_fill_super: sb_set_blocksize(sb,
logical_sector_size)). So the rest of the last sector is zero on disk, and the later
sectors of the last cluster keep whatever they held before (R9S11: a real fsck.fat run
kept 416 zero bytes to the 512-byte sector boundary and 2,048 older bytes after it). The
caller passes the ESP's logical sector size; 512, the smallest FAT allows, is the
default.

On an ESP shared with Windows nothing is removed: Emaki mounts it read-only and never
checks it, a record there may be Windows' own, and records are only reported because
they take room that Emaki's boot files cannot use.
"""
from collections import namedtuple
import os
from pathlib import Path
import re

NAME = re.compile(r'FSCK[0-9]{4}\.REC', re.IGNORECASE)
# The smallest FAT logical sector; Linux writes a FAT file in blocks of the logical sector.
SECTOR = 512

Tidied = namedtuple('Tidied', 'removed kept kept_bytes free')
Tidied.__doc__ = """What tidy did: names removed and kept, the bytes the kept ones occupy
in whole clusters, and the free bytes of the ESP afterwards (None if unknown)."""


def records(esp):
    """The FSCKnnnn.REC files in the ESP root, any letter case, regular files only (no
    symlinks or directories), sorted by name."""
    with os.scandir(esp) as entries:
        found = [Path(entry.path) for entry in entries
                 if NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False)]
    return sorted(found, key=lambda path: path.name)


def whole_clusters(size, cluster):
    """What size bytes occupy on FAT: whole clusters."""
    return -(-size // cluster) * cluster


def emaki_copy(data, known, cluster, sector=SECTOR):
    """The label in known ({label: bytes}) whose content data is a lost copy of, or None.

    The rules are in the module docstring. When several labels hold the same content,
    the first one in known's order is named."""
    size = len(data)
    if cluster <= 0 or size < cluster or size % cluster or data.count(data[0]) == size:
        return None
    for label, content in known.items():
        if len(content) < cluster:
            continue  # a few known bytes prove nothing about a whole cluster (R9S10 #1)
        sector_end = whole_clusters(len(content), max(sector, SECTOR))
        if size == whole_clusters(len(content), cluster) and data[:len(content)] == content \
                and not any(data[len(content):sector_end]):
            return label
        if size <= len(content) and data == content[:size]:
            return label
    return None


def _read(path, limit):
    """The bytes of a record, or None when it is longer than limit (then it cannot be a
    copy of anything known and is not read). Symlinks are refused."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            return None
        return stream.read(limit + 1)


def _sync_directory(path):
    """Make the removals durable. A failure leaves them to the next write-back."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _free(esp):
    try:
        info = os.statvfs(esp)
    except OSError:
        return None
    return info.f_bavail * info.f_frsize


def tidy(esp, known, shared, cluster, log, sector=SECTOR):
    """Remove the records in the ESP root proven to be lost copies of Emaki content and
    keep every other one, writing one line per record to log (a callable taking a line).

    known is {label: bytes} of every Emaki slot and EFI/BOOT file and every sidecar of an
    update in progress. On a shared ESP (shared True) nothing is read or removed. An
    unreadable or unremovable record is kept and reported; this never raises OSError.
    Returns Tidied; its free bytes are measured after the removals."""
    esp = Path(esp)
    removed, kept, kept_bytes = [], [], 0
    try:
        found = records(esp)
    except OSError as error:
        log(f'Could not look for files saved by a disk check in the startup partition '
            f'({error.strerror or error}).')
        return Tidied(removed, kept, kept_bytes, _free(esp))
    limit = max((whole_clusters(len(content), cluster) for content in known.values()
                 if content), default=0) if cluster > 0 else 0
    for path in found:
        name = path.name
        try:
            size = os.lstat(path).st_size
        except OSError as error:
            log(f'Kept {name}: it could not be read ({error.strerror or error}).')
            kept.append(name)
            continue
        occupied = whole_clusters(size, cluster) if cluster > 0 else size
        if shared:
            log(f'Kept {name} ({size} bytes): the startup partition is shared with Windows; '
                'it was not removed and counts against its free space.')
            kept.append(name)
            kept_bytes += occupied
            continue
        try:
            data = _read(path, limit)
        except OSError as error:
            log(f'Kept {name} ({size} bytes): it could not be read ({error.strerror or error}).')
            kept.append(name)
            kept_bytes += occupied
            continue
        label = None if data is None else emaki_copy(data, known, cluster, sector)
        if label is None:
            log(f'Kept {name} ({size} bytes): its content is not a known Emaki boot file.')
            kept.append(name)
            kept_bytes += occupied
            continue
        try:
            path.unlink()
        except OSError as error:
            log(f'Kept {name} ({size} bytes): it could not be removed ({error.strerror or error}).')
            kept.append(name)
            kept_bytes += occupied
            continue
        log(f'Removed {name} ({size} bytes): a lost copy of {label} left by a power cut.')
        removed.append(name)
    if removed:
        _sync_directory(esp)
    return Tidied(removed, kept, kept_bytes, _free(esp))
