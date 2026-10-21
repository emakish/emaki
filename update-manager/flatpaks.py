# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Flatpak discovery and updates, kept behind the update backend."""

try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import translation

_ = translation('programs').gettext

import re
import shutil

REF = re.compile(r'(?:app/|runtime/)?[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\Z')
COMMIT = re.compile(r'[0-9a-f]{12,64}\Z')
SCOPE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z')


def fields(text, count):
    for line in text.splitlines():
        row = line.split('\t')
        if (len(row) != count or any(len(value) > 512 or any(ord(c) < 32 for c in value) for value in row)
                or not REF.fullmatch(row[0]) or not COMMIT.fullmatch(row[1])):
            raise ValueError(_('The Flatpak package list could not be read.'))
        yield row


def scope_option(scope):
    if not SCOPE.fullmatch(scope):
        raise ValueError(_('The Flatpak installation could not be read.'))
    return '--' + scope if scope in ('user', 'system') else '--installation=' + scope


def installed(command):
    if not shutil.which('flatpak'):
        return {}
    raw = command(['flatpak', 'list', '--all', '--columns=ref,active,version,origin,installation'])
    result = {}
    for ref, commit, version, origin, scope in fields(raw, 5):
        scope_option(scope)
        key = (scope, origin, ref)
        if key in result:
            raise ValueError(_('The Flatpak package list contains duplicate entries.'))
        result[key] = (commit, version)
    return result


def discover(command):
    local = installed(command)
    rows = []
    for scope in sorted({key[0] for key in local}):
        raw = command(['flatpak', 'remote-ls', scope_option(scope), '--updates', '--all',
                       '--columns=ref,commit,version,origin'])
        seen = set()
        for ref, commit, version, origin in fields(raw, 4):
            key = (scope, origin, ref)
            if key not in local:
                continue
            if key in seen:
                raise ValueError(_('The Flatpak package list contains duplicate entries.'))
            seen.add(key)
            old_commit, old_version = local[key]
            if commit.startswith(old_commit) or old_commit.startswith(commit):
                continue
            # Some applications keep their version label across a rebuild.
            # Show the deployment change instead of a misleading "1 -> 1".
            old_label = old_version if old_version != version else ''
            new_label = version if old_version != version else ''
            rows.append(dict(kind='flatpak', source=_('Flatpak ({scope})').format(scope=scope),
                             name=ref, ref=ref, installation=scope, origin=origin,
                             old=old_label or old_commit, new=new_label or commit,
                             oldCommit=old_commit, newCommit=commit, downloadSize=None, aur=False))
    return rows


def pending(rows, command):
    """Filter terminal updates without contacting remotes."""
    local = installed(command)
    return [row for row in rows if local.get((row.get('installation'), row.get('origin'), row.get('ref')), (None,))[0]
            == row.get('oldCommit')]


def update(command, emit):
    """Run only after the full repository transaction has succeeded."""
    scopes = sorted({key[0] for key in installed(command)})
    for scope in scopes:
        emit('progress', phase='flatpak', message=_('Updating Flatpak applications ({scope})…').format(scope=scope))
        output = command(['systemd-inhibit', '--what=sleep:handle-lid-switch', '--mode=block',
                          '--who=Emaki updates', '--why=' + _('Updating Flatpak applications'),
                          'flatpak', 'update', scope_option(scope), '--noninteractive', '--assumeyes'],
                         timeout=7200)
        if output.strip():
            emit('progress', message=output[-16384:])
    return bool(scopes)
