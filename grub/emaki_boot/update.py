# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Accept a usable updated system, or spend one automatic snapshot return."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from . import refresh, update_menu, usable

ESP = Path('/efi/EFI/Emaki')
SNAPSHOTS = Path('/.snapshots')
MENU = Path('/boot/grub/grub.cfg')
BASELINE = Path('/run/emaki-update-transaction.json')
SLEEP = Path('/run/emaki-update-sleep.json')
DEADLINE = 300
STABLE = 15
HOOK_LOCK_TIMEOUT = 10


def log(message):
    print('Update boot protection: ' + message, file=sys.stderr, flush=True)


def command(argv):
    result = subprocess.run([str(item) for item in argv], capture_output=True,
                            text=True, timeout=90, check=False)
    refresh.require(result.returncode == 0,
                    f'{argv[0]} could not prepare update recovery: {result.stderr.strip()}')
    return result.stdout.strip()


def private_read(path):
    info = path.lstat()
    refresh.require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and not info.st_mode & 0o022, f'Unsafe update recovery file: {path}')
    return refresh.regular(path)


def record():
    path = ESP / 'update.json'
    if not path.exists():
        return None
    value = json.loads(private_read(path))
    refresh.require(isinstance(value, dict) and value.get('version') == 1
                    and re.fullmatch(r'[0-9a-f]{32}', value.get('transaction', ''))
                    and re.fullmatch(r'[1-9][0-9]*', value.get('snapshot', ''))
                    and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value.get('date', ''))
                    and re.fullmatch(refresh.UUID, value.get('armed_boot', ''))
                    and re.fullmatch(refresh.UUID, value.get('root_uuid', '')),
                    'The pending update recovery record is invalid.')
    return value


def environment():
    path = ESP / 'update.env'
    if not path.exists():
        return {}
    data = private_read(path)
    refresh.require(len(data) == 1024 and data.startswith(b'# GRUB Environment Block\n'),
                    'The update boot counter is unreadable.')
    return dict(line.split('=', 1) for line in data.decode('ascii').splitlines()
                if '=' in line and not line.startswith('#'))


def matches(value, env):
    return (value is not None and env.get('emaki_update') == value['transaction']
            and env.get('emaki_attempt') in ('0', '1', '2'))


def write_environment(env):
    refresh.atomic(ESP / 'update.env', refresh.trial_block(env))


def finish_acceptance(value, env):
    """Finish only a durable acceptance, including a reset between its writes."""
    if (value is None or env.get('emaki_update') != value['transaction']
            or env.get('emaki_attempt') != 'accepted'):
        return False
    if ((SNAPSHOTS / value['snapshot'] / 'info.xml').exists()
            and (SNAPSHOTS / value['snapshot'] / 'snapshot').is_dir()):
        pin(value['snapshot'], value['cleanup'])
    (ESP / 'update.json').unlink(missing_ok=True)
    refresh.sync_directory(ESP)
    write_environment({})
    return True


@contextmanager
def locked(blocking=False, timeout=0):
    with refresh.open_run_lock('a') as lock:
        if blocking:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:
            end = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Boot maintenance is busy; continuing without update recovery protection.')
                    time.sleep(min(0.1, remaining))
        yield


def supported():
    # HookDir also runs in live installation targets. Never touch their shared /run.
    sys.path.insert(0, '/usr/libexec/emaki')
    import emaki_session_state as session
    if (session.foreign_root() or Path('/etc/emaki-live/greetd.toml').exists()
            or Path('/.emaki-install-incomplete').exists()):
        return None
    root = refresh.mount_info('/', True)
    if root['fstype'] != 'btrfs' or root['fsroot'] != '/@':
        return None
    identity = refresh.discover()
    snapshots = refresh.mount_info('/.snapshots', True)
    refresh.require(snapshots['fstype'] == 'btrfs' and snapshots['fsroot'] == '/@snapshots'
                    and snapshots['uuid'] == identity['uuid'],
                    'Update recovery needs the installed snapshot filesystem.')
    return identity


def transaction_identity():
    # libalpm executes the hook directly; both pre hooks have the same parent.
    pid = os.getppid()
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return [pid, fields[19], refresh.current_boot_id()]


def prepare(identity):
    """Run before snap-pac; a stale temporary receipt cannot identify this update."""
    refresh.recover(identity)
    previous = record()
    finish_acceptance(previous, environment())
    value = {'parent': transaction_identity(), 'uuid': identity['uuid'],
             'snapshots': sorted(path.name for path in SNAPSHOTS.iterdir()
                                 if re.fullmatch(r'[1-9][0-9]*', path.name))}
    refresh.atomic(BASELINE, json.dumps(value) + '\n')


