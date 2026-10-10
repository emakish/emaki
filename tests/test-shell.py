#!/usr/bin/env python3
"""Lint/format + real qs IPC, with no windows, Wayland or user profile access."""
import json
import math
import os
from pathlib import Path
import shutil
import socket
import signal
import threading
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install, launches
from xml.sax.saxutils import quoteattr
from runtime_fixture import short_runtime
from wait_fixture import wait_for
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
TOOLS = Path(os.environ.get('QML_TOOLS_DIR', '/usr/lib/qt6/bin'))
FILES = sorted(str(p) for p in Path('shell').glob('*.qml'))
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

def checked(command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=30, **kwargs)

checked(['python', 'scripts/render-shell-palette', '--check'])
for filename in [*FILES, *map(str, sorted(Path('tests/fixtures').rglob('*.qml')))]:
    formatted = checked([str(TOOLS / 'qmlformat'), '--ignore-settings', filename]).stdout
    assert formatted == Path(filename).read_text(), f'Run qmlformat --ignore-settings -i {filename}'

lint = subprocess.run([str(TOOLS / 'qmllint'), '--ignore-settings', '-W', '0', '--json', '-',
                       '-I', os.environ.get('QML_IMPORT_DIR', '/usr/lib/qt6/qml'),
                       '-I', 'shell', *FILES], text=True, capture_output=True, timeout=30)
