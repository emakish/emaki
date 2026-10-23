#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reset Emaki's own layer of preferences, keeping a copy that undoes it.

Only Emaki's preference files are touched: the managed settings (through the core, so the
running session applies the defaults and History records one entry), the dock pins, Night
Light and the blanking/lock delays. The person's own files, app settings, notes, wallpapers,
clipboard, notification history and anything system-wide are never read or written here.

Protocol (the shell's PrivateJob): one JSON request line on stdin, one JSON reply on stdout.
  {"op": "settings-reset"}                    back up, then reset; reply names the backup
  {"op": "settings-reset-restore", "id": ID}  put the backup back and undo the core entry
"""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

# The newest backups kept; older ones in our own backup folder are removed.
KEEP = 5
BACKUP_ID = re.compile(r'^[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?$')
# name in the backup -> (XDG base, path below it). Only these files are ever written or removed.
FILES = {
    'dock.json': ('state', 'emaki/dock.json'),
    'night-light.json': ('state', 'emaki/night-light.json'),
    'power.json': ('config', 'emaki/power.json'),
}
# Copied for the record only: the core restores it by undoing its history entry.
SETTINGS = ('config', 'emaki/settings.toml')


class Refused(Exception):
    pass


def base(kind):
    name, fallback = {'config': ('XDG_CONFIG_HOME', '.config'),
                      'state': ('XDG_STATE_HOME', '.local/state')}[kind]
    value = os.environ.get(name, '')
    root = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    if not root.is_absolute():
        raise Refused('invalid_environment')
    return root


def target(entry):
    kind, relative = entry
    return base(kind) / relative


def backups_root():
    return base('state') / 'emaki/settings-backups'


def regular(path):
    """The file as a regular file of this user, None when absent; anything else is refused."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise Refused('unsafe_file')
    return info


def private_dir(path):
    """Our own backup folders: created 0700 and never a symlink or someone else's."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Refused('unsafe_directory')
    os.chmod(path, 0o700)


def read(path):
    """Bytes and permission bits of a regular file of this user, never through a symlink;
    None when absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused('unsafe_file')
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise Refused('unsafe_file')
        return stream.read(), stat.S_IMODE(info.st_mode)


def write_atomic(path, data, mode=0o600):
    """Replace path by rename. The parent folder is created when missing but otherwise left as
    it is (the person's ~/.config may be a link to their dotfiles)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.parent / ('.' + path.name + '.' + secrets.token_hex(8) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


# How long one core call may take. A call that runs out may still have committed.
TIMEOUT = 30


def core(args):
    binary = os.environ.get('EMAKI_BIN') or 'emaki'
    try:
        result = subprocess.run([binary, 'settings', *args, '--json'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise Refused('core_missing')
    try:
        reply = json.loads(result.stdout)
    except ValueError:
        raise Refused('core_failed')
    if not isinstance(reply, dict):
        raise Refused('core_failed')
    return reply


def new_backup():
    root = backups_root()
    private_dir(root)
    # UTC, so a time zone or daylight-saving change never sorts a new backup before old ones.
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    # Names sort in creation order: a second backup within one second takes the next number,
    # never a number freed by pruning.
    taken = [name[16:] for name in os.listdir(root) if BACKUP_ID.match(name) and name[:15] == stamp]
    first = max((int(n or 0) for n in taken), default=-1) + 1
    for number in range(first, 1000):
        path = root / (stamp + ('-%d' % number if number else ''))
        try:
            os.mkdir(path, 0o700)
            return path
        except FileExistsError:
            continue
    raise Refused('backup_failed')


def prune(keep, current):
    """Best effort: an old backup left behind is harmless. The one just made is never removed."""
    root = backups_root()
    try:
        names = sorted((p.name for p in root.iterdir()
                        if BACKUP_ID.match(p.name) and p.is_dir() and not p.is_symlink()),
                       key=lambda name: (name[:15], int(name[16:] or 0)))
        for name in names[:-keep]:
            if name != current:
                shutil.rmtree(root / name)
    except OSError:
        pass


def reset():
    backup = new_backup()
    try:
        saved, modes = {}, {}
        for name, entry in FILES.items():
            content = read(target(entry))
            saved[name] = content is not None
            if content is not None:
                write_atomic(backup / name, content[0])
                modes[name] = content[1]
        content = read(target(SETTINGS))
        if content is not None:
            write_atomic(backup / 'settings.toml', content[0])
        # Written before the core may change anything: whatever happens next, this record
        # restores the files, and restoring a "pending" record says the core's part is unknown.
        manifest = dict(version=1, status='pending', files=saved, modes=modes, settings_change=None)
        write_atomic(backup / 'manifest.json', json.dumps(manifest).encode())
    except BaseException:
        # Nothing was changed yet.
        shutil.rmtree(backup, ignore_errors=True)
        raise
    try:
        reply = core(['reset', 'all'])
    except Refused as error:
        if str(error) == 'core_missing':
            # The core never started.
            shutil.rmtree(backup, ignore_errors=True)
            raise
        reply = {}
    except Exception:
        # A timeout or a crash: the core may have committed before it stopped.
        reply = {}
    status = str(reply.get('status', ''))
    reason = str(reply.get('reason', ''))
    change = reply.get('change_id')
    manifest['settings_change'] = change if isinstance(change, str) and change else None
    recovery = reply.get('recovery')
    live_pending = not isinstance(recovery, list) or 'session_recovery_pending' in recovery
    if status == 'rejected' and reason != 'session_apply_and_rollback_failed' and not live_pending:
        # A proven refusal: the core changed nothing, or put the running session back, so
        # neither does the reset.
        shutil.rmtree(backup, ignore_errors=True)
        return dict(state='settings_refused', reason=reason)
    if status == 'rejected':
        # Refused after the running session took the defaults and could not be put back yet:
        # the settings file is unchanged and the session catches up with it on the next
        # settings change or login. The backup stays; Undo or another reset finishes it.
        manifest['status'] = 'uncertain'
        manifest['live_pending'] = True
        try:
            write_atomic(backup / 'manifest.json', json.dumps(manifest).encode())
        except Exception:
            pass
        prune(KEEP, backup.name)
        return dict(state='reset_incomplete', id=backup.name, settings='live_pending', files=[])
    if not (status.startswith('committed') or status == 'unchanged'):
        # Unknown, uncertain, or applied and not rolled back: the files stay as they are and
        # so does the backup, so Undo still puts back what is known.
        manifest['status'] = 'uncertain'
        try:
            write_atomic(backup / 'manifest.json', json.dumps(manifest).encode())
        except Exception:
            pass
        prune(KEEP, backup.name)
        return dict(state='reset_incomplete', id=backup.name, settings='uncertain', files=[])
    manifest['status'] = 'complete'
    try:
        write_atomic(backup / 'manifest.json', json.dumps(manifest).encode())
    except Exception:
        # The core already reset; the pending record still restores the files.
        prune(KEEP, backup.name)
        return dict(state='reset_incomplete', id=backup.name, settings='uncertain', files=[])
    # The manifest exists before anything is removed: from here the backup undoes the reset.
    state = 'reset'
    for name, entry in FILES.items():
        if saved[name]:
            try:
                os.unlink(target(entry))
            except FileNotFoundError:
                pass
            except OSError:
                state = 'reset_incomplete'
    prune(KEEP, backup.name)
    return dict(state=state, id=backup.name, settings=status,
                files=[name for name, kept in saved.items() if kept])


def restore(identifier):
    if not isinstance(identifier, str) or not BACKUP_ID.match(identifier):
        raise Refused('invalid_request')
    backup = backups_root() / identifier
    try:
        manifest = json.loads((backup / 'manifest.json').read_text())
    except (OSError, ValueError):
        raise Refused('backup_missing')
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise Refused('backup_missing')
    files = manifest.get('files', {})
    modes = manifest.get('modes', {})
    # Every precondition before the first write: all copies readable, all targets safe.
    copies = {}
    for name, entry in FILES.items():
        if files.get(name) is True:
            content = read(backup / name)
            if content is None:
                raise Refused('backup_missing')
            copies[name] = content[0]
        regular(target(entry))
    for name, entry in FILES.items():
        if name in copies:
            mode = modes.get(name)
            mode = mode if isinstance(mode, int) and 0 <= mode <= 0o777 else 0o600
            write_atomic(target(entry), copies[name], mode)
        elif files.get(name) is False and regular(target(entry)) is not None:
            # It was absent before the reset: absent again means the same default.
            os.unlink(target(entry))
    change = manifest.get('settings_change')
    if manifest.get('live_pending') is True and not (isinstance(change, str) and change):
        # The core refused that reset and kept its settings file; only the running session may
        # still show defaults until it catches up with that file.
        return dict(state='settings_pending', id=identifier)
    if not (isinstance(change, str) and change) and manifest.get('status') in ('pending', 'uncertain'):
        # The core's part of that reset is unknown (it ran out of time, or its record could not
        # be finished): the files are back; History shows whether the core changed anything.
        return dict(state='settings_uncertain', id=identifier)
    if isinstance(change, str) and change:
        reply = core(['undo', change])
        status = reply.get('status', '')
        if not status.startswith('committed') and status != 'unchanged':
            # A later change of a reset key refuses the whole core undo (History shows it);
            # anything else may pass on another try.
            if reply.get('reason') == 'undo_conflict':
                return dict(state='settings_conflict', id=identifier)
            return dict(state='settings_failed', id=identifier, reason=str(reply.get('reason', '')))
    return dict(state='restored', id=identifier)




def handle(request):
    op = request.get('op') if isinstance(request, dict) else None
    if op == 'settings-reset':
        return reset()
    if op == 'settings-reset-restore':
        return restore(request.get('id'))
    raise Refused('invalid_request')


def main():
    protocol = os.fdopen(os.dup(1), 'w')
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
    try:
        raw = sys.stdin.buffer.readline(16385)
        if len(raw) > 16384:
            raise Refused('invalid_request')
        value = handle(json.loads(raw))
    except Refused as error:
        value = dict(state=str(error))
    except (TimeoutError, subprocess.TimeoutExpired):
        value = dict(state='timeout')
    except PermissionError:
        value = dict(state='access_denied')
    except Exception:
        value = dict(state='operation_failed')
    protocol.write(json.dumps(dict(schema_version=1, **value), ensure_ascii=True) + '\n')
    protocol.flush()


if __name__ == '__main__':
    main()
