# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the NTFS writable-open checks against a disposable snapshot only."""
from .i18n import _
from pathlib import Path
import re
import tempfile

from .errors import Code, InstallError, require


REFUSAL = (_('Emaki could not confirm that the Windows disk is safe to shrink. Start Windows, let it check the disk if it asks, then choose Shut down (not Restart) and start from the USB stick again.'))
CLEANUP_REFUSAL = ('A safety check could not finish cleanly. Windows was not changed. '
                   'Restart the computer from the USB stick and try again.')


def _remove_snapshot(runner, name):
    """Drain udev readers and retry removal, with at most 50 seconds of waits.

    A settle failure still refuses the probe, but must not skip removal. The
    kernel must release the snapshot before either backing loop is detached.
    """
    settle_failure = None
    for ignored in range(3):
        try:
            runner.run(['udevadm', 'settle', '--timeout=5'], timeout=5)
        except (OSError, RuntimeError) as exc:
            settle_failure = exc
        try:
            # --retry handles a reader that opens after settle has returned.
            runner.run(['dmsetup', 'remove', '--retry', name], timeout=10)
            return True, settle_failure
        except (OSError, RuntimeError) as exc:
            removal_failure = exc
    # A failed create can leave no mapping, and a failed remove can have
    # completed its ioctl before its userspace udev wait failed. Confirm absence
    # before releasing the loops; a missing command or failed listing proves
    # nothing. The command failure still prevents this probe from authorizing a
    # resize even when cleanup can finish.
    try:
        names = runner.run(['dmsetup', 'info', '--columns', '--noheadings',
                            '--options', 'name'], timeout=5)
        if name not in {line.strip() for line in names.splitlines()}:
            return True, removal_failure
    except (OSError, RuntimeError):
        pass
    return False, removal_failure


def check_ntfs_journal(runner, device, size, sector_size=512, *, work='/run/emaki-installer'):
    """libntfs checks $LogFile only on a writable, non-forensic open.

    A read-only loop pins the origin and a private COW snapshot receives every
    write, including metadata changes during open/close. Never probe the origin
    read-write, never merge the snapshot, and fail closed on setup or cleanup.
    """
    require(type(size) is int and size > 0 and size % 512 == 0
            and sector_size in (512, 4096), Code.SHRINK_BOUNDS, REFUSAL)
    root = Path(tempfile.mkdtemp(prefix='ntfs-journal-', dir=work))
    cow = root / 'cow'
    name = root.name
    origin = overlay = None
    mapped = False
    failure = None
    probe_output = ''
    cleanup_failure = None
    try:
        with cow.open('xb') as stream:
            stream.truncate(64 * 1024 * 1024)
        cow.chmod(0o600)
        origin = runner.run(['losetup', '--find', '--show', '--read-only',
                             '--sector-size', str(sector_size), '--', str(device)]).strip()
        require(re.fullmatch(r'/dev/loop[0-9]+', origin), Code.SHRINK_BOUNDS, REFUSAL)
        overlay = runner.run(['losetup', '--find', '--show', '--', str(cow)]).strip()
        require(re.fullmatch(r'/dev/loop[0-9]+', overlay) and overlay != origin,
                Code.SHRINK_BOUNDS, REFUSAL)
        require(runner.run(['blockdev', '--getro', origin]).strip() == '1',
                Code.SHRINK_BOUNDS, REFUSAL)
        require(runner.run(['blockdev', '--getsize64', origin]).strip() == str(size),
                Code.SHRINK_BOUNDS, REFUSAL)
        # A failed command may still have created a mapping (for example its
        # udev wait failed); attempt removal before touching either loop.
        mapped = True
        runner.run(['dmsetup', 'create', name, '--table',
                    f'0 {size // 512} snapshot {origin} {overlay} N 8'])
        # No recovery or read-only fallback flags: both would conceal $LogFile.
        try:
            runner.run(['ntfs-3g.probe', '--readwrite', '/dev/mapper/' + name])
        except InstallError as exc:
            # Preserve the diagnostic that distinguishes hibernation from a
            # dirty journal. Never expose unrelated teardown output as NTFS.
            probe_output = exc.output
            raise
        status = runner.run(['dmsetup', 'status', name]).strip()
        require(re.fullmatch(r'0 ' + str(size // 512) + r' snapshot [0-9]+/[0-9]+ [0-9]+', status),
                Code.SHRINK_BOUNDS, REFUSAL)
    except (OSError, RuntimeError) as exc:
        failure = exc
    finally:
        # Always drain transient udev readers, even when the probe itself failed.
        # A genuinely unremovable snapshot still forbids detaching its backing.
        if mapped:
            removed, cleanup_failure = _remove_snapshot(runner, name)
            mapped = not removed
        if not mapped:
            detached = True
            for loop in (overlay, origin):
                if loop and re.fullmatch(r'/dev/loop[0-9]+', loop):
                    try:
                        runner.run(['losetup', '--detach', loop])
                    except (OSError, RuntimeError) as exc:
                        cleanup_failure = exc
                        detached = False
            if detached:
                try:
                    cow.unlink(missing_ok=True)
                    root.rmdir()
                except OSError as exc:
                    cleanup_failure = exc
    if cleanup_failure is not None:
        raise InstallError(Code.SHRINK_BOUNDS, CLEANUP_REFUSAL) from cleanup_failure
    if failure is not None:
        raise InstallError(Code.SHRINK_BOUNDS, REFUSAL, output=probe_output) from failure
