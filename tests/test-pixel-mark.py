#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check generated mark pixels and physical alignment in a real Qt window."""
import hashlib
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile

from PIL import Image
from runtime_fixture import runtime_path

ROOT = Path(__file__).resolve().parent.parent


def main():
    subprocess.run([sys.executable, str(ROOT / 'art/logo/build-pixel-mark'), '--check'], check=True)
    generator = runpy.run_path(str(ROOT / 'art/logo/build-pixel-mark'))
    # Approved 2026-10-17 launcher and finer mark, before adding their outlines.
    approved = {
        24: '2a6a06a5cffb789b9169ee661d493b0e87f0442f293ba82a68a27e95600e2aaf',
        48: 'b8180b6e5a46ff990726c07a975b2d1d309cb42f696f89d42752d475cc0fe402',
    }
    for size in (24, 48):
        mark = generator['raster'](size)
        assert hashlib.sha256(mark.tobytes()).hexdigest() == approved[size]
        outlined = generator['outline'](mark)
        assert outlined.size == (size + 2, size + 2)
        for y in range(size + 2):
            for x in range(size + 2):
                neighbors = [mark.getpixel((sx, sy)) for sy in range(max(0, y - 2), min(size, y + 1))
                             for sx in range(max(0, x - 2), min(size, x + 1))]
                inside = 0 < x <= size and 0 < y <= size and mark.getpixel((x - 1, y - 1))[3]
                expected = mark.getpixel((x - 1, y - 1)) if inside else (0, 0, 0, 255 if any(p[3] for p in neighbors) else 0)
                assert outlined.getpixel((x, y)) == expected
    with tempfile.TemporaryDirectory(prefix='pixel-mark-') as work:
        profile = Path(work)
        for name in ('Icon.qml', 'SystemIcons.js', 'mark-bar-26.png'):
            shutil.copyfile(ROOT / 'shell' / name, profile / name)
        (profile / 'ShellPalette.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { property color text: "white" }\n')
        (profile / 'qmldir').write_text('singleton ShellPalette 1.0 ShellPalette.qml\nIcon 1.0 Icon.qml\n')
        (profile / 'test.qml').write_text('''import QtQuick
import QtQuick.Window
import Quickshell
ShellRoot {
    Window {
        id: window
        visible: true
        width: 100; height: 100
        color: "transparent"
        Item {
            id: content
            anchors.fill: parent
            Item {
                id: holder
                x: 2.9; y: 11.7
                Icon { x: 23.2; y: 19.4; width: 18; height: 18; kind: "logo" }
            }
        }
        // The update card's box: a whole multiple of 26 device pixels and a ready status.
        Icon { id: big; visible: false; width: 44; height: 44; kind: "logo" }
        // An ancestor moves after the first snap; the mark must follow it to whole pixels.
        Timer { interval: 150; running: true; onTriggered: holder.x = 7.3 }
        Timer {
            interval: 600; running: true
            onTriggered: content.grabToImage(result => {
                result.saveToFile(Qt.resolvedUrl("mark.png").toString().replace("file://", ""));
                console.log("BIG " + big.ready + " " + Math.round(big.children[0].width * window.devicePixelRatio));
                console.log("PIXEL_MARK_COMPLETE");
                Qt.quit();
            })
        }
    }
}
''')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   TMPDIR=work, NIRI_SOCKET='', DBUS_SESSION_BUS_ADDRESS='unix:path=' + work + '/no-session',
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + work + '/no-system')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'QT_SCREEN_SCALE_FACTORS'):
            env.pop(name, None)
        mark = Image.open(ROOT / 'shell/mark-bar-26.png').convert('RGBA')
        for scale in (1, 1.25, 1.5, 2, 3):
            env['QT_SCALE_FACTOR'] = str(scale)
            result = subprocess.run(['qs', '-p', str(profile / 'test.qml'), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0 and 'PIXEL_MARK_COMPLETE' in result.stdout, result.stdout + result.stderr
            image = Image.open(profile / 'mark.png').convert('RGBA')
            # The bar button's 18-pixel box: 26 physical pixels per whole unit of scale (FR11 P3-e:
            # at scale 2 the E stayed 26 physical pixels, half its pill), never smoothed.
            factor = max(1, int(scale))
            side = 26 * factor
            origin = (int((7.3 + 23.2 + 9) * scale - side / 2 + .5), int((11.7 + 19.4 + 9) * scale - side / 2 + .5))
            expected = Image.new('RGBA', image.size)
            expected.paste(mark.resize((side, side), Image.NEAREST), origin)
            assert image.tobytes() == expected.tobytes(), (scale, image.getbbox(), expected.getbbox(), result.stdout)
            assert f'BIG true {26 * max(int(scale), int(44 * scale // 26))}' in result.stdout, (scale, result.stdout)
            print(f'PASS: DPR {scale}: {side} physical pixels, snapped after an ancestor moves, exact colours '
                  f'without smoothing, ready in a larger box')
    # Offscreen software rendering draws only dirty frames, so it cannot see forced redraws; on
    # Wayland a FrameAnimation here redrew the bar 120 times in 2 idle seconds (2026-10-18).
    icon = (ROOT / 'shell/Icon.qml').read_text()
    assert not re.search(r'\bFrameAnimation\s*\{', icon) and 'onAfterAnimating' in icon
    print('PASS: the mark re-snaps on drawn frames only, no FrameAnimation')


if __name__ == '__main__':
    main()
