#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pictures of the shell before and after the shared canvas, drawn with this machine's real apps.

AFTER is the working tree as it is (LiquidPalette.sharedCanvas true, the default); BEFORE is the
same staged tree with that one constant set to false, 0.8.0's look. Production QML renders
offscreen through tests/theme-pictures.py (llvmpipe, the real RHI glass shaders). Real: the
host's installed application entries and icons, the dock pins (installed desktop IDs), the
shortcut rows from niri/default.kdl, the polkit policy text and the composed report. Made up by
the fixtures, and said so in the captions: the dock's window IDs, the playback stream, the
microphone use, the notices, the account in Settings and the Programs & Updates catalogue.
"""
import argparse
import configparser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

from PIL import Image, ImageChops

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.cache/n81c-render'
FRAMES = WORK / 'frames'
OUT = ROOT / '.cache/n81c-pictures'
SPEC = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
THEME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(THEME)
THEME.WORK = WORK  # fonts, the wallpaper texture and fixture data live beside these frames
SHOTS = ['launcher', 'settings-launcher', 'system-sound', 'reminder', 'clock', 'privacy', 'polkit', 'welcome', 'shortcuts', 'report',
         'programs-browse']
# Surfaces that already are the Settings canvas: AFTER must equal BEFORE pixel for pixel.
UNCHANGED = ('settings-launcher', 'report', 'programs-browse')
THEMES = ('light', 'dark')
SIDES = ('before', 'after')
SYSTEM_DATA = '/usr/local/share:/usr/share'
APPLICATIONS = [Path(path) / 'applications' for path in SYSTEM_DATA.split(':')]
# The browser, the terminal and the file manager. The first two are this machine's own dock pins.
PINS = ['firefox', 'kitty', 'org.gnome.Nautilus']
FLAG = re.compile(r'^(\s*readonly property bool sharedCanvas:\s*)true(\s*)$', re.M)
# The update-apply request as update-manager/backend.py starts it through pkexec.
POLKIT_ACTION = 'org.emaki.update.apply'
POLKIT_PROGRAM = '/usr/libexec/emaki/emaki-update-apply'


def desktop_entry(identifier):
    for directory in APPLICATIONS:
        path = directory / (identifier + '.desktop')
        if path.is_file():
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            parser.read(path, encoding='utf-8')
            return path, parser['Desktop Entry']
    raise SystemExit(f'{identifier}.desktop is not installed on this machine')


def snapshot():
    """Freeze the working tree's shell (and what the helpers read) and render its install paths."""
    source = WORK / 'source'
    if source.exists():
        shutil.rmtree(source)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    for name in ('shell', 'update-manager', 'niri', 'scripts'):
        shutil.copytree(ROOT / name, source / name, ignore=ignore, symlinks=True)
    subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/render-paths'), '--source', str(source)], check=True)
    return source


def stage(source, side):
    destination = WORK / side
    THEME.stage(source, destination)
    palette = destination / 'LiquidPalette.qml'
    text = palette.read_text()
    found = len(FLAG.findall(text))
    assert found == 1, f'LiquidPalette.qml must declare sharedCanvas true exactly once (found {found})'
    if side == 'before':
        palette.write_text(FLAG.sub(r'\1false\2', text))
    return destination


