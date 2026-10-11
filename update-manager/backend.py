#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unprivileged package discovery and the update window's line protocol."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import update_catalog as catalog
except ModuleNotFoundError:
    import catalog
try:
    import update_flatpaks as flatpaks
except ModuleNotFoundError:
    import flatpaks
try:
    import update_reminder as reminder
except ModuleNotFoundError:
    import reminder

DAY = 86400
ENV = dict(os.environ, LC_ALL='C')


def emit(kind, **fields):
    print(json.dumps(dict(type=kind, **fields)), flush=True)


def command(args, allowed=(0,), timeout=180):
    # A private process group includes fakeroot's package-manager child. Stop
    # all writers before a signal or timeout removes their private database.
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=ENV, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    if process.returncode not in allowed:
        # The same wording used by the terminal entry point, installed beside us.
        try:
            from emaki_update_errors import explain
        except ModuleNotFoundError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
            from emaki_installer.update_errors import explain
        raise RuntimeError(explain(stderr + '\n' + stdout))
    return stdout


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def fetch(url, cache, validator):
    """Bounded HTTPS GET; one-hour cache also applies to manual refreshes."""
    key = hashlib.sha256(url.encode()).hexdigest()
    path = cache / (key + '.json')
    previous = read_json(path)
    cached = None
    if isinstance(previous.get('body'), str):
        try:
            cached = validator(previous['body'].encode())
        except (ValueError, catalog.ET.ParseError):
            pass
    stamp = previous.get('time', 0)
    if cached is not None and isinstance(stamp, (int, float)) and time.time() - stamp < 3600:
        return cached, False
    try:
        request = Request(url, headers={'User-Agent': 'Emaki-update-manager', 'Accept': '*/*'})
        with urlopen(request, timeout=15) as response:
            if not response.url.startswith('https://'):
                raise ValueError('The package source redirected to an insecure address.')
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('The package source returned too much data.')
        parsed = validator(raw)
        save_json(path, dict(time=time.time(), body=raw.decode('utf-8')))
        return parsed, False
    except (OSError, ValueError, catalog.ET.ParseError) as error:
        if cached is not None:
            return cached, True
        raise RuntimeError('The online information could not be checked. Check your connection, then try again.') from error


def compare(new, old):
    return int(command(['/usr/bin/vercmp', new, old]).strip())


def package_paths():
    return (Path(command(['/usr/bin/pacman-conf', 'DBPath']).strip()),
            Path(command(['/usr/bin/pacman-conf', 'LogFile']).strip()))


def fingerprint(database):
    stat = (database / 'local').stat()
    return [stat.st_mtime_ns, stat.st_ino]


