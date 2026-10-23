#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reset Emaki settings: what changes, what never changes, the backup, Undo and the page."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'shell/helpers/settings-reset.py'

# A stand-in for `emaki settings`: reset all empties the managed file and remembers it for undo.
CORE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ['FIXTURE_ROOT'])
with (base / 'core-calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\n')
source = Path(os.environ['XDG_CONFIG_HOME']) / 'emaki/settings.toml'
args = sys.argv[1:]
assert args[0] == 'settings' and args[-1] == '--json', args
if args[1:3] == ['reset', 'all']:
    count = base / 'core-resets'
    seen = int(count.read_text()) if count.exists() else 0
    count.write_text(str(seen + 1))
    # RESET_FIXTURE_REFUSE=N: resets from the Nth on (counting from 0) are refused.
    if os.environ.get('RESET_FIXTURE_REFUSE') and seen >= int(os.environ['RESET_FIXTURE_REFUSE']):
        print(json.dumps({'status': 'rejected', 'reason': 'settings_conflict', 'recovery': []}))
        sys.exit(1)
    # Rejected after the live session was half applied and could not be rolled back.
    if os.environ.get('RESET_FIXTURE_REJECT'):
        print(json.dumps({'status': 'rejected', 'reason': os.environ['RESET_FIXTURE_REJECT'],
                          'recovery': ['session_recovery_pending']}))
        sys.exit(1)
    (base / ('core-undo-change-%d.toml' % (seen + 1))).write_bytes(
        source.read_bytes() if source.exists() else b'')
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('schema_version = 1\n')
    # Committed, then the reply is lost: garbage, or no reply before the helper gives up.
    after = os.environ.get('RESET_FIXTURE_AFTER')
    if after == 'garbage':
        print('Segmentation fault')
        sys.exit(139)
    if after == 'hang':
        import time
        time.sleep(30)
    status = os.environ.get('RESET_FIXTURE_STATUS', 'committed')
    print(json.dumps({'status': status, 'reason': 'generation_and_history_committed',
                      'change_id': 'change-%d' % (seen + 1)}))
    sys.exit(0)
if args[1] == 'undo':
    count = base / 'core-undos'
    seen = int(count.read_text()) if count.exists() else 0
    count.write_text(str(seen + 1))
    refuse = os.environ.get('RESET_FIXTURE_UNDO')
    # RESET_FIXTURE_UNDO_BUSY_AT=N: the Nth undo (counting from 0) finds the settings busy.
    if os.environ.get('RESET_FIXTURE_UNDO_BUSY_AT') == str(seen):
        refuse = 'settings_busy'
    if refuse:
        print(json.dumps({'status': 'rejected', 'reason': refuse}))
        sys.exit(1)
    source.write_bytes((base / ('core-undo-%s.toml' % args[2])).read_bytes())
    print(json.dumps({'status': 'committed', 'reason': 'generation_and_history_committed',
                      'change_id': 'undo-1'}))
    sys.exit(0)
