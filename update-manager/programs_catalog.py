# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Program parsing, removal refusal rules, and optional popularity cache.

The package manager decides what a removal takes: a recursive removal without
cascade or dependency overrides never removes a package another installed
package still requires. These rules are the second line: the preview must not
contain an Emaki component or a system package, whatever pacman would allow.
A list of what must stay is never complete, so the person also reads the full
preview before anything is removed.
"""

try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import translation

_ = translation('programs').gettext

import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import unicodedata

_ASCII_CONTROLS = dict.fromkeys([*range(9), *range(11, 32), 127])


def clean_text(value):
    """Remove invisible direction/format controls at the display boundary.

    Preserve paragraph/tab layout and spelling/emoji joiners, but no terminal
    controls, surrogate code points or other format characters from metadata.
    """
    if value.isascii():
        return value.translate(_ASCII_CONTROLS)
    return ''.join(char for char in value
                   if char in '\n\t\u200c\u200d' or unicodedata.category(char) not in ('Cc', 'Cf', 'Cs'))


def clean_display(value):
    """Sanitize every string in a JSON value, including nested diagnostics."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean_text(key): clean_display(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_display(item) for item in value]
    return value

NAME = re.compile(r'[a-z0-9@_+][a-z0-9@._+-]{0,127}')
# A preview token is the sorted list of package names the person saw.
TOKEN = re.compile(r'[a-z0-9@_+][a-z0-9@._+,-]{0,8191}')
EMAKI = re.compile(r'emaki(?:-[a-z0-9@._+-]+)?|[a-z0-9@._+-]+-emaki')
# Emaki's own optional packages. A person who installed one may remove
# it again; every other Emaki component stays.
OPTIONAL = frozenset(('emaki-sudo-key', 'snapguard', 'emaki-nvidia'))
# The installed base of every Emaki system (installer constants PACKAGES,
# SNAPSHOT_PACKAGES and mkinitcpio; tests keep them in step) and the services
# the desktop is built on. Patterns cover kernels, firmware and their kin.
SYSTEM = frozenset('''
base base-devel filesystem glibc bash coreutils systemd systemd-libs systemd-sysvcompat dbus
dbus-broker polkit pam shadow util-linux pacman pacman-mirrorlist archlinux-keyring sudo
linux linux-lts linux-firmware intel-ucode amd-ucode mkinitcpio btrfs-progs e2fsprogs
dosfstools cryptsetup grub efibootmgr terminus-font inotify-tools zram-generator
networkmanager wpa_supplicant wireless-regdb snapper snap-pac grub-btrfs flatpak
pipewire pipewire-pulse pipewire-alsa wireplumber mesa xdg-utils xdg-user-dirs
qt6-base qt6-declarative qt6-wayland gtk3 gtk4 xwayland-satellite greetd
kitty qt6ct blueman print-manager
'''.split())
SYSTEM_PATTERNS = re.compile(
    r'linux(?:-[a-z0-9]+)*|[a-z0-9]+-ucode|nvidia(?:-[a-z0-9-]+)?|lib32-nvidia(?:-[a-z0-9-]+)?'
    r'|grub(?:-[a-z0-9-]+)?|xdg-desktop-portal(?:-[a-z0-9-]+)?|pipewire(?:-[a-z0-9-]+)?'
    r'|vulkan-[a-z0-9-]+|mesa(?:-[a-z0-9-]+)?')
OWNED = re.compile(r'(?P<path>/.+) is owned by (?P<name>\S+) (?P<version>\S+)')
BREAKS = re.compile(r":: removing (?P<name>\S+) breaks dependency '(?P<dep>[^']+)' required by (?P<by>\S+)")

MESSAGES = {
    'ready': _('These packages will be removed.'),
    'not-installed': _('This program is no longer installed.'),
    'not-program': _('This package is not a program you can remove here.'),
    'required': _('Other installed software needs this program, so it stays.'),
    'emaki': _('Removing this program would remove part of Emaki, so it stays.'),
    'system': _('Removing this program would remove part of the system, so it stays.'),
    'busy': _('Another package operation is running. Try again when it finishes.'),
    'restart': _('Restart into the normal system before removing programs. Nothing was removed.'),
    'changed': _('The list of packages to remove changed. Review it again.'),
    'failed': _('The removal could not be checked. Nothing was removed.'),
    'not-authorized': _('Nothing was removed.'),
    'removed': _('The program was removed.'),
    'not-removed': _('The program could not be removed. Nothing was changed.'),
    'unknown': _('The removal did not finish as expected. Check the installed programs again.'),
}


def valid_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(_('Invalid package name.'))
    return value


def is_emaki(name):
    # An optional package can keep its own project's name (snapguard).
    return bool(EMAKI.fullmatch(name)) or name in OPTIONAL


def is_system(name):
    return name in SYSTEM or bool(SYSTEM_PATTERNS.fullmatch(name))


def is_protected(name):
    """Desktop/session/settings components and the store must survive every removal."""
    return (is_emaki(name) and name not in OPTIONAL) or is_system(name)


