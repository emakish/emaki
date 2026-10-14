#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Settings canvas as one shared material, behind one switch (0.8.1 item 16).

Static part: one switch, on by default; the canvas colour is drawn only by CanvasTint; every
surface with a body lays a CanvasTint under its content; Settings, Report a problem and
Programs & Updates draw it whatever the switch says.

Rendered part (production QML, llvmpipe, the real glass shaders; skipped with --static), light
and dark. The default (on): the launcher header keeps the plain glass and its body is exactly
the Settings material, the glass under the Settings tint, 0.8.0's pixel * (1 - a) + tint * a;
the bar islands, the dock, the OSD and the lock are the same pixels as with the switch off;
the system panel, the update reminder, the clock drawer, the privacy panel and Welcome change
by the same formula in their bodies.
Off gives back 0.8.0: the launcher equals the frozen light reference outside the approved
gear button. tests/theme-pictures.py
--compare-only checks the dock, Settings and Programs & Updates as shipped against the frozen
0.8.0 references.
"""
import importlib.util
from pathlib import Path
import re
import shutil
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / 'shell'
SWITCH = 'readonly property bool sharedCanvas: true'
# objectName of each surface's CanvasTint, and the file that lays it.
SURFACES = {
    'launcher-canvas': 'LauncherBody.qml',
    'system-canvas': 'SystemPanel.qml',
    'clock-canvas': 'ClockPanel.qml',
    'privacy-canvas': 'PrivacyPanel.qml',
    'welcome-canvas': 'Welcome.qml',
    'shortcuts-canvas': 'ShortcutSheet.qml',
    'polkit-canvas': 'PolkitPromptCard.qml',
}
# Surfaces that keep their own material: the bar islands, the dock, the OSD and the lock.
KEPT = ('bar', 'dock', 'osd', 'lock')
# Listed surfaces that already had the plain glass under their body, so with the switch on
# the body is that glass under the tint: the system panel (Sound), the update reminder, the
# clock drawer, the privacy panel and Welcome. The shortcut sheet and the password card had a
# flat fill and get glass only with the switch, so the formula over 0.8.0's pixel does not
# apply to them.
GLASS_BODIES = ('system-sound', 'notification', 'clock', 'privacy', 'welcome')
TINTS = {'light': (1, .9647058823529412, .9411764705882353), 'dark': (.11, .075, .063)}
ALPHA = .58
# The launcher plate in the 1536 x 960 fixture frame: origin (10, 8), 720 wide, header 60.
PLATE_X, PLATE_Y, PLATE_W, HEADER = 10, 8, 720, 60


def qml(name):
    return (SHELL / name).read_text()


class Static(unittest.TestCase):
    def test_one_switch_on_by_default(self):
        palette = qml('LiquidPalette.qml')
        self.assertEqual(palette.count(SWITCH), 1)
        self.assertIn('readonly property color canvas: ShellPalette.settingsSidebar', palette)
        switches = [path.name for path in SHELL.rglob('*.qml') if 'sharedCanvas:' in path.read_text()]
        self.assertEqual(switches, ['LiquidPalette.qml'])

    def test_tint_is_one_component(self):
        tint = qml('CanvasTint.qml')
        self.assertIn('property bool shown: LiquidPalette.sharedCanvas', tint)
        self.assertIn('color: LiquidPalette.canvas', tint)
        self.assertIn('CanvasTint 1.0 CanvasTint.qml', qml('qmldir').splitlines())
        # No surface paints the Settings canvas colour itself.
        pattern = re.compile(r'settingsSidebar|SettingsTheme\.sidebar|LiquidPalette\.canvas\b')
        owners = {'CanvasTint.qml', 'LiquidPalette.qml', 'ShellPalette.qml', 'SettingsTheme.qml'}
        painters = sorted(str(path.relative_to(ROOT)) for path in list(SHELL.rglob('*.qml')) + list((ROOT / 'update-manager/ui').glob('*.qml'))
                          if path.name not in owners and pattern.search(path.read_text()))
        self.assertEqual(painters, [])

    def test_every_surface_lays_the_canvas(self):
        for name, file in SURFACES.items():
            with self.subTest(surface=name):
                text = qml(file)
                block = text[text.index(f'objectName: "{name}"') - 200:]
                self.assertIn('CanvasTint {', block[:200])

    def test_settings_surfaces_draw_it_whatever_the_switch(self):
        glass = qml('SettingsGlass.qml')
        self.assertRegex(glass, r'CanvasTint \{\s+shown: true')
        self.assertRegex(qml('SettingsView.qml'), r'CanvasTint \{\s+shown: root\.embedded')
        self.assertIn('SettingsGlass {', qml('ReportView.qml'))
        self.assertIn('Shell.SettingsGlass {', (ROOT / 'update-manager/ui/ManagerView.qml').read_text())

    def test_flat_stand_in_lays_the_canvas_under_its_drops(self):
        # Without the GPU the drops are rectangles under the content layer, so the content's
        # tint would cover the selected (orange) drops: the flat stand-in lays its own tint
        # over the plate and before the drops, and the content's tint is only for the glass.
        for name, file in SURFACES.items():
            text = qml(file)
            if 'glassReady ? [] :' not in text:
                continue
            with self.subTest(surface=name):
                owner = 'body' if file == 'LauncherBody.qml' else 'panel'
                flat = text.index(f'objectName: "{name}-flat"')
                self.assertIn('CanvasTint {', text[flat - 60:flat])
                self.assertIn(f'!{owner}.glassReady', text[flat:flat + 200])
                self.assertLess(text.index(f'color: LiquidPalette.flatPlate'), flat)
                self.assertLess(flat, text.index('glassReady ? [] :'))
                glass = text.index(f'objectName: "{name}"')
                self.assertIn(f'shown: LiquidPalette.sharedCanvas', text[glass:glass + 200])
                self.assertIn(f'{owner}.glassReady', text[glass:glass + 200])

    def test_launcher_canvas_starts_under_the_header(self):
        text = qml('LauncherBody.qml')
        block = text[text.index('objectName: "launcher-canvas"'):][:600]
        self.assertIn('shown: LiquidPalette.sharedCanvas && !body.settingsActive', block)
        self.assertIn('y: Metrics.launcherHeader', block)

    def test_windows_without_glass_load_it_only_with_the_switch(self):
        for file in ('ShortcutSheet.qml', 'PolkitPromptCard.qml'):
            with self.subTest(file=file):
                text = qml(file)
                self.assertIn('visible: !LiquidPalette.sharedCanvas', text)
                self.assertIn('active: LiquidPalette.sharedCanvas', text)
        self.assertIn('wallpaperTexture: LiquidPalette.sharedCanvas ? wallpaper.texture : ""', qml('ShellScene.qml'))
        # The prompt round moved the card from the protected surface into PolkitPromptBody.qml.
        body = qml('PolkitPromptBody.qml')
        self.assertIn('wallpaperTexture: LiquidPalette.sharedCanvas ? surface.scene?.wallpaper.texture ?? "" : ""', body)
        self.assertIn('dpr: surface.scene?.outputScale ?? 1', body)


def pictures():
    spec = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORK = ROOT / '.cache/shared-canvas-test'
    module.CACHE = module.WORK / 'frames'
    module.CACHE.mkdir(parents=True, exist_ok=True)
    return module


def staged(module, shared):
    target = module.WORK / ('on' if shared else 'off')
    module.stage(ROOT, target)
    if not shared:
        palette = target / 'LiquidPalette.qml'
        text = palette.read_text()
        assert text.count(SWITCH) == 1
        palette.write_text(text.replace(SWITCH, SWITCH.replace('true', 'false')))
    return target


@unittest.skipIf('--static' in sys.argv, 'static checks only')
class Rendered(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image, ImageChops
        cls.Image, cls.ImageChops = Image, ImageChops
        module = pictures()
        cls.module = module
        cls.frames = {}
        for shared in (False, True):
            tree = staged(module, shared)
            for theme in ('light', 'dark'):
                for shot in ('launcher',) + KEPT + GLASS_BODIES:
                    target = module.CACHE / f'{theme}-{"on" if shared else "off"}-{shot}'
                    module.capture(tree, target, shot, theme, reference_text=True)
                    cls.frames[theme, shared, shot] = Image.open(target.with_suffix('.png')).convert('RGB')
                cls.frames[theme, shared] = cls.frames[theme, shared, 'launcher']

    def test_switch_off_is_the_frozen_light_default(self):
        # Outside the approved change (the grey gear of the Settings button), as
        # tests/theme-pictures.py --compare-only compares it.
        reference = self.Image.open(ROOT / 'tests/fixtures/theme-light/launcher.png').convert('RGB')
        frame = self.frames['light', False].copy()
        approved = self.module.APPROVED_CHANGES['launcher']
        frame.paste(reference.crop((approved[0], approved[1], approved[2] + 1, approved[3] + 1)), approved[:2])
        self.assertEqual(list(reference.getdata()), list(frame.getdata()))

    def test_default_keeps_the_islands_dock_osd_and_lock(self):
        # The shipped default (on) against the switch off, which is 0.8.0's look: the frozen light
        # references prove the off launcher here, and tests/theme-pictures.py --compare-only proves
        # the dock, Settings and Programs & Updates against them with the switch on.
        for theme in ('light', 'dark'):
            for shot in KEPT:
                with self.subTest(theme=theme, shot=shot):
                    off, on = self.frames[theme, False, shot], self.frames[theme, True, shot]
                    self.assertIsNone(self.ImageChops.difference(off, on).getbbox())

    def test_switch_on_is_the_settings_material_under_the_header(self):
        tints = {'light': (1, .9647058823529412, .9411764705882353), 'dark': (.11, .075, .063)}
        alpha = .58
        for theme, tint in tints.items():
            with self.subTest(theme=theme):
                off, on = self.frames[theme, False], self.frames[theme, True]
                # The header keeps the plain glass: away from its lower edge, where the mode
                # bubble's refraction reaches into the body, nothing changes.
                header = (PLATE_X + 24, PLATE_Y + 6, PLATE_X + PLATE_W - 24, PLATE_Y + HEADER - 16)
                changed = [(x, y) for x in range(header[0], header[2]) for y in range(header[1], header[3])
                           if off.getpixel((x, y)) != on.getpixel((x, y))]
                self.assertEqual(changed, [])
                # The body, from the header down to the plate's bottom (the last changed row,
                # inside its rounded corners): wherever no content is drawn, today's pixel
                # under the tint.
                rows = [y for y in range(PLATE_Y, off.height)
                        if any(off.getpixel((x, y)) != on.getpixel((x, y)) for x in range(PLATE_X, PLATE_X + PLATE_W, 4))]
                self.assertEqual(rows[0], PLATE_Y + HEADER)
                self.assertGreater(rows[-1], PLATE_Y + HEADER + 100)
                body = (PLATE_X + 30, PLATE_Y + HEADER + 6, PLATE_X + PLATE_W - 30, rows[-1] - 12)
                matched = total = 0
                for x in range(body[0], body[2], 3):
                    for y in range(body[1], body[3], 3):
                        before, after = off.getpixel((x, y)), on.getpixel((x, y))
                        expected = [round(before[i] * (1 - alpha) + 255 * tint[i] * alpha) for i in range(3)]
                        total += 1
                        matched += all(abs(after[i] - expected[i]) <= 2 for i in range(3))
                # Text, icons and the selection bubble sit above the tint, so they differ.
                self.assertGreater(matched / total, .8, (matched, total))

    def test_switch_on_is_the_settings_material_on_the_glass_bodies(self):
        # Of the pixels the switch changes on these surfaces, those of the body are today's pixel
        # under the tint. Text, icons and the glass drops (slider knobs, the selected device rows,
        # Welcome's highlighted block) sit above the tint, so they change otherwise. Measured on
        # the merged 0.8.1 tree: 0.77 (Sound, light) to 0.97 (clock drawer, dark) of all changed
        # samples; a missing, misplaced or miscoloured tint gives far less.
        for theme, tint in TINTS.items():
            for shot in GLASS_BODIES:
                with self.subTest(theme=theme, shot=shot):
                    off = list(self.frames[theme, False, shot].getdata())[::3]
                    on = list(self.frames[theme, True, shot].getdata())[::3]
                    changed = matched = 0
                    for before, after in zip(off, on):
                        if before == after:
                            continue
                        changed += 1
                        expected = [round(before[i] * (1 - ALPHA) + 255 * tint[i] * ALPHA) for i in range(3)]
                        matched += all(abs(after[i] - expected[i]) <= 2 for i in range(3))
                    print(f'{theme} {shot}: {matched} of {changed} changed samples follow the Settings formula '
                          f'({matched / max(changed, 1):.3f})', file=sys.stderr)
                    self.assertGreater(changed, 1000)
                    self.assertGreater(matched / changed, .7, (matched, changed))


if __name__ == '__main__':
    argv = [arg for arg in sys.argv if arg != '--static']
    unittest.main(argv=argv)
