import fcntl
import importlib.util
import io
import json
from pathlib import Path
import secrets
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
INSTALLED_UI = '/usr/share/emaki-installer/ui/shell.qml'
spec = importlib.util.spec_from_file_location('ui_helper', UI / 'ui-helper.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

# Test-only probe appended to a temporary copy of shell.qml; never shipped. close() sends the
# window the same Qt close event a compositor's xdg_toplevel.close produces; hide() emits the
# signal behind "Try Emaki first" and the Hide button.
REOPEN_PROBE = '''
    IpcHandler {
        id: reopenProbe
        target: "installertest"
        property int clears: 0
        function state(): string {
            const window = windowLoader.item as FloatingWindow;
            return JSON.stringify({
                loaded: windowLoader.active,
                shown: !!window && window.backingWindowVisible,
                step: controller.step,
                name: controller.fullName,
                clears: reopenProbe.clears
            });
        }
        function prepare(): void {
            controller.fullName = "Alex Morgan";
            controller.step = "you";
        }
        function close(): void {
            const window = windowLoader.item as FloatingWindow;
            window.contentItem.children[0].Window.window.close();
        }
        function hide(): void {
            const window = windowLoader.item as FloatingWindow;
            window.contentItem.children[0].hideRequested();
        }
    }
    Connections {
        target: controller
        function onClearPasswords(): void {
            reopenProbe.clears++;
        }
    }
'''


# Test-only probe for the partition editor: gparted() opens it as the warning's button does,
# on a disk whose stable id differs from its device path, as on real hardware (the mock worker's
# manual-by-id screen; Open GParted probes the disks again before GParted starts).
EDITOR_PROBE = '''
    IpcHandler {
        target: "installertest"
        function state(): string {
            const window = windowLoader.item as FloatingWindow;
            return JSON.stringify({
                ready: controller.session.ready && !!controller.session.inventory,
                shown: !!window && window.backingWindowVisible,
                partitioning: controller.partitioning,
                step: controller.step
            });
        }
        function gparted(): string {
            controller.diskId = controller.session.inventory.disks[0].id;
            if (controller.diskId !== "/dev/disk/by-id/virtio-emaki-target")
                return "unexpected disk id " + controller.diskId;
            controller.mode = "manual";
            controller.step = "disk";
            controller.partitionEditorCommand = [Quickshell.env("EMAKI_TEST_EDITOR")];
            controller.gpartedWarning = true;
            controller.openGparted();
            return "opened";
        }
    }
'''
EDITOR = '''#!/bin/sh
printf '%s\\n' "$@" > "$EMAKI_TEST_EDITOR_ARGS"
i=0
while [ ! -e "$EMAKI_TEST_EDITOR_RELEASE" ] && [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done
'''


# Looks the entry up the way the shell launcher does (DesktopEntries → Quickshell.iconPath(icon,
# true) → an Image at the launcher tile's sourceSize 80) and saves what the icon provider drew.
ICON_PROBE = '''import QtQuick
import Quickshell

ShellRoot {
    FloatingWindow {
        implicitWidth: 80
        implicitHeight: 80
        color: "transparent"
        Image {
            id: icon
            width: 80
            height: 80
            sourceSize: Qt.size(80, 80)
            smooth: false
            onStatusChanged: if (status === Image.Ready)
                grabToImage(result => {
                    console.log("ICON_SAVED " + result.saveToFile(Quickshell.env("ICON_OUT")));
                    Qt.quit();
                })
        }
    }
    Timer {
        property int tries: 0
        interval: 100
        running: true
        repeat: true
        onTriggered: {
            const entry = DesktopEntries.byId("emaki-install");
            if (!entry && ++tries < 50)
                return;
            stop();
            const path = entry ? Quickshell.iconPath(entry.icon, true) : "";
            console.log("ICON_NAME " + (entry ? entry.icon : ""));
            console.log("ICON_PATH " + path);
            console.log("ICON_OLD " + Quickshell.iconPath("system-software-install", true));
            if (path)
                icon.source = path;
            else
                Qt.quit();
        }
    }
}
'''


def fake_launcher_env(root, **extra):
    (root / 'runtime').mkdir(mode=0o700)
    return dict(os.environ, XDG_RUNTIME_DIR=str(root / 'runtime'), EMAKI_LAUNCH_TEST_LOG=str(root / 'calls'),
                PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'], **extra)


def fake_qs(root, body):
    """A stand-in qs that logs "launch" or "ipc", then runs body (Python, argv in sys.argv)."""
    (root / 'bin').mkdir()
    fake = root / 'bin/qs'
    fake.write_text('#!/usr/bin/python3\nimport os, subprocess, sys, time\n'
                    'with open(os.environ["EMAKI_LAUNCH_TEST_LOG"], "a") as f: f.write("ipc\\n" if "ipc" in sys.argv else "launch\\n")\n'
                    + body)
    fake.chmod(0o700)


class Helpers(unittest.TestCase):
    def test_catalog(self):
        rows = helper.layouts()
        self.assertTrue(any(x['layout'] == 'us' and not x['variant'] for x in rows))
        self.assertTrue(any(x['layout'] == 'us' and x['variant'] == 'dvorak' for x in rows))
        self.assertIn('Europe/Lisbon', helper.zones())

    def test_catalog_says_whether_the_boot_medium_is_mounted(self):
        # With copytoram archiso removes the boot mount and the worker's unit never starts
        # (ConditionPathExists); the window learns it here instead of waiting for the worker.
        with tempfile.TemporaryDirectory() as temp, patch.object(helper, 'layouts', return_value=[]), \
                patch.object(helper, 'zones', return_value=['UTC']):
            mount = Path(temp) / 'bootmnt'
            with patch.object(helper, 'BOOT_MOUNT', mount):
                self.assertIs(helper.catalog()['boot_medium'], False)
                mount.mkdir()
                self.assertIs(helper.catalog()['boot_medium'], True)

    def test_nmcli_escape_and_credentials(self):
        self.assertEqual(helper.terse(r'Cafe\: A\\B:72'), ['Cafe: A\\B', '72'])
        secret = secrets.token_hex(16)
        with patch.object(helper, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            result = helper.join(dict(bssid='AA:BB:CC:DD:EE:FF', device='wlan0', password=secret))
            self.assertTrue(result['ok'])
            argv = run.call_args.args[0]
            self.assertNotIn(secret, ' '.join(argv))
            self.assertIn('--ask', argv)
            self.assertEqual(run.call_args.kwargs['input'], (secret + '\n').encode())
            self.assertNotIn(secret, json.dumps(result))

    def test_network_one_row_per_name_and_security(self):
        def scan(in_use):
            wifi = ''.join(f'{"*" if in_use and strength == 60 else " "}:HomeNet:AA\\:BB\\:CC\\:DD\\:EE\\:{n:02X}:{strength}:WPA2:wlan0\n'
                           for n, strength in enumerate((40, 80, 60)))
            wifi += ' :HomeNet:AA\\:BB\\:CC\\:DD\\:EE\\:10:90:--:wlan0\n :Other:AA\\:BB\\:CC\\:DD\\:EE\\:20:50:WPA2:wlan0\n'
            def run(argv, **_):
                return subprocess.CompletedProcess(argv, 0, b'wifi:connected\n' if 'status' in argv else wifi.encode(), b'')
            with patch.object(helper, 'run', side_effect=run):
                return helper.network()['networks']
        rows = scan(True)
        self.assertEqual(len(rows), 3)
        secured = [x for x in rows if x['ssid'] == 'HomeNet' and x['security'] == 'WPA2']
        self.assertEqual([(x['bssid'], x['connected']) for x in secured], [('AA:BB:CC:DD:EE:02', True)])
        self.assertEqual([x['bssid'] for x in rows if x['ssid'] == 'HomeNet' and x['security'] == ''], ['AA:BB:CC:DD:EE:10'])
        self.assertEqual([x['bssid'] for x in scan(False) if x['ssid'] == 'HomeNet' and x['security'] == 'WPA2'], ['AA:BB:CC:DD:EE:01'])

    def test_layout_state_reads_niri_and_names_codes(self):
        # niri answers KeyboardLayouts with xkeyboard-config descriptions; the window gets codes.
        rows = [dict(layout='us', variant='', label='English (US)'), dict(layout='de', variant='', label='German'),
                dict(layout='us', variant='dvorak', label='English (Dvorak)')]
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'niri.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen(1)
                requests = []

                def serve():
                    conn, _ = server.accept()
                    with conn:
                        requests.append(conn.recv(4096))
                        names = ['German', 'English (Dvorak)', 'Custom layout']
                        conn.sendall(json.dumps({'Ok': {'KeyboardLayouts': {'names': names, 'current_idx': 1}}}).encode() + b'\n')
                thread = threading.Thread(target=serve)
                thread.start()
                with patch.dict(os.environ, NIRI_SOCKET=path), patch.object(helper, 'layouts', return_value=rows), \
                        patch.object(helper, 'LEDS', Path(temp) / 'no-leds'):
                    state = helper.layout_state()
                thread.join(5)
        self.assertEqual(requests, [b'"KeyboardLayouts"\n'])
        self.assertEqual(state, dict(ok=True, codes=['DE', 'US', 'Custom layout'], current=1, caps=None, num=None))
        with patch.dict(os.environ, NIRI_SOCKET=''):
            self.assertFalse(helper.layout_state()['ok'])

    def test_caps_lock_is_read_from_the_keyboard_leds(self):
        # As the lock screen reads it (shell/helpers/lock-environment.py): any keyboard's LED on.
        with tempfile.TemporaryDirectory() as temp:
            leds = Path(temp)
            with patch.object(helper, 'LEDS', leds):
                self.assertIsNone(helper.caps_lock())
                for name, value in (('input3::capslock', '0'), ('input3::numlock', '1'), ('input9::capslock', '0')):
                    (leds / name).mkdir()
                    (leds / name / 'brightness').write_text(value + '\n')
                self.assertIs(helper.caps_lock(), False)
                (leds / 'input9::capslock/brightness').write_text('1\n')
                self.assertIs(helper.caps_lock(), True)

    def test_num_lock_is_read_from_the_keyboard_leds(self):
        # The same way as Caps Lock: any keyboard's Num Lock LED on; a Caps Lock LED does not count.
        with tempfile.TemporaryDirectory() as temp:
            leds = Path(temp)
            with patch.object(helper, 'LEDS', leds):
                self.assertIsNone(helper.num_lock())
                for name, value in (('input3::numlock', '0'), ('input3::capslock', '1'), ('input9::numlock', '0')):
                    (leds / name).mkdir()
                    (leds / name / 'brightness').write_text(value + '\n')
                self.assertIs(helper.num_lock(), False)
                (leds / 'input9::numlock/brightness').write_text('1\n')
                self.assertIs(helper.num_lock(), True)

    def test_first_layout_sends_switch_layout_zero(self):
        # What `niri msg action switch-layout 0` sends (niri-ipc 26.04, LayoutSwitchTarget::Index).
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'niri.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen(1)
                requests = []

                def serve():
                    conn, _ = server.accept()
                    with conn:
                        requests.append(conn.recv(4096))
                        conn.sendall(b'{"Ok":"Handled"}\n')
                thread = threading.Thread(target=serve)
                thread.start()
                with patch.dict(os.environ, NIRI_SOCKET=path):
                    result = helper.first_layout()
                thread.join(5)
        self.assertEqual(result, dict(ok=True))
        self.assertEqual([json.loads(r) for r in requests], [{'Action': {'SwitchLayout': {'layout': {'Index': 0}}}}])
        with patch.dict(os.environ, NIRI_SOCKET=''):
            self.assertFalse(helper.first_layout()['ok'])

    def test_layout_events_counts_switches_after_niris_current_state(self):
        # niri 26.04's event stream: the reply, the current state (replicate), then events.
        def layouts(names, idx):
            return {'KeyboardLayoutsChanged': {'keyboard_layouts': {'names': names, 'current_idx': idx}}}
        events = [{'Ok': 'Handled'}, {'WorkspacesChanged': {'workspaces': []}}, layouts(['English (US)', 'Russian'], 0),
                  {'OverviewOpenedOrClosed': {'is_open': False}}, {'KeyboardLayoutSwitched': {'idx': 1}},
                  {'WindowFocusChanged': {'id': None}}, {'KeyboardLayoutSwitched': {'idx': 0}},
                  layouts(['English (US)', 'Russian'], 0), layouts(['German'], 0)]
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'niri.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen(1)
                requests = []

                def serve():
                    conn, _ = server.accept()
                    with conn:
                        requests.append(conn.recv(4096))
                        # Split across writes, as a socket may deliver it.
                        data = b''.join(json.dumps(event).encode() + b'\n' for event in events)
                        for start in range(0, len(data), 37):
                            conn.sendall(data[start:start + 37])
                            time.sleep(.001)
                thread = threading.Thread(target=serve)
                thread.start()
                read, write = os.pipe()
                output = io.StringIO()
                try:
                    with patch.dict(os.environ, NIRI_SOCKET=path), open(read, 'rb', buffering=0) as stdin:
                        helper.layout_events(stdin, output)
                finally:
                    os.close(write)
                thread.join(5)
        self.assertEqual(requests, [b'"EventStream"\n'])
        # Ready after the current state; Russian, back to English (US), German. The same list
        # again is no change.
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [{'ready': True}, {'switched': True}, {'switched': True}, {'switched': True}])

    def test_layout_events_end_when_the_window_closes_its_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'niri.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen(1)
                read, write = os.pipe()
                os.close(write)
                output = io.StringIO()
                with patch.dict(os.environ, NIRI_SOCKET=path), open(read, 'rb', buffering=0) as stdin:
                    helper.layout_events(stdin, output)
        self.assertEqual(output.getvalue(), '')

    @unittest.skipUnless(Path('/usr/share/X11/xkb/rules/evdev.lst').is_file(), 'xkeyboard-config is not installed')
    def test_fake_niri_names_layouts_as_xkeyboard_config_does(self):
        # The window tests' niri (FakeNiri.js) names layouts by their evdev.lst descriptions.
        text = (UI / 'tests/FakeNiri.js').read_text()
        names = dict(re.findall(r'^\s+(\w+): "([^"]+)",?$', text.split('var NAMES = {', 1)[1].split('};', 1)[0], re.M))
        self.assertGreaterEqual(len(names), 5)
        labels = {row['layout']: row['label'] for row in reversed(helper.layouts()) if not row['variant']}
        self.assertEqual(names, {code: labels[code] for code in names})

    def test_trial_refuses_nonlive(self):
        with patch.object(helper, 'trial_available', return_value=False), patch.object(helper.os, 'open') as op:
            self.assertFalse(helper.trial(['us'])['ok'])
            op.assert_not_called()

    def test_trial_is_refused_where_niri_never_reads_the_file(self):
        # The peer of NIRI_SOCKET decides: here it is this test's own process, not niri-emaki.
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'niri.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                server.listen(4)
                with patch.dict(os.environ, NIRI_SOCKET=path):
                    self.assertFalse(helper.trial_reloads())
                    with patch.object(helper.os, 'readlink', return_value='/usr/bin/niri-emaki (deleted)'):
                        self.assertTrue(helper.trial_reloads())
                    with patch.object(helper.os, 'readlink', side_effect=PermissionError()):
                        self.assertTrue(helper.trial_reloads(), 'unknown is not a refusal')
                    with patch.object(helper, 'trial_available', return_value=True), patch.object(helper.os, 'open') as op:
                        self.assertEqual(helper.trial(['de']), dict(ok=False, reloads=False, message='This session does not read the keyboard trial.'))
                        op.assert_not_called()

    def test_media_filters_unmounted_and_nonremovable(self):
        mounts = dict(filesystems=[dict(target='/home/live', source='/dev/vda2'),
                                   dict(target='/run/media/live/USB', source='/dev/sdb1'),
                                   dict(target='/run/media/live/INTERNAL', source='/dev/vda3')])
        def run(argv, **_):
            data = mounts if argv[0] == 'findmnt' else dict(blockdevices=[dict(rm=argv[-1] == '/dev/sdb1', tran='sata')])
            return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'')
        with patch.object(helper, 'run', side_effect=run):
            self.assertEqual(helper.media()['media'], ['/run/media/live/USB'])

    def test_mock_worker_stdio(self):
        secret = secrets.token_hex(16)
        requests = [dict(type='hello', id='h', proto=1), dict(type='probe', id='i'),
                    dict(type='plan', id='p', config=dict(encryption='none', user=dict(password=secret))),
                    dict(type='confirm', id='c', plan_id='fixture-plan', token='mock-capability')]
        result = subprocess.run([sys.executable, '-B', str(UI / 'tests/mock-worker.py'), '--stdio', '--delay', '0'],
                                input=''.join(json.dumps(x) + '\n' for x in requests), text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['type'], 'done')

    def test_launcher_single_instance_and_reopen(self):
        with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
            root = Path(temporary)
            fake_qs(root, 'if "ipc" in sys.argv: print("presented")\nelse: time.sleep(1.5)\n')
            env = fake_launcher_env(root)
            log = root / 'calls'
            first = subprocess.Popen([str(UI / 'emaki-install')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 2
                while not log.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(log.exists())
                second = subprocess.run([str(UI / 'emaki-install')], env=env, capture_output=True, timeout=3)
                self.assertEqual(second.returncode, 0)
                self.assertEqual(log.read_text().splitlines(), ['launch', 'ipc'])
            finally:
                first.terminate()
                first.communicate(timeout=3)

    def test_launcher_fails_when_the_window_does_not_come_back(self):
        # `qs ipc ... call installer show` once printed target metadata and exited 0 without
        # calling anything; the launcher reported success and nothing appeared.
        with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
            root = Path(temporary)
            fake_qs(root, 'print("target installer\\n  function present(): string")\n')
            env = fake_launcher_env(root)
            lock = os.open(root / 'runtime/emaki-install.lock', os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)  # an installer that never answers
                result = subprocess.run([str(UI / 'emaki-install')], env=env, capture_output=True, text=True, timeout=20)
            finally:
                os.close(lock)
            self.assertEqual(result.returncode, 1)
            self.assertIn('did not show its window', result.stderr)
            self.assertIn('function present(): string', result.stderr)
            self.assertNotIn('launch', (root / 'calls').read_text().splitlines())

    def test_launcher_lock_ends_with_the_window_process(self):
        # Quickshell's Process children (and its crash reporter) inherit open descriptors. The
        # lock must not outlive qs through them, or the next launch only pings a dead instance.
        with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
            root = Path(temporary)
            fake_qs(root, 'if "ipc" not in sys.argv:\n'
                          '    child = subprocess.Popen(["sleep", "30"], close_fds=False)\n'
                          '    with open(os.environ["EMAKI_LAUNCH_TEST_CHILDREN"], "a") as f: f.write(f"{child.pid}\\n")\n')
            env = fake_launcher_env(root, EMAKI_LAUNCH_TEST_CHILDREN=str(root / 'children'))
            try:
                for _ in range(2):
                    result = subprocess.run([str(UI / 'emaki-install')], env=env, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, timeout=10)
                    self.assertEqual(result.returncode, 0)
                self.assertEqual((root / 'calls').read_text().splitlines(), ['launch', 'launch'])
            finally:
                for pid in (root / 'children').read_text().split() if (root / 'children').exists() else []:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(shutil.which('qs'), 'Quickshell is not installed')
    def test_window_comes_back_after_close_and_hide(self):
        """The real window, launcher and `qs ipc`, offscreen: no compositor, no visible window."""
        with tempfile.TemporaryDirectory(prefix='eir-') as temporary:  # short: sun_path is 108 bytes
            root = Path(temporary)
            # A worker socket that accepts and stays silent: no reconnect loop, so the only
            # clearPasswords signals come from closing and hiding the window.
            worker = socket.socket(socket.AF_UNIX)
            self.addCleanup(worker.close)
            try:
                worker.bind(str(root / 'w.sock'))
            except PermissionError:
                self.skipTest('this sandbox denies Unix socket binds')
            worker.listen(4)
            ui = root / 'ui'
            shutil.copytree(UI, ui, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
            if not Path('/usr/share/emaki/shell/qmldir').exists():
                for filename in ui.glob('*.qml'):
                    filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"'))
            shell = ui / 'shell.qml'
            text = shell.read_text().rstrip()
            self.assertTrue(text.endswith('}'))
            shell.write_text(text[:-1] + REOPEN_PROBE + '}\n')
            launcher = root / 'emaki-install'
            launcher.write_text((UI / 'emaki-install').read_text().replace(INSTALLED_UI, str(shell)))
            launcher.chmod(0o700)
            for name in ['runtime', 'cache', 'config', 'state', 'data']:
                (root / name).mkdir(mode=0o700)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                       QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=str(root / 'runtime'), XDG_CACHE_HOME=str(root / 'cache'),
                       XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                       EMAKI_INSTALLER_SOCKET=str(root / 'w.sock'))
            for key in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
                env.pop(key, None)

            def probe(function):
                result = subprocess.run(['qs', 'ipc', '-p', str(shell), 'call', 'installertest', function], env=env,
                                        capture_output=True, text=True, timeout=10)
                return result.stdout.strip() if result.returncode == 0 else ''

            def until(**expected):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    reply = probe('state')
                    state = json.loads(reply) if reply.startswith('{') else {}
                    if all(state.get(key) == value for key, value in expected.items()):
                        return state
                    time.sleep(.1)
                self.fail(f'window never reached {expected}; last state {reply!r}; log:\n' + log.read_text())

            def reopen():
                result = subprocess.run([str(launcher)], env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)

            log = root / 'qs.log'
            with log.open('w') as output:
                first = subprocess.Popen([str(launcher)], env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                until(loaded=True, shown=True, clears=0)
                probe('prepare')
                probe('close')
                until(loaded=False, shown=False, clears=1)
                reopen()
                state = until(loaded=True, shown=True)
                self.assertEqual((state['step'], state['name']), ('you', 'Alex Morgan'))
                probe('hide')
                until(loaded=True, shown=False, clears=2)
                reopen()
                state = until(loaded=True, shown=True)
                self.assertEqual((state['step'], state['name']), ('you', 'Alex Morgan'))
                self.assertIsNone(first.poll(), 'the first installer process must keep running')
            finally:
                os.killpg(first.pid, signal.SIGTERM)
                first.wait(timeout=10)
            for diagnostic in ['ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign']:
                self.assertNotIn(diagnostic, log.read_text())

    @unittest.skipUnless(shutil.which('qs'), 'Quickshell is not installed')
    def test_window_steps_aside_while_the_partition_editor_runs(self):
        """GParted opens tiled, under the floating installer (VM walk B3, frame B-19), and on the
        first disk it finds, the live stick. The real window and launcher, offscreen: the editor
        gets the chosen disk's device path, the window hides while it runs and comes back after."""
        with tempfile.TemporaryDirectory(prefix='eir-') as temporary:  # short: sun_path is 108 bytes
            root = Path(temporary)
            worker = subprocess.Popen([sys.executable, '-B', str(UI / 'tests/mock-worker.py'), '--socket', str(root / 'w.sock'),
                                       '--screen', 'manual-by-id', '--delay', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(worker.wait, 5)
            self.addCleanup(worker.terminate)
            if worker.stdout.readline().strip() != str(root / 'w.sock'):
                self.skipTest('the mock worker could not bind its socket: ' + worker.stderr.read())
            ui = root / 'ui'
            shutil.copytree(UI, ui, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
            if not Path('/usr/share/emaki/shell/qmldir').exists():
                for filename in ui.glob('*.qml'):
                    filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"'))
            shell = ui / 'shell.qml'
            text = shell.read_text().rstrip()
            shell.write_text(text[:-1] + EDITOR_PROBE + '}\n')
            launcher = root / 'emaki-install'
            launcher.write_text((UI / 'emaki-install').read_text().replace(INSTALLED_UI, str(shell)))
            launcher.chmod(0o700)
            editor = root / 'editor'
            editor.write_text(EDITOR)
            editor.chmod(0o700)
            for name in ['runtime', 'cache', 'config', 'state', 'data']:
                (root / name).mkdir(mode=0o700)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                       QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=str(root / 'runtime'), XDG_CACHE_HOME=str(root / 'cache'),
                       XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                       EMAKI_INSTALLER_SOCKET=str(root / 'w.sock'), EMAKI_TEST_EDITOR=str(editor),
                       EMAKI_TEST_EDITOR_ARGS=str(root / 'args'), EMAKI_TEST_EDITOR_RELEASE=str(root / 'release'))
            for key in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
                env.pop(key, None)

            def probe(function):
                result = subprocess.run(['qs', 'ipc', '-p', str(shell), 'call', 'installertest', function], env=env,
                                        capture_output=True, text=True, timeout=10)
                return result.stdout.strip() if result.returncode == 0 else ''

            def until(**expected):
                deadline = time.monotonic() + 15
                reply = ''
                while time.monotonic() < deadline:
                    reply = probe('state')
                    state = json.loads(reply) if reply.startswith('{') else {}
                    if all(state.get(key) == value for key, value in expected.items()):
                        return state
                    time.sleep(.1)
                self.fail(f'window never reached {expected}; last state {reply!r}; log:\n' + log.read_text())

            log = root / 'qs.log'
            with log.open('w') as output:
                first = subprocess.Popen([str(launcher)], env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                until(ready=True, shown=True, partitioning=False)
                self.assertEqual(probe('gparted'), 'opened')
                until(partitioning=True, shown=False)
                deadline = time.monotonic() + 5
                while not (root / 'args').exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual((root / 'args').read_text().splitlines(), ['/dev/vda'])
                (root / 'release').touch()
                state = until(partitioning=False, shown=True)
                self.assertEqual(state['step'], 'disk')
            finally:
                os.killpg(first.pid, signal.SIGTERM)
                first.wait(timeout=10)
            for diagnostic in ['ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign']:
                self.assertNotIn(diagnostic, log.read_text())

    @unittest.skipUnless(shutil.which('qs') and Path('/usr/lib/qt6/plugins/platformthemes/libqt6ct.so').exists()
                         and Path('/usr/share/icons/hicolor/index.theme').exists(),
                         'needs qs, the qt6ct platform theme and hicolor-icon-theme')
    def test_launcher_finds_and_draws_the_packaged_icon(self):
        """The packaged entry and icon, found as the shell finds them, drawn crisp at 80 px.

        The live shell runs on Wayland with Qt's generic Unix theme: system icon theme "hicolor",
        search paths $XDG_DATA_DIRS/icons. Offscreen Qt has no icon theme at all, so the probe
        loads qt6ct, a subclass of that generic theme which, with an empty config, gives the same
        two answers. AdwaitaLegacy's system-software-install is not in that chain.
        """
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('python-pillow is not installed')
        spec = importlib.util.spec_from_file_location('icon', ROOT / 'art/icons/emaki-install.py')
        icon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(icon)
        with tempfile.TemporaryDirectory(prefix='emaki-icon-') as temporary:
            root = Path(temporary)
            block = (ROOT / 'packaging/emaki-installer/PKGBUILD').read_text().split('# --- installer window ---')[1].split('# --- end installer window ---')[0]
            subprocess.run(['bash', '-c', 'set -eu\nstage() {\n' + block + '\n}\nstage\n'], cwd=ROOT, check=True,
                           env=dict(os.environ, pkgdir=str(root / 'pkg')))
            (root / 'probe.qml').write_text(ICON_PROBE)
            for name in ['runtime', 'cache', 'config', 'state', 'data']:
                (root / name).mkdir(mode=0o700)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                       QS_DISABLE_CRASH_HANDLER='1', QT_QPA_PLATFORMTHEME='qt6ct', XDG_RUNTIME_DIR=str(root / 'runtime'),
                       XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                       XDG_DATA_DIRS=str(root / 'pkg/usr/share') + ':/usr/share', ICON_OUT=str(root / 'icon.png'))
            for key in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'QS_ICON_THEME', 'XDG_CURRENT_DESKTOP']:
                env.pop(key, None)
            result = subprocess.run(['qs', '-p', str(root / 'probe.qml'), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            log = result.stdout + result.stderr
            self.assertIn('ICON_NAME emaki-install', log)
            self.assertIn('ICON_PATH image://icon/emaki-install', log)
            self.assertIn('ICON_OLD \n', log)
            self.assertIn('ICON_SAVED true', log)
            with Image.open(root / 'icon.png') as image:
                pixels = image.convert('RGBA')
                self.assertEqual(pixels.size, (80, 80))
                rows = icon.VARIANTS[icon.DEFAULT]
                for y in range(80):
                    for x in range(80):
                        cell = rows[y // 5][x // 5]
                        got = pixels.getpixel((x, y))
                        if cell == '.':
                            self.assertEqual(got[3], 0, (x, y))
                        else:
                            self.assertEqual(got, tuple(bytes.fromhex(icon.PALETTE[cell][1:])) + (255,), (x, y))


if __name__ == '__main__': unittest.main()