sys.exit(2)
'''

OWN = {
    'config/emaki/settings.toml': b'schema_version = 1\n[appearance]\naccent = "blue"\n',
    'config/emaki/power.json': b'{"blank_battery": 300, "blank_ac": 600, "lock_delay": 60, "lid": "system"}\n',
    'state/emaki/dock.json': b'{"version":1,"on":true,"auto_hide":true,"pinned":["kitty","org.example.Custom"],"pins_chosen":true}',
    'state/emaki/night-light.json': b'{"version":1,"on":false,"warmth":80}',
}
# The person's own files and every Emaki file outside the reset's layer.
NEVER = {
    'config/niri/config.kdl': b'// mine\ninclude "/somewhere/default.kdl"\ninput { keyboard { repeat-rate 50; } }\n',
    'config/emaki/niri-emaki.kdl': b'// my own include\n',
    'config/emaki/displays.kdl': b'// displays\noutput "eDP-1" { scale 1.5; }\n',
    'config/kitty/kitty.conf': b'font_size 13\n',
    'config/wpaperd/config.toml': b'[default]\npath = "/home/me/Pictures/wall.png"\n',
    'config/autostart/my.desktop': b'[Desktop Entry]\nType=Application\nExec=true\n',
    'home/Pictures/wall.png': bytes(range(256)) * 4,
    'home/Notes/Shopping.md': b'# Shopping\n- [ ] milk\n',
    'home/Notes/.emaki-notes.json': b'{"pinned":["Shopping.md"]}',
    'data/emaki/calendar/events.ics': b'BEGIN:VCALENDAR\nEND:VCALENDAR\n',
    'state/emaki/notifications.json': b'{"version":1,"entries":[]}',
    'state/emaki/recent.json': b'{"version":1,"items":["kitty"]}',
    'state/emaki/frequent.json': b'{"version":1}',
    'state/emaki/welcome.json': b'{"version":1,"seen":true}',
    'state/emaki/whatsnew.json': b'{"seenVersion":"0.11"}',
    'state/emaki/clock-tools.json': b'{"alarms":[]}',
    'state/emaki/apps.json': b'{}',
    'state/emaki/history/entries.jsonl': b'{"id":"x"}\n',
}


def tree(base, skip=()):
    files = {}
    for path in sorted(base.rglob('*')):
        relative = path.relative_to(base).as_posix()
        if path.is_file() and not any(relative.startswith(prefix) for prefix in skip):
            files[relative] = path.read_bytes()
    return files


def setup(base):
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    for name, data in {**OWN, **NEVER}.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644 if name.endswith('power.json') else 0o600)
    core = base / 'bin/emaki'
    core.write_text(CORE)
    core.chmod(0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), EMAKI_BIN=str(core), FIXTURE_ROOT=str(base))
    env.pop('RESET_FIXTURE_REFUSE', None)
    return env


def helper(env, request):
    result = subprocess.run([sys.executable, '-B', str(HELPER)], input=json.dumps(request) + '\n',
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result
    reply = json.loads(result.stdout)
    assert reply['schema_version'] == 1, reply
    return reply


def backups(base):
    root = base / 'state/emaki/settings-backups'
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


SKIP = ('state/emaki/settings-backups', 'core-', 'bin/')

# 1. The helper: what changes, what never changes, the backup, Undo, the backup limit.
with tempfile.TemporaryDirectory(prefix='settings-reset-') as temporary:
    base = Path(temporary)
    env = setup(base)
    before = tree(base, SKIP)
    reply = helper(env, {'op': 'settings-reset'})
    assert reply['state'] == 'reset', reply
    assert sorted(reply['files']) == ['dock.json', 'night-light.json', 'power.json'], reply
    after = tree(base, SKIP)
    for name in NEVER:
        assert after[name] == NEVER[name], name
    for name in ('config/emaki/power.json', 'state/emaki/dock.json', 'state/emaki/night-light.json'):
        assert name not in after, name
    assert after['config/emaki/settings.toml'] == b'schema_version = 1\n'
    calls = [json.loads(line) for line in (base / 'core-calls.jsonl').read_text().splitlines()]
    assert calls == [['settings', 'reset', 'all', '--json']], calls
    # The backup: private, timestamped, byte-identical copies and a manifest naming the core entry.
    [identifier] = backups(base)
    folder = base / 'state/emaki/settings-backups' / identifier
    assert len(identifier) == 15 and identifier[8] == '-', identifier
    assert folder.stat().st_mode & 0o777 == 0o700
    assert (folder / 'dock.json').read_bytes() == OWN['state/emaki/dock.json']
    assert (folder / 'night-light.json').read_bytes() == OWN['state/emaki/night-light.json']
    assert (folder / 'power.json').read_bytes() == OWN['config/emaki/power.json']
    assert (folder / 'settings.toml').read_bytes() == OWN['config/emaki/settings.toml']
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest == {'version': 1, 'status': 'complete', 'settings_change': 'change-1',
                        'files': {'dock.json': True, 'night-light.json': True, 'power.json': True},
                        'modes': {'dock.json': 0o600, 'night-light.json': 0o600, 'power.json': 0o644}}, manifest
    # Undo: every file byte-identical to before, the core entry undone.
    reply = helper(env, {'op': 'settings-reset-restore', 'id': identifier})
    assert reply == {'schema_version': 1, 'state': 'restored', 'id': identifier}, reply
    assert tree(base, SKIP) == before
    calls = [json.loads(line) for line in (base / 'core-calls.jsonl').read_text().splitlines()]
    assert calls[-1] == ['settings', 'undo', 'change-1', '--json'], calls
    assert (base / 'config/emaki/power.json').stat().st_mode & 0o777 == 0o644
    # A core undo refused for a later change still puts the files back; another failure is
    # reported as retryable.
    for reason, state in (('undo_conflict', 'settings_conflict'), ('settings_busy', 'settings_failed')):
        made = helper(env, {'op': 'settings-reset'})['id']
        reply = helper(dict(env, RESET_FIXTURE_UNDO=reason), {'op': 'settings-reset-restore', 'id': made})
        assert reply['state'] == state, reply
        assert (base / 'state/emaki/dock.json').read_bytes() == OWN['state/emaki/dock.json']
        assert helper(env, {'op': 'settings-reset-restore', 'id': made})['state'] == 'restored'
    assert tree(base, SKIP) == before
    # A file absent before the reset is absent again after Undo, whatever was written meanwhile.
    (base / 'state/emaki/night-light.json').unlink()
    reply = helper(env, {'op': 'settings-reset'})
    second = reply['id']
    (base / 'state/emaki/night-light.json').write_text('{"version":1,"on":true}')
    assert helper(env, {'op': 'settings-reset-restore', 'id': second})['state'] == 'restored'
    assert not (base / 'state/emaki/night-light.json').exists()
    assert (base / 'state/emaki/dock.json').read_bytes() == OWN['state/emaki/dock.json']
    # Refused requests touch nothing.
    snapshot = tree(base)
    for request in ({'op': 'settings-reset-restore', 'id': '../../config'},
                    {'op': 'settings-reset-restore', 'id': '20990101-000000'},
                    {'op': 'settings-reset-restore'}, {'op': 'other'}):
        assert helper(env, request)['state'] in ('invalid_request', 'backup_missing'), request
    assert tree(base) == snapshot
    # Only the newest five backups stay; nothing else in the folder's parent is removed.
    for _ in range(6):
        assert helper(env, {'op': 'settings-reset'})['state'] == 'reset'
    kept = backups(base)
    assert len(kept) == 5, kept
    assert identifier not in kept and second not in kept, kept
    for name in NEVER:
        assert (base / name).read_bytes() == NEVER[name], name

# 2. A core that refuses: no file removed, no backup left behind.
with tempfile.TemporaryDirectory(prefix='settings-reset-') as temporary:
    base = Path(temporary)
    env = setup(base)
    (base / 'core-resets').write_text('1')
    env['RESET_FIXTURE_REFUSE'] = '1'
    before = tree(base, ('core-',))
    reply = helper(env, {'op': 'settings-reset'})
    assert reply['state'] == 'settings_refused', reply
    assert tree(base, ('core-',)) == before
    assert backups(base) == []
    # No core at all: the same.
    env['EMAKI_BIN'] = str(base / 'bin/missing-emaki')
    assert helper(env, {'op': 'settings-reset'})['state'] == 'core_missing'
    assert tree(base, ('core-',)) == before


# 2b. A reset whose core part may have happened keeps its recovery record and offers Undo:
# garbage after a commit, a core that stops answering, an uncertain or a half-applied core,
# and a final record that cannot be written. Undo puts the files back and says when the core's
# part is unknown.
def load_helper():
    import importlib.util
    spec = importlib.util.spec_from_file_location('settings_reset_helper', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_process(env, module, call, *args):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        return call(*args)
    finally:
        os.environ.clear()
        os.environ.update(saved)


for fault in ('garbage', 'hang', 'uncertain', 'rollback_failed', 'manifest_enospc'):
    with tempfile.TemporaryDirectory(prefix='settings-reset-fault-') as temporary:
        base = Path(temporary)
        env = setup(base)
        before = tree(base, SKIP)
        if fault == 'garbage':
            reply = helper(dict(env, RESET_FIXTURE_AFTER='garbage'), {'op': 'settings-reset'})
        elif fault == 'uncertain':
            reply = helper(dict(env, RESET_FIXTURE_STATUS='uncertain'), {'op': 'settings-reset'})
        elif fault == 'rollback_failed':
            reply = helper(dict(env, RESET_FIXTURE_REJECT='session_apply_and_rollback_failed'),
                           {'op': 'settings-reset'})
        elif fault == 'hang':
            module = load_helper()
            module.TIMEOUT = 2
            reply = in_process(dict(env, RESET_FIXTURE_AFTER='hang'), module, module.reset)
        else:
            # The disk fills after the core committed: the final record cannot be written.
            module = load_helper()
            written = module.write_atomic
            manifests = []

            def failing(path, data, mode=0o600, written=written, manifests=manifests):
                if path.name == 'manifest.json':
                    manifests.append(json.loads(data))
                    if len(manifests) > 1:
                        raise OSError(28, 'No space left on device')
                return written(path, data, mode)
            module.write_atomic = failing
            reply = in_process(env, module, module.reset)
            assert [m['status'] for m in manifests] == ['pending', 'complete'], manifests
        assert reply['state'] == 'reset_incomplete' and reply.get('id'), (fault, reply)
        identifier = reply['id']
        assert backups(base) == [identifier], (fault, backups(base))
        folder = base / 'state/emaki/settings-backups' / identifier
        manifest = json.loads((folder / 'manifest.json').read_text())
        assert manifest['files'] == {'dock.json': True, 'night-light.json': True,
                                     'power.json': True}, (fault, manifest)
        assert (folder / 'dock.json').read_bytes() == OWN['state/emaki/dock.json'], fault
        reply = helper(env, {'op': 'settings-reset-restore', 'id': identifier})
        for name in ('state/emaki/dock.json', 'state/emaki/night-light.json',
                     'config/emaki/power.json'):
            assert (base / name).read_bytes() == OWN[name], (fault, name)
        calls = [json.loads(line) for line in (base / 'core-calls.jsonl').read_text().splitlines()]
        if fault == 'uncertain':
            # The core named its entry: Undo undoes it.
            assert reply['state'] == 'restored', (fault, reply)
            assert calls[-1] == ['settings', 'undo', 'change-1', '--json'], calls
            assert tree(base, SKIP) == before
        elif fault == 'rollback_failed':
            # Refused with the running session not yet put back: the settings file stays.
            assert reply == {'schema_version': 1, 'state': 'settings_pending', 'id': identifier}, \
                (fault, reply)
            assert not any(call[1] == 'undo' for call in calls), calls
        else:
            assert reply == {'schema_version': 1, 'state': 'settings_uncertain', 'id': identifier}, \
                (fault, reply)
            assert not any(call[1] == 'undo' for call in calls), calls

# 2c. The real core: the running session takes the reset, the settings file changes under it
# (a conflict), and putting the session back fails or waits for a session. The core refuses and
# says the session is not back yet; the backup stays and Undo or another reset finishes it.
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
assert BINARY is not None and BINARY.is_file(), 'Build the emaki binary before this test'
SHELL = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
base = Path(os.environ['FIXTURE_ROOT'])
with (base / 'shell-calls').open('a') as log:
    log.write(sys.argv[-1] + '\n')
marker = base / 'live-applied.json'
if marker.exists() and not os.environ.get('SHELL_FIXTURE_ACCEPT'):
    raise SystemExit(1)
marker.write_text(sys.argv[-1])
if not os.environ.get('SHELL_FIXTURE_ACCEPT'):
    source = Path(os.environ['XDG_CONFIG_HOME']) / 'emaki/settings.toml'
    source.write_text(source.read_text() + '# an edit of my own\n')
print('applied')
'''
for session in (False, True):
    with tempfile.TemporaryDirectory(prefix='settings-reset-core-') as temporary:
        base = Path(temporary)
        env = setup(base)
        for tool, body in (('emaki-shell', SHELL), ('emaki-machine-settings', 'raise SystemExit(1)\n'),
                           ('niri', "import sys\nif sys.argv[1] == '--version':\n"
                                    "    print('niri 26.04 (fixture)')\n")):
            path = base / 'bin' / tool
            path.write_text(body if body.startswith('#!') else '#!/usr/bin/env python3\n' + body)
            path.chmod(0o700)
        source = base / 'config/emaki/settings.toml'
        source.write_text('schema_version = 1\n[bar]\nautohide = true\n')
        # The stand-in history line above is not the real core's journal.
        (base / 'state/emaki/history/entries.jsonl').unlink()
        runtime = base / 'runtime'
        runtime.mkdir(mode=0o700)
        env.update(EMAKI_BIN=str(BINARY), XDG_RUNTIME_DIR=str(runtime), XDG_CURRENT_DESKTOP='niri',
                   PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'])
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS',
                    'EMAKI_SETTINGS_PROFILE'):
            env.pop(key, None)
        if session:
            # A session to put back: the compensation runs and fails.
            env['WAYLAND_DISPLAY'] = str(base / 'absent-display')
        reply = helper(env, {'op': 'settings-reset'})
        calls = (base / 'shell-calls').read_text().splitlines()
        assert len(calls) == (2 if session else 1), (session, calls)
        assert (base / 'state/emaki/history/live-pending.json').exists(), session
        assert '# an edit of my own' in source.read_text(), session
        assert reply['state'] == 'reset_incomplete' and reply['settings'] == 'live_pending', \
            (session, reply)
        identifier = reply['id']
        assert backups(base) == [identifier], (session, backups(base))
        folder = base / 'state/emaki/settings-backups' / identifier
        manifest = json.loads((folder / 'manifest.json').read_text())
        assert manifest['status'] == 'uncertain' and manifest['live_pending'] is True, manifest
        # Nothing of the person's was removed or changed.
        unchanged = [name for name in {**OWN, **NEVER} if name not in (
            'config/emaki/settings.toml', 'state/emaki/history/entries.jsonl')]
        for name in unchanged:
            assert (base / name).read_bytes() == {**OWN, **NEVER}[name], (session, name)
        if session:
            # Another reset, once the session accepts: the core puts the session back first,
            # then resets for real.
            env['SHELL_FIXTURE_ACCEPT'] = '1'
            reply = helper(env, {'op': 'settings-reset'})
            assert reply['state'] == 'reset' and reply['settings'].startswith('committed'), reply
            assert not (base / 'state/emaki/history/live-pending.json').exists()
            assert 'autohide' not in source.read_text(), source.read_text()
        else:
            reply = helper(env, {'op': 'settings-reset-restore', 'id': identifier})
            assert reply == {'schema_version': 1, 'state': 'settings_pending', 'id': identifier}, reply
            for name in unchanged:
                assert (base / name).read_bytes() == {**OWN, **NEVER}[name], name