def report_data(source):
    """A real composed report from this machine (the Report a problem helper, redacted)."""
    result = subprocess.run([sys.executable, '-I', '-B', str(source / 'scripts/emaki-report'), 'compose', '--part', 'other'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        print('NOTE: emaki-report compose failed:', result.stdout[-400:], result.stderr[-800:], flush=True)
        return None
    return json.loads(result.stdout)


def picture_data(source):
    windows = {}
    for number, identifier in enumerate(PINS, 501):
        windows[str(number)] = dict(title=desktop_entry(identifier)[1]['Name'], appId=identifier)
    shortcuts = json.loads(subprocess.check_output(
        [sys.executable, '-I', '-B', str(source / 'shell/helpers/shortcuts.py')], text=True))
    assert shortcuts['state'] == 'ready' and shortcuts['rows'], shortcuts
    policy = ElementTree.parse(ROOT / 'update-manager/org.emaki.update.policy')
    action = policy.find(f".//action[@id='{POLKIT_ACTION}']")
    # The same row the shell builds from a microphone stream (ShellScene.privacyRows).
    browser = desktop_entry('firefox')[1]['Name']
    privacy = [dict(kind='info', what='mic', label=browser, note='Using the microphone', tag='', id=0)]
    # Firefox names its PipeWire playback streams "Firefox" (application.name, no icon name); the
    # stream's media name is the page's title, which stays out of the pictures.
    stream = dict(name=browser, detail='', icon='')
    data = dict(pins=PINS, windows=windows, shortcuts=shortcuts['rows'], privacy=privacy, stream=stream,
                polkit=dict(action=POLKIT_ACTION, message=action.findtext('message'),
                            details=dict(program=POLKIT_PROGRAM, command_line=POLKIT_PROGRAM + ' apply')))
    report = report_data(source)
    if report:
        data['report'] = report
    return data


def hicolor_icon(name):
    """The file the hicolor theme gives for a launcher tile (80 px): scalable first, else the nearest size."""
    found = []
    for data in SYSTEM_DATA.split(':'):
        theme = Path(data) / 'icons/hicolor'
        for path in sorted(theme.glob(f'*/*/{name}.svg')) + sorted(theme.glob(f'*/*/{name}.png')):
            size = path.parts[-3]
            if size == 'scalable':
                found.append((0, path))
            elif re.fullmatch(r'\d+x\d+', size):
                pixels = int(size.split('x')[0])
                found.append((abs(pixels - 80) + (0.5 if pixels < 80 else 0), path))
    return min(found)[1] if found else None


def icon_shim():
    """Make the hicolor icons reachable offscreen.

    The offscreen Qt platform has no XDG icon theme paths (its icon loader probes only
    ":/icons/hicolor/index.theme"); it does search <XDG_DATA_DIRS>/pixmaps. A session's
    platform theme searches <XDG_DATA_DIRS>/icons, where the shell finds the hicolor icons.
    So each installed entry's hicolor icon is linked into a pixmaps directory listed first.
    Names hicolor lacks stay unresolved, as in a session without an icon theme.
    """
    shim = WORK / 'icon-shim'
    if shim.exists():
        shutil.rmtree(shim)
    pixmaps = shim / 'pixmaps'
    pixmaps.mkdir(parents=True)
    names = {}
    for directory in APPLICATIONS:
        for path in sorted(directory.glob('*.desktop')):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                parser.read(path, encoding='utf-8')
                icon = parser['Desktop Entry'].get('Icon', '')
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if icon and '/' not in icon:
                names.setdefault(icon, hicolor_icon(icon))
    for name, path in names.items():
        if path:
            (pixmaps / (name + path.suffix)).symlink_to(path)
    resolved = sorted(name for name, path in names.items() if path)
    (WORK / 'icon-shim.json').write_text(json.dumps(dict(
        resolved={name: str(names[name]) for name in resolved},
        not_in_hicolor=sorted(name for name, path in names.items() if not path)), indent=2) + '\n')
    return shim


def environment_factory(data, shim):
    base = THEME.environment

    def environment():
        values = base()
        home = WORK / 'data-host'
        (home / 'applications').mkdir(parents=True, exist_ok=True)
        # The host's own installed entries and icons; an empty personal data directory.
        values.update(XDG_DATA_HOME=str(home), XDG_DATA_DIRS=str(shim) + ':' + SYSTEM_DATA,
                      EMAKI_PICTURE_DATA=json.dumps(data))
        return values
    return environment


def frame(side, theme, shot):
    return FRAMES / f'{side}-{theme}-{shot}'


def render(sides, shots):
    # Shot by shot, so each AFTER frame follows its BEFORE frame.
    for theme in THEMES:
        for shot in shots:
            for side in sides:
                THEME.capture(WORK / side, frame(side, theme, shot), shot, theme)


def changed(shot, theme):
    old = Image.open(frame('before', theme, shot).with_suffix('.png')).convert('RGB')
    new = Image.open(frame('after', theme, shot).with_suffix('.png')).convert('RGB')
    box = ImageChops.difference(old, new).getbbox()
    if not box:
        return 0, None
    diff = ImageChops.difference(old, new).convert('L').point(lambda value: 255 if value else 0)
    return diff.histogram()[255], box


def sheets(shots):
    OUT.mkdir(parents=True, exist_ok=True)
    # Each caption says which data is this machine's and which the render fixture made up.
    titles = {'launcher': "launcher: this machine's apps; dock windows are fixture",
              'settings-launcher': 'Settings in the launcher: fixture account',
              'system-sound': 'system panel, Sound: fixture stream named Firefox',
              'reminder': 'update reminder: fixture notice',
              'clock': 'clock drawer: fixture notices',
              'privacy': 'privacy: fixture microphone use by Firefox',
              'polkit': 'password prompt: fixture request, real policy text',
              'welcome': 'welcome', 'shortcuts': 'keyboard shortcuts: real niri/default.kdl',
              'report': 'Report a problem: report composed on this machine',
              'programs-browse': 'Programs & Updates: fixture catalogue'}
    sides = {'before': 'Before (0.8.0)', 'after': 'After (shared canvas)'}
    # Eight sheets at most: two surfaces side by side where one would leave a surface out.
    names = [('1-launcher.png', ('launcher', 'settings-launcher')), ('2-system-sound.png', ('system-sound',)),
             ('3-clock.png', ('reminder', 'clock')), ('4-privacy.png', ('privacy',)), ('5-polkit.png', ('polkit',)),
             ('6-welcome.png', ('welcome',)), ('7-shortcuts.png', ('shortcuts',)),
             ('8-report-programs.png', ('report', 'programs-browse'))]
    for name, group in names:
        group = [shot for shot in group if shot in shots]
        if group:
            THEME.sheet(OUT / name, [[(f'{theme.title()} / {sides[side]} / {titles[shot]}', frame(side, theme, shot).with_suffix('.png'))
                                      for shot in group for side in SIDES] for theme in THEMES])
    assert len(list(OUT.glob('*.png'))) <= 8, sorted(OUT.iterdir())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-only', action='store_true', help='Render only the BEFORE frames.')
    parser.add_argument('--only', nargs='+', choices=SHOTS, help='Only these shots.')
    parser.add_argument('--sheets-only', action='store_true', help='Assemble pictures from frames already rendered.')
    args = parser.parse_args()
    shots = args.only or SHOTS
    FRAMES.mkdir(parents=True, exist_ok=True)
    source = snapshot()
    data = picture_data(source)
    if 'report' not in data and 'report' in shots:
        print('SKIP report: the composed report is unavailable', flush=True)
        shots = [shot for shot in shots if shot != 'report']
    (WORK / 'picture-data.json').write_text(json.dumps({key: value for key, value in data.items() if key != 'report'}, indent=2) + '\n')
    THEME.environment = environment_factory(data, icon_shim())
    sides = ('before',) if args.before_only else SIDES
    if not args.sheets_only:
        for side in sides:
            stage(source, side)
        render(sides, shots)
    if args.before_only:
        return
    # Count and assemble every shot with all four frames, including ones rendered by an earlier --only run.
    shots = [shot for shot in SHOTS if all(frame(side, theme, shot).with_suffix('.png').exists()
                                           for side in SIDES for theme in THEMES)]
    counts = {}
    for shot in shots:
        for theme in THEMES:
            count, box = changed(shot, theme)
            counts[f'{theme}-{shot}'] = dict(changed_pixels=count, bounds=box)
            print(f'CHANGED {theme:5} {shot:17} {count:8} px  bounds {box}', flush=True)
    (WORK / 'changed-pixels.json').write_text(json.dumps(counts, indent=2) + '\n')
    sheets(shots)
    same = {key: row['changed_pixels'] for key, row in counts.items() if key.split('-', 1)[1] in UNCHANGED and row['changed_pixels']}
    assert not same, f'Surfaces already on the Settings canvas changed: {same}'


if __name__ == '__main__':
    main()
