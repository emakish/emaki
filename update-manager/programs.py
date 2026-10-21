#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed programs: the list, a removal preview, and removal through one fixed helper.

  emaki-programs list --json
  emaki-programs preview PACKAGE --json
  emaki-programs remove PACKAGE TOKEN --json

TOKEN is the preview the person saw; the privileged helper computes the
preview again and removes nothing unless it is the same.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/usr/libexec/emaki')
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import translation

_ = translation('programs').gettext

import programs_catalog as catalog

ENV = dict(os.environ, LC_ALL='C')
HELPER = '/usr/libexec/emaki/emaki-programs-remove'


def pacman(*args):
    return subprocess.run(['/usr/bin/pacman', *args], capture_output=True, text=True,
                          env=ENV, timeout=60, stdin=subprocess.DEVNULL)


def desktop_apps():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    result = []
    for app in Gio.AppInfo.get_all():
        filename = app.get_filename() if hasattr(app, 'get_filename') else None
        if app.get_id() and filename and app.should_show():
            result.append({'id': app.get_id(), 'name': app.get_display_name(), 'filename': filename})
    return result


def listing():
    apps = desktop_apps()
    paths = sorted({app['filename'] for app in apps})
    # -Qo exits 1 when one path has no owner (a personal entry); owned lines still print.
    owned = catalog.owners(pacman('-Qo', *paths).stdout) if paths else {}
    names = sorted({name for name, _unused in owned.values()})
    details = catalog.info(pacman('-Qi', *names).stdout) if names else {}
    return {'ok': True, 'programs': catalog.programs(apps, owned, details, foreign=foreign_names())}


def foreign_names():
    """Installed packages that no configured repository has: pacman calls them foreign."""
    # -Qmq exits 1 with no output when there are none; an unreadable answer marks nothing.
    result = pacman('-Qmq')
    return frozenset(result.stdout.split()) if result.returncode == 0 else frozenset()


def preview(name):
    result = pacman('-Rs', '--print', '--print-format', '%n', catalog.valid_name(name))
    return catalog.assess(name, result.returncode, result.stdout, result.stderr)


def authorized_removal(name, expected):
    reply = None
    with subprocess.Popen(['/usr/bin/pkexec', HELPER, name, expected], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True) as process:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') == 'finished':
                reply = event
        code = process.wait()
    if reply is None:
        # 126: the person cancelled or was not authorized; 127: authentication failed.
        return catalog.result('not-authorized' if code in (126, 127) else 'unknown')
    reply.pop('type', None)
    return reply


def remove(name, expected):
    catalog.valid_name(name)
    if not catalog.TOKEN.fullmatch(expected):
        raise ValueError(_('Invalid preview token.'))
    # pkexec authorizes its parent. The settings window closes when the password window
    # takes focus, and closing it kills this process: polkit would then cancel the
    # request. A child in its own session waits for pkexec instead, so a confirmed
    # removal finishes; the list shows the result when Settings opens again.
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(reader)
            os.setsid()
            answer = json.dumps(authorized_removal(name, expected)).encode()
            try:
                os.write(writer, answer)
            except OSError:
                pass
        finally:
            os._exit(0)
    os.close(writer)
    with os.fdopen(reader, 'rb') as stream:
        data = stream.read()
    os.waitpid(child, 0)
    try:
        reply = json.loads(data)
        if isinstance(reply, dict) and 'code' in reply:
            return reply
    except ValueError:
        pass
    return catalog.result('unknown')


def main(argv):
    if argv and argv[0] in ('catalog', 'search', 'details', 'related', 'plan', 'execute', 'updates', 'aur',
                            'popular', 'serve'):
        import programs_engine
        return programs_engine.main(argv, sys.modules[__name__])
    if os.geteuid() == 0:
        print(json.dumps(catalog.result('failed')))
        return 2
    if argv and argv[0] in ('ratings', 'reviews', 'review'):
        # Its own process beside the engine's: a slow ratings service never holds a store request.
        import programs_reviews
        return programs_reviews.main(argv)
    try:
        if argv == ['list', '--json']:
            answer = listing()
        elif len(argv) == 3 and argv[0] == 'preview' and argv[2] == '--json':
            answer = preview(argv[1])
        elif len(argv) == 4 and argv[0] == 'remove' and argv[3] == '--json':
            answer = remove(argv[1], argv[2])
        else:
            print(_('Usage: emaki-programs list|preview PACKAGE|remove PACKAGE TOKEN --json'), file=sys.stderr)
            return 2
    except (OSError, ValueError, ImportError, subprocess.SubprocessError):
        answer = catalog.result('failed')
        answer['programs'] = []
    print(json.dumps(catalog.clean_display(answer)))
    return 0 if answer.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