report = json.loads(lint.stdout)
(CACHE / 'shell-qmllint.json').write_text(lint.stdout)
known = []
additional_metadata = {
    ('SystemService.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('NiriService.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('SessionUpdateNotice.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('WelcomeController.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('SnapshotRecovery.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('greeter.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('AppCatalog.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('GreeterState.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('GreeterAuth.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('SessionStartup.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('session-cover.qml', 'signal-handler-parameters', 'Type QProcess::ExitStatus of parameter exitStatus in signal called exited was not found, but is required to compile onExited. Did you add all imports and dependencies?'),
    ('WindowLabels.qml', 'signal-handler-parameters', 'Type QLocalSocket::LocalSocketError of parameter error in signal called error was not found, but is required to compile onError. Did you add all imports and dependencies?'),
}
for file in report['files']:
    for warning in file['warnings']:
        # QS registers PanelWindow's backend at runtime. Its installed 0.3.1
        # qmltypes mark the interface uncreatable and omit BackgroundEffect's
        # cross-module Region type. No blanket warning suppression.
        permitted = {
            ('uncreatable-type', 'Type PanelWindow is not creatable.'),
            ('missing-type', 'No type found for property "blurRegion". This may be due to a missing import statement or incomplete qmltypes files.'),
        }
        capture_panel_metadata = (Path(file['filename']).name in ('LockCapturePanel.qml', 'greeter.qml', 'session-cover.qml')
            and warning['id'] == 'uncreatable-type' and warning['message'] == 'Type PanelWindow is not creatable.')
        if (Path(file['filename']).name == 'Surfaces.qml'
                and (warning['id'], warning['message']) in permitted) or capture_panel_metadata or (Path(file['filename']).name, warning['id'], warning['message']) in additional_metadata:
            known.append(warning)
        else:
            raise AssertionError((file['filename'], warning))
# Exact Quickshell metadata gaps; runtime tests exercise these handlers.
assert len(known) <= 23, known
assert lint.returncode == 0 or known, (lint.returncode, lint.stderr)
print(f'QML format OK; qmllint raw rc={lint.returncode}, {len(known)} documented QS metadata diagnostics, no others')

qs = shutil.which('qs')
assert qs, 'qs is required'
assert '0.3.1' in checked([qs, '--version']).stdout, 'Review the API/metadata exemptions for another qs version'
if '--lint-only' in sys.argv:
    raise SystemExit(0)

# Desktop ids the launcher never lists (Arch live-ISO/dependency utilities; DECISIONS 2026-10-03).
HIDDEN_IDS = ['avahi-discover', 'bssh', 'bvnc', 'lftp', 'lstopo', 'qv4l2', 'qvidcap',
              'stoken-gui', 'stoken-gui-small', 'vim',
              'qt6ct', 'org.kde.kdeconnect.nonplasma', 'mpv', 'org.kde.kwrite',
              'blueman-adapters', 'blueman-manager']
_catalog = Path('shell/AppCatalog.qml').read_text()
_listed = json.loads(_catalog[_catalog.index('hiddenIds: [') + len('hiddenIds: '):].split('\n', 1)[0])
assert _listed == HIDDEN_IDS, ('AppCatalog.hiddenIds and the fixture list differ', _listed)

def smoke(width, height, scale, exclusive_zone=None):
    with short_runtime() as runtime:
        smoke_with_runtime(width, height, scale, exclusive_zone, Path(runtime))


def smoke_with_runtime(width, height, scale, exclusive_zone, runtime):
    profile = Path(tempfile.mkdtemp(prefix='sh-', dir=CACHE))
    for part in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / part).mkdir(mode=0o700)
    (profile / 'bin').mkdir()
    (profile / 'data' / 'applications').mkdir()
    for name, terminal in [('Alpha', False), ('Terminal', True), ('Fail', False), ('Browser', False)]:
        (profile / 'data' / 'applications' / f'emaki-{name.lower()}.desktop').write_text(
            f'[Desktop Entry]\nType=Application\nName=Emaki {name}\nExec=/usr/bin/true\nTerminal={str(terminal).lower()}\nCategories=Utility;\nKeywords=fixture;{"terminal;" if name == "Browser" else ""}\n')
    # Browser carries the keyword "terminal": the query below must still rank Emaki Terminal (name) first.
    # The launcher-hidden utility ids (AppCatalog.hiddenIds) are valid, displayable entries
    # here; the launcher must still list only the four above, in Apps, search and Recent.
    for desktop_id in HIDDEN_IDS:
        (profile / 'data' / 'applications' / f'{desktop_id}.desktop').write_text(
            f'[Desktop Entry]\nType=Application\nName=Hidden {desktop_id}\nExec=/usr/bin/true\nCategories=Utility;\nKeywords=fixture;\n')
    (profile / 'state' / 'emaki').mkdir(mode=0o700)
    (profile / 'state' / 'emaki' / 'recent.json').write_text(json.dumps([
        dict(kind='app', ref='vim', t=3), dict(kind='app', ref='emaki-alpha', t=2), dict(kind='app', ref='avahi-discover', t=1)]))
    # Deliberately ignores Exec: checks only that the shell passes the selected
    # desktop ID, with no query interpolation. Real gtk-launch is checked in a live session.
    launcher = profile / 'bin' / 'gtk-launch'
    launcher.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\np=Path(os.environ['EMAKI_SHELL_FIXTURE'])\n(p/'launch.json').write_text(json.dumps(sys.argv[1:]))\nsys.exit(1 if 'fail' in sys.argv[-1] else 0)\n")
    launcher.chmod(0o700)
    terminal_capture = profile / 'bin' / 'terminal'
    terminal_capture.write_text("#!/usr/bin/env python3\nimport os,sys,json\nfrom pathlib import Path\np=Path(os.environ['EMAKI_SHELL_FIXTURE'])\n(p/'terminal.json').write_text(json.dumps(sys.argv[1:]))\n")
    terminal_capture.chmod(0o700)
    (profile / 'PRIVATE_PARENT').mkdir()
    recent_file = profile / 'PRIVATE_PARENT/PRIVATE_RECENT12 <b> & кавычки.txt'
    recent_file.write_text('Fixture, not user data.\n')
    (profile / 'data/recently-used.xbel').write_text('<xbel version="1.0"><bookmark href=' + quoteattr(recent_file.as_uri()) + ' visited="2026-09-23T04:30:00Z"/></xbel>')
    data_before = {str(p): p.read_bytes() for p in (profile / 'data').rglob('*') if p.is_file()}
    observation = dict(schema_version=1, ipc_release='26.04', generation=1,
        connection=dict(status='connected', reason='synchronized'), model=dict(
            windows={'701': dict(id=701, workspace_id=101, is_focused=True)},
            workspaces={'101':dict(id=101, idx=1, output='A', is_active=True), '202':dict(id=202, idx=2, output='A', is_active=False)},
            focused_output='A', overview_open=False, keyboard_layouts=dict(names=['English (US)', 'Russian'], current_idx=0)))
    def publish():
        tmp = profile / 'observation.tmp'
        tmp.write_text(json.dumps(observation))
        tmp.replace(profile / 'observation.json')
    publish()
    labels_socket = socket.socket(socket.AF_UNIX)
    labels_socket.bind(str(runtime / 'niri.sock'))
    labels_socket.listen()
    labels_socket.settimeout(.1)
    stop = threading.Event()
    connections = []
    private_title = 'PRIVATE_TITLE_10 <b>literal</b>'
    def titles_server():
        while not stop.is_set():
            try:
                conn, _ = labels_socket.accept()
                connections.append(conn)
                conn.settimeout(1)
                request = conn.recv(4096)
                assert request == b'"EventStream"\n', request
                conn.sendall((json.dumps({'Ok':'Handled'}) + '\n' + json.dumps({'WindowsChanged':{'windows':[{'id':701, 'title':private_title}]}}) + '\n').encode())
            except (TimeoutError, OSError):
                pass
    thread = threading.Thread(target=titles_server, daemon=True)
    thread.start()
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR=str(scale),
               EMAKI_BIN=str(ROOT / 'tests/fixtures/shell-core.py'),
               EMAKI_TERMINAL=str(terminal_capture), EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_FIXTURE=str(profile), PATH=str(profile / 'bin') + os.pathsep + os.environ['PATH'],
               XDG_DATA_DIRS=str(profile / 'data'),
               EMAKI_SHELL_BORDER='soft', EMAKI_SHELL_HEADLESS='1',
               EMAKI_SHELL_TEST_WIDTH=str(width), EMAKI_SHELL_TEST_HEIGHT=str(height),
               EMAKI_SHELL_EXCLUSIVE_ZONE='0', EMAKI_SHELL_OUTPUT='',
               XDG_RUNTIME_DIR=str(runtime), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), TMPDIR=str(profile / 'tmp'))
    if exclusive_zone is None:
        env.pop('EMAKI_SHELL_EXCLUSIVE_ZONE', None)
    else:
        env['EMAKI_SHELL_EXCLUSIVE_ZONE'] = str(exclusive_zone)
    for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(key, None)
    env['NIRI_SOCKET'] = str(runtime / 'niri.sock')
    install(profile, env)
    # An invalid environment must end qs with a nonzero status (systemd restarts on failure).
    bad = subprocess.run([qs, '-p', str(ROOT / 'shell'), '--no-color'], env=dict(env, EMAKI_SHELL_BORDER='bad'),
                         capture_output=True, text=True, timeout=10)
    assert bad.returncode == 1, (bad.returncode, bad.stdout, bad.stderr)
    assert 'choose EMAKI_SHELL_BORDER=soft|full' in bad.stdout + bad.stderr, bad
    (profile / 'calculator-fixture').mkdir()
    shutil.copy(ROOT / 'tests/fixtures/CalculatorTest.qml', profile / 'calculator-fixture/shell.qml')
    shutil.copy(ROOT / 'shell/Calculator.js', profile / 'calculator-fixture/Calculator.js')
    calc_log = (profile / 'calculator.log').open('w')
    calc = subprocess.Popen([qs, '-p', str(profile / 'calculator-fixture'), '--no-color'], env=env, stdout=calc_log, stderr=subprocess.STDOUT)
    try:
        def arithmetic_ready():
            result = subprocess.run([qs, 'ipc', '--pid', str(calc.pid), 'call', 'tests', 'arithmetic'], env=env, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                assert json.loads(result.stdout) == [], result.stdout
                return True
            assert calc.poll() is None
            return False
        try:
            wait_for(arithmetic_ready)
        except TimeoutError:
            raise AssertionError('calculator harness timeout') from None
    finally:
        calc.terminate()
        calc.wait(timeout=3)
        calc_log.close()
    log_path = profile / 'qs.log'
    with log_path.open('w') as log:
        proc = subprocess.Popen([qs, '-p', str(ROOT / 'shell'), '--no-color'], env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        def ipc(*args):
            return subprocess.run([qs, 'ipc', '--pid', str(proc.pid), 'call', *args],
                                  env=env, capture_output=True, text=True, timeout=30)
        def call(*args):
            result = ipc(*args)
            assert result.returncode == 0, (args, result.returncode, result.stdout, result.stderr)
            return result.stdout.strip()
        def wait_state(predicate):
            state = None
            def ready():
                nonlocal state
                assert proc.poll() is None, log_path.read_text()
                reply = ipc('launcher', 'status')
                if reply.returncode == 0:
                    state = json.loads(reply.stdout)
                    if predicate(state):
                        return state
                return None
            try:
                return wait_for(ready)
            except TimeoutError:
                raise AssertionError(('state timeout', state, log_path.read_text())) from None
        try:
            # Only the existing workspaces (two on output A): cells of 28 on a pitch of 30 and
            # 8 px of island on each side (liquid-glass/launcher.html, symmetric inset).
            strip_width = 2 * 30 - 2 + 16
            initial = wait_state(lambda s: s['launcher'] == 'closed' and s['niri']['connection'] == 'connected' and s['search']['app_count'] == 4 and abs(s['workspaces']['width'] - strip_width) < 1e-6)
            assert initial['headless'] and not initial['input_focused']
            assert initial['exclusive_zone'] == (52 if exclusive_zone is None else exclusive_zone)
            assert initial['logo'] == dict(x=10, y=8, width=36, height=36)
            assert initial['workspaces']['x'] == 54 and initial['workspaces']['y'] == 8 and initial['workspaces']['height'] == 36, initial
            # The active workspace is a drop 32 x 42 around its cell (3 px proud of the island).
            left = wait_state(lambda s: s['left_glass']['workspace']['alpha'] == 1)['left_glass']
            assert left['workspace'] == dict(alpha=1, x=6, y=-3, width=32, height=42), left
            assert not left['ready'] and left['logo']['alpha'] == 0, left
            assert initial['time'] == time.strftime('%H:%M')
            assert initial['viewport'] == dict(width=width, height=height)
            call('launcher', 'open')
            opened = wait_state(lambda s: s['launcher_panel']['width'] == 720)
            # An empty All (Recent with nothing logged) is a short panel: header, one line, foot.
            assert opened['launcher'] == 'open' and 140 <= opened['launcher_panel']['height'] <= 560, opened['launcher_panel']
            wait_state(lambda s: s['search']['labels'] == 'ready')
            assert private_title not in call('launcher', 'status')
            # Recent keeps the visible app and skips the two hidden ids logged around it.
            call('launcher', 'mode', 'All')
            recent = wait_state(lambda s: s['search']['recent']['state'] == 'ready' and s['search']['recent']['count'] == 3)['search']
            assert recent['result_count'] == 1 and recent['recent']['groups'] == ['Apps'], recent
            # Every fixture entry has the keyword; only the four listed apps may match.
            call('launcher', 'mode', 'Apps')
            call('launcher', 'query', 'fixture')
            wait_state(lambda s: s['search']['result_count'] == 4)
            call('launcher', 'query', 'Hidden')
            wait_state(lambda s: s['search']['result_count'] == 0)
            call('launcher', 'query', '')
            call('launcher', 'mode', 'Windows')
            call('launcher', 'query', 'PRIVATE_TITLE_10')
            wait_state(lambda s: s['search']['result_count'] == 1 and s['search']['selected_kind'] == 'window')
            call('launcher', 'activate')
            wait_state(lambda s: s['niri']['action'] == 'confirmed')
            action = json.loads((profile / 'actions.jsonl').read_text().splitlines()[-1])
            assert action == ['niri', 'focus-window', '--id', '701', '--json', '--timeout-ms', '1500'], action
            call('launcher', 'open')
            call('launcher', 'mode', 'Apps')
            call('launcher', 'query', 'Alpha')
            wait_state(lambda s: s['search']['result_count'] == 1)
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['launch'] == 'requested' and not s['search']['launch_busy'] and s['launcher'] == 'closed')
            assert json.loads((profile / 'launch.json').read_text()) in (['--', 'emaki-alpha'], ['--', 'emaki-alpha.desktop'])
            (profile / 'launch.json').unlink()
            call('launcher', 'open')
            call('launcher', 'query', 'Terminal')
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['launch'] == 'requested' and not s['search']['launch_busy'] and s['launcher'] == 'closed')
            assert json.loads((profile/'terminal.json').read_text()) == ['-e', '/usr/bin/true']
            call('launcher', 'open')
            assert not (profile / 'launch.json').exists()
            call('launcher', 'query', 'Fail')
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['launch'] == 'failed' and s['launcher'] == 'open')
            call('launcher', 'mode', 'All')  # the launcher opens on Apps; the web fallback lives in All
            call('launcher', 'query', '$(touch injected); eval(1)')
            assert json.loads(call('launcher', 'status'))['search']['selected_kind'] == 'web'
            call('launcher', 'mode', 'All')
            call('launcher', 'query', '(2+3)*4')
            wait_state(lambda s: s['search']['selected_kind'] == 'calculator')
            call('launcher', 'activate')
            wait_state(lambda s: s['launcher'] == 'closed')
            call('launcher', 'open')
            call('launcher', 'mode', 'Files')
            wait_state(lambda s: s['search']['files']['state'] == 'ready' and s['search']['result_count'] == 1 and s['search']['selected_kind'] == 'file')
            assert 'PRIVATE' not in call('launcher', 'status') and str(profile) not in call('launcher', 'status')
            call('launcher', 'query', 'PRIVATE_PARENT')
            assert json.loads(call('launcher', 'status'))['search']['result_count'] == 0
            call('launcher', 'query', 'RECENT12')
            wait_state(lambda s: s['search']['result_count'] == 1)
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['files']['open'] == 'no_handler' and not s['search']['files']['opening'] and s['launcher'] == 'open')
            recent_file.unlink()
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['files']['open'] == 'file_missing' and s['launcher'] == 'open')
            recent_file.write_text('Fixture, not user data.\n')
            call('launcher', 'close')
            call('launcher', 'open')
            call('launcher', 'mode', 'Files')
            wait_state(lambda s: s['search']['files']['state'] == 'ready')
            file_record = profile / 'file-opened.json'
            file_capture = profile / 'capture-file.py'
            file_capture.write_text('import json,sys\nfrom pathlib import Path\n'
                f'Path({str(file_record)!r}).write_text(json.dumps(sys.argv[1:]))\n')
            file_entry = profile / 'data/applications/emaki-file-test.desktop'
            file_entry.write_text('[Desktop Entry]\nType=Application\nName=File test\nNoDisplay=true\n'
                f'Exec=python3 -B {file_capture} %f\nMimeType=text/plain;application/octet-stream;\n')
            file_defaults = profile / 'config/mimeapps.list'
            file_defaults.write_text('[Default Applications]\ntext/plain=emaki-file-test.desktop\napplication/octet-stream=emaki-file-test.desktop\n')
            call('launcher', 'activate')
            wait_state(lambda s: s['search']['files']['open'] == 'requested' and s['launcher'] == 'closed' and file_record.exists())
            assert json.loads(file_record.read_text()) == [str(recent_file)]
            file_entry.unlink()
            file_defaults.unlink()
            call('launcher', 'open')
            call('launcher', 'mode', 'Files')
            wait_state(lambda s: s['search']['files']['state'] == 'ready')
            call('launcher', 'mode', 'All')
            call('launcher', 'open') # idempotent
            assert call('review', 'border', 'full') == 'true'
            assert json.loads(call('launcher', 'status'))['border'] == 'full'
            assert call('review', 'border', 'invalid') == 'false'
            assert json.loads(call('launcher', 'status'))['border'] == 'full'
            assert call('review', 'border', 'soft') == 'true'
            call('launcher', 'toggle')
            closed = wait_state(lambda s: s['launcher'] == 'closed')
            assert closed['launcher_panel']['width'] == 36
            for _ in range(4):
                call('launcher', 'open')
                call('launcher', 'close')
            final = wait_state(lambda s: s['launcher'] == 'closed')
            assert final['launcher_panel']['width'] == 36 and final['query_length'] == 0
            assert call('workspaces', 'focus', '202') == 'true'
            wait_state(lambda s: s['niri']['action'] == 'confirmed')
            assert json.loads((profile / 'actions.jsonl').read_text().splitlines()[-1])[3] == '202'
            assert call('workspaces', 'focus', '999') == 'false'
            helper_pid = int((profile / 'core.pid').read_text())
            assert b'tests/fixtures/shell-core.py' in Path(f'/proc/{helper_pid}/cmdline').read_bytes()
            os.kill(helper_pid, signal.SIGKILL)
            wait_state(lambda s: s['niri']['reason'] == 'core_process_stopped' and s['niri']['window_count'] == 0)
            wait_state(lambda s: s['niri']['connection'] == 'connected')
            observation['model']['keyboard_layouts']['current_idx'] = 1
            publish()
            wait_state(lambda s: s['niri']['layout'] == 'RU')
            # Codes come from evdev.lst, not from the first two letters: Ukrainian is UA (UK is Britain).
            observation['model']['keyboard_layouts'] = dict(names=['English (US)', 'Ukrainian', 'Czech (QWERTY)'], current_idx=1)
            publish()
            wait_state(lambda s: s['niri']['layout'] == 'UA')
            observation['model']['keyboard_layouts']['current_idx'] = 2
            publish()
            wait_state(lambda s: s['niri']['layout'] == 'CZ')
            observation['model']['keyboard_layouts'] = dict(names=['English (US)'], current_idx=0)
            publish()
            wait_state(lambda s: s['niri']['layout_count'] == 1)
            observation['model']['workspaces']['101']['is_active'] = False
            observation['model']['workspaces']['303'] = dict(id=303, idx=11, output='A', is_active=True)
            publish()
            # No pages any more: the island grows by one cell and the drop flows to the third.
            wait_state(lambda s: s['workspace_active'] == 11 and s['workspace_count'] == 3 and s['workspaces']['width'] == 3 * 30 - 2 + 16)
            wait_state(lambda s: s['left_glass']['workspace']['x'] == 8 + 2 * 30 - 2 and s['left_glass']['workspace']['alpha'] == 1)
            observation['model']['workspaces']['303']['id'] = 2**53 + 1
            publish()
            wait_state(lambda s: s['niri']['reason'] == 'id_out_of_js_range' and len(s['niri']['workspaces']) == 0)
            observation['model']['workspaces']['303']['id'] = 303
            saved = observation['model']
            observation['model'] = None
            observation['connection'] = dict(status='disconnected', reason='connection_closed')
            publish()
            wait_state(lambda s: s['niri']['connection'] == 'disconnected' and s['niri']['window_count'] == 0)
            assert call('workspaces', 'focus', '101') == 'false'
            observation['model'] = saved
            observation['connection'] = dict(status='connected', reason='synchronized')
            observation['generation'] = 2
            publish()
            wait_state(lambda s: s['niri']['generation'] == 2 and s['niri']['connection'] == 'connected')
            call('launcher', 'open')
            call('launcher', 'mode', 'Windows')
            wait_state(lambda s: s['search']['selected_window_id'] == 701)
            observation['model']['windows']['700'] = dict(id=700, workspace_id=101, is_focused=False)
            publish()
            wait_state(lambda s: s['search']['result_count'] == 2 and s['search']['selected_window_id'] == 701)
            del observation['model']['windows']['701']
            publish()
            wait_state(lambda s: s['search']['result_count'] == 1 and s['search']['selected_window_id'] is None)
            actions_before = (profile / 'actions.jsonl').read_bytes()
            call('launcher', 'activate')
            assert (profile / 'actions.jsonl').read_bytes() == actions_before
            call('launcher', 'close')
            (profile / 'geometry.json').write_text(json.dumps(dict(closed=initial, opened=opened), indent=2))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
    stop.set()
    thread.join(timeout=2)
    for conn in connections:
        conn.close()
    labels_socket.close()
    log = log_path.read_text()
    assert private_title not in log and 'touch injected' not in log and 'PRIVATE_RECENT12' not in log and 'PRIVATE_PARENT' not in log
    assert 'ERROR' not in log and 'WARN' not in log, log
    assert not list((profile / 'config').rglob('*'))
    # notifications.json: the failed launch of Fail is reported as a drawer notice.
    assert {str(p.relative_to(profile/'state')) for p in (profile/'state').rglob('*')} == {'emaki','emaki/frequent.lock','emaki/frequent.json','emaki/recent.lock','emaki/recent.json','emaki/notifications.json'}
    launches(profile, ['emaki-alpha', 'emaki-fail', 'emaki-terminal', 'emaki-file-test'])
    assert json.loads((profile/'state/emaki/frequent.json').read_text()) == {'emaki-alpha':1,'emaki-terminal':1}
    assert data_before == {str(p): p.read_bytes() for p in (profile / 'data').rglob('*') if p.is_file()}
    print(f'Headless qs IPC/niri/restart/apps/windows/calculator/animation {width}x{height} scale={scale}: OK; {profile.relative_to(ROOT)}')

smoke(1536, 960, 1.25)
smoke(1280, 800, 1, exclusive_zone=0)
print('No Wayland surfaces created; compositor focus/input regions and blur require scheduled live review.')
