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
    def notice(self, previous, orphans='', query_status=0, keyring_status=1):
        with tempfile.TemporaryDirectory(prefix='emaki-notice-') as directory:
            log = Path(directory) / 'calls'
            env = dict(os.environ, NOTICE_LOG=str(log), NOTICE_ORPHANS=orphans,
                       NOTICE_STATUS=str(query_status), NOTICE_KEYRING_STATUS=str(keyring_status))
            result = subprocess.run(['bash', '-eu', '-c', '''
                pacman() {
                    printf '%s\\n' "$*" >> "$NOTICE_LOG"
                    case "$*" in
                        '-Qdtq')
                            printf '%s\\n' "$NOTICE_ORPHANS"
                            return "$NOTICE_STATUS" ;;
                        '-Qq gnome-keyring') return "$NOTICE_KEYRING_STATUS" ;;
                        *) return 99 ;;
                    esac
                }
                source "$1"
                post_upgrade '0.3.1-1' "$2"
            ''', 'notice-test', str(HOOK), previous], env=env,
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            calls = log.read_text().splitlines() if log.exists() else []
            self.assertTrue(all(call in ('-Qdtq', '-Qq gnome-keyring') for call in calls), calls)
            return result.stdout, calls

    def test_upgrade_lists_only_legacy_orphans(self):
        for previous in ('0.1.0-1', '0.1.1-1', '0.1.2-1', '0.2.0-1'):
            with self.subTest(previous=previous):
                output, calls = self.notice(previous, 'loupe\nnautilus\nfile-roller\npapers\n'
                                           'xdg-desktop-portal-gnome\n7zip\nunrar\nother-app')
                self.assertEqual(calls, ['-Qdtq', '-Qq gnome-keyring'])
                self.assertIn('pacman -R loupe nautilus file-roller papers\n', output)
                self.assertIn('review the removal transaction', output)
                self.assertIn('The desktop portal is required [xdg-desktop-portal-gnome].\n', output)
                self.assertNotIn('xdg-desktop-portal-gnome', next(line for line in output.splitlines() if 'pacman -R' in line))
                for excluded in ('7zip', 'unrar', 'other-app'):
                    self.assertNotIn(excluded, output)

    def test_installed_but_required_or_explicit_apps_are_not_listed(self):
        output, calls = self.notice('0.1.1-1', 'papers\nother-app')
        self.assertEqual(calls, ['-Qdtq', '-Qq gnome-keyring'])
        self.assertIn('pacman -R papers\n', output)
        self.assertNotIn('nautilus', output)
        self.assertNotIn('loupe', output)

    def test_empty_or_failed_query_is_silent(self):
        for status in (0, 1, 127):
            with self.subTest(status=status):
                output, calls = self.notice('0.2.0-1', '', status)
                self.assertEqual(output, '')
                self.assertEqual(calls, ['-Qdtq', '-Qq gnome-keyring'])

    def test_notice_is_not_repeated_after_migration(self):
        for previous in ('0.2.0-2', '0.2.0-3', '0.3.0-1'):
            with self.subTest(previous=previous):
                output, calls = self.notice(previous, 'nautilus')
                self.assertEqual((output, calls), ('', ['-Qdtq', '-Qq gnome-keyring']))

    def test_new_cleanup_notice_only_names_orphaned_agent(self):
        for previous in ('0.2.0-1', '0.2.0-2', '0.3.0-1'):
            with self.subTest(previous=previous):
                output, _ = self.notice(previous, 'polkit-gnome\ngnome-keyring\nother-app')
                self.assertIn('pacman -R polkit-gnome\n', output)
                self.assertNotIn('pacman -R gnome-keyring', output)
                self.assertNotIn('other-app', output)
        output, _ = self.notice('0.3.0-1', 'other-app')
        self.assertEqual(output, '')

    def test_failed_orphan_query_cannot_recommend_agent_removal(self):
        output, _ = self.notice('0.3.0-1', 'polkit-gnome', query_status=127)
        self.assertEqual(output, '')

    def test_keyring_notice_requires_all_accounts_to_verify_copies(self):
        for query_status in (0, 1, 127):
            with self.subTest(query_status=query_status):
                output, calls = self.notice('0.3.0-1', 'gnome-keyring',
                                            query_status=query_status, keyring_status=0)
                self.assertEqual(calls, ['-Qdtq', '-Qq gnome-keyring'])
                self.assertIn('Your old keyring files are untouched.', output)
                self.assertIn('Keep the old password service [gnome-keyring] until every account has copied and verified all its secrets.', output)
                self.assertIn('emaki-keyring-recover', output)
                self.assertIn('Saved passwords and application master keys are copied at the first graphical login.', output)
                condition = 'Only after every account has verified its copies may an administrator review this removal transaction:'
                self.assertIn(condition, output)
                self.assertLess(output.index(condition), output.index('pacman -R gnome-keyring'))
                self.assertIn('Do not remove the old password service [gnome-keyring] while any account still needs its old secrets.', output)

    def test_keyring_query_failure_does_not_claim_secrets_are_safe(self):
        output, _ = self.notice('0.3.0-1', keyring_status=127)
        self.assertEqual(output, '')

    def test_new_notice_is_not_repeated_after_transition(self):
        for previous in ('0.3.1-1', '0.3.1-2', '0.4.0-1'):
            with self.subTest(previous=previous):
                self.assertEqual(self.notice(previous, 'polkit-gnome\ngnome-keyring',
                                             keyring_status=0), ('', []))

    def test_recipe_delivers_upgrade_hook(self):
        helpers = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))
        info = helpers['metadata'](ROOT / 'packaging/emaki-desktop/PKGBUILD')
        self.assertEqual(info['install'], ['emaki-desktop.install'])
        # Newer than 0.2.0-2, the release the scriptlet's notice cut-off names.
        newer = subprocess.run(['vercmp', f"{info['pkgver'][0]}-{info['pkgrel'][0]}", '0.2.0-2'], capture_output=True, text=True)
        self.assertEqual(newer.stdout.strip(), '1', info)
        self.assertNotIn('nautilus', info['conflicts'])
        for package in ('gnome-keyring', 'polkit-gnome'):
            self.assertNotIn(package, info['conflicts'])
            self.assertNotIn(package, info['replaces'])


if __name__ == '__main__':
    unittest.main()
