#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render real theme surfaces with llvmpipe and compare the original light pixels (light chosen explicitly)."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import io

from PIL import Image, ImageChops, ImageDraw, ImageFont

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/n81t-pictures/round3'
OWNER = ROOT / '.cache/n81t-pictures/owner'
WORK = ROOT / '.cache/n81t-render'
REFERENCES = ROOT / 'tests/fixtures/theme-light'
COMPARISON_SHOTS = ['launcher', 'system-sound', 'dock', 'notification', 'settings', 'programs-browse', 'programs-program']
CANVAS_SWITCH = 'readonly property bool sharedCanvas: true'
CANVAS_SHOTS = ('launcher', 'system-sound', 'notification')
SPEC = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)
SHOTS = ['launcher', 'system-sound', 'dock', 'notification', 'appearance', 'programs-browse', 'programs-program', 'clock', 'polkit', 'lock', 'snapshot', 'shortcuts', 'capture', 'osd', 'privacy', 'welcome']


def constants(path):
    return {statement.targets[0].id: ast.literal_eval(statement.value)
            for statement in ast.parse(path.read_text()).body
            if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id in ('HEADER', 'UPDATE', 'PROGRAMS')}


def stage(source, destination):
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source / 'shell', destination)
    shutil.copytree(source / 'update-manager/ui', destination / 'manager')
    for name in ('Platform.qml', 'helpers/emaki_paths.py'):
        if not (destination / name).exists():
            shutil.copyfile(ROOT / 'shell' / name, destination / name)
    for path in (destination / 'manager').glob('*.qml'):
        path.write_text(path.read_text().replace('file:@EMAKI_DATADIR@/shell', '..'))
    for name in ('ThemeShots.qml', 'SystemFixture.qml', 'ShellRender.qml', 'SettingsVisualTest.qml'):
        shutil.copyfile(ROOT / 'tests/fixtures' / name, destination / name)
    shell = destination / 'ShellRender.qml'
    shell.write_text(shell.read_text().replace('id: root\n', 'id: root\n    readonly property alias fixtureScene: scene\n', 1))
    settings = destination / 'SettingsVisualTest.qml'
    text = settings.read_text().replace('id: catalog\n', '''id: catalog
        function set(key, value) {}
        function reset(key) {}
''')
    text = text.replace('function row(key) {', '''function row(key) {
            const saved = SettingsBridge.values.find(row => row.key === key)?.value;
            if (saved !== undefined) return {key: key, value: saved, default: saved, editable: true};''')
    settings.write_text(text)
    fixtures = constants(ROOT / 'update-manager/ui/tests/interactions.py')
    for name, key in [('PictureUpdates.qml', 'UPDATE'), ('PicturePrograms.qml', 'PROGRAMS')]:
        content = fixtures[key].replace('row.package === "krita" ? [Quickshell.env("TEST_SCREENSHOT")] : []', '[]')
        (destination / 'manager' / name).write_text(fixtures['HEADER'] + content)
    with (destination / 'qmldir').open('a') as module:
        module.write('SystemFixture 1.0 SystemFixture.qml\n')


