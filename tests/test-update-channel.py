#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage mirror definitions and migrate channel choices into an unowned selector."""

import hashlib
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'packaging/emaki-mirrorlist'


class ChannelMigration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='emaki-channel-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage()
        self.mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        self.channel = self.root / 'etc/emaki/channel'
        self.pacnew = self.mirrorlist.with_suffix('.pacnew')
        self.defaults = self.root / 'usr/share/emaki/mirrors/default.conf'

    def stage(self):
        subprocess.run(['bash', '-eu', '-c',
                        'source "$1/PKGBUILD"; srcdir=$1; pkgdir=$2; package',
                        'stage', str(RECIPE), str(self.root)], check=True, capture_output=True)

    def invoke(self, function, expected=0):
        result = subprocess.run(['bash', '-eu', '-c',
                                 'source "$1/emaki-mirrorlist.install"; "$2" "$3"',
                                 'hook', str(RECIPE), function, str(self.root)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def migrate(self, expected=0):
        return self.invoke('_emaki_migrate_mirrorlist', expected)

    def initialize(self):
        self.invoke('_emaki_initialize_channel')
        return self.migrate()

    def assert_channel(self, channel):
        self.assertTrue(self.channel.is_file(), "Channel selector was not created")
        active = [line for line in self.channel.read_text().splitlines()
                  if line.strip() and not line.startswith('#')]
        self.assertEqual(active, [f'Include = /usr/share/emaki/mirrors/{channel}.conf'])
        self.assertIn('Include = /etc/emaki/channel', self.mirrorlist.read_text())
        server = self.root / f'usr/share/emaki/mirrors/{channel}.conf'
        self.assertIn(f'Server = https://pkgs.emaki.sh/{channel}/$arch', server.read_text())

    def test_staged_payload_excludes_channel_and_checksums_match(self):
        self.assertFalse(self.channel.exists())
        self.assertIn('Include = /usr/share/emaki/mirrors/stable.conf', self.mirrorlist.read_text())
        files = {str(path.relative_to(self.root)) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(files, {'etc/pacman.d/emaki-mirrorlist',
                                 'usr/share/emaki/mirrors/default.conf',
                                 'usr/share/emaki/mirrors/channel.conf',
                                 'usr/share/emaki/mirrors/stable.conf',
                                 'usr/share/emaki/mirrors/testing.conf'})
        metadata = subprocess.run(['bash', '-eu', '-c',
                                   'source "$1/PKGBUILD"; printf "%s\\n" "${backup[@]}"; '
                                   'for i in "${!source[@]}"; do '
                                   'printf "%s %s\\n" "${source[i]}" "${sha256sums[i]}"; done',
                                   'metadata', str(RECIPE)], text=True, capture_output=True, check=True)
        lines = metadata.stdout.splitlines()
        self.assertEqual(lines.pop(0), 'etc/pacman.d/emaki-mirrorlist')
        self.assertEqual(len(lines), 5)
        for line in lines:
            filename, digest = line.split()
            self.assertEqual(hashlib.sha256((RECIPE / filename).read_bytes()).hexdigest(), digest)

    def test_install_initializes_stable_once(self):
        self.initialize()
        self.assert_channel('stable')
        self.assertEqual(self.channel.stat().st_mode & 0o777, 0o644)
        self.channel.write_text('# Personal choice\nInclude = /usr/share/emaki/mirrors/testing.conf\n')
        original = self.channel.read_bytes()
        self.channel.chmod(0o600)
        self.initialize()
        self.assertEqual(self.channel.read_bytes(), original)
        self.assertEqual(self.channel.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(shutil.which('pacman-conf'), 'pacman-conf is required')
    def test_packaged_channels_require_database_signatures(self):
        config = self.root / 'pacman.conf'
        for channel in ('stable', 'testing'):
            config.write_text('[options]\nArchitecture = x86_64\n'
                              'SigLevel = Required DatabaseOptional\n[emaki]\n'
                              f'Include = {self.root}/usr/share/emaki/mirrors/{channel}.conf\n')
            parsed = subprocess.run(['pacman-conf', '--config', str(config), '--repo', 'emaki', 'SigLevel'],
                                    capture_output=True, text=True, check=True).stdout
            self.assertIn('DatabaseRequired', parsed)
            self.assertNotIn('DatabaseOptional', parsed)

    def test_known_legacy_channels_and_previous_selector_with_pacnew(self):
        for channel in ('stable', 'testing'):
            for entry in (f'Server = https://pkgs.emaki.sh/{channel}/$arch',
                          f'Server = https://github.com/emakish/packages/releases/download/{channel}',
                          f'Include = /usr/share/emaki/mirrors/{channel}.conf'):
                with self.subTest(channel=channel, entry=entry):
                    self.channel.unlink(missing_ok=True)
                    self.mirrorlist.write_text(f'# Chosen channel\n {entry}\n# Server = unused\n')
                    self.mirrorlist.chmod(0o640)
                    self.pacnew.write_bytes(self.defaults.read_bytes())
                    self.migrate()
                    self.assert_channel(channel)
                    other = 'stable' if channel == 'testing' else 'testing'
                    self.assertIn(f'# Include = /usr/share/emaki/mirrors/{other}.conf', self.channel.read_text())
                    self.assertEqual(self.mirrorlist.stat().st_mode & 0o777, 0o640)
                    self.assertFalse(self.pacnew.exists())
                    original = self.channel.read_bytes()
                    self.migrate()
                    self.assertEqual(self.channel.read_bytes(), original)

    def test_custom_configurations_are_unchanged_and_warn(self):
        for content in ('Server = https://mirror.example/emaki/$arch\n',
                        'Server = https://pkgs.emaki.sh/testing/$arch\nServer = https://backup.example/$arch\n',
                        'Include = /etc/pacman.d/custom-emaki\n',
                        'Server = https://pkgs.emaki.sh/testing/$arch\nSigLevel = Optional\n',
                        '# Deliberately disabled\n'):
            with self.subTest(content=content):
                self.mirrorlist.write_text(content)
                self.pacnew.write_bytes(self.defaults.read_bytes())
                result = self.migrate()
                self.assertIn('custom server configuration kept', result.stderr)
                self.assertEqual(self.mirrorlist.read_text(), content)
                self.assertEqual(self.pacnew.read_bytes(), self.defaults.read_bytes())
                self.assertTrue(self.channel.is_file())

    def test_new_channel_choice_survives_package_update(self):
        self.initialize()
        self.channel.write_text('# My channel\nInclude = /usr/share/emaki/mirrors/testing.conf\n')
        selected = self.channel.read_bytes()
        self.channel.chmod(0o600)
        self.stage()
        self.migrate()
        self.assertEqual(self.channel.read_bytes(), selected)
        self.assertEqual(self.channel.stat().st_mode & 0o777, 0o600)
        self.assert_channel('testing')

    def test_existing_choice_is_preserved_during_legacy_migration(self):
        self.initialize()
        original = self.channel.read_bytes()
        self.mirrorlist.write_text('Server = https://pkgs.emaki.sh/testing/$arch\n')
        self.migrate()
        self.assertEqual(self.channel.read_bytes(), original)
        self.assert_channel('stable')

    def test_missing_choice_is_initialized_after_unedited_upgrade(self):
        self.migrate()
        self.assert_channel('stable')

    def test_unrecognized_pacnew_is_preserved(self):
        self.mirrorlist.write_text('Server = https://pkgs.emaki.sh/testing/$arch\n')
        self.pacnew.write_text('Server = https://custom.example/$arch\n')
        self.migrate()
        self.assert_channel('testing')
        self.assertEqual(self.pacnew.read_text(), 'Server = https://custom.example/$arch\n')

    def test_symlink_and_hardlink_mirrorlist_are_not_replaced(self):
        target = self.root / 'personal'
        original = 'Server = https://pkgs.emaki.sh/testing/$arch\n'
        target.write_text(original)
        for symbolic in (True, False):
            with self.subTest(symbolic=symbolic):
                self.mirrorlist.unlink()
                if symbolic:
                    self.mirrorlist.symlink_to(target)
                else:
                    os.link(target, self.mirrorlist)
                result = self.migrate()
                self.assertIn('left unchanged', result.stderr)
                self.assertEqual(target.read_text(), original)
                self.assertEqual(self.mirrorlist.read_text(), original)
                self.assertFalse(self.channel.exists())

    def test_existing_linked_channel_is_never_written(self):
        self.channel.parent.mkdir(parents=True, exist_ok=True)
        target = self.root / 'personal'
        original = '# Personal configuration\n'
        target.write_text(original)
        for symbolic in (True, False):
            with self.subTest(symbolic=symbolic):
                if symbolic:
                    self.channel.symlink_to(target)
                else:
                    os.link(target, self.channel)
                self.invoke('_emaki_initialize_channel', expected=1)
                self.assertEqual(target.read_text(), original)
                self.assertEqual(self.channel.read_text(), original)
                self.channel.unlink()

    def test_linked_channel_directory_is_not_written(self):
        target = self.root / 'personal'
        target.mkdir()
        self.channel.parent.rmdir()
        self.channel.parent.symlink_to(target)
        self.invoke('_emaki_initialize_channel', expected=1)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