def token(names):
    return ','.join(sorted(names))


def owners(text):
    """`pacman -Qo PATH...` stdout: path to (package, version); unowned paths are absent."""
    result = {}
    for line in text.splitlines():
        match = OWNED.fullmatch(line.strip())
        if match:
            result[match['path']] = (match['name'], match['version'])
    return result


def info(text):
    """`pacman -Qi NAME...` stdout (LC_ALL=C) to per-package fields.

    Lists such as "Required By" hold names separated by two spaces; "None" is empty.
    """
    result, current = {}, None
    for line in text.splitlines():
        key, separator, value = line.partition(' : ')
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if key == 'Name':
            current = result.setdefault(value, {'name': value})
        elif current is None:
            continue
        elif key in ('Required By', 'Optional For'):
            current[key] = [] if value == 'None' else value.split()
        elif key in ('Version', 'Install Reason'):
            current[key] = value
    return result


def preview_names(text):
    """`pacman -Rs --print --print-format %n NAME` stdout: one package name per line."""
    names = [line.strip() for line in text.splitlines() if line.strip()]
    for name in names:
        valid_name(name)
    if len(set(names)) != len(names):
        raise ValueError(_('The removal preview lists a package twice.'))
    return names


def required_by(text):
    """Packages that block the removal, from pacman's dependency refusal."""
    return sorted({match['by'] for match in BREAKS.finditer(text)})


def assess(target, returncode, stdout, stderr):
    """Turn one removal preview into an answer: what goes, or why nothing may go."""
    valid_name(target)
    if returncode:
        # pacman prints the error to stderr and the broken dependencies to stdout.
        blockers = required_by(stdout + '\n' + stderr)
        if blockers:
            code = 'emaki' if any(is_emaki(name) for name in blockers) else 'required'
            return result(code, requiredBy=blockers)
        if 'target not found' in stderr:
            return result('not-installed')
        if 'unable to lock database' in stderr:
            return result('busy')
        return result('failed')
    try:
        names = preview_names(stdout)
    except ValueError:
        return result('failed')
    if target not in names:
        return result('failed')
    emaki = sorted(name for name in names if is_emaki(name) and name not in OPTIONAL)
    if emaki:
        return result('emaki', packages=names, blocked=emaki)
    system = sorted(name for name in names if is_system(name))
    if system:
        return result('system', packages=names, blocked=system)
    return result('ready', packages=names, token=token(names))


def result(code, **fields):
    answer = {'ok': code in ('ready', 'removed'), 'code': code, 'message': MESSAGES[code],
              'packages': [], 'requiredBy': [], 'blocked': [], 'token': ''}
    answer.update(fields)
    return answer


def programs(apps, owned, details, preinstalled_by='emaki-apps', foreign=()):
    """Group launchable desktop entries by their owning package.

    apps: [{'id', 'name', 'filename'}]; owned: owners() output; details: info() output;
    foreign: installed packages no configured repository has (built from the AUR or by hand).
    Required programs remain visible. Protected desktop and system components
    have no removal action; the preview independently enforces that boundary.
    """
    grouped = {}
    for app in apps:
        owner = owned.get(app.get('filename') or '')
        if not owner:
            continue
        name, version = owner
        entry = grouped.setdefault(name, {'package': name, 'version': version, 'names': [], 'ids': []})
        entry['names'].append(app['name'])
        entry['ids'].append(app['id'])
    result_list = []
    for name, entry in grouped.items():
        detail = details.get(name)
        if detail is None:
            continue
        entry['names'] = sorted(set(entry['names']), key=str.casefold)
        entry['ids'] = sorted(set(entry['ids']))
        entry['preinstalled'] = bool({preinstalled_by, 'emaki-desktop'} & set(detail.get('Optional For', [])))
        entry['requiredBy'] = sorted(set(detail.get('Required By', [])))
        entry['protected'] = is_protected(name) or any(is_emaki(item) for item in entry['requiredBy'])
        entry['removable'] = not entry['protected'] and not entry['requiredBy']
        entry['actions'] = ['remove'] if entry['removable'] else []
        entry['foreign'] = name in foreign
        result_list.append(entry)
    return sorted(result_list, key=lambda row: (row['names'][0].casefold(), row['package']))


# Source contracts (bounded samples; missing entries have unknown popularity):
# https://raw.githubusercontent.com/flathub-infra/website/main/backend/app/routes/collection.py
# https://raw.githubusercontent.com/flathub-infra/website/main/backend/app/search.py
# https://pkgstats.archlinux.de/api/doc
_POPULARITY_URLS = {
    'arch': 'https://pkgstats.archlinux.de/api/packages?limit=10000&offset=0',
    'flathub': 'https://flathub.org/api/v2/collection/popular?page=1&per_page=250',
}
_POPULARITY_DAY = 86400
_POPULARITY_RETRY = 3600
_POPULARITY_DEADLINE = 60
_POPULARITY_BYTES = 4 * 1024 * 1024
_POPULARITY_ROWS = 10000
_APP_ID = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){2,}')


