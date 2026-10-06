#!/usr/bin/env python3
"""Scope/fallback regression, plus offscreen production launch wiring without IPC."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install, launches
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
root = Path(tempfile.mkdtemp(prefix='as-', dir=ROOT / '.cache'))
for name in ('r', 'c', 's', 'd/applications', 'cache', 'tmp'):
    (root / name).mkdir(parents=True, mode=0o700)
env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
           QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1',
           XDG_RUNTIME_DIR=str(root / 'r'), XDG_CONFIG_HOME=str(root / 'c'),
           XDG_DATA_HOME=str(root / 'd'), XDG_DATA_DIRS=str(root / 'd'),
           XDG_STATE_HOME=str(root / 's'), XDG_CACHE_HOME=str(root / 'cache'),
           TMPDIR=str(root / 'tmp'), DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'no-bus'),
           DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(root / 'no-system'))
for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
    env.pop(name, None)
install(root, env)
# The guard must allow the settings core's real validator, while refusing session IPC.
subprocess.run(['niri', '--version'], env=env, check=True, capture_output=True)
subprocess.run(['niri', 'validate', '--config', str(ROOT / 'niri/default.kdl')],
               env=env, check=True, capture_output=True)
assert subprocess.run(['niri', 'msg', 'action', 'spawn', '--', 'false'],
                      env=env, capture_output=True).returncode != 0
app = root / 'app.py'
app.write_text('''import json, os, sys, time
from pathlib import Path
if 'forked' in sys.argv:
    if os.fork(): sys.exit(0)
    time.sleep(.1)
for fd in Path('/proc/self/fd').iterdir():
    try: assert not os.readlink(fd).endswith(' (deleted)')
    except FileNotFoundError: pass
with (Path(os.environ['EMAKI_SCOPE_FIXTURE']) / 'fds.jsonl').open('a') as log:
    log.write(json.dumps([os.readlink('/proc/self/fd/' + str(fd)) for fd in (1, 2)]) + '\\n')
with (Path(os.environ['EMAKI_SCOPE_FIXTURE']) / 'apps.jsonl').open('a') as log:
    log.write(json.dumps([os.environ.get('EMAKI_TEST_SCOPE'), sys.argv[1:]]) + '\\n')
if 'slow' in sys.argv: time.sleep(3)
if 'overrun' in sys.argv:
    time.sleep(5.5)
    Path(os.environ['EMAKI_SCOPE_FIXTURE'], 'finished').touch()
if 'fail' in sys.argv: print('fixture launch failed', file=sys.stderr)
sys.exit(7 if 'fail' in sys.argv else 0)
''')
helper = ROOT / 'shell/helpers/app_scope.py'
warning = 'emaki: app scope unavailable; launching without cgroup isolation'


def records():
    return [json.loads(line) for line in (root / 'apps.jsonl').read_text().splitlines()]


def launch(*args, environment=env):
    return subprocess.run([sys.executable, '-B', str(helper), '--exec', 'org.test-App.desktop',
                           sys.executable, '-B', str(app), *args], env=environment,
                          capture_output=True, text=True, timeout=10)


literal = 'PRIVATE $HOME $(touch BAD); кавычки'
assert launch(literal).returncode == 0
assert records()[-1][0].startswith(r'app-emaki-org.test\x2dApp.desktop-')
assert records()[-1][1] == [literal]
assert launch('slow').returncode == 0  # Setup deadline is not an application deadline.
assert launch('forked').returncode == 0
assert launch('overrun').returncode == 0  # Already handed off: never report failed.
time.sleep(.7)
assert (root / 'finished').exists(), 'the slow launcher was killed after acceptance'
before = len(records())
failed = launch('fail')
assert failed.returncode == 7 and warning not in failed.stderr and 'fixture launch failed' not in failed.stderr
assert len(records()) == before + 1  # An application failure never retries.
for mode in ('fail', 'late'):
    (root / 'scope-mode').write_text(mode)
    before = len(records())
    result = launch('manager-' + mode, 'slow')
    assert result.returncode == 0 and result.stderr.count(warning) == 1, result
    time.sleep(.5)  # Let the deliberately late worker try its revoked ticket.
    assert len(records()) == before + 1 and records()[-1] == [None, ['manager-' + mode, 'slow']], records()
missing = launch('missing', environment=dict(env, PATH=str(root / 'empty-bin')))
assert missing.returncode == 0 and missing.stderr.count(warning) == 1, missing
assert records()[-1] == [None, ['missing']]
launches(root, ['org.test-App.desktop'])

# A wl-copy-like daemon receives its bytes privately and forks a selection owner.
# The original JSON helper must finish, while that owner retains the app scope.
(root / 'scope-mode').write_text('ok')
copier = root / 'copy.py'
copier.write_text('''import json, os, sys, time
from pathlib import Path
data = sys.stdin.buffer.read()
if os.fork(): sys.exit(0)
root = Path(os.environ['EMAKI_SCOPE_FIXTURE'])
assert data == b'private clipboard bytes'
p = root / 'owner.tmp'
p.write_text(json.dumps([os.getpid(), os.environ.get('EMAKI_TEST_SCOPE')]))
p.rename(root / 'owner.json')
time.sleep(10)
''')
# Exercise the public helper operation rather than moving the protocol helper itself.
cliphist = root / 'cliphist'
cliphist.write_text('#!/bin/sh\nprintf "private clipboard bytes"\n'); cliphist.chmod(0o700)
copy = root / 'wl-copy'
copy.write_text('#!/bin/sh\nexec ' + sys.executable + ' -B ' + str(copier) + '\n'); copy.chmod(0o700)
(root / 'cache/cliphist').mkdir(); (root / 'cache/cliphist/db').touch()
copied = subprocess.run([sys.executable, '-B', str(ROOT / 'shell/helpers/launcher-tools.py')],
                        input=json.dumps(dict(op='clip-copy', id='1')), text=True, capture_output=True,
                        env=dict(env, EMAKI_CLIPHIST=str(cliphist), EMAKI_WL_COPY=str(copy)), timeout=10)
assert json.loads(copied.stdout)['state'] == 'copied', copied
until = time.monotonic() + 2
while not (root / 'owner.json').exists():
    assert time.monotonic() < until
    time.sleep(.02)
pid, unit = json.loads((root / 'owner.json').read_text())
try:
    os.kill(pid, 0)
    assert unit.startswith(r'app-emaki-wl\x2dcopy-'), unit
    assert os.readlink(f'/proc/{pid}/fd/2') == '/dev/null'
finally:
    import signal
    os.kill(pid, signal.SIGTERM)

# Automatically drive actual AppCatalog/PrivateJob/RecentFiles, so this also works
# where the sandbox forbids binding the Quickshell IPC socket. Two fallback routes
# share one warning in the shell; helper protocols continue to report acceptance.
(root / 'scope-mode').write_text('fail')
apps = root / 'd/applications'
for ident in ('scope-app', 'scope-term', 'scope-file'):
    (apps / (ident + '.desktop')).write_text(
        '[Desktop Entry]\nType=Application\nName=' + ident + '\nExec=' + sys.executable +
        ' -B ' + str(app) + ' ' + ident + ' %U\nTerminal=' + str(ident == 'scope-term').lower() +
        '\nMimeType=text/plain;x-scheme-handler/https;x-scheme-handler/http;\n')
(root / 'c/mimeapps.list').write_text('[Default Applications]\ntext/plain=scope-file.desktop\nx-scheme-handler/https=scope-file.desktop\nx-scheme-handler/http=scope-file.desktop\n')
file = root / 'sample.txt'
file.write_text('fixture')
(root / 'd/recently-used.xbel').write_text('<xbel><bookmark href="' + file.as_uri() + '"/></xbel>')
terminal = root / 'terminal'
terminal.write_text('#!/bin/sh\nshift\nexec "$@"\n')
terminal.chmod(0o700)
# gtk-launch needs a display even with isolated XDG: record its literal argv here.
gtk = root / 'gtk-launch'
gtk.write_text('#!/usr/bin/python3\nimport os, sys, time\ntime.sleep(3)\n'
               'os.execv(' + repr(sys.executable) + ', [' + repr(sys.executable) + ', "-B", '
               + repr(str(app)) + ', *sys.argv[1:]])\n')
gtk.chmod(0o700)
env.update(EMAKI_TERMINAL=str(terminal), EMAKI_GTK_LAUNCH=str(gtk), EMAKI_SCOPE_FILE=str(file))
qml = root / 'q'
shutil.copytree(ROOT / 'shell', qml)
(qml / 'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    id: root
    property int stage: 0
    AppCatalog {
        id: apps
        onLaunched: {
            if (root.stage === 0) {
                root.stage = 1;
                next.restart();
            } else {
                root.stage = 2;
                job.start({op: "web", query: "PRIVATE query"});
            }
        }
        onFailed: Qt.exit(3)
    }
    PrivateJob {
        id: job
        onCompleted: value => {
            if (value.state !== "requested") Qt.exit(4);
            portal.start({op: "portal-open"});
        }
    }
    PrivateJob {
        id: portal
        helper: Quickshell.shellPath("helpers/system-tools.py")
        onCompleted: value => {
            if (value.state !== "requested") Qt.exit(6);
            files.open(Quickshell.env("EMAKI_SCOPE_FILE"));
        }
    }
    RecentFiles {
        id: files
        active: true
        onLaunched: { console.log("APP_SCOPE_PASS"); Qt.quit(); }
    }
    Timer {
        id: next
        interval: 100
        running: true
        onTriggered: apps.launch(apps.entries.find(e => e.id === (root.stage === 0 ? "scope-app" : "scope-term")))
    }
    Timer { interval: 10000; running: true; onTriggered: Qt.exit(5) }
    Component.onCompleted: Quickshell.watchFiles = false
}
''')
for mode in ('ok', 'fail'):
    (root / 'scope-mode').write_text(mode)
    before = len(records())
    result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                            capture_output=True, text=True, timeout=15)
    log = result.stdout + result.stderr
    (root / ('qs-' + mode + '.log')).write_text(log)
    assert result.returncode == 0 and 'APP_SCOPE_PASS' in log, log
    assert log.count(warning) == (1 if mode == 'fail' else 0), log
    assert 'PRIVATE' not in log and 'TypeError' not in log and 'ReferenceError' not in log, log
    until = time.monotonic() + 2
    while len(records()) < before + 5:
        assert time.monotonic() < until
        time.sleep(.02)
    assert all(bool(scope) == (mode == 'ok') for scope, _ in records()[-5:]), records()
assert len(launches(root, ['org.test-App.desktop', 'scope-app', 'scope-term', 'scope-file', 'wl-copy'])) == 18
assert len(records()) == 18, records()  # Eight CLI attempts plus five QML routes twice.
assert all(json.loads(line) == ['/dev/null', '/dev/null']
           for line in (root / 'fds.jsonl').read_text().splitlines())
print('PASS: unit/slice/escaping, literal argv, app failure without retry, absent/failed/late manager, offscreen app/terminal/web/portal/file fallback and one warning;', root)