def environment():
    data = WORK / 'data'
    apps = data / 'applications'
    apps.mkdir(parents=True, exist_ok=True)
    icon = ROOT / 'art/logo/mark.svg'
    icons = data / 'icons/hicolor/scalable/apps'
    icons.mkdir(parents=True, exist_ok=True)
    (icons.parents[1] / 'index.theme').write_text('[Icon Theme]\nName=Fixture\nDirectories=scalable/apps\n[scalable/apps]\nSize=48\nType=Scalable\nContext=Applications\n')
    # Programs & Updates as packaged: its shipped desktop entry names the icon, and the
    # installed SVG sits in the hicolor theme. The shell recognises that name to draw the accent.
    shutil.copyfile(ROOT / 'update-manager/emaki-programs-updates.svg', icons / 'emaki-programs-updates.svg')
    shutil.copyfile(ROOT / 'update-manager/emaki-update-manager.desktop', apps / 'emaki-update-manager.desktop')
    (apps / 'emaki-programs-updates.desktop').unlink(missing_ok=True)
    for identifier, name in [('fixture-browser', 'Browser'), ('fixture-editor', 'Editor'), ('fixture-files', 'Files')]:
        (apps / (identifier + '.desktop')).write_text(
            f'[Desktop Entry]\nType=Application\nName={name}\nExec=true\nIcon={icon}\nTerminal=false\n')
    (apps / 'emaki-settings.desktop').unlink(missing_ok=True)
    storefront = json.loads((ROOT / 'update-manager/storefront.json').read_text())
    shelf = json.loads((ROOT / 'update-manager/emaki-shelf.json').read_text())
    if isinstance(shelf, dict):
        shelf = shelf.get('items', shelf.get('programs', []))
    return dict(USER='emaki', LOGNAME='emaki', EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
                EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_GLASS_RENDERER='rhi',
                XDG_CONFIG_HOME=str(WORK / 'wallpaper/config'), XDG_DATA_HOME=str(data), XDG_DATA_DIRS=str(data), PYTHONDONTWRITEBYTECODE='1',
                EMAKI_FIXTURE_WALLPAPER=wallpaper(),
                EMAKI_FIXTURE_PICTURE=str(ROOT / 'art/wallpaper/ring.png'),
                EMAKI_FIXTURE_ACCOUNT=json.dumps(dict(state='ready', name='Emaki', login='emaki', home='/fixture', avatar='')),
                QT_QUICK_CONTROLS_STYLE='Basic', QT_HASH_SEED='0', FONTCONFIG_FILE=str(fonts()),
                # Name hicolor, the theme a session falls back to. The Programs & Updates
                # icon does not depend on this lookup: the shell matches its name and draws it.
                QS_ICON_THEME='hicolor',
                TEST_ICON=icon.as_uri(), TEST_SCREENSHOT='',
                TEST_STOREFRONT=json.dumps(storefront), TEST_SHELF=json.dumps(shelf))


def fonts():
    # There is no shipped semibold face. Qt can pick Medium or Bold for that
    # weight depending on discovery order, even in two runs of identical code.
    # Pin the test's family to its four base faces in both sides of the comparison.
    profile = WORK / 'font-profile'
    config = profile / 'fonts.conf'
    if not config.exists():
        (profile / 'cache').mkdir(parents=True, exist_ok=True)
        RENDER.shipped_font_config(profile)
        for path in (profile / 'fonts').iterdir():
            name = path.resolve().name
            if name.startswith('NotoSans-') and name not in {
                    'NotoSans-Regular.ttf', 'NotoSans-Bold.ttf',
                    'NotoSans-Italic.ttf', 'NotoSans-BoldItalic.ttf'}:
                path.unlink()
    return config


def wallpaper():
    config = WORK / 'wallpaper/config/wpaperd'
    config.mkdir(parents=True, exist_ok=True)
    (config / 'config.toml').write_text('[default]\npath = ' + json.dumps(str(ROOT / 'art/wallpaper/ring.png')) + '\n')
    result = json.loads(subprocess.check_output(
        [sys.executable, '-B', str(ROOT / 'shell/helpers/wallpaper.py'), '1536', '960', '1', '', 'sharp'],
        env=dict(os.environ, XDG_CONFIG_HOME=str(config.parent), XDG_CACHE_HOME=str(WORK / 'wallpaper/cache')), text=True))
    assert result['state'] == 'ready', result
    return result['texture']