# 3. The page: review lists the items, confirm resets, the stores reread, Undo restores.
with tempfile.TemporaryDirectory(prefix='settings-reset-page-') as temporary:
    base = Path(temporary)
    env = setup(base)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsResetPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    for name in ('SettingsMaintenancePage', 'SettingsPowerPage', 'SettingsReset', 'DockStore'):
        if f'{name} 1.0 {name}.qml' not in definitions.read_text():
            with definitions.open('a') as stream:
                stream.write(f'\n{name} 1.0 {name}.qml\n')
    before = tree(base, SKIP + ('shell/',))
    env.update(XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_DIRS=str(base / 'data'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
               QML_DISABLE_DISK_CACHE='1', RESET_FIXTURE_REFUSE='3', RESET_FIXTURE_UNDO_BUSY_AT='1',
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=90)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'SETTINGS_RESET_RESULT 2 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    # Each Undo reached the core with its own reset's entry: the busy one and its retry the
    # third reset, the older notice the second.
    calls = [json.loads(line) for line in (base / 'core-calls.jsonl').read_text().splitlines()]
    assert [call[2] for call in calls if call[1] == 'undo'] == [
        'change-1', 'change-3', 'change-3', 'change-2'], calls
    after = tree(base, SKIP + ('shell/',))
    for name in NEVER:
        assert after[name] == NEVER[name], name
    # After Undo the person's Emaki files are back byte for byte (the refused second reset
    # changed nothing); the stores only reread them.
    for name in OWN:
        assert after[name] == OWN[name], name
print('PASS: settings reset scope, backup, undo, limit, refusal and the Maintenance page')
