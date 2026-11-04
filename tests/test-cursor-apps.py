#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real cursor validation and GTK readback in private paths, without a compositor."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / '.cache/target/debug/emaki'
SCHEMA = 'org.gnome.desktop.interface'

with tempfile.TemporaryDirectory(prefix='cursor-apps-') as temporary:
    base = Path(temporary)
    for name in ('home', 'config', 'state', 'cache', 'runtime', 'data', 'bin'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_STATE_HOME=str(base / 'state'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_RUNTIME_DIR=str(base / 'runtime'), XDG_DATA_HOME=str(base / 'data'),
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               GSETTINGS_BACKEND='keyfile', CURSOR_TEST_ROOT=str(base),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
    for key in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY', 'EMAKI_SETTINGS_PROFILE'):
        env.pop(key, None)
    activation = base / 'bin/dbus-update-activation-environment'
    activation.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['CURSOR_TEST_ROOT'], 'activation.json').write_text(json.dumps(sys.argv[1:]))
''')
    activation.chmod(0o700)

    def run(*argv):
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
        return result.stdout.strip()

    def settings(*argv):
        return json.loads(run(str(BINARY), 'settings', *argv, '--profile-root', str(base), '--json'))

    personal = base / 'home/.config/niri/config.kdl'
    personal.parent.mkdir(parents=True)
    original = b'cursor { xcursor-size 36; hide-when-typing; }\n'
    personal.write_bytes(original)
    for choice, theme in (('white', 'Emaki-White'), ('dark', 'Emaki-Dark'), ('automatic', 'Emaki-White')):
        # This path invokes the installed niri validator on both generated configs.
        reply = settings('set', 'appearance.cursor', choice)
        fragment = (Path(reply['generation_path']) / 'niri.kdl').read_text()
        assert f'cursor {{ xcursor-theme "{theme}"; }}' in fragment
        assert 'xcursor-size' not in fragment and 'hide-when-typing' not in fragment
        source = (base / 'config/emaki/settings.toml').read_bytes()
        assert json.loads(run(str(BINARY), 'settings', 'sync-cursor')) == {'ok': True}
        assert run('gsettings', 'get', SCHEMA, 'cursor-theme') == repr(theme)
        assert json.loads((base / 'activation.json').read_text()) == ['--systemd', f'XCURSOR_THEME={theme}']
        assert (base / 'config/emaki/settings.toml').read_bytes() == source
        assert personal.read_bytes() == original
    settings('reset', 'appearance.cursor')
    assert json.loads(run(str(BINARY), 'settings', 'sync-cursor')) == {'ok': True}
    assert run('gsettings', 'get', SCHEMA, 'cursor-theme') == "'Emaki-White'"
    assert personal.read_bytes() == original
    # The same refresh entry point runs when the shell's appearance changes,
    # including the automatic timer. Drive time through a private date helper.
    date = base / 'bin/date'
    date.write_text("#!/bin/sh\nprintf '%s\\n' \"$CURSOR_TEST_TIME\"\n")
    date.chmod(0o700)
    for appearance, clock, expected in [('dark', '12:00', 'Emaki-Dark'),
                                         ('light', '12:00', 'Emaki-White'),
                                         ('automatic', '19:00', 'Emaki-Dark'),
                                         ('automatic', '07:00', 'Emaki-White')]:
        env['CURSOR_TEST_TIME'] = clock
        settings('set', 'appearance.theme', appearance)
        before = (base / 'config/emaki/settings.toml').read_bytes()
        assert json.loads(run(str(BINARY), 'settings', 'sync-appearance')) == {'ok': True}
        assert run('gsettings', 'get', SCHEMA, 'cursor-theme') == repr(expected)
        assert json.loads((base / 'activation.json').read_text()) == ['--systemd', f'XCURSOR_THEME={expected}']
        fragments = list((base / 'runtime/emaki-settings').glob('settings-*.kdl'))
        assert any(f'xcursor-theme "{expected}"' in path.read_text() for path in fragments)
        assert (base / 'config/emaki/settings.toml').read_bytes() == before
        assert personal.read_bytes() == original
    # An automatic cursor leaves either personal compositor entry point in charge.
    # Size-only declarations above still inherit the automatic theme.
    for relative in ('niri/config.kdl', 'emaki/niri-emaki.kdl'):
        personal.write_bytes(original)
        custom = base / 'home/.config' / relative
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text('cursor { xcursor-theme "Personal-Cursor"; }\n')
        run('gsettings', 'set', SCHEMA, 'cursor-theme', 'Personal-Cursor')
        settings('reset', 'appearance.cursor')
        wrapper = Path(run(str(BINARY), 'settings', 'session-config'))
        fragment_path = [line.split('"')[1] for line in wrapper.read_text().splitlines()
                         if line.startswith('include "')][-1]
        assert 'xcursor-theme' not in Path(fragment_path).read_text()
        run(str(BINARY), 'settings', 'sync-cursor')
        assert run('gsettings', 'get', SCHEMA, 'cursor-theme') == "'Personal-Cursor'"
        settings('set', 'appearance.cursor', 'dark')
        wrapper = Path(run(str(BINARY), 'settings', 'session-config'))
        fragment_path = [line.split('"')[1] for line in wrapper.read_text().splitlines()
                         if line.startswith('include "')][-1]
        assert 'xcursor-theme "Emaki-Dark"' in Path(fragment_path).read_text()
        run(str(BINARY), 'settings', 'sync-cursor')
        assert run('gsettings', 'get', SCHEMA, 'cursor-theme') == "'Emaki-Dark'"
        assert custom.read_text() == 'cursor { xcursor-theme "Personal-Cursor"; }\n'
        custom.unlink()
    print('PASS: real niri validation, GTK keyfile readback, activation arguments and inherited size')