def snapshot_info(number):
    path = SNAPSHOTS / number
    refresh.require(not path.is_symlink() and not (path / 'snapshot').is_symlink()
                    and (path / 'snapshot').is_dir(), 'The pre-update snapshot is missing.')
    info = ET.fromstring(private_read(path / 'info.xml'))
    refresh.require(info.findtext('type') == 'pre' and info.findtext('num') == number,
                    'The snapshot receipt does not identify a pre-update snapshot.')
    try:
        stamp = datetime.strptime(info.findtext('date', ''), '%Y-%m-%d %H:%M:%S')
        date = stamp.replace(tzinfo=timezone.utc).astimezone().date().isoformat()
    except ValueError as error:
        raise refresh.Refuse('The pre-update snapshot has no valid date.') from error
    return date, info.findtext('cleanup', '')


def current_snapshot(identity):
    baseline = json.loads(private_read(BASELINE))
    refresh.require(baseline['parent'] == transaction_identity()
                    and baseline['uuid'] == identity['uuid'],
                    'The snapshot transaction changed before update recovery was prepared.')
    number = private_read(Path(tempfile.gettempdir()) / 'snap-pac-pre_root').decode().strip()
    refresh.require(re.fullmatch(r'[1-9][0-9]*', number)
                    and number not in baseline['snapshots'],
                    'snap-pac did not create a pre-update snapshot for this transaction.')
    return number, snapshot_info(number)


def pin(number, cleanup):
    # The service owns an in-memory snapshot list. Its normal D-Bus entry point
    # also activates it when needed, keeping cleanup and the on-disk receipt aligned.
    command(['snapper', '-c', 'root', 'modify', '--cleanup-algorithm', cleanup, number])


def disarm(value):
    accepted = {'emaki_update': value['transaction'], 'emaki_attempt': 'accepted'}
    write_environment(accepted)
    finish_acceptance(value, accepted)


def missing_snapshot(value):
    path = SNAPSHOTS / value['snapshot']
    return not (path / 'snapshot').is_dir() or not (path / 'info.xml').is_file()


def install_menu(identity):
    original = refresh.regular(MENU).decode()
    wrapped = update_menu.wrap_menu(original, identity['esp_uuid'])
    # Check the candidate before replacing the menu used by all installed loaders.
    with tempfile.TemporaryDirectory(prefix='emaki-update-') as directory:
        candidate = Path(directory) / 'grub.cfg'
        candidate.write_text(wrapped)
        command(['grub-script-check', candidate])
    refresh.atomic(MENU, wrapped)


def mark(identity):
    previous = record()
    if previous is not None and missing_snapshot(previous):
        disarm(previous)
        log('The pending snapshot is missing. Automatic return is disabled; '
            'this transaction will continue without update recovery protection.')
        return
    number, (date, cleanup) = current_snapshot(identity)
    if previous is not None:
        env = environment()
        # An interrupted preparation can leave a durable snapshot/config without
        # a counter. Reuse that earlier boundary, including after a fresh boot.
        if not env:
            env = {'emaki_update': previous['transaction'], 'emaki_attempt': '0'}
            write_environment(env)
        refresh.require(matches(previous, env) and previous['root_uuid'] == identity['uuid'],
                        'The previous update recovery state needs repair before another update.')
        snapshot_info(previous['snapshot'])
        # Do not accept changes made after this kernel started; do not replace the
        # first good boundary or replenish an already spent return.
        previous['armed_boot'] = refresh.current_boot_id()
        refresh.atomic(ESP / 'update.json', json.dumps(previous) + '\n')
        install_menu(identity)
        return
    value = {'version': 1, 'transaction': uuid.uuid4().hex, 'snapshot': number,
             'date': date, 'cleanup': cleanup, 'root_uuid': identity['uuid'],
             'armed_boot': refresh.current_boot_id()}
    # Pin before relying on the snapshot. Keep it pinned until a usable boot has
    # accepted the transaction; manual deletion is still possible and bounded.
    try:
        pin(number, '')
        with refresh.pause_snapshots():
            command(['/etc/grub.d/41_snapshots-btrfs'])
            body = update_menu.extract_snapshot_body(
                refresh.regular(MENU.parent / 'grub-btrfs.cfg').decode(), number, identity['uuid'])
            config = update_menu.render(body, value['transaction'], number, date, identity['uuid'])
            with tempfile.TemporaryDirectory(prefix='emaki-update-') as directory:
                candidate = Path(directory) / 'update.cfg'
                candidate.write_text(config)
                command(['grub-script-check', candidate])
            refresh.atomic(ESP / 'update.cfg', config)
            install_menu(identity)
        # Publish the independent counter before package extraction. If protection
        # fails, the hook reports that failure but still permits package repairs.
        refresh.atomic(ESP / 'update.json', json.dumps(value) + '\n')
        write_environment({'emaki_update': value['transaction'], 'emaki_attempt': '0'})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # A failed menu build must not leave an untracked snapshot pinned forever.
        try:
            disarm(value)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            log(f'Could not release incomplete update recovery: {error}')
        raise