def _score(value, source):
    return (type(value) in (int, float)
            and 0 <= value <= (100 if source == 'arch' else 2 ** 53) and math.isfinite(value))


def _popularity_entry(key, value, source):
    pattern = NAME if source == 'arch' else _APP_ID
    return (isinstance(key, str) and len(key) <= 255 and pattern.fullmatch(key)
            and _score(value, source))


def _popularity_values(values, source):
    if not isinstance(values, dict) or len(values) > _POPULARITY_ROWS:
        raise ValueError(_('Invalid popularity data'))
    valid = {key: value for key, value in values.items() if _popularity_entry(key, value, source)}
    if len(valid) * 2 < len(values):
        raise ValueError(_('Invalid popularity data'))
    return valid


def _parse_popularity(data, source):
    if not isinstance(data, dict):
        raise ValueError(_('Invalid popularity response'))
    rows = data.get('packagePopularities' if source == 'arch' else 'hits')
    if not isinstance(rows, list) or len(rows) > _POPULARITY_ROWS:
        raise ValueError(_('Invalid popularity response'))
    name, score = ('name', 'popularity') if source == 'arch' else ('app_id', 'installs_last_month')
    values = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, value = row.get(name), row.get(score)
        if not _popularity_entry(key, value, source) or key in values:
            continue
        values[key] = value
    if len(values) * 2 < len(rows):
        raise ValueError(_('Invalid popularity response'))
    return values


class _NoPopularityRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(_('Unexpected popularity redirect'))


def _popularity_timeout(signum, frame):
    raise TimeoutError(_('Popularity request timed out'))


def _fetch_popularity(source):
    # Called in the worker's main thread. Each source gets its own wall-clock
    # deadline covering DNS, connection, and even a trickling response body.
    previous = signal.signal(signal.SIGALRM, _popularity_timeout)
    try:
        signal.alarm(_POPULARITY_DEADLINE)
        opener = urllib.request.build_opener(_NoPopularityRedirect())
        request = urllib.request.Request(_POPULARITY_URLS[source], headers={'Accept': 'application/json'})
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                raise ValueError(_('Invalid popularity response'))
            body = response.read(_POPULARITY_BYTES + 1)
            if len(body) > _POPULARITY_BYTES:
                raise ValueError(_('Popularity response too large'))
        return _parse_popularity(json.loads(body), source)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class PopularityCache:
    """Read a daily local snapshot; refresh in a detached, bounded worker.

    Attempt records are written before fetching, under an exclusive lock. A
    failed or interrupted request is retried after an hour; only success holds
    for a full day.
    Snapshot never performs or waits for network I/O, even on a cold cache.
    """
    def __init__(self, cache):
        self.cache = Path(cache) / 'popularity'
        self._launched = None
        self._child = None

    def _read(self, source, now):
        try:
            fd = os.open(self.cache / (source + '.json'), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                details = os.fstat(stream.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size > _POPULARITY_BYTES:
                    return None
                body = stream.read(_POPULARITY_BYTES + 1)
            if len(body) > _POPULARITY_BYTES:
                return None
            record = json.loads(body)
            timestamp = record['attempted']
            # Old empty attempt records also receive the short retry window.
            failed = record.get('failed', not bool(record['values']))
            if type(failed) is not bool:
                return None
            lifetime = _POPULARITY_RETRY if failed else _POPULARITY_DAY
            if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                    or not 0 <= timestamp <= now or now - timestamp >= lifetime):
                return None
            values = _popularity_values(record['values'], source)
            return {} if failed else values
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
            return None

    def snapshot(self):
        now = time.time()
        records = {source: self._read(source, now) for source in _POPULARITY_URLS}
        if (any(value is None for value in records.values())
                and (self._launched is None or not 0 <= now - self._launched < _POPULARITY_RETRY)):
            self._launched = now
            try:
                self._child = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), '--refresh-popularity', str(self.cache.parent)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, start_new_session=True)
            except OSError:
                pass
        if self._child is not None:
            self._child.poll()
        return {source: values or {} for source, values in records.items()}

    def _write(self, source, attempted, values, *, failed=False):
        body = json.dumps({'attempted': attempted, 'values': values, 'failed': failed}, allow_nan=False).encode()
        if len(body) > _POPULARITY_BYTES:
            raise ValueError(_('Popularity cache too large'))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.cache, prefix='.', delete=False) as stream:
                temporary = stream.name
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.cache / (source + '.json'))
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def refresh(self):
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.cache / 'refresh.lock', os.O_CREAT | os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as lock:
                if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                    return
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for source in _POPULARITY_URLS:
                    now = time.time()
                    if self._read(source, now) is not None:
                        continue
                    self._write(source, now, {}, failed=True)
                    try:
                        values = _fetch_popularity(source)
                        self._write(source, now, values)
                    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                        pass
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            pass


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--refresh-popularity':
    PopularityCache(sys.argv[2]).refresh()
