#!/usr/bin/env python3
"""Production WlrLayershell backend and capture handlers, with deterministic visual pixels.

Instantiate PanelWindow's production WlrLayershell backend directly: the factory
selects no backend on offscreen. Set native geometry in lieu of compositor configure;
the real proxy content item, QML scene and grab handlers are unchanged.
The plate is still the real QML Loader item used by LockSurface.
"""
import json
import os
import re
import resource
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
for scale in (1, 1.5, 2):
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='greeter-grab-') as tmp:
        base = Path(tmp)
        qml = base / 'qml'
        shutil.copytree(ROOT / 'shell', qml)
        for name in ('runtime', 'config', 'cache', 'state', 'data'):
            (base / name).mkdir(mode=0o700)
        for name in ('lock-environment', 'wallpaper'):
            (qml / f'helpers/{name}.py').write_text('import sys\nsys.stdin.read()\n')
        # Keep the production visual root/Loader, but paint an exact opaque plate.
        visual = (qml / 'LockVisual.qml').read_text()
        visual = visual[:visual.rfind('}')] + 'Rectangle { anchors.fill: parent; color: "#1256ab"; z: 999 }\n}\n'
        (qml / 'LockVisual.qml').write_text(visual)
        entry = (qml / 'greeter.qml').read_text()
        panel = entry[entry.index('        PanelWindow {'):entry.rfind('\n    }')]
        panel = panel.replace('PanelWindow {', 'WlrLayershell {\n            implicitWidth: 320; implicitHeight: 240')
        panel = re.sub(r'            anchors \{.*?\n            }', '', panel, flags=re.S)
        panel = panel.replace('required property ShellScreen modelData', 'property ShellScreen modelData: Quickshell.screens[0]')
        # A second visible scene element proves the grab includes the scene, not just its plate.
        panel = panel.replace('id: greeterScene', '''id: greeterScene
                Rectangle { x: 4; y: 4; width: 20; height: 20; color: "#ed3210"; z: 999 }''')
        entry = '''pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland
Scope {
    id: root
    property bool captured: false
    property bool launchRecorded: false
    property bool selectionLoaded: true
    property string picturePath: PATH
    signal captureRequested
    AuthController { id: controller; property string handoffMarker: '{"token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' }
    GreeterSession { id: lockSession; auth: controller; started: false; phase: "handoff"; clock: 4 }
    LockEnvironment { id: lockEnvironment }
    QtObject { id: memory; property string wallpaperRoot: "" }
    QtObject {
        id: publishPicture
        property list<string> command: []
        property bool running: false
        onRunningChanged: {
            if (command[4] !== "publish" || command[5] !== "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                Qt.exit(3);
            console.log("GRAB_SIZE " + JSON.stringify([panel.width, panel.height, panel.screen.devicePixelRatio]));
            Qt.callLater(Qt.quit);
        }
    }
    Timer { interval: 100; running: true; onTriggered: {
        panel.contentItem.Window.window.width = 320;
        panel.contentItem.Window.window.height = 240;
    } }
    Timer { interval: 400; running: true; onTriggered: root.captureRequested() }
    Timer { interval: 2200; running: true; onTriggered: Qt.exit(2) }
PANEL
}
'''.replace('PATH', json.dumps(str(base / 'frame.png'))).replace('PANEL', panel)
        (qml / 'check.qml').write_text(entry)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR=str(scale),
                   QML_DISABLE_DISK_CACHE='1', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'GREETD_SOCK'):
            env.pop(name, None)
        for name in ('runtime', 'config', 'cache', 'state', 'data'):
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(base / name)
        try:
            done = subprocess.run(['qs', '-p', str(qml / 'check.qml'), '--no-color'], env=env,
                                  text=True, capture_output=True, timeout=5,
                                  preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
        except subprocess.TimeoutExpired as error:
            raise AssertionError((error.stdout, error.stderr)) from error
        log = done.stdout + done.stderr
        assert done.returncode == 0 and 'GRAB_SIZE ' in log, log
        assert 'grabToImage:' not in log and 'ReferenceError' not in log and 'TypeError' not in log, log
        dimensions = json.loads(log.split('GRAB_SIZE ', 1)[1].splitlines()[0])
        assert dimensions[:2] == [320, 240], dimensions
        expected = tuple(int(v * dimensions[2] + .5) for v in dimensions[:2])
        with Image.open(base / 'frame.png') as full, Image.open(base / 'frame-plate.png') as plate:
            assert full.size == plate.size == expected, (full.size, plate.size, expected)
            assert plate.convert('RGB').getextrema() == ((18,18), (86,86), (171,171))
            full = full.convert('RGB')
            assert full.getpixel((int(10 * scale), int(10 * scale))) == (237,50,16)
            assert full.getpixel((expected[0]-2, expected[1]-2)) == (18,86,171)
        print('PASS real WlrLayershell (PanelWindow backend) scene and Loader plate grab, scale', scale)
