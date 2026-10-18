#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Report a problem has Emaki's own icon, and nothing outside Welcome and the tests' placeholders
shows the old flat orange "E" (art/logo/mark.svg, installed as the emaki-welcome icon) (0.9.0)."""
import configparser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
DEMO_FILL = '#e2733f'           # the flat mark's colour: the old orange accent
MARK_PATH = 'M3 22L7.161 22'    # the mark's geometry, as mark.svg and Icon.qml carry it
HOT = {'#b01e78', '#ec2a55', '#ff6a2a', '#ffb62e'}


def desktop(path):
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry.read(path)
    return entry['Desktop Entry']


class ReportIcon(unittest.TestCase):
    def test_source_and_generated_icon_have_license_headers(self):
        for name in ("emaki-report.py", "emaki-report.svg"):
            header = "\n".join((ROOT / "art/icons" / name).read_text().splitlines()[:3])
            self.assertIn("Copyright (C) 2026 Artur Yakymenko", header)
            self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", header)

    def test_report_entry_has_its_own_icon(self):
        self.assertEqual(desktop(ROOT / 'shell/emaki-report.desktop')['Icon'], 'emaki-report')

    def test_report_window_resolves_its_desktop_entry(self):
        source = (ROOT / 'shell/AppIdentity.qml').read_text()
        match = re.search(r'function lookupWindow\(appId: string, title: string\): var \{(.*?)\n    \}',
                          source, re.S)
        self.assertIsNotNone(match, 'window-specific desktop entry resolution is missing')
        entry = desktop(ROOT / 'shell/emaki-report.desktop')
        script = r"""
const assert = require('node:assert/strict');
const Quickshell = {appId: 'org.quickshell'};
const report = {id: 'emaki-report', icon: ICON};
let installed = true;
const DesktopEntries = {byId: id => installed && id === report.id ? report : null};
const fallback = {entry: {id: 'ordinary'}, source: 'exact'};
const lookup = appId => ({...fallback, appId});
const lookupWindow = new Function('Quickshell', 'DesktopEntries', 'lookup',
    'return function(appId, title) {' + BODY + '}')(Quickshell, DesktopEntries, lookup);
assert.deepEqual(lookupWindow(Quickshell.appId, 'Report a problem'), {entry: report, source: 'exact'});
assert.equal(report.icon, 'emaki-report');
for (const [appId, title] of [
    [Quickshell.appId, 'Welcome to Emaki'], [Quickshell.appId, 'Other window'],
    ['another-app', 'Report a problem'], ['', 'Report a problem'],
    [Quickshell.appId, 'Report a problem elsewhere']
]) assert.deepEqual(lookupWindow(appId, title), lookup(appId));
installed = false;
assert.deepEqual(lookupWindow(Quickshell.appId, 'Report a problem'), lookup(Quickshell.appId));
""".replace('ICON', json.dumps(entry['Icon'])).replace('BODY', json.dumps(match[1]))
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        dock = (ROOT / 'shell/Dock.qml').read_text()
        self.assertIn('identity.lookupWindow(meta?.appId ?? "", meta?.title ?? "")', dock)
        window = (ROOT / 'shell/ReportWindow.qml').read_text()
        self.assertIn('title: "Report a problem"', window)

    def test_icon_is_the_generated_pixel_drawing(self):
        result = subprocess.run([sys.executable, str(ROOT / 'art/icons/emaki-report.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        icon = (ROOT / 'art/icons/emaki-report.svg').read_text()
        fills = set(re.findall(r'fill="(#[0-9a-f]{6})"', icon))
        self.assertTrue(HOT <= fills, fills)              # the bubble in the mark's Hot colours
        self.assertIn('#f2e6d8', fills)                   # the cream "!"
        self.assertNotIn(DEMO_FILL, fills)
        self.assertNotIn(MARK_PATH, icon)

    def test_icon_is_installed_under_its_name(self):
        makefile = (ROOT / 'Makefile').read_text()
        self.assertTrue('install -Dm644 art/icons/emaki-report.svg $(ICONS)/hicolor/scalable/apps/emaki-report.svg'
                        in makefile, 'make install does not install the icon')
        self.assertTrue(re.search(r'rm -f [^\n]*\$\(ICONS\)/hicolor/scalable/apps/emaki-report\.svg', makefile),
                        'make uninstall does not remove the icon')
        expected = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        self.assertIn('/usr/share/icons/hicolor/scalable/apps/emaki-report.svg', expected)

    def test_release_card_shows_the_real_logo(self):
        # "What's new in Emaki" in Programs & Updates: the mark in its Hot colours, as the bar.
        updates = (ROOT / 'update-manager/ui/ManagerUpdates.qml').read_text()
        card = updates[updates.index('id: releaseIcon') - 200:updates.index('id: releaseIcon') + 400]
        self.assertIn('kind: "logo"', card)
        # The logo draws a child pixel image; the outer Image status stays Null for it.
        self.assertIn('visible: ready', card)
        self.assertNotIn('emaki-welcome', updates)

    def test_no_other_shipped_place_uses_the_demo_mark(self):
        # The old orange demo mark (art/logo/mark.svg) must not ship anywhere. Since the 0.9 Welcome
        # redo, "emaki-welcome" names Welcome's own accent icon (SystemIcons.welcomeSvg,
        # art/icons/emaki-welcome.svg), so only the mark file itself is checked; tests use the flat
        # mark only as a placeholder picture for made-up programs.
        allowed = {'art/logo/mark.py', 'art/logo/mark.svg'}
        if (ROOT / '.git').exists():
            tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True,
                                     check=True).stdout.split()
        else:
            # A package build checks an exported source tree with no .git; leave out build output.
            tracked = []
            for directory, dirs, names in os.walk(ROOT):
                dirs[:] = [d for d in dirs if d not in ('.cache', 'target')]
                tracked.extend(str((Path(directory) / name).relative_to(ROOT)) for name in names)
        found = []
        for name in tracked:
            if name.startswith(('tests/', 'docs/', 'update-manager/ui/tests/')) or name in allowed:
                continue
            path = ROOT / name
            if not name.endswith(('.qml', '.js', '.py', '.desktop', '.svg', '.kdl', '.sh', '.conf')) \
                    or path.is_symlink() or not path.is_file():
                continue
            text = path.read_text(errors='replace')
            if 'art/logo/mark.svg' in text or 'logo/mark.svg' in text:
                found.append(name)
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
