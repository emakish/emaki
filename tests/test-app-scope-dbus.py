#!/usr/bin/env python3
"""Cold D-Bus activation must finish before the launch worker exits; private bus only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install

ROOT = Path(__file__).resolve().parent.parent
if '--inside' not in sys.argv:
    root = Path(tempfile.mkdtemp(prefix='ad-', dir=ROOT / '.cache'))
    for name in ('r', 'services', 'data/applications', 'config', 'tmp'):
        (root / name).mkdir(parents=True, mode=0o700)
    config = root / 'bus.conf'
    config.write_text(f'<busconfig><type>session</type><listen>unix:path={root}/r/bus</listen>'
                     f'<auth>EXTERNAL</auth><servicedir>{root}/services</servicedir>'
                     '<policy context="default"><allow send_destination="*"/>'
                     '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    env = dict(os.environ, XDG_DATA_HOME=str(root / 'data'), XDG_DATA_DIRS=str(root / 'data'),
               XDG_CONFIG_HOME=str(root / 'config'), XDG_RUNTIME_DIR=str(root / 'r'),
               TMPDIR=str(root / 'tmp'), DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(root / 'absent'))
    for key in ('DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    subprocess.run(['dbus-run-session', '--config-file=' + str(config), '--', sys.executable,
                    '-B', __file__, '--inside', str(root)], env=env, check=True, timeout=60)
    raise SystemExit

root = Path(sys.argv[-1])
assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path=' + str(root / 'r/bus'))
install(root, os.environ)
service = root / 'service.py'
service.write_text('''import json, os, time
from pathlib import Path
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib
root = Path(__file__).parent
# Startup and the reply are deliberately delayed. Each invocation cold-starts.
time.sleep(.2)
loop = GLib.MainLoop()
xml = '<node><interface name="org.freedesktop.Application"><method name="Open"><arg type="as" direction="in"/><arg type="a{sv}" direction="in"/></method></interface></node>'
node = Gio.DBusNodeInfo.new_for_xml(xml)
def call(conn, sender, path, interface, method, parameters, invocation):
    def reply():
        with (root / 'opened.jsonl').open('a') as log:
            log.write(json.dumps(parameters.unpack()[0]) + '\\n')
        invocation.return_value(None)
        conn.flush_sync(None)
        loop.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(250, reply)
def acquired(conn, name):
    conn.register_object('/org/emaki/ScopeActivation', node.interfaces[0], call, None, None)
Gio.bus_own_name(Gio.BusType.SESSION, 'org.emaki.ScopeActivation', Gio.BusNameOwnerFlags.NONE,
                acquired, None, None)
GLib.timeout_add_seconds(10, loop.quit)
loop.run()
''')
(root / 'services/org.emaki.ScopeActivation.service').write_text(
    '[D-BUS Service]\nName=org.emaki.ScopeActivation\nExec=' + sys.executable + ' -B ' + str(service) + '\n')
desktop = root / 'data/applications/org.emaki.ScopeActivation.desktop'
desktop.write_text('[Desktop Entry]\nType=Application\nName=Scope activation\n'
                   'Exec=/usr/bin/false\nDBusActivatable=true\nMimeType=text/plain;\n')
(root / 'config/mimeapps.list').write_text('[Default Applications]\ntext/plain=org.emaki.ScopeActivation.desktop\n')
subprocess.run(['busctl', '--user', 'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'ReloadConfig'], check=True, capture_output=True)
for i in range(10):
    # Wait for the previous service to leave the bus, never reuse a warm owner.
    until = time.monotonic() + 3
    while subprocess.run(['busctl', '--user', 'status', 'org.emaki.ScopeActivation'],
                         capture_output=True).returncode == 0:
        assert time.monotonic() < until
        time.sleep(.02)
    file = root / f'file-{i}.txt'
    file.write_text('private fixture')
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'shell/helpers/recent-files.py'), 'open'],
                            input=json.dumps(dict(path=str(file))), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)['state'] == 'requested', result
    # No settling sleep: a completed helper must have waited for the activation reply.
    rows = [json.loads(line) for line in (root / 'opened.jsonl').read_text().splitlines()]
    assert len(rows) == i + 1 and rows[-1] == [file.as_uri()], rows
print('PASS: 10/10 cold D-Bus file opens acknowledged before helper exit; no linger heuristic')