def capture(staged, target, shot, theme='light', accent='orange', shader_dir=None, reference_text=False):
    log = RENDER.render(staged / 'ThemeShots.qml', png=target.with_suffix('.png'), stats=target.with_suffix('.json'),
                        properties=dict(shot=shot, theme=theme, accent=accent, wallpaper=wallpaper()),
                        width=1536, height=960, warmup=1500, milliseconds=100, ready_property='ready',
                        shader_dir=shader_dir or ROOT / '.cache/shell-shaders',
                        # Distance-field glyph atlas placement can shift an edge by one
                        # channel unit across identical runs. Reference checks use Qt's
                        # native glyph rasterizer; presentation captures keep normal text.
                        extra_env=dict(environment(), QML_DISABLE_DISTANCEFIELD='1' if reference_text else '0'))
    target.with_suffix('.log').write_text(log)
    errors = ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign',
              'Cannot create delegate', 'Required property', 'Failed to deserialize', 'Failed to find shader')
    assert not any(error in log for error in errors), log
    stats = json.loads(target.with_suffix('.json').read_text())
    assert stats['readiness_reached'] and 'llvmpipe' in stats['renderer'], stats
    assert stats['fixture_end']['graphics_api'] == 3, stats
    assert stats['fixture_end']['textRasterizer'] == ('native' if reference_text else 'distance-field'), stats
    if shot in ('notification', 'reminder'):
        assert stats['fixture_end']['notificationVisible'], stats
    print('PASS render:', target.name, flush=True)