def install_date(directory=Path('/var/log/emaki-install')):
    """The installer records completion in a UTC-named log on the target."""
    stamps = []
    for path in directory.glob('install-*.log'):
        try:
            stamps.append(datetime.strptime(path.name, 'install-%Y%m%dT%H%M%SZ.log').replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return min(stamps) if stamps else None


def cleanup_databases(cache):
    # Called only with check.lock held, so no live check owns these directories.
    for path in cache.glob('database-*'):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def interrupted(signum, frame):
    # SystemExit unwinds command and TemporaryDirectory before releasing lock.
    raise SystemExit(128 + signum)


def discover(cache):
    database, logfile = package_paths()
    initial = fingerprint(database)
    if (database / 'db.lck').exists():
        raise RuntimeError('Another package operation may be running. Wait for it to finish before checking again.')
    warnings = []
    emit('progress', message='Checking repository packages…')
    # As in checkupdates: only the private sync database is refreshed. Copy local
    # metadata too, so the query cannot write through a link into the installed DB.
    with tempfile.TemporaryDirectory(prefix='database-', dir=cache) as temporary:
        private = Path(temporary)
        shutil.copytree(database / 'local', private / 'local', symlinks=True)
        base = ['--dbpath', str(private), '--logfile', '/dev/null', '--color', 'never']
        command(['/usr/bin/fakeroot', '--', '/usr/bin/pacman', '-Sy',
                 '--disable-sandbox-filesystem', *base])
        installed = catalog.packages(command(['/usr/bin/pacman', '-Q', *base]))
        available = catalog.upgrade_lines(command(['/usr/bin/pacman', '-Qu', *base], allowed=(0, 1)))
        updates = catalog.transaction(command(['/usr/bin/pacman', '-Sup', '--noconfirm',
                                               '--print-format', '%r|%n|%v|%s', *base]), installed)
        foreign = catalog.packages(command(['/usr/bin/pacman', '-Qm', *base], allowed=(0, 1)))
    if (database / 'db.lck').exists() or fingerprint(database) != initial:
        raise RuntimeError('Installed packages changed during the check. Check again when the package operation finishes.')
    ignored = [name for name, row in available.items() if row['ignored']]
    if ignored:
        warnings.append('Your package settings hold back these updates: ' + ', '.join(ignored) + '.')
    emit('progress', message='Checking AUR package versions…')
    missing = set()
    names = sorted(foreign)
    for offset in range(0, len(names), 100):
        batch = {name: foreign[name] for name in names[offset:offset + 100]}
        url = 'https://aur.archlinux.org/rpc/v5/info?' + urlencode([('arg[]', name) for name in batch])
        try:
            def validate(raw):
                return catalog.aur_updates(json.loads(raw), batch, compare)
            (entries, found), stale = fetch(url, cache, validate)
            updates.extend(entries)
            missing.update(set(batch) - found)
            if stale:
                warnings.append('AUR could not be reached. Its listed versions come from an older check.')
        except (RuntimeError, ValueError):
            warnings.append('AUR could not be checked. Some program updates may be missing from this list.')
            break
        if offset + 100 < len(names):
            time.sleep(1)
    if missing:
        warnings.append('These installed packages were not found in AUR: ' + ', '.join(sorted(missing)) + '.')
    if foreign:
        warnings.append('AUR download sizes are not available. Development packages may need rebuilding even when their listed version has not changed.')
    try:
        since = catalog.last_upgrade(logfile.read_text(errors='replace'))
    except OSError:
        since = None
    if since is None:
        since = install_date()
    if since is None:
        warnings.append('The date of the last full update is unknown. Review all the available Arch news notices.')
    emit('progress', message='Checking Arch news…')
    try:
        news, stale = fetch('https://archlinux.org/feeds/news/', cache,
                            lambda raw: catalog.news_items(raw, since))
        if stale:
            warnings.append('Arch news could not be reached. The notices come from an older check.')
    except (RuntimeError, ValueError, catalog.ET.ParseError):
        news = []
        warnings.append('Arch news could not be checked. Visit https://archlinux.org/news/ before updating.')
    warnings += catalog.warnings_for(updates)
    try:
        updates.extend(flatpaks.discover(command))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        warnings.append('[PLACEHOLDER] Flatpak updates could not be checked. Check again before updating applications.')
    warnings.append('The upgrade checks the repositories again. Its final package list and download size may change.')
    data = catalog.summary(updates, news, list(dict.fromkeys(warnings)), datetime.now(timezone.utc).isoformat())
    data['databaseStamp'] = initial
    helpers = [name for name in ('paru', 'yay', 'pikaur', 'trizen', 'aura', 'pamac') if shutil.which(name)]
    data['aurHelpers'] = helpers
    data['aurNotice'] = ('[PLACEHOLDER] AUR: update in a terminal' +
                         (' with ' + ', '.join(helpers) if helpers else ' with your preferred AUR helper') + '.')
    if not any(row.get('aur') for row in updates):
        data['aurNotice'] = ''
    return data


def check(cache, refresh):
    previous = read_json(cache / 'updates.json')
    with (cache / 'check.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            data = previous or catalog.summary([], [], [], '')
            data['error'] = 'An update check is already running. Check again in a moment.'
            return data
        try:
            cleanup_databases(cache)
            database, _ = package_paths()
            if (not refresh and previous and not previous.get('error')
                    and time.time() - catalog.utc(previous['checkedAt']).timestamp() < DAY
                    and previous.get('databaseStamp') == fingerprint(database)):
                return previous
            data = discover(cache)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            data = previous or catalog.summary([], [], [], '')
            data['error'] = 'Could not check updates.'
            data['errorDetails'] = str(error)[:2500]
            if previous:
                data['error'] += ' The list is from the previous check.'
        save_json(cache / 'updates.json', data)
        return data


def status(cache):
    data = read_json(cache / 'updates.json')
    updates = data.get('updates', [])
    # Terminal updates must clear the indicator too; no sync or network is done.
    try:
        installed = catalog.packages(command(['/usr/bin/pacman', '-Q']))
        updates = [row for row in updates if row.get('kind') == 'flatpak'
                   or row.get('old') == installed.get(row.get('name'), 'Not installed')]
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pass
    flatpak_rows = [row for row in updates if row.get('kind') == 'flatpak']
    if flatpak_rows:
        try:
            updates = [row for row in updates if row.get('kind') != 'flatpak'] + flatpaks.pending(flatpak_rows, command)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pass
    return dict(pending=len(updates), checkedAt=data.get('checkedAt', ''), error=data.get('error', ''),
                **local_state(cache))


def rollback_pending():
    """The existing rollback backend owns interpretation of the mounted root."""
    try:
        result = subprocess.run(['emaki-rollback', 'status', '--json'], capture_output=True,
                                text=True, env=ENV, timeout=20)
        state = json.loads(result.stdout)
        return result.returncode == 0 and isinstance(state, dict) and state.get('mode') == 'pending'
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def local_state(cache):
    state = reminder.status(cache)
    state['restorePending'] = rollback_pending()
    if state['restorePending']:
        state['restart'] = True
    return state


def restore(operation, token=None):
    """Read the public inventory directly; authenticate only an actual restore."""
    listing = operation == '--restore-points'
    arguments = ['list'] if listing else ['restore', token]
    expected = 'restorePoints' if listing else 'restoreFinished'
    received = False
    command = [str(Path(__file__).resolve().with_name('emaki-update-restore')), *arguments]
    if not listing:
        command.insert(0, 'pkexec')
    with subprocess.Popen(command,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
        for line in process.stdout:
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get('type') == expected:
                    emit(event.pop('type'), **event)
                    received = True
            except ValueError:
                pass
        code = process.wait()
    if not received:
        message = ('[PLACEHOLDER] The system restore request was not authorized.' if code in (126, 127) else
                   '[PLACEHOLDER] The system restore connection ended. Check the restore status before trying again.')
        pending = rollback_pending()
        if listing:
            emit(expected, points=[], error=message, restorePending=pending)
        else:
            emit(expected, ok=False, message=message, restart=pending)


def service_state():
    result = subprocess.run(['/usr/bin/systemctl', 'show', 'emaki-update.service',
                             '--property=ActiveState,Result,ExecMainStatus'], capture_output=True,
                            text=True, env=ENV, timeout=20)
    if result.returncode:
        return {}
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def observe_existing(cache):
    state = service_state()
    if state.get('ActiveState') not in ('activating', 'active', 'deactivating'):
        return False
    emit('progress', message='A repository update is already running. Waiting for its result…')
    while state.get('ActiveState') in ('activating', 'active', 'deactivating'):
        time.sleep(2)
        state = service_state()
    ok = state.get('Result') == 'success' and state.get('ExecMainStatus') == '0'
    previous = read_json(cache / 'updates.json')
    installed = catalog.packages(command(['/usr/bin/pacman', '-Q']))
    changed = [row for row in previous.get('updates', [])
               if not row.get('aur') and row.get('kind') != 'flatpak'
               and row.get('old') != installed.get(row.get('name'))]
    notices = catalog.warnings_for(changed)
    emit('finished', ok=ok,
         message=('Repository updates finished. Check again to refresh the list.' if ok else
                  'The update did not finish. Open the update details with an administrator before trying again [journalctl -u emaki-update.service].'),
         **local_state(cache),
         signOut=any('sign out' in line for line in notices))
    return True


def apply(cache):
    emit('progress', message='Waiting for permission to update…')
    finished = None
    with subprocess.Popen(['/usr/bin/pkexec', '/usr/libexec/emaki/emaki-update-apply', 'apply'],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            try:
                event = json.loads(line)
                if event.get('type') == 'progress':
                    emit(event.pop('type'), **event)
                elif event.get('type') == 'finished':
                    finished = event
            except (ValueError, AttributeError):
                pass
        code = process.wait()
    if finished is None:
        message = ('The update was not started.' if code in (126, 127)
                   else 'The update connection ended. Reopen the window to check the result; a started update keeps running.')
        emit('finished', ok=False, notStarted=code in (126, 127), message=message,
             signOut=False, **local_state(cache))
    else:
        finished.pop('type', None)
        if code != 0:
            finished['ok'] = False
        if finished.get('ok'):
            try:
                if flatpaks.update(command, emit):
                    finished['message'] = '[PLACEHOLDER] System and Flatpak updates finished. Update AUR packages in a terminal.'
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                emit('progress', message=str(error)[:2500])
                finished.update(ok=False, message='[PLACEHOLDER] System updates finished, but Flatpak updates did not finish. Review the details before trying again.')
        finished.update(local_state(cache))
        emit('finished', **finished)
    # Keep AUR reminders; --status drops versions changed by this transaction,
    # and the database stamp invalidates the next cached check.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = ('--status', '--check', '--refresh', '--apply', '--restart', '--dismiss-restart', '--restore-points')
    # Exactly one fixed operation, or one bound snapshot token; never a command.
    restoring = (len(sys.argv) == 3 and sys.argv[1] == '--restore'
                 and re.fullmatch(r'[1-9][0-9]{0,18}:[0-9a-f]{64}', sys.argv[2]))
    if not restoring and (len(sys.argv) != 2 or sys.argv[1] not in operations):
        parser.error('[PLACEHOLDER] Choose a fixed update operation or --restore with a restore point token.')
    if os.geteuid() == 0:
        parser.error('open the update manager as a regular user')
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    os.umask(0o077)
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'emaki-update-manager'
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    operation = sys.argv[1]
    try:
        if operation == '--status':
            print(json.dumps(status(cache)))
        elif operation in ('--check', '--refresh'):
            if not observe_existing(cache):
                emit('result', data=dict(check(cache, operation == '--refresh'), **local_state(cache)))
        elif operation == '--apply':
            apply(cache)
        elif operation == '--dismiss-restart':
            return 0 if reminder.acknowledge(cache) else 1
        elif operation in ('--restore-points', '--restore'):
            restore(operation, sys.argv[2] if restoring else None)
        else:
            result = subprocess.run(['/usr/bin/systemctl', 'reboot'], capture_output=True, text=True)
            if result.returncode:
                emit('finished', ok=False, message='The restart was not authorized. Try again from the power menu.',
                     signOut=False, **local_state(cache))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        emit('finished', ok=False, message='The update operation could not start. Check that the update manager is installed correctly.', restart=False, signOut=False)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
