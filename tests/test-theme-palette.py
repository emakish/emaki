#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check palette generation, preserved defaults, and dark text contrast."""
from pathlib import Path
import subprocess
import os
import re
import ast
import operator
import shutil
import tempfile
from runtime_fixture import runtime_path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOKENS = tomllib.loads((ROOT / 'tokens.toml').read_text())
ACCENTS = {value.lower() for row in TOKENS['accents'].values()
           for key, value in row.items() if key != 'name'}
# These are fixed brand artwork, not interface accents. No component exemptions.
BRAND = {'b01e78', 'ec2a55', 'ff6a2a', 'ffb62e'}


def number(expression):
    """Evaluate numeric colour arithmetic, never names or executable expressions."""
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand)
        operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                      ast.Mult: operator.mul, ast.Div: operator.truediv}
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            return operations[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError('not numeric')
    return visit(ast.parse(expression.strip(), mode='eval').body)


def literal_accents(code, suffix):
    found = set()
    for value in re.findall(r'#([0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)\b', code):
        value = value.lower()
        color = value if len(value) == 6 else value[:6] if suffix in ('.svg', '.kdl') else value[2:]
        if color in ACCENTS - BRAND:
            found.add(color)
    for match in re.finditer(r'(?:Qt\.(?:rgba|vector3d|vector4d)|\bvec[34])\s*\(', code):
        start = end = match.end()
        depth = 1
        while end < len(code) and depth:
            depth += (code[end] == '(') - (code[end] == ')')
            end += 1
        if depth:
            continue
        args = code[start:end-1]
        parts = args.split(',')
        if len(parts) < 3:
            continue
        try:
            channels = [number(part) for part in parts[:3]]
            value = ''.join(f'{round(channel * 255):02x}' for channel in channels)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            continue
        if value in ACCENTS - BRAND:
            found.add(value)
    return found


def rgb(value):
    if isinstance(value, list):
        return value[:3]
    return [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]


def composite(foreground, background):
    alpha = foreground[3] if isinstance(foreground, list) and len(foreground) == 4 else 1
    return [alpha * front + (1 - alpha) * back for front, back in zip(rgb(foreground), rgb(background))]


def luminance(color):
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb(color)]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(first, second):
    a, b = sorted([luminance(first), luminance(second)])
    return (b + .05) / (a + .05)


