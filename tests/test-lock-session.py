#!/usr/bin/env python3
"""Production input/session integration with deliberately broken optional visuals."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import importlib.util
import contextlib
import io
import re
import shlex
from unittest.mock import patch
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
# Exercise the helper protocol with a pipe and fake compositor replies, never a
# live socket. Even stale presentation commands must not wake idle monitors.
spec = importlib.util.spec_from_file_location('lock_environment', ROOT / 'shell/helpers/lock-environment.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
queries = []
def compositor(path, request):
    queries.append(request)
    if request == 'KeyboardLayouts':
        return {'KeyboardLayouts': {'names': ['English (US)', 'Russian'], 'current_idx': 1}}
    if request == 'Outputs':
        return {'Outputs': {'fixture': {'current_mode': 0}}}
    raise AssertionError('metadata helper issued a mutation')
read_fd, write_fd = os.pipe()
os.write(write_fd, b'ignored\npresent\n')
os.close(write_fd)
observed = io.StringIO()
with os.fdopen(read_fd) as commands, patch.dict(os.environ, NIRI_SOCKET='fixture-only'), \
        patch.object(helper, 'request', compositor), patch.object(helper, 'caps_state', return_value=None), \
        patch.object(helper.sys, 'stdin', commands), contextlib.redirect_stdout(observed):
    helper.main()
assert queries and all(query in ('KeyboardLayouts', 'Outputs') for query in queries)
assert '"caps":null' in observed.getvalue() and '"layout":"RU"' in observed.getvalue()
print('PASS: metadata never wakes monitors; unknown Caps LED is not guessed; no window metadata queried')
# One code table for the lock/greeter label and the bar (shell/XkbCodes.js): every layout and
# variant name of the installed evdev.lst gives its xkb code, uppercased, in both readers.
rules = Path(os.environ.get('EMAKI_XKB_RULES') or '/usr/share/X11/xkb/rules/evdev.lst')
assert rules.is_file(), rules
expected, section = {}, ''
for line in rules.read_text().splitlines():
    if line.startswith('!'):
        section = line[1:].strip()
    elif line.strip() and section == 'layout':
        code, _, name = line.strip().partition(' ')
        expected.setdefault(name.strip(), code.upper())
    elif line.strip() and section == 'variant':
        match = re.fullmatch(r'\s*(\S+)\s+(\S+):\s*(.+)', line)
        if match:
            expected.setdefault(match[3].strip(), match[2].upper())
assert len(expected) > 400 and expected['Ukrainian'] == 'UA' and expected['Czech (QWERTY)'] == 'CZ', len(expected)
codes = helper.layout_codes()
assert {name: helper.layout_label(name, codes) for name in expected} == expected
assert helper.layout_label('Something niri made up', codes) == 'Something niri made up'
assert helper.layout_label('', codes) == 'Layout unavailable'
library = (ROOT / 'shell/XkbCodes.js').read_text().replace('.pragma library', '')
script = library + '\nconst t = parse(require("fs").readFileSync(process.argv[1], "utf8"));'
script += '\nconst out = {}; for (const n of JSON.parse(process.argv[2])) out[n] = code(n, t); console.log(JSON.stringify(out));'
names = list(expected) + ['Something niri made up']
got = json.loads(subprocess.run(['node', '-e', script, str(rules), json.dumps(names)], check=True, capture_output=True, text=True).stdout)
assert got == dict(expected, **{'Something niri made up': 'SO'}), [k for k in names if got.get(k) != expected.get(k)][:10]
print('PASS: layout codes follow evdev.lst in the helper and in XkbCodes.js (%d names)' % len(expected))
with tempfile.TemporaryDirectory(prefix='ls-', dir=CACHE) as work:
    profile = Path(work)
    for name in ('r', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copy(ROOT / 'tests/fixtures/lock/LockPam.qml', profile / 'qml/LockPam.qml')
    shutil.copy(ROOT / 'tests/fixtures/LockSessionTest.qml', profile / 'qml/check.qml')
    for visual in ('LockVisual.qml', 'LockWordmark.qml'):
        (profile / 'qml' / visual).write_text('This is a deliberately broken optional visual component\n')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
               XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
               XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
               TMPDIR=str(profile / 'tmp'), PYTHONDONTWRITEBYTECODE='1',
               NIRI_SOCKET='', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'none'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'none-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    # Build a test-only QObject plugin: QML properties always notify on writes,
    # so a QML-only fake would conceal the real QS 0.3.1 acquisition bug again.
    plugin = profile / 'imports/LockNotifyFixture'
    plugin.mkdir(parents=True)
    source = ROOT / 'tests/fixtures/lock/lock-notify-fixture.cpp'
    qt_libexec = subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip()
    qt_cflags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', 'Qt6Qml'], text=True))
    subprocess.run([str(Path(qt_libexec) / 'moc'), *qt_cflags, str(source), '-o', str(plugin / 'lock-notify-fixture.moc')],
                   check=True, text=True, timeout=30)
    qt_flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Qml'], text=True))
    subprocess.run([*shlex.split(os.environ.get('CXX', 'c++')), '-std=c++17', '-shared', '-fPIC',
                    '-I' + str(plugin), str(source), '-o', str(plugin / 'liblocknotifyfixture.so'), *qt_flags],
                   check=True, text=True, timeout=60,
                   env=dict(os.environ, TMPDIR=str(profile / 'tmp')))
    (plugin / 'qmldir').write_text('module LockNotifyFixture\nplugin locknotifyfixture\n')
    notify_env = dict(env, QML_IMPORT_PATH=str(profile / 'imports'))
    production = (ROOT / 'shell/lock.qml').read_text()
    bindings = re.findall(r'^\s*started: (.+)$', production, re.MULTILINE)
    assert len(bindings) == 1, 'exercise the exact production session-start binding'
    fixture = (ROOT / 'tests/fixtures/lock/LockNotifyTest.qml').read_text()
    marker = 'false // PRODUCTION_STARTED_BINDING'
    assert fixture.count(marker) == 1
    for expression, negative in [(bindings[0], False), ('lock.locked', True)]:
        target = profile / 'qml/check-notify.qml'
        target.write_text(fixture.replace(marker, expression))
        result = subprocess.run(['qs', '-p', str(target), '--no-color'], env=notify_env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
        if negative:
            assert result.returncode == 1 and 'LOCK_NOTIFY_FAILED Error: secure notification must start the real session timer' in result.stdout, result.stdout
            print('PASS: regression control rejects the former started: lock.locked binding')
        else:
            assert result.returncode == 0 and 'LOCK_NOTIFY_COMPLETE' in result.stdout, result.stdout
            for line in result.stdout.splitlines():
                if 'LOCK_NOTIFY_PASS ' in line:
                    print(line[line.index('LOCK_NOTIFY_PASS '):])
    result = subprocess.run(['qs', '-p', str(profile / 'qml/check.qml'), '--no-color'],
                            env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 0 and 'LOCK_SESSION_COMPLETE' in result.stdout, result.stdout
    assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
    for line in result.stdout.splitlines():
        if 'LOCK_SESSION_PASS' in line:
            print(line[line.index('LOCK_SESSION_PASS'):])
    # No Window and no frameSwapped: both output mode states must complete the
    # real timer-driven pour and sleep barrier without a compositor IPC wake.
    shutil.copy(ROOT / 'tests/fixtures/LockPresentationTest.qml', profile / 'qml/check-presentation.qml')
    result = subprocess.run(['qs', '-p', str(profile / 'qml/check-presentation.qml'), '--no-color'],
                            env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=10)
    assert result.returncode == 0 and 'LOCK_PRESENTATION_COMPLETE' in result.stdout, result.stdout
    assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
    for line in result.stdout.splitlines():
        if 'LOCK_PRESENTATION_PASS' in line:
            print(line[line.index('LOCK_PRESENTATION_PASS'):])
    # Exercise a real helper death after reporting all outputs off. Stale metadata
    # must not freeze the pour/queued Enter; the restarted helper restores layout.
    helper = profile / 'qml/helpers/lock-environment.py'
    helper.write_text('''import json, sys, time
from pathlib import Path
marker = Path(__file__).with_suffix('.started')
if not marker.exists():
    marker.touch()
    print(json.dumps(dict(layout='RU', outputs_active=False, caps=None)), flush=True)
    time.sleep(.1)
    raise SystemExit(1)
print(json.dumps(dict(layout='EN', outputs_active=True, caps=None)), flush=True)
sys.stdin.buffer.read()
''')
    shutil.copy(ROOT / 'tests/fixtures/LockEnvironmentTest.qml', profile / 'qml/check-environment.qml')
    result = subprocess.run(['qs', '-p', str(profile / 'qml/check-environment.qml'), '--no-color'],
                            env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=10)
    assert result.returncode == 0 and 'LOCK_ENVIRONMENT_PASS' in result.stdout, result.stdout
    assert 'ReferenceError' not in result.stdout and 'TypeError' not in result.stdout, result.stdout
    print('PASS: metadata helper death cannot freeze authentication readiness')
print('PASS: lock input, session, hotplug, broken visuals and reduced motion offscreen')
