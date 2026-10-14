#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode real image sources and exercise fallback in the shipped icon component."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" fill="#ef476f"/></svg>'
QML = '''import QtQuick
import QtTest
import Quickshell
ShellRoot {
    id: test
    property bool failed: false
    ProgramIcon { id: icon }
    TestCase { id: input; when: false; name: "ProgramIcons" }
    function check(ok, description) {
        if (!ok) { failed = true; console.error("ASSERTION_FAILED " + description); }
    }
    function load(icons, expected, theme = "") {
        icon.program = {icons: icons, icon: theme};
        let left = 5000;
        while (left > 0 && icon.children[0].status !== Image.Ready) {
            input.wait(20); left -= 20;
        }
        check(icon.children[0].status === Image.Ready, "image decoded: " + expected);
        check(String(icon.children[0].source) === expected, "selected source: " + expected);
        check(!input.findChild(icon, "package-icon").visible, "decoded image hides package fallback");
    }
    function packageFallback(size) {
        icon.width = size; icon.height = size;
        const fallback = input.findChild(icon, "package-icon");
        const body = input.findChild(icon, "package-body");
        const tape = input.findChild(icon, "package-tape");
        check(fallback.visible, "package fallback is visible at " + size);
        check(body.width > 0 && body.height > 0 && tape.width > 0, "package has visible carton and tape");
        check(tape.y < body.y && tape.x > body.x && tape.x + tape.width < body.x + body.width,
              "tape crosses the lid into the carton");
        check(String(tape.color) !== String(body.color), "tape contrasts with carton");
        check(fallback.width <= size && fallback.height <= size, "package fits compact and full size rows");
        check(Number.isInteger(body.x) && Number.isInteger(tape.width), "package uses whole pixels");
    }
    Timer {
        interval: 20; running: true
        onTriggered: {
            const cached = {kind: "cached", value: CACHE};
            const broken = {kind: "cached", value: BROKEN};
            const remote = {kind: "remote", value: REMOTE};
            test.load([remote, cached], CACHE_URL);
            test.load([remote, broken, cached], CACHE_URL);
            const theme = String(Quickshell.iconPath("test-program", true));
            test.check(theme.length > 0, "test theme is available");
            test.load([remote, broken, {kind: "stock", value: "test-program"}], theme);
            test.load([remote, broken], theme, "test-program");
            test.load([remote, broken, {kind: "stock", value: "missing-test-program"}], REMOTE);
            icon.program = {icons: [broken]};
            input.wait(300);
            test.check(icon.children[0].status === Image.Null, "exhausted sources stop without a retry loop");
            test.packageFallback(22);
            test.packageFallback(38);
            test.packageFallback(64);
            icon.program = {};
            input.wait(40);
            test.check(icon.children[0].status === Image.Null && icon.sourceIndex === 0,
                       "empty inventory does not retry image loading");
            test.packageFallback(48);
            test.load([cached], CACHE_URL);
            if (!test.failed) console.log("ICONS_OK");
            Qt.quit();
        }
    }
}
'''


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix='icons-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        (root / 'cached.svg').write_text(SVG)
        (root / 'remote.svg').write_text(SVG)
        (root / 'broken.jxl').write_bytes(b'unsupported or corrupt image')
        (root / 'ProgramIcon.qml').write_text((UI / 'ProgramIcon.qml').read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QPA_PLATFORMTHEME='qt6ct', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1')
        for name in ('cache', 'config', 'data', 'state'):
            (root / name).mkdir()
            env['XDG_' + name.upper() + '_HOME'] = str(root / name)
        env['XDG_DATA_DIRS'] = str(root / 'data')
        theme = root / 'data/icons/hicolor'
        (theme / '48x48/apps').mkdir(parents=True)
        (theme / '48x48/apps/test-program.svg').write_text(SVG)
        (theme / 'index.theme').write_text('[Icon Theme]\nName=Hicolor\nDirectories=48x48/apps\n[48x48/apps]\nSize=48\nType=Fixed\nContext=Applications\n')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            qml = QML
            for key, value in {'CACHE_URL': (root / 'cached.svg').as_uri(),
                               'CACHE': str(root / 'cached.svg'), 'BROKEN': str(root / 'broken.jxl'),
                               'REMOTE': f'http://127.0.0.1:{server.server_port}/remote.svg'}.items():
                qml = qml.replace(key, json.dumps(value))
            (root / 'test.qml').write_text(qml)
            with tempfile.TemporaryDirectory(prefix='', dir=ROOT / '.cache') as runtime:
                env['XDG_RUNTIME_DIR'] = runtime
                result = subprocess.run(['qs', '-p', str(root / 'test.qml'), '--no-color'], env=env,
                                        capture_output=True, text=True, timeout=30)
            log = result.stdout + result.stderr
            assert result.returncode == 0 and 'ICONS_OK' in log, log
            assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
            print('PASS icons: cached decoding, corrupt cache fallback, theme before remote, pixel package fallback, exhaustion and reuse')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
