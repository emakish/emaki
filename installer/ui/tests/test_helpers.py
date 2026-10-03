import fcntl
import importlib.util
import json
from pathlib import Path
import secrets
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
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

    def test_trial_refuses_nonlive(self):
        with patch.object(helper, 'trial_available', return_value=False), patch.object(helper.os, 'open') as op:
            self.assertFalse(helper.trial(['us'])['ok'])
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
                    dict(type='plan', id='p', config=dict(user=dict(password=secret))),
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