def may_accept(value, env, boot_id, root_uuid, cmdline):
    flags = [option for word in cmdline.split() if word.startswith('rootflags=')
             for option in word[10:].split(',')]
    snapshot = any(option.startswith('subvol=')
                   and option[7:].lstrip('/').startswith('@snapshots/') for option in flags)
    return (matches(value, env) and value['armed_boot'] != boot_id
            and value['root_uuid'] == root_uuid and env['emaki_attempt'] in ('1', '2')
            and not snapshot and not any(word.startswith('emaki.auto_return=') for word in cmdline.split()))


def accept(value, identity):
    with locked(blocking=True):
        current = record()
        env = environment()
        if current != value or not may_accept(current, env, refresh.current_boot_id(),
                                              identity['uuid'], Path('/proc/cmdline').read_text()):
            log('Acceptance deferred: the pending update, boot, or root changed.')
            return False
        # Clear the GRUB selector durably before dropping the marker. A reset
        # between these writes cannot turn an accepted boot into a return.
        accepted = {'emaki_update': value['transaction'], 'emaki_attempt': 'accepted'}
        write_environment(accepted)
        return finish_acceptance(value, accepted)


def monitor(identity, observe=usable.observe, now=time.monotonic, wait=time.sleep):
    with locked(blocking=True):
        value = record()
        if finish_acceptance(value, environment()):
            return True
        if value is not None and 'emaki.update_unprotected=1' in Path('/proc/cmdline').read_text().split():
            disarm(value)
            log('GRUB could not save the boot attempt. Automatic return is disabled for this update.')
            return False
    if value is None or value['armed_boot'] == refresh.current_boot_id():
        log('Acceptance deferred: no update from an earlier boot is pending.')
        return False
    env = environment()
    # A cold start after an unsuccessful resume gets the same acceptance test.
    if matches(value, env) and env['emaki_attempt'] == '0':
        with locked(blocking=True):
            if record() != value or environment() != env:
                return False
            env.update(emaki_attempt='1', next_entry='emaki-auto-recovery')
            write_environment(env)
    if not may_accept(value, env, refresh.current_boot_id(), identity['uuid'],
                      Path('/proc/cmdline').read_text()):
        log('Acceptance deferred: this boot does not match the pending updated system.')
        return False
    log('Waiting for a usable local login or desktop session.')
    end, seen, since = now() + DEADLINE, None, None
    while now() < end:
        evidence = observe()
        tick = now()
        if evidence is None or evidence != seen:
            if evidence:
                log('A usable session is present; waiting for fifteen stable seconds.')
            elif seen:
                log('The usable session disappeared; restarting the stability window.')
            seen, since = evidence, tick if evidence else None
        if evidence and since is not None and tick - since >= STABLE and tick < end:
            accepted = accept(value, identity)
            if accepted:
                log('The updated boot was accepted.')
            return accepted
        wait(min(1, max(0, end - now())))
    log('Acceptance timed out: no session stayed usable for fifteen seconds.')
    return False


def sleep_phase(phase):
    value, env = record(), environment()
    if phase == 'pre':
        if matches(value, env):
            refresh.atomic(SLEEP, json.dumps({'transaction': value['transaction'], 'env': env}) + '\n')
            write_environment({**env, 'emaki_suppress': '1'})
        return
    if not SLEEP.exists():
        return
    saved = json.loads(private_read(SLEEP))
    if matches(value, env) and saved['transaction'] == value['transaction']:
        # /run and its kernel return only on resume, never on a fresh boot.
        write_environment(saved['env'])
    SLEEP.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'mark', 'accept', 'sleep-pre', 'sleep-post'))
    args = parser.parse_args(argv)
    try:
        refresh.require(os.geteuid() == 0, 'Update boot protection must run as root.')
        identity = supported()
        if identity is None:
            return 0
        if args.action == 'accept':
            monitor(identity)
        elif args.action.startswith('sleep-'):
            phase = args.action.removeprefix('sleep-')
            with refresh.sleep_run_lock(phase, SLEEP, 'Update boot protection') as acquired:
                if acquired:
                    sleep_phase(phase)
        else:
            with locked(timeout=HOOK_LOCK_TIMEOUT):
                if args.action == 'prepare':
                    prepare(identity)
                elif args.action == 'mark':
                    mark(identity)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ET.ParseError,
            subprocess.SubprocessError) as error:
        log(f'Could not finish: {error}')
        if args.action in ('prepare', 'mark'):
            log('Package management will continue without update recovery protection.')
            return 0
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
