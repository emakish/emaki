#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo a recorded install by exact package removal or its pre-install snapshot.

Run through pkexec with the receipt, the program's package (shown in the
authentication prompt) and the preview token the person saw. The receipt must
be root-owned, and the undo preview is computed again here; any difference
refuses before pacman changes anything. Never a cascade (-c), a recursive
removal (-s) or a dependency override (-d).
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
import os
from pathlib import Path
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programs_catalog as catalog
import programs_receipts as receipts
import emaki_update_root
import restore

ENV = {'PATH': os.defpath, 'LC_ALL': 'C'}
LOCK = '/run/emaki-update-manager.lock'
MESSAGES = {
    'restored': _('Your system is ready to return to its state before this install. Restart to finish. Files in your home folder are unchanged.'),
    'restore-failed': _('The system restore did not finish. Review the details before trying again.'),
    'undone': _('The install was taken back.'),
    'refused': _('This install can no longer be taken back as it was. Nothing was removed.'),
    'changed': _('What the undo removes changed. Review it again.'),
    'busy': _('Another package operation is running. Try again when it finishes.'),
    'restart': _('Restart into the normal system before changing programs. Nothing was removed.'),
    'failed': _('The undo could not be checked. Nothing was removed.'),
    'not-removed': _('The undo did not finish. Nothing was removed.'),
    'unknown': _('The undo did not finish as expected. Check the installed programs again.'),
}


def emit(code, **fields):
    answer = dict(type='finished', ok=code in ('undone', 'restored'), code=code, action='undo', message=MESSAGES[code])
    answer.update(fields)
    try:
        print(json.dumps(answer), flush=True)
    except OSError:
        pass
    return 0 if code in ('undone', 'restored') else 1


def pacman(*args, timeout=60):
    return subprocess.run(['pacman', *args], capture_output=True, text=True, env=ENV,
                          cwd='/', timeout=timeout, stdin=subprocess.DEVNULL)


def undo(identity, package, expected):
    path = receipts.DIRECTORY / (identity + '.json')
    try:
        value = receipts.read(path, trusted=True)
    except (OSError, ValueError):
        return emit('refused')
    if value['kind'] == 'flatpak' or value['target'] != package:
        return emit('refused')
    plan = receipts.undo_plan(value, pacman)
    if not plan['ok']:
        return emit('refused', reason=plan['code'], requiredBy=plan.get('requiredBy', []))
    if plan['token'] != expected:
        return emit('changed', packages=plan['remove'], token=plan['token'])
    if plan.get('mode') == 'snapshot':
        try:
            result = restore.restore_install_point(plan['restoreToken'])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return emit('restore-failed', restart=False,
                        details=str(error) if isinstance(error, restore.Refused) else '')
        # The restored root predates this receipt. The running retained root
        # rejects further transactions until restart; do not risk losing the
        # restart result to a receipt write after the root exchange.
        return emit('restored' if result.get('ok') else 'restore-failed',
                    restart=result.get('restart', False), details=result.get('details', ''))
    # A started removal finishes even when the window closes.
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
        signal.signal(signum, signal.SIG_IGN)
    with subprocess.Popen(['pacman', '-Rn', '--noconfirm', '--', *plan['remove']], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV, cwd='/',
                          start_new_session=True) as process:
        output = process.communicate()[0]
    remaining = [name for name in plan['remove'] if pacman('-Q', name).returncode == 0]
    if process.returncode == 0 and not remaining:
        receipts.mark_undone(path, value)
        return emit('undone', packages=plan['remove'], kept=plan['keep'])
    if 'unable to lock database' in output:
        return emit('busy')
    if remaining == plan['remove']:
        return emit('not-removed', packages=plan['remove'])
    return emit('unknown', packages=plan['remove'], remaining=remaining)


def main(argv):
    try:
        if len(argv) != 3 or not receipts.IDENTITY.fullmatch(argv[0]) or not receipts.TOKEN.fullmatch(argv[2]):
            raise ValueError(_('Expected a receipt, its package and a preview token.'))
        catalog.valid_name(argv[1])
        if os.geteuid() != 0:
            raise ValueError(_('Permission is required to undo an install.'))
    except ValueError:
        return emit('failed')
    os.umask(0o077)
    try:
        descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return emit('busy')
            try:
                # A pending restore discards this root at restart, with the undo in it.
                emaki_update_root.require_update_root(
                    lambda command: subprocess.run(command, capture_output=True, text=True, env=ENV,
                                                   cwd='/', timeout=20), 'restart', 'failed')
            except RuntimeError as refusal:
                return emit(str(refusal))
            return undo(*argv)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return emit('unknown')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
