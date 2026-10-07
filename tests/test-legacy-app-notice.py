#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy application cleanup is a one-time, read-only package upgrade notice."""
from pathlib import Path
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'packaging/emaki-desktop/emaki-desktop.install'


@unittest.skipUnless(shutil.which('vercmp'), 'needs pacman vercmp')
class LegacyNoticeTests(unittest.TestCase):
    def notice(self, previous, orphans='', query_status=0):
        with tempfile.TemporaryDirectory(prefix='emaki-notice-') as directory:
            log = Path(directory) / 'calls'
            env = dict(os.environ, NOTICE_LOG=str(log), NOTICE_ORPHANS=orphans,
                       NOTICE_STATUS=str(query_status))
            result = subprocess.run(['bash', '-eu', '-c', '''
                pacman() {
                    printf '%s\\n' "$*" >> "$NOTICE_LOG"
                    [[ "$*" == '-Qdtq' ]] || return 99
                    printf '%s\\n' "$NOTICE_ORPHANS"
                    return "$NOTICE_STATUS"
                }
                source "$1"
                post_upgrade '0.2.0-3' "$2"
            ''', 'notice-test', str(HOOK), previous], env=env,
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            calls = log.read_text().splitlines() if log.exists() else []
            self.assertTrue(all(call == '-Qdtq' for call in calls), calls)
            return result.stdout, calls

    def test_upgrade_lists_only_legacy_orphans(self):
        for previous in ('0.1.0-1', '0.1.1-1', '0.1.2-1', '0.2.0-1'):
            with self.subTest(previous=previous):
                output, calls = self.notice(previous, 'loupe\nnautilus\nfile-roller\npapers\n'
                                           'xdg-desktop-portal-gnome\n7zip\nunrar\nother-app')
                self.assertEqual(calls, ['-Qdtq'])
                self.assertIn('pacman -R loupe nautilus file-roller papers\n', output)
                self.assertIn('review the removal transaction', output)
                self.assertIn('The desktop portal is required [xdg-desktop-portal-gnome].\n', output)
                self.assertNotIn('xdg-desktop-portal-gnome', next(line for line in output.splitlines() if 'pacman -R' in line))
                for excluded in ('7zip', 'unrar', 'other-app'):
                    self.assertNotIn(excluded, output)

    def test_installed_but_required_or_explicit_apps_are_not_listed(self):
        output, calls = self.notice('0.1.1-1', 'papers\nother-app')
        self.assertEqual(calls, ['-Qdtq'])
        self.assertIn('pacman -R papers\n', output)
        self.assertNotIn('nautilus', output)
        self.assertNotIn('loupe', output)

    def test_empty_or_failed_query_is_silent(self):
        for status in (0, 1, 127):
            with self.subTest(status=status):
                output, calls = self.notice('0.2.0-1', '', status)
                self.assertEqual(output, '')
                self.assertEqual(calls, ['-Qdtq'])

    def test_notice_is_not_repeated_after_migration(self):
        for previous in ('0.2.0-2', '0.2.0-3', '0.3.0-1'):
            with self.subTest(previous=previous):
                output, calls = self.notice(previous, 'nautilus')
                self.assertEqual((output, calls), ('', []))

    def test_recipe_delivers_upgrade_hook(self):
        helpers = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))
        info = helpers['metadata'](ROOT / 'packaging/emaki-desktop/PKGBUILD')
        self.assertEqual(info['install'], ['emaki-desktop.install'])
        # Newer than 0.2.0-2, the release the scriptlet's notice cut-off names.
        newer = subprocess.run(['vercmp', f"{info['pkgver'][0]}-{info['pkgrel'][0]}", '0.2.0-2'], capture_output=True, text=True)
        self.assertEqual(newer.stdout.strip(), '1', info)
        self.assertNotIn('nautilus', info['conflicts'])


if __name__ == '__main__':
    unittest.main()
