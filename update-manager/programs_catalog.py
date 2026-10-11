# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure parsing and refusal rules for removing an installed program.

The package manager decides what a removal takes: a recursive removal without
cascade or dependency overrides never removes a package another installed
package still requires. These rules are the second line: the preview must not
contain an Emaki component or a system package, whatever pacman would allow.
A list of what must stay is never complete, so the person also reads the full
preview before anything is removed.
"""
import re
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
'''.split())
SYSTEM_PATTERNS = re.compile(
    r'linux(?:-[a-z0-9]+)*|[a-z0-9]+-ucode|nvidia(?:-[a-z0-9-]+)?|lib32-nvidia(?:-[a-z0-9-]+)?'
    r'|grub(?:-[a-z0-9-]+)?|xdg-desktop-portal(?:-[a-z0-9-]+)?|pipewire(?:-[a-z0-9-]+)?'
    r'|vulkan-[a-z0-9-]+|mesa(?:-[a-z0-9-]+)?')
OWNED = re.compile(r'(?P<path>/.+) is owned by (?P<name>\S+) (?P<version>\S+)')
BREAKS = re.compile(r":: removing (?P<name>\S+) breaks dependency '(?P<dep>[^']+)' required by (?P<by>\S+)")

MESSAGES = {
    'ready': '[PLACEHOLDER] These packages will be removed.',
    'not-installed': '[PLACEHOLDER] This program is no longer installed.',
    'not-program': '[PLACEHOLDER] This package is not a program you can remove here.',
    'required': '[PLACEHOLDER] Other installed software needs this program, so it stays.',
    'emaki': '[PLACEHOLDER] Removing this program would remove part of Emaki, so it stays.',
    'system': '[PLACEHOLDER] Removing this program would remove part of the system, so it stays.',
    'busy': '[PLACEHOLDER] Another package operation is running. Try again when it finishes.',
    'restart': '[PLACEHOLDER] Restart into the normal system before removing programs. Nothing was removed.',
    'changed': '[PLACEHOLDER] The list of packages to remove changed. Review it again.',
    'failed': '[PLACEHOLDER] The removal could not be checked. Nothing was removed.',
    'not-authorized': '[PLACEHOLDER] Nothing was removed.',
    'removed': '[PLACEHOLDER] The program was removed.',
    'not-removed': '[PLACEHOLDER] The program could not be removed. Nothing was changed.',
    'unknown': '[PLACEHOLDER] The removal did not finish as expected. Check the installed programs again.',
}


def valid_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Invalid package name.')
    return value


def is_emaki(name):
    return bool(EMAKI.fullmatch(name))


def is_system(name):
    return name in SYSTEM or bool(SYSTEM_PATTERNS.fullmatch(name))


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
        raise ValueError('The removal preview lists a package twice.')
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
    emaki = sorted(name for name in names if is_emaki(name))
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


def programs(apps, owned, details, preinstalled_by='emaki-apps'):
    """Group launchable desktop entries by their owning package.

    apps: [{'id', 'name', 'filename'}]; owned: owners() output; details: info() output.
    Emaki components, system packages and packages another installed package
    requires are not listed: they are never removable here. The preview still
    refuses them on its own, because the list can be stale.
    """
    grouped = {}
    for app in apps:
        owner = owned.get(app.get('filename') or '')
        if not owner:
            continue
        name, version = owner
        if is_emaki(name) or is_system(name):
            continue
        entry = grouped.setdefault(name, {'package': name, 'version': version, 'names': [], 'ids': []})
        entry['names'].append(app['name'])
        entry['ids'].append(app['id'])
    result_list = []
    for name, entry in grouped.items():
        detail = details.get(name)
        if detail is None or detail.get('Required By', ['unknown']):
            continue
        entry['names'] = sorted(set(entry['names']), key=str.casefold)
        entry['ids'] = sorted(set(entry['ids']))
        entry['preinstalled'] = preinstalled_by in detail.get('Optional For', [])
        result_list.append(entry)
    return sorted(result_list, key=lambda row: (row['names'][0].casefold(), row['package']))
