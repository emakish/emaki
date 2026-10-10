# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""One selected Wi-Fi profile, with no host network or root changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.errors import InstallError
from emaki_installer.network_profiles import active_wifi, capture_wifi, install_wifi, private_command
from emaki_installer.runtime import TargetFiles

UUID = 'e6685942-10ed-4eaf-9741-1bfed347fc6f'
PROFILE = ("[connection]\nid=Install fixture\nuuid=" + UUID +
           "\ntype=wifi\nautoconnect=false\npermissions=user:live:;\n\n[wifi]\nmode=infrastructure\nssid=Install fixture\n"
           "\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=fixture-password-only\n"
           "\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n")


class WifiProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'profiles'
        self.source.mkdir()
        self.profile = self.source / 'selected.nmconnection'
        self.profile.write_text(PROFILE)
        self.profile.chmod(0o600)
        self.other = self.source / 'other.nmconnection'
        self.other.write_text('This unrelated profile must never be read or copied.')
        self.calls = []
        self.active = True

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if '--active' in argv:
            return UUID + ':wifi\n' if self.active else ''
        if 'UUID,TYPE,FILENAME' in argv:
            return UUID + ':wifi:' + str(self.profile) + '\n'
        return private_command(argv, **kwargs)

    def capture(self):
        return capture_wifi(UUID, run=self.command, directories=(self.source,), owner=os.getuid())

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_selected_profile_valid_in_installed_tree_and_private(self):
        data = self.capture()
        target = self.root / 'target'
        target.mkdir()
        install_wifi(UUID, data, TargetFiles(target))
        profiles = list((target / 'etc/NetworkManager/system-connections').iterdir())
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, UUID + '.nmconnection')
        self.assertEqual(profiles[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(profiles[0].stat().st_uid, os.geteuid())
        self.assertEqual(profiles[0].stat().st_gid, os.getegid())
        self.assertNotIn(b'autoconnect=false', data)
        self.assertEqual(profiles[0].parent.stat().st_mode & 0o777, 0o700)
        verified = private_command(['nmcli', '--offline', 'connection', 'modify',
                                    'connection.autoconnect', 'yes'], input=profiles[0].read_bytes())
        self.assertIn('psk=fixture-password-only', verified)
        self.assertNotIn('permissions=user:live:', verified)
        self.assertIn('uuid=' + UUID, verified)
        self.assertNotIn('fixture-password-only', str([argv for argv, _ in self.calls]))
        self.assertEqual(self.other.read_text(), 'This unrelated profile must never be read or copied.')

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_legacy_wifi_key_is_carried_only_when_saved_privately(self):
        data = PROFILE.replace('key-mgmt=wpa-psk\npsk=fixture-password-only',
                               'key-mgmt=none\nwep-key0=0123456789\nwep-key-type=1')
        self.profile.write_text(data)
        self.assertIn(b'wep-key0=0123456789', self.capture())
        self.profile.write_text(data.replace('wep-key-type=1', 'wep-key-type=1\nwep-key-flags=1'))
        with self.assertRaises(InstallError): self.capture()

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_full_security_section_requires_a_saved_secret_after_normalization(self):
        profile = PROFILE.replace('[wifi-security]', '[802-11-wireless-security]')
        self.profile.write_text(profile)
        self.assertIn(b'psk=fixture-password-only', self.capture())
        self.profile.write_text(profile.replace('psk=fixture-password-only', 'psk-flags=1'))
        with self.assertRaises(InstallError):
            self.capture()

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_active_filename_listing_accepts_long_type_and_colons_in_filename(self):
        old = self.profile
        self.profile = old.with_name('selected:network.nmconnection')
        old.rename(self.profile)
        def run(argv, **kwargs):
            return self.command(argv, **kwargs).replace(':wifi', ':802-11-wireless')
        self.assertIn(b'psk=fixture-password-only', capture_wifi(
            UUID, run=run, directories=(self.source,), owner=os.getuid()))

    def test_inactive_or_world_readable_profile_refused(self):
        self.active = False
        with self.assertRaises(InstallError): self.capture()
        self.active = True
        self.profile.chmod(0o644)
        with self.assertRaises(InstallError): self.capture()

    def test_symlink_and_hardlink_refused(self):
        self.profile.unlink()
        self.profile.symlink_to(self.other)
        with self.assertRaises(InstallError): self.capture()
        self.profile.unlink()
        self.profile.hardlink_to(self.other)
        self.profile.chmod(0o600)
        with self.assertRaises(InstallError): self.capture()

    def test_mismatched_uuid_session_secret_and_enterprise_refused(self):
        for data in (PROFILE.replace(UUID, 'other'), PROFILE.replace('psk=', 'psk-flags=1\npsk='),
                     PROFILE + '\n[802-1x]\neap=tls;\n'):
            self.profile.write_text(data)
            with self.assertRaises(InstallError): self.capture()

    def test_target_symlink_is_refused_without_touching_other_tree(self):
        target = self.root / 'target'
        (target / 'etc/NetworkManager').mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (target / 'etc/NetworkManager/system-connections').symlink_to(outside)
        with self.assertRaises(InstallError):
            install_wifi(UUID, PROFILE.encode(), TargetFiles(target))
        self.assertEqual(list(outside.iterdir()), [])

    def test_private_command_does_not_include_output_in_failure(self):
        with patch('emaki_installer.network_profiles.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 1, b'private-output', b'private-error')):
            with self.assertRaises(InstallError) as error:
                private_command(['nmcli'])
        self.assertNotIn('private-', str(error.exception))

    def test_desktop_wifi_selection_ignores_wired_connections(self):
        calls = []
        def run(argv):
            calls.append(argv)
            return 'wired:802-3-ethernet\n' + UUID + ':wifi\n'
        self.assertEqual(active_wifi(run=run), UUID)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(active_wifi(run=lambda argv: 'wired:ethernet\n'))
        self.assertIsNone(active_wifi(run=lambda argv: ''))
        self.assertEqual(active_wifi(run=lambda argv: UUID + ':802-11-wireless\n'), UUID)
        with self.assertRaises(InstallError):
            active_wifi(run=lambda argv: UUID + ':wifi\nother:802-11-wireless\n')
