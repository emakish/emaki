#!/usr/bin/env python3
"""Render the actual view and controller with a recorded mock worker, without IPC.

Like tests/test-shell.py: isolated XDG directories, offscreen Qt, software scene
graph, no native service access. The stdio transport avoids sandbox bind denial.
Use --repo-shell to validate the repository material instead of installed files.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
SCREENS = ['welcome', 'keyboard', 'network', 'disk', 'manual', 'filesystem', 'you', 'review', 'plan-errors', 'install', 'done', 'error']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/tmp/emaki-s4-render'))
    parser.add_argument('--repo-shell', action='store_true')
    parser.add_argument('--iso-fonts', action='store_true', help='restrict Fontconfig to Adwaita Sans/Mono')
    parser.add_argument('--scroll-bottom', action='store_true', help='capture the end of the step body')
    parser.add_argument('--width', type=int, default=1180)
    parser.add_argument('--height', type=int, default=820)
    parser.add_argument('--screen', choices=SCREENS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix='emaki-render-') as temp:
        root = Path(temp)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (root / 'ui/tests/TestWindow.qml').read_text().replace('".." as UI', '"." as UI').replace('"mock-worker.py"', '"tests/mock-worker.py"')
        (root / 'ui/shell-test.qml').write_text(entry)
        if args.repo_shell or not Path('/usr/share/emaki/shell/qmldir').exists():
            for filename in (root / 'ui').glob('*.qml'):
                filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"'))
        for name in ['runtime', 'cache', 'config', 'state', 'data']:
            (root / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=str(root / 'runtime'),
                   XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'),
                   XDG_DATA_HOME=str(root / 'data'), EMAKI_INSTALLER_WIDTH=str(args.width), EMAKI_INSTALLER_HEIGHT=str(args.height))
        env['EMAKI_INSTALLER_SCROLL_BOTTOM'] = '1' if args.scroll_bottom else '0'
        for key in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS']:
            env.pop(key, None)
        if args.iso_fonts:
            fonts = root / 'fonts'
            fonts.mkdir()
            available = subprocess.check_output(['fc-list', '--format=%{family}\t%{file}\n'], text=True)
            families = set()
            for line in available.splitlines():
                family, filename = line.split('\t', 1)
                if family in ('Adwaita Sans', 'Adwaita Mono'):
                    shutil.copy2(filename, fonts / Path(filename).name)
                    families.add(family)
            assert 'Adwaita Sans' in families, 'ISO interface font is unavailable on this host'
            fontconfig = root / 'fonts.conf'
            fontconfig.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
                                  '<fontconfig><dir>' + escape(str(fonts)) + '</dir><cachedir>' +
                                  escape(str(root / 'cache/fontconfig')) + '</cachedir></fontconfig>')
            env['FONTCONFIG_FILE'] = str(fontconfig)
            env['EMAKI_INSTALLER_ISO_FONTS'] = '1'
        for screen in [args.screen] if args.screen else SCREENS:
            env.update(EMAKI_INSTALLER_SCREEN=screen, EMAKI_INSTALLER_SCREENSHOT=str(args.output / (screen + '.png')))
            result = subprocess.run(['qs', '-p', str(root / 'ui/shell-test.qml'), '--no-color'], env=env,
                                    text=True, capture_output=True, timeout=15)
            log = result.stdout + result.stderr
            (args.output / (screen + '.log')).write_text(log)
            assert result.returncode == 0 and 'SCREENSHOT_OK ' + screen in log, log
            assert 'LAYOUT_OK' in log, log
            if args.iso_fonts:
                assert 'FONT_OK Adwaita Sans' in log, log
            for diagnostic in ['ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign', 'failed to load', 'Renderer timeout']:
                assert diagnostic not in log, log
            assert (args.output / (screen + '.png')).stat().st_size > 1000
            print('PASS render ' + screen, flush=True)
    print(args.output)


if __name__ == '__main__': main()
