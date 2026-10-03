#!/usr/bin/env python3
"""Guest only; use ssh.sh 'python3 -' < tests/vm/check-app-scope.py after installing."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import uuid

if subprocess.run(['systemd-detect-virt', '--vm', '--quiet']).returncode:
    raise SystemExit('Refusing: this check restarts emaki-shell and must run in the VM.')
os.environ['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
displays = sorted(p.name for p in runtime.glob('wayland-*') if not p.name.endswith('.lock'))
if not displays:
    raise SystemExit('Log into the guest Niri session first.')
os.environ['WAYLAND_DISPLAY'] = displays[0]


def command(*argv):
    return subprocess.check_output(argv, text=True, timeout=15).strip()


def shell(*args):
    return command('emaki-shell', 'call', *args)


def wait_for(check, seconds=15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.2)
    raise AssertionError('Timed out waiting for guest app/shell')


def identity(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def scope(pid):
    cgroup = Path(f'/proc/{pid}/cgroup').read_text()
    assert '/app.slice/' in cgroup and 'emaki-shell.service' not in cgroup, cgroup
    found = re.search(r'/(app-emaki-[^/\n]+\.scope)(?:/|$)', cgroup)
    assert found, cgroup
    unit = found[1]
    assert command('systemctl', '--user', 'show', unit, '-p', 'Slice', '--value') == 'app.slice'
    return unit


def shell_helpers():
    """The service's surviving internal children must not follow apps into scopes."""
    main = int(command('systemctl', '--user', 'show', 'emaki-shell', '-p', 'MainPID', '--value'))
    expected = Path(f'/proc/{main}/cgroup').read_text()
    assert 'emaki-shell.service' in expected
    children = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            tail = path.read_text().rsplit(')', 1)[1].split()
            if int(tail[1]) != main:
                continue
            pid = int(path.parent.name)
            argv = (path.parent / 'cmdline').read_bytes().split(b'\0')
            # qs directly owns the core watcher and protocol/monitor helpers.
            if any(b'/helpers/' in arg for arg in argv) or Path(os.fsdecode(argv[0])).name in ('emaki', 'nmcli', 'udevadm'):
                assert (path.parent / 'cgroup').read_text() == expected, (pid, argv)
                children.append((pid, [os.fsdecode(a) for a in argv if a]))
        except (FileNotFoundError, ProcessLookupError):
            pass
    assert any('watch' in argv and 'niri' in argv for _, argv in children), children
    print('PASS shell helpers in emaki-shell.service:', children, flush=True)


token = uuid.uuid4().hex[:8]
ids = {site: f'org.emaki.Scope{site.title()}.{token}' for site in ('dock', 'launcher', 'file')}
applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
applications.mkdir(parents=True, exist_ok=True)
entries = []
owned = {}
defaults = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'mimeapps.list'
recent = applications.parent / 'recently-used.xbel'
saved = {path: path.read_bytes() if path.exists() else None for path in (defaults, recent)}
with tempfile.TemporaryDirectory(prefix='emaki-scope-') as folder:
    folder = Path(folder)
    probe = folder / 'probe.py'
    probe.write_text('''import json, os, sys
from pathlib import Path
site, folder = sys.argv[1:3]
Path(folder, site + '.json').write_text(json.dumps(os.getpid()))
os.execvp('kitty', ['kitty', '--class', 'emaki-scope-' + site,
                    '--title', 'Scope survival: ' + site, 'sleep', '300'])
''')
    try:
        for site, ident in ids.items():
            entry = applications / (ident + '.desktop')
            entry.write_text('[Desktop Entry]\nType=Application\nName=Scope ' + site + ' ' + token +
                             '\nIcon=utilities-terminal\nExec=python3 ' + str(probe) + ' ' + site + ' ' + str(folder) +
                             (' %f\nNoDisplay=true\nMimeType=text/plain;\n' if site == 'file' else '\n') +
                             'Terminal=false\nDBusActivatable=false\n')
            entries.append(entry)
        sample = folder / ('Scope file ' + token + '.txt')
        sample.write_text('Scope survival fixture')
        defaults.parent.mkdir(parents=True, exist_ok=True)
        defaults.write_text('[Default Applications]\ntext/plain=' + ids['file'] + '.desktop\n')
        recent.write_text('<xbel><bookmark href="' + sample.as_uri() + '"/></xbel>')
        # DesktopEntries watches the applications directory; allow its update to settle.
        time.sleep(2)
        assert shell('dock', 'pin', ids['dock']) == 'true'
        print('In the VM, click the newly pinned "Scope dock ' + token + '" icon (within 90 s).', flush=True)
        wait_for(lambda: (folder / 'dock.json').exists(), 90)
        shell('launcher', 'open')
        shell('launcher', 'mode', 'Apps')
        shell('launcher', 'query', 'Scope launcher ' + token)
        wait_for(lambda: json.loads(shell('launcher', 'status'))['search']['result_count'] == 1)
        shell('launcher', 'activate')
        wait_for(lambda: (folder / 'launcher.json').exists())
        shell('launcher', 'open')
        shell('launcher', 'mode', 'Files')
        shell('launcher', 'query', 'Scope file ' + token)
        wait_for(lambda: json.loads(shell('launcher', 'status'))['search']['files']['state'] == 'ready'
                 and json.loads(shell('launcher', 'status'))['search']['result_count'] == 1)
        shell('launcher', 'activate')
        wait_for(lambda: (folder / 'file.json').exists())
        for site in ids:
            pid = json.loads((folder / (site + '.json')).read_text())
            owned[site] = (pid, identity(pid), None)
            owned[site] = (pid, owned[site][1], scope(pid))
        assert len({v[2] for v in owned.values()}) == 3, owned
        shell_helpers()
        old = command('systemctl', '--user', 'show', 'emaki-shell', '-p', 'MainPID', '--value')
        command('systemctl', '--user', 'restart', 'emaki-shell')
        wait_for(lambda: command('systemctl', '--user', 'show', 'emaki-shell', '-p', 'MainPID', '--value') not in ('0', old))
        time.sleep(2)
        shell_helpers()
        for site, (pid, started, unit) in owned.items():
            os.kill(pid, 0)
            assert identity(pid) == started and scope(pid) == unit
            print(f'PASS {site}: pid={pid}, survived shell restart, {unit}', flush=True)
    finally:
        for path, content in saved.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        subprocess.run(['emaki-shell', 'call', 'dock', 'unpin', ids['dock']], timeout=10)
        for entry in entries:
            entry.unlink(missing_ok=True)
        for site in ids:
            record = folder / (site + '.json')
            if record.exists():
                pid = json.loads(record.read_text())
                try:
                    unit = scope(pid)
                    subprocess.run(['systemctl', '--user', 'stop', unit], timeout=10)
                except (FileNotFoundError, ProcessLookupError):
                    pass
                except AssertionError:
                    # On the unfixed shell, never stop its whole service to clean up
                    # a probe. Terminate only the still-identifiable fixture kitty.
                    try:
                        cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                        if ('emaki-scope-' + site).encode() in cmdline:
                            os.kill(pid, signal.SIGTERM)
                    except (FileNotFoundError, ProcessLookupError):
                        pass