def contact(theme):
    # Each frame retains its complete 1536 × 960 native resolution.
    canvas = Image.new('RGB', (1536 * 2, ((len(SHOTS) + 1) // 2) * 990), '#29201e')
    draw = ImageDraw.Draw(canvas)
    for index, shot in enumerate(SHOTS):
        x, y = index % 2 * 1536, index // 2 * 990
        draw.text((x + 12, y + 8), theme.title() + ' / ' + shot, fill='white')
        canvas.paste(Image.open(CACHE / f'{theme}-{shot}.png').convert('RGB'), (x, y + 30))
    canvas.save(CACHE / f'contact-{theme}.png')


def surface_contacts(shots):
    for shot in shots:
        sheet = Image.new('RGB', (3072, 990), '#29201e')
        draw = ImageDraw.Draw(sheet)
        for column, theme in enumerate(('light', 'dark')):
            draw.text((column * 1536 + 12, 8), f'{theme.title()} / {shot}', fill='white')
            sheet.paste(Image.open(CACHE / f'{theme}-{shot}.png').convert('RGB'), (column * 1536, 30))
        sheet.save(CACHE / f'surface-{shot}.png')


def accent_contact(theme):
    for shot in ('launcher', 'appearance', 'programs-browse'):
        canvas = Image.new('RGB', (1536 * 3, 990), '#29201e')
        draw = ImageDraw.Draw(canvas)
        for column, accent in enumerate(('orange', 'teal', 'violet')):
            frame = Image.open(CACHE / f'{theme}-example-{accent}-{shot}.png').convert('RGB')
            draw.text((column * 1536 + 12, 8), f'{theme.title()} / {accent.title()} / {shot}', fill='white')
            canvas.paste(frame, (column * 1536, 30))
        canvas.save(CACHE / f'accents-{theme}-{shot}.png')


OWNER_ACCENTS = ('orange', 'teal', 'blue', 'violet')
# The launcher's Settings button in the 1536 x 960 frame, and the header band around it.
GEAR_BUTTON = (672, 13, 722, 63)
LAUNCHER_HEADER = (0, 0, 768, 240)
# Regions of the released light references where an approved change may differ.
APPROVED_CHANGES = {'settings': (14, 317, 286, 354), 'launcher': GEAR_BUTTON}


def sheet(path, rows, size=(1536, 960), zoom=None):
    """Frames at their native size in a grid, each under a one-line label; no scaling except
    an optional enlarged inset of one region under each frame."""
    font = ImageFont.load_default(size=24)
    columns = max(len(row) for row in rows)
    inset = (zoom[0][2] - zoom[0][0]) * zoom[1] if zoom else 0
    cell = 40 + size[1] + (inset + 20 if zoom else 0)
    canvas = Image.new('RGB', (size[0] * columns, cell * len(rows)), '#29201e')
    draw = ImageDraw.Draw(canvas)
    for y, row in enumerate(rows):
        for x, (label, frame) in enumerate(row):
            image = Image.open(frame).convert('RGB')
            if image.size != size:
                image = image.crop((0, 0) + size)
            left, top = x * size[0], y * cell
            draw.text((left + 14, top + 8), label, fill='white', font=font)
            canvas.paste(image, (left, top + 40))
            if zoom:
                region, factor = zoom
                enlarged = image.crop(region).resize((inset, inset), Image.Resampling.NEAREST)
                canvas.paste(enlarged, (left + (size[0] - inset) // 2, top + 40 + size[1] + 10))
            if x:
                draw.line((left, top, left, top + cell), fill='#29201e', width=4)
    canvas.save(path)
    print('PASS owner picture:', path.name, canvas.size, flush=True)


def owner(current):
    """Six pictures for the owner, each a full-size light/dark (or accent, or gear) comparison."""
    OWNER.mkdir(parents=True, exist_ok=True)
    frames = CACHE / 'owner-frames'
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)

    def frame(shot, theme, accent='orange'):
        target = frames / f'{theme}-{accent}-{shot}'
        if not target.with_suffix('.png').exists():
            capture(current, target, shot, theme, accent)
        return target.with_suffix('.png')

    def pair(name, shot, title):
        sheet(OWNER / name, [[(f'Light / {title}', frame(shot, 'light')), (f'Dark / {title}', frame(shot, 'dark'))]])

    stage(ROOT, current)
    pair('a-desktop-launcher-light-dark.png', 'launcher', 'desktop with the launcher open')
    pair('b-settings-appearance-light-dark.png', 'appearance', 'Settings > Appearance')
    pair('c-programs-browse-light-dark.png', 'programs-browse', 'Programs & Updates, browse')
    sheet(OWNER / 'd-accents-panel-and-programs-light.png',
          [[(f'Light / {accent.title()} accent / panel island', frame('system-sound', 'light', accent)),
            (f'Light / {accent.title()} accent / Programs & Updates', frame('programs-browse', 'light', accent))]
           for accent in OWNER_ACCENTS])
    pair('f-lock-light-dark.png', 'lock', 'lock screen')
    assert len(list(OWNER.glob('*.png'))) <= 6, sorted(OWNER.iterdir())


def released_tree(commit):
    """The shell and Programs & Updates sources of one commit, for a before picture."""
    tree = WORK / f'source-{commit}'
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', '--format=tar', commit, 'shell', 'update-manager/ui'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as members:
        members.extractall(tree, filter='data')
    return tree


def gear_final(before):
    """Today's launcher Settings button (the sun at `before`) beside the shipped grey gear,
    in light and dark, each header at native size with a 6x inset of the button."""
    OWNER.mkdir(parents=True, exist_ok=True)
    frames = CACHE / 'gear-final-frames'
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    rows = []
    for theme in ('light', 'dark'):
        row = []
        for label, source in (('Today', released_tree(before)), ('New: grey gear', ROOT)):
            staged = WORK / ('gear-before' if source != ROOT else 'gear-after')
            stage(source, staged)
            target = frames / f'{theme}-{"before" if source != ROOT else "after"}-launcher'
            capture(staged, target, 'launcher', theme)
            row.append((f'{theme.title()} / {label}', target.with_suffix('.png')))
        rows.append(row)
    sheet(OWNER / 'gear-final.png', rows, size=LAUNCHER_HEADER[2:], zoom=(GEAR_BUTTON, 6))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Render one launcher to check the environment.')
    parser.add_argument('--compare', action='store_true', help='Prove unchanged default light pixels against frozen approved light fixtures.')
    parser.add_argument('--gear-final', metavar='COMMIT', help='Only the owner picture of the Settings button: COMMIT (today) beside the working tree.')
    parser.add_argument('--compare-only', action='store_true', help='Only compare the default pixels.')
    parser.add_argument('--accents-only', action='store_true', help='Render three accents in Settings, launcher and Programs.')
    parser.add_argument('--owner', action='store_true', help='Only the six full-size comparison pictures for the owner.')
    parser.add_argument('--only', nargs='+', choices=SHOTS + ['settings', 'settings-launcher'], help='Only these surfaces.')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    current = WORK / 'current'
    stage(ROOT, current)
    if args.owner:
        owner(current)
        return
    if args.gear_final:
        gear_final(args.gear_final)
        return
    if args.accents_only:
        for theme in ('light', 'dark'):
            for accent in ('orange', 'teal', 'violet'):
                for shot in ('launcher', 'appearance', 'programs-browse'):
                    capture(current, CACHE / f'{theme}-example-{accent}-{shot}', shot, theme, accent)
            accent_contact(theme)
        return
    if args.quick:
        capture(current, CACHE / 'light-launcher', 'launcher')
        return
    for theme in (() if args.compare_only else ('light', 'dark')):
        for shot in args.only or SHOTS:
            capture(current, CACHE / f'{theme}-{shot}', shot, theme)
        if not args.only:
            contact(theme)
    if not args.compare_only:
        surface_contacts(args.only or SHOTS)
    if args.compare or args.compare_only:
        # The shared canvas (on by default) changes these bodies on purpose: they are compared with
        # the switch off, which must give back the frozen 0.8.0 pixels. tests/test-shared-canvas.py
        # proves the on state. The dock, Settings and Programs & Updates are compared as they ship.
        canvas_off = WORK / 'canvas-off'
        stage(ROOT, canvas_off)
        palette = canvas_off / 'LiquidPalette.qml'
        text = palette.read_text()
        assert text.count(CANVAS_SWITCH) == 1, 'LiquidPalette.qml must declare sharedCanvas true exactly once'
        palette.write_text(text.replace(CANVAS_SWITCH, CANVAS_SWITCH.replace('true', 'false')))
        report = {}
        for shot in COMPARISON_SHOTS:
            old = Image.open(REFERENCES / f'{shot}.png').convert('RGB')
            tree = canvas_off if shot in CANVAS_SHOTS else current
            for attempt in range(2):
                capture(tree, CACHE / f'default-{shot}', shot, reference_text=True)
                new = Image.open(CACHE / f'default-{shot}.png').convert('RGB')
                diff = ImageChops.difference(old, new)
                count = sum(pixel != (0, 0, 0) for pixel in diff.getdata())
                row = dict(changed_pixels=count, total_pixels=old.width * old.height, attempts=attempt + 1,
                           shared_canvas=tree is current)
                # Approved light changes: the Appearance navigation entry in Settings and the
                # grey gear of the launcher's Settings button. Nothing else may change.
                approved = APPROVED_CHANGES.get(shot)
                if approved:
                    unchanged = diff.copy()
                    ImageDraw.Draw(unchanged).rectangle(approved, fill=(0, 0, 0))
                    row['approved_change_bounds'] = approved
                    row['approved_changed_pixels'] = count - sum(pixel != (0, 0, 0) for pixel in unchanged.getdata())
                    row['outside_approved_changed_pixels'] = count - row['approved_changed_pixels']
                if not row.get('outside_approved_changed_pixels', count):
                    break
            report[shot] = row
            difference = CACHE / f'difference-{shot}.png'
            if count:
                diff.save(difference)
            else:
                difference.unlink(missing_ok=True)
        (CACHE / 'default-pixel-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        assert not any(row.get('outside_approved_changed_pixels', row['changed_pixels']) for row in report.values()), report


if __name__ == '__main__':
    main()