class PaletteTests(unittest.TestCase):
    def test_lock_reads_managed_appearance(self):
        # The passive catalog is exercised against durable writes by
        # test-appearance-live.py; the lock must actually instantiate it too.
        source = (ROOT / 'shell/lock.qml').read_text()
        self.assertRegex(source, r'SettingsCatalog\s*\{\s*active:\s*false\s*\}')

    def test_runtime_palette_switch(self):
        with tempfile.TemporaryDirectory(prefix='emaki-palette-') as temporary:
            base = Path(temporary)
            for name in ('ShellPalette.qml', 'LiquidPalette.qml', 'Appearance.qml', 'AppearanceLogic.js', 'SettingsBridge.qml'):
                shutil.copyfile(ROOT / 'shell' / name, base / name)
            (base / 'qmldir').write_text(''.join(f'singleton {name} 1.0 {name}.qml\n' for name in ('ShellPalette','LiquidPalette','Appearance','SettingsBridge')))
            shutil.copyfile(ROOT / 'tests/fixtures/AppearancePaletteTest.qml', base / 'test.qml')
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(base)),
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
            env.pop('WAYLAND_DISPLAY', None)
            env.pop('DISPLAY', None)
            result = subprocess.run(['qs', '-p', str(base / 'test.qml'), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('APPEARANCE_PALETTE_RESULT 6 0', output)

    def test_no_literal_accent_in_components(self):
        offenders = []
        paths = [path for tree in ('shell', 'update-manager', 'niri', 'installer/ui')
                 for extension in ('*.qml', '*.js', '*.frag', '*.vert', '*.svg', '*.kdl')
                 for path in (ROOT / tree).rglob(extension)
                 if 'tests' not in path.parts]
        paths += list((ROOT / 'scripts/templates').glob('*.svg'))
        # Generated outputs are verified against their source, never blanket-exempt.
        generated = {'shell/ShellPalette.qml', 'update-manager/emaki-programs-updates.svg', 'niri/theme.kdl'}
        for path in paths:
            if str(path.relative_to(ROOT)) in generated:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.lstrip().startswith(('//', '<!--')):
                    continue
                if literal_accents(line, path.suffix):
                    offenders.append(f'{path.relative_to(ROOT)}:{number}')
        self.assertEqual(offenders, [], '\n'.join(offenders))
        subprocess.run(['python3', str(ROOT / 'scripts/render-shell-palette'), '--check'], check=True)
        with tempfile.TemporaryDirectory(prefix='theme-generated-') as directory:
            target = Path(directory)
            (target / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts/render-theme', target / 'scripts/render-theme')
            shutil.copyfile(ROOT / 'tokens.toml', target / 'tokens.toml')
            subprocess.run(['python3', str(target / 'scripts/render-theme')], check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((ROOT / 'niri/theme.kdl').read_bytes(), (target / 'niri/theme.kdl').read_bytes())

    def test_accent_scanner_recognizes_all_families_and_numeric_forms(self):
        for value in ACCENTS - BRAND:
            channels = [int(value[i:i+2], 16) for i in (0, 2, 4)]
            for suffix in ('.qml', '.js', '.svg', '.kdl', '.frag'):
                self.assertEqual(literal_accents('fill="#' + value + '"', suffix), {value})
            for constructor in ('Qt.rgba', 'vec3', 'vec4', 'Qt.vector3d'):
                args = ', '.join('(' + str(c) + '/255)' for c in channels)
                self.assertEqual(literal_accents(constructor + '(' + args + ', .8)', '.qml'), {value})
        self.assertEqual(literal_accents('Qt.rgba(accent.r, accent.g, accent.b, 1)', '.qml'), set())

    def test_default_accent_is_rose_everywhere(self):
        # DECISIONS 2026-10-14: nothing chosen means Rose, not Orange; Orange stays a choice.
        default = TOKENS['defaults']['accent']
        self.assertEqual(default, 'rose')
        rose = TOKENS['accents'][default]
        self.assertIn('orange', TOKENS['accents'])
        self.assertEqual((TOKENS['color']['accent'], TOKENS['color']['on_accent']), (rose['base'], rose['text']))
        fallback = re.search(r'"appearance\.accent", "(\w+)"\)', (ROOT / 'shell/Appearance.qml').read_text())
        self.assertEqual(fallback.group(1), default)
        palette = (ROOT / 'shell/ShellPalette.qml').read_text()
        self.assertIn(f'readonly property string defaultAccent: "{default}"', palette)
        self.assertIn('|| accents.find(entry => entry.id === defaultAccent)', palette)
        self.assertIn(f'active-color "#{rose["base"]}ff"', (ROOT / 'niri/theme.kdl').read_text())
        icon = (ROOT / 'update-manager/emaki-programs-updates.svg').read_text()
        self.assertTrue(all('#' + rose[key] in icon for key in ('base', 'strong', 'soft', 'shade')))
        installer = (ROOT / 'installer/ui/InstallerView.qml').read_text()
        self.assertIn('readonly property color accent: Shell.ShellPalette.accent', installer)
        self.assertIn('readonly property color accentInk: Shell.LiquidPalette.accentOnLight', installer)
        # The installer is light: its focus rims and marks use the on-light ink, 3:1 on white glass.
        for name, accent in TOKENS['accents'].items():
            self.assertGreaterEqual(contrast(accent['on_light'], 'ffffff'), 3, name)

    def test_generated_palette_matches(self):
        subprocess.run(['python3', str(ROOT / 'scripts/render-shell-palette'), '--check'], check=True)

    def test_original_light_glass_values(self):
        # Preserve the material, accent and distinct secondary ink levels;
        # the secondary ink must also meet the readable-text threshold.
        light = TOKENS['shell_light']
        expected = dict(ink_on_light='241018', ink_on_dark='f4e9f0', glass_text='fbf3ea',
                        flat_plate=[1,.973,.953,.70], flat_panel=[1,.973,.953,.86],
                        flat_drop=[36/255,16/255,24/255,.07], flat_drop_rim=[36/255,16/255,24/255,.16],
                        dim_on_light=[36/255,16/255,24/255,.68], faint_on_light=[36/255,16/255,24/255,.625],
                        settings_ink='241018', settings_dim=[36/255,16/255,24/255,.68],
                        settings_faint=[36/255,16/255,24/255,.625],
                        settings_drop=[36/255,16/255,24/255,.065],
                        settings_drop_hover=[36/255,16/255,24/255,.11], settings_rim=[36/255,16/255,24/255,.14],
                        settings_field=[1,1,1,.62], settings_card=[1,250/255,246/255,.42], settings_knob='fffaf6')
        for key, value in expected.items():
            self.assertEqual(light[key], value, key)
        orange = TOKENS['accents']['orange']
        self.assertEqual([orange[k] for k in ('base','on_light','on_dark','text','soft','strong')],
                         ['e2733f','c2481f','ff9b6a','2a0f05','f39a5f','c9503a'])

    def test_light_secondary_contrast(self):
        light = TOKENS['shell_light']
        # The shared Settings canvas covers Regular glass, including its darkest backdrop.
        fields = [composite(light['settings_sidebar'], composite(light['flat_plate'], back))
                  for back in ('000000', 'ffffff')]
        fields += [light['manager_background'], light['manager_panel']]
        for ink in ('settings_dim', 'settings_faint', 'dim_on_light', 'faint_on_light', 'manager_dim'):
            ratios = [contrast(composite(light[ink], field), field) for field in fields]
            print(f'light {ink}: minimum {min(ratios):.2f}:1')
            self.assertGreaterEqual(min(ratios), 4.5, (ink, min(ratios)))

    def test_update_secondary_uses_readable_ink(self):
        source = (ROOT / 'update-manager/ui/ManagerUpdates.qml').read_text()
        self.assertIn('component Muted: ManagerLabel { color: Shell.ShellPalette.managerDim;', source)
        package_label = next(line for line in source.splitlines() if 'text: root.plainName(updateRow.modelData) !==' in line)
        self.assertIn('color: Shell.ShellPalette.managerDim;', package_label)
        self.assertNotIn('opacity: modelData.name === "AUR"', source)

    def test_dark_body_contrast(self):
        dark = TOKENS['shell_dark']
        # White wallpaper is the brightest possible backdrop of dark glass.
        plate = composite(dark['flat_plate'], 'ffffff')
        panel = composite(dark['flat_panel'], 'ffffff')
        fields = [plate, panel, dark['manager_background'], dark['manager_panel'], dark['polkit_card'],
                  composite(dark['settings_field'], plate), composite(dark['settings_card'], plate),
                  composite([.055, .05, .065, .86], 'ffffff')]
        for ink in ('settings_ink', 'settings_dim', 'settings_faint', 'settings_error', 'manager_ink', 'manager_dim', 'manager_body', 'polkit_ink', 'polkit_dim'):
            ratios = [contrast(dark[ink], field) for field in fields]
            self.assertGreaterEqual(min(ratios), 4.5, (ink, min(ratios)))
            print(f'{ink}: minimum {min(ratios):.2f}:1')

    def test_accent_contrast_and_selection(self):
        self.assertEqual(list(TOKENS['accents']), ['orange','coral','rose','amber','green','teal','blue','violet'])
        for name, accent in TOKENS['accents'].items():
            self.assertGreaterEqual(contrast(accent['text'], accent['base']), 4.5, name)
            self.assertGreaterEqual(contrast(accent['on_dark'], TOKENS['shell_dark']['manager_panel']), 4.5, name)
            selected = [value * .32 for value in rgb(accent['base'])]
            self.assertGreaterEqual(contrast(TOKENS['shell_dark']['ink_on_dark'], selected), 4.5, name)
            # Featured summaries remain body text on the hovered accent gradient.
            # Include the real glass foundation and SettingsGlass tint over white.
            glass = composite([.055, .05, .065, .86], 'ffffff')
            manager = composite(TOKENS['shell_dark']['settings_sidebar'], glass)
            feature = composite(rgb(accent['base']) + [.34], manager)
            self.assertGreaterEqual(contrast(TOKENS['shell_dark']['manager_dim'], feature), 4.5, name)

    def test_semantic_text_contrast(self):
        for theme in ('shell_light', 'shell_dark'):
            palette = TOKENS[theme]
            self.assertGreaterEqual(contrast(palette['polkit_key_text'], palette['polkit_key']), 4.5, theme)
            self.assertGreaterEqual(contrast(palette['settings_ink'], palette['settings_display_idle']), 4.5, theme)
        self.assertEqual(TOKENS['shell_light']['settings_display_idle'], 'd7d9de')
        self.assertEqual(TOKENS['accents']['orange']['display_selected'], 'e7b39a')
        for name, accent in TOKENS['accents'].items():
            self.assertGreaterEqual(contrast(TOKENS['shell_light']['settings_ink'], accent['display_selected']), 4.5, name)
            self.assertGreaterEqual(contrast(TOKENS['shell_dark']['settings_ink'], accent['deep']), 4.5, name)
        dark = TOKENS['shell_dark']
        text = contrast(dark['polkit_allow_text'], dark['polkit_allow'])
        edge = contrast(dark['polkit_allow'], dark['polkit_card'])
        self.assertGreaterEqual(text, 4.5)
        self.assertGreaterEqual(edge, 3)
        print(f'dark Allow: text {text:.2f}:1; card {edge:.2f}:1')


if __name__ == '__main__':
    unittest.main()
