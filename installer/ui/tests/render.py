#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the actual view and controller with a recorded mock worker, without IPC.

Like tests/test-shell.py: isolated XDG directories, offscreen Qt, software scene
graph, no native service access. The stdio transport avoids sandbox bind denial.
Always use the matching repository shell and translation module.
"""
import argparse
import json
import re
import sys
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image
from xml.sax.saxutils import escape
from render_content import validate_frame, validate_restart

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
SCREENS = ['welcome', 'welcome-bios', 'welcome-no-boot-medium', 'keyboard', 'network', 'timezone', 'timezone-search', 'timezone-empty', 'disk', 'alongside', 'alongside-review', 'manual', 'manual-empty', 'manual-editor', 'manual-delete', 'manual-create', 'manual-new-table', 'disk-mbr', 'disk-none', 'disk-refused-hibernation', 'disk-refused-bitlocker', 'disk-refused-space', 'disk-refused-efi', 'filesystem', 'encryption', 'encryption-none', 'encryption-account', 'encryption-separate', 'encryption-separate-empty', 'encryption-manual', 'encryption-alongside', 'encryption-mismatch', 'encryption-invalid', 'encryption-revealed', 'encryption-caps', 'encryption-numlock', 'live-keyboard-you', 'live-keyboard-encryption', 'live-keyboard-failed', 'live-keyboard-second', 'you', 'you-valid', 'you-cyrillic', 'you-emoji', 'you-tab', 'you-empty', 'you-console', 'you-paste', 'you-caps', 'you-numlock', 'software', 'software-minimal', 'review', 'review-encrypted', 'review-manual', 'plan-errors', 'install', 'install-signatures', 'install-updates', 'install-step', 'done', 'done-restart', 'done-restart-removable', 'done-warning', 'done-no-package-lists', 'done-wifi-not-copied', 'error-login-name', 'error', 'error-details', 'error-real']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence/installer-render')
    parser.add_argument('--repo-shell', action='store_true', help='compatibility option; repository shell is always used')
    parser.add_argument('--iso-fonts', action='store_true', help='restrict Fontconfig to Noto Sans/Mono')
    parser.add_argument('--scroll-bottom', action='store_true', help='capture the end of the step body')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=700)
    parser.add_argument('--screen', choices=SCREENS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    evidence = ROOT / '.cache/evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-render-', dir=evidence) as temp, \
            tempfile.TemporaryDirectory(prefix='eir-', dir='/tmp') as runtime:
        root = Path(temp)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (root / 'ui/tests/TestWindow.qml').read_text().replace('".." as UI', '"." as UI').replace('"mock-worker.py"', '"tests/mock-worker.py"').replace('"FakeNiri.js"', '"tests/FakeNiri.js"').replace('"real-worker.py"', '"tests/real-worker.py"')
        (root / 'ui/shell-test.qml').write_text(entry)
        for filename in (root / 'ui').glob('*.qml'):
            filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"'))
        for name in ['cache', 'config', 'state', 'data']:
            (root / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=runtime,
                   XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'),
                   XDG_DATA_HOME=str(root / 'data'), EMAKI_INSTALLER_WIDTH=str(args.width), EMAKI_INSTALLER_HEIGHT=str(args.height),
                   EMAKI_INSTALLER_CHECKOUT=str(ROOT), TMPDIR=str(root))
        env.update(EMAKI_LANGUAGE='en', EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   QML2_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'))
        # Qt may terminate the real worker before its context manager runs.
        # Keep child scratch directories inside the renderer's cleanup scope.
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
                if family in ('Noto Sans', 'Noto Sans Mono'):
                    shutil.copy2(filename, fonts / Path(filename).name)
                    families.add(family)
            assert 'Noto Sans' in families, 'ISO interface font is unavailable on this host'
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
            (args.output / (screen + '.log')).write_text(log.replace(str(ROOT), '<checkout>'))
            assert result.returncode == 0 and 'SCREENSHOT_OK ' + screen in log, log
            assert 'LAYOUT_OK' in log, log
            if screen == 'welcome':
                with Image.open(args.output / 'welcome.png') as frame:
                    # Inside the 16 px window curve, outside the old 26 px glass curve.
                    ratio = frame.width / args.width
                    for x, y in ((5, 5), (args.width - 6, 5),
                                 (5, args.height - 6), (args.width - 6, args.height - 6)):
                        assert frame.getpixel((round(x * ratio), round(y * ratio)))[3] > 128, 'Window corner is hollow'
                    assert frame.getpixel((0, 0))[3] == 0, 'Window corner is square'
                    # The installer retains its explicit light row, matching the shell default. Sample the panel
                    # in the empty margin between its left edge and the layout inset in it, at mid
                    # height, so the point lies inside the window at every size down to the 960x480
                    # minimum and never on a label.
                    matches = re.findall(r'PANEL_FRAME welcome (\{[^\n]+\})', log)
                    assert len(matches) == 1, 'Missing or repeated panel evidence'
                    bounds = json.loads(matches[0])
                    panel, layout = bounds['panel'], bounds['layout']
                    assert (panel['x'], panel['y'], panel['width'], panel['height']) == (0, 0, args.width, args.height), bounds
                    x = (panel['x'] + layout['x']) / 2
                    y = panel['y'] + panel['height'] / 2
                    assert layout['x'] - panel['x'] >= 8 and layout['y'] <= y <= layout['y'] + layout['height'], bounds
                    sample = frame.getpixel((round(x * ratio), round(y * ratio)))
                    assert min(sample[:3]) > 200, f'Installer panel is not light: {sample} at {x},{y}'
            if args.iso_fonts:
                assert 'FONT_OK Noto Sans' in log, log
            for diagnostic in ['ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign', 'failed to load', 'Renderer timeout']:
                assert diagnostic not in log, log
            hello = json.loads((UI / 'tests/transcripts/render.json').read_text())['hello']
            version = hello['emaki_version']
            if screen == 'welcome-no-boot-medium':
                version = None
            elif screen == 'error-real':
                sys.path.insert(0, str(ROOT / 'installer'))
                from emaki_installer import __version__, __label__
                version = __version__ + (' ' + __label__ if __label__ else '')
            if screen.startswith('done-restart'):
                validate_restart(args.output / (screen + '.png'), log, args.width, args.height,
                                 removable=screen == 'done-restart-removable')
                print('PASS render ' + screen, flush=True)
                continue
            validate_frame(args.output / (screen + '.png'), log, screen, args.width, args.height,
                           version, scrolled=args.scroll_bottom or screen == 'manual-delete')
            if screen == 'error-details':
                assert 'DETAILS_VISIBLE' in log, log
            if screen == 'disk':
                assert 'ERASE_IN_VIEW' in log, log
            if screen == 'manual-empty':
                assert 'NO_PARTITIONS_HINT This disk has no partitions yet.' in log, log
            if screen == 'manual-delete':
                assert ('DELETE_CONFIRM_VISIBLE Partition 2 holds Windows. Deleting it removes the partition and '
                        'all its files when you press Install. Type DELETE to confirm.') in log, log
            if screen == 'manual-create':
                assert 'CREATE_FORM_VISIBLE' in log, log
            if screen in ('manual-editor', 'review-manual'):
                assert 'EDITOR_ROWS partition1 partition2 deleted3 new3 new4 new5 free' in log, log
            if screen == 'manual-new-table':
                assert 'EDITOR_ROWS new1 new2 free' in log, log
            if screen.startswith('disk-refused-'):
                assert 'UNSELECTED_REFUSAL_VISIBLE' in log, log
            if screen == 'disk-none':
                assert 'BLOCKED_REASON Choose a disk.' in log, log
            if screen == 'live-keyboard-failed':
                assert 'KEYBOARD_PROBLEM The keyboard could not be switched to German' in log, log
            if screen in ('done-warning', 'done-no-package-lists'):
                assert 'DONE_WARNING_VISIBLE' in log, log
            if screen == 'done-wifi-not-copied':
                assert 'DONE_WIFI_VISIBLE Wi-Fi was not copied; join it again after restarting.' in log, log
            if screen == 'error-login-name':
                assert 'LOGIN_REFUSAL_VISIBLE This login name belongs to the system. Choose another name; the disk has not been changed.' in log, log
            if screen == 'install-signatures':
                assert 'PHASE_STATUS Checking package signatures… 312 of 871' in log, log
            if screen == 'install-updates':
                assert 'PHASE_STATUS Downloading updates… 37 of 144' in log, log
            if screen == 'install-step':
                assert 'INSTALL_STEP_VISIBLE Running package setup step 12 of 18 (pacman: Updating the desktop file MIME type cache).' in log, log
                assert 'STEP_WRAPPED' in log and 'PROGRESS_BAR_VISIBLE' in log, log
            if screen == 'welcome-no-boot-medium':
                assert "NOTICE Restart from the USB stick without the 'copy to RAM' option to install." in log, log
            if screen in ('you-valid', 'you-cyrillic', 'you-emoji', 'you-tab'):
                assert 'PASSWORD_RULE ' + ('hint' if screen == 'you-valid' else 'error') in log, log
            if screen == 'you-empty':
                assert 'PROBLEM_UNDER loginField' in log, log
            if screen == 'you-console':
                assert ('CONSOLE_WARNING The text console types these characters differently: ~. '
                        'Choose a password without them if you may need the console.') in log, log
            if screen in ('you-caps', 'encryption-caps', 'you-numlock', 'encryption-numlock'):
                assert 'SCROLLED_WARNING ' in log, log
                assert 'CAPS_LOCK Caps Lock is on' in log, log
            if screen in ('you-numlock', 'encryption-numlock'):
                assert ('NUM_LOCK Num Lock is on — the login screen starts with Num Lock off; '
                        'type digits on the main row.') in log, log
            if screen == 'you-paste':
                assert 'PASTE_REFUSED Pasted text cannot be used for this password. Type it key by key.' in log, log
            print('PASS render ' + screen, flush=True)
    print(args.output)


if __name__ == '__main__': main()
