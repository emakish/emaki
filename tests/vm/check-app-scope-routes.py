#!/usr/bin/env python3
"""Real user-manager/GIO/clipboard scopes in the VM; --logout also ends its session."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

if subprocess.run(['systemd-detect-virt', '--vm', '--quiet']).returncode:
    raise SystemExit('Refusing: this real-manager check is for the disposable VM only.')
runtime = '/run/user/' + str(os.getuid())
env = dict(os.environ, XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/bus')
for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
    env.pop(key, None)
helpers = Path('/usr/share/emaki/shell/helpers')


def command(*argv):
    return subprocess.check_output(argv, env=env, text=True, timeout=15).strip()


def wait_for(check, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for real-scope fixture')


assert command('systemctl', '--user', 'is-active', 'graphical-session.target') == 'active'
owned = []
with tempfile.TemporaryDirectory(prefix='scope-routes-', dir=runtime) as folder:
    folder = Path(folder)
    for name in ('data/applications', 'config', 'cache/cliphist'):
        (folder / name).mkdir(parents=True)
    env.update(XDG_DATA_HOME=str(folder / 'data'), XDG_DATA_DIRS=str(folder / 'data'),
               XDG_CONFIG_HOME=str(folder / 'config'), XDG_CACHE_HOME=str(folder / 'cache'),
               EMAKI_SCOPE_PROBE=str(folder))
    probe = folder / 'probe.py'
    probe.write_text('''import json, os, sys, time
from pathlib import Path
folder = Path(os.environ['EMAKI_SCOPE_PROBE'])
site = sys.argv[1]
if site == 'clipboard':
    assert sys.stdin.buffer.read() == b'fixture clipboard'
    if os.fork(): sys.exit(0)
elif site == 'browser':
    site = 'portal' if sys.argv[-1] == 'http://nmcheck.gnome.org/' else 'web'
pid = os.getpid()
data = dict(pid=pid, start=Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
temporary = folder / (site + '.tmp')
temporary.write_text(json.dumps(data)); temporary.rename(folder / (site + '.json'))
time.sleep(120)
''')
    for site in ('terminal', 'file', 'browser'):
        (folder / f'data/applications/org.emaki.Scope.{site}.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Scope ' + site + '\nDBusActivatable=false\n'
            'Exec=python3 -B ' + str(probe) + ' ' + site + ' %U\n'
            'Terminal=' + str(site == 'terminal').lower() + '\nMimeType=text/plain;x-scheme-handler/https;x-scheme-handler/http;\n')
    (folder / 'config/mimeapps.list').write_text('[Default Applications]\n'
        'text/plain=org.emaki.Scope.file.desktop\nx-scheme-handler/https=org.emaki.Scope.browser.desktop\n'
        'x-scheme-handler/http=org.emaki.Scope.browser.desktop\n')
    terminal = folder / 'terminal'
    terminal.write_text('#!/bin/sh\nshift\nexec "$@"\n'); terminal.chmod(0o700)
    copy = folder / 'wl-copy'
    copy.write_text('#!/bin/sh\nexec python3 -B ' + str(probe) + ' clipboard\n'); copy.chmod(0o700)
    cliphist = folder / 'cliphist'
    cliphist.write_text('#!/bin/sh\nprintf "fixture clipboard"\n'); cliphist.chmod(0o700)
    env.update(EMAKI_WL_COPY=str(copy), EMAKI_CLIPHIST=str(cliphist))
    env['XDG_RUNTIME_DIR'] = str(folder)
    (folder / 'emaki-cliphist.db').touch()
    file = folder / 'file.txt'; file.write_text('fixture')
    requests = [
        ('terminal', 'launcher-tools.py', [], dict(op='terminal', id='org.emaki.Scope.terminal', terminal=str(terminal))),
        ('file', 'recent-files.py', ['open'], dict(path=str(file))),
        ('web', 'launcher-tools.py', [], dict(op='web', query='scope fixture')),
        ('portal', 'system-tools.py', [], dict(op='portal-open')),
        ('clipboard', 'launcher-tools.py', [], dict(op='clip-copy', id='1')),
    ]
    try:
        for site, helper, args, request in requests:
            result = subprocess.run(['python3', '-B', str(helpers / helper), *args],
                                    input=json.dumps(request) + '\n', env=env, text=True,
                                    capture_output=True, timeout=15)
            assert result.returncode == 0 and json.loads(result.stdout)['state'] in ('requested', 'copied'), result
            wait_for(lambda: (folder / (site + '.json')).exists())
            record = json.loads((folder / (site + '.json')).read_text())
            owned.append(record)
            assert 'scope unavailable' not in result.stderr, result.stderr
            pid = record['pid']
            path = Path(f'/proc/{pid}/cgroup').read_text().strip().split(':', 2)[2]
            unit = path.rsplit('/', 1)[1]
            record['unit'] = unit
            assert '/app.slice/app-emaki-' in path and unit.endswith('.scope'), path
            for prop in ('BindsTo', 'PartOf', 'After'):
                assert 'graphical-session.target' in command('systemctl', '--user', 'show', unit, '-p', prop, '--value').split()
            assert command('systemctl', '--user', 'show', unit, '-p', 'Slice', '--value') == 'app.slice'
            for fd in ('1', '2'):
                assert os.readlink(f'/proc/{pid}/fd/{fd}') == '/dev/null'
            print(f'PASS real scope {site}: pid={pid}, {unit}, session-bound, stdio=/dev/null', flush=True)
        assert len({record['unit'] for record in owned}) == len(requests)
        if '--logout' in sys.argv:
            print('Ending the disposable guest graphical session to verify BindsTo/PartOf.', flush=True)
            command('systemctl', '--user', 'stop', 'graphical-session.target')
            wait_for(lambda: all(not Path(f"/proc/{record['pid']}").exists() for record in owned))
            print('PASS: every app and background clipboard owner stopped with the graphical session', flush=True)
    finally:
        for record in owned:
            try:
                pid = record['pid']
                if Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19] == record['start']:
                    if 'unit' in record and record['unit'].startswith('app-emaki-'):
                        subprocess.run(['systemctl', '--user', 'stop', record['unit']], env=env, timeout=10)
                    else:
                        os.kill(pid, signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError):
                pass
