# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""One selected Wi-Fi profile, with no host network or root changes."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.errors import InstallError
from emaki_installer.network_profiles import (WifiNotCopied, capture_wifi, choose_wifi, install_wifi,
                                              private_command, saved_wifi)
from emaki_installer.runtime import TargetFiles

UUID = 'e6685942-10ed-4eaf-9741-1bfed347fc6f'
PROFILE = ("[connection]\nid=Install fixture\nuuid=" + UUID +
           "\ntype=wifi\nautoconnect=false\ninterface-name=wlan0\npermissions=user:live:;\n\n[wifi]\nmode=infrastructure\nssid=Install fixture\n"
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
        self.kind = 'wifi'

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if 'UUID,TYPE,TIMESTAMP,ACTIVE,FILENAME' in argv:
            # Inactive: the link dropped after the join; the keyfile is still read.
            return 'wired:802-3-ethernet:1:yes:/run/w\n' + UUID + ':' + self.kind + ':7:no:' + str(self.profile) + '\n'
        return private_command(argv, **kwargs)

    def capture(self, note=None):
        return capture_wifi(UUID, run=self.command, directories=(self.source,), owner=os.getuid(), note=note)

    def refused(self):
        with self.assertRaises(WifiNotCopied) as refusal:
            self.capture()
        self.assertNotIn('fixture-password-only', refusal.exception.reason)
        return refusal.exception.reason

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_selected_profile_valid_in_installed_tree_and_private(self):
        notes = []
        data = self.capture(note=notes.append)
        self.assertEqual(notes, ['captured ' + UUID + ' from ' + str(self.source) + ': security wpa-psk, '
                                 'secret saved in the keyfile; autoconnect preserved, any adapter, every user.'])
        target = self.root / 'target'
        target.mkdir()
        install_wifi(UUID, data, TargetFiles(target))
        profiles = list((target / 'etc/NetworkManager/system-connections').iterdir())
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, UUID + '.nmconnection')
        self.assertEqual(profiles[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(profiles[0].stat().st_uid, os.geteuid())
        self.assertEqual(profiles[0].stat().st_gid, os.getegid())
        self.assertIn(b'autoconnect=false', data)
        self.assertEqual(profiles[0].parent.stat().st_mode & 0o777, 0o700)
        verified = private_command(['nmcli', '--offline', 'connection', 'modify',
                                    'connection.autoconnect', 'yes'], input=profiles[0].read_bytes())
        self.assertIn('psk=fixture-password-only', verified)
        self.assertNotIn('permissions=user:live:', verified)
        # Both join paths bind the live adapter's name (VM check, 2026-10-10); the copy must not.
        self.assertNotIn('interface-name', verified)
        self.assertIn('uuid=' + UUID, verified)
        self.assertNotIn('fixture-password-only', str([argv for argv, _ in self.calls]))
        self.assertEqual(self.other.read_text(), 'This unrelated profile must never be read or copied.')

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_autoconnect_is_preserved_for_every_security_method_and_default(self):
        for security in ('', 'key-mgmt=owe', 'key-mgmt=wpa-psk\npsk=fixture-password-only',
                         'key-mgmt=sae\npsk=fixture-password-only',
                         'key-mgmt=none\nwep-key0=0123456789\nwep-key-type=1'):
            for source in ('autoconnect=false\n', 'autoconnect=true\n', ''):
                with self.subTest(security=security or 'open', source=source or 'default'):
                    section = '\n[wifi-security]\n' + security + '\n' if security else ''
                    self.profile.write_text(PROFILE.replace(
                        '\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=fixture-password-only\n', section)
                        .replace('autoconnect=false\n', source))
                    notes = []
                    data = self.capture(note=notes.append).decode()
                    copied = configparser.ConfigParser(interpolation=None)
                    copied.read_string(data)
                    self.assertEqual(copied.getboolean('connection', 'autoconnect', fallback=True),
                                     source != 'autoconnect=false\n')
                    self.assertNotIn('permissions=user:live:', data)
                    self.assertNotIn('interface-name', data)
                    self.assertTrue(notes[0].startswith('captured '))
                    self.assertIn('autoconnect preserved', notes[0])

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_legacy_wifi_key_is_carried_only_when_saved_privately(self):
        data = PROFILE.replace('key-mgmt=wpa-psk\npsk=fixture-password-only',
                               'key-mgmt=none\nwep-key0=0123456789\nwep-key-type=1')
        self.profile.write_text(data)
        self.assertIn(b'wep-key0=0123456789', self.capture())
        self.profile.write_text(data.replace('wep-key-type=1', 'wep-key-type=1\nwep-key-flags=1'))
        self.assertIn('wep-key-flags 1', self.refused())

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_full_security_section_requires_a_saved_secret_after_normalization(self):
        profile = PROFILE.replace('[wifi-security]', '[802-11-wireless-security]')
        self.profile.write_text(profile)
        self.assertIn(b'psk=fixture-password-only', self.capture())
        self.profile.write_text(profile.replace('psk=fixture-password-only', 'psk-flags=1'))
        self.assertIn('psk-flags 1: held by a desktop keyring client', self.refused())

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_active_filename_listing_accepts_long_type_and_colons_in_filename(self):
        old = self.profile
        self.profile = old.with_name('selected:network.nmconnection')
        old.rename(self.profile)
        self.kind = '802-11-wireless'
        self.assertIn(b'psk=fixture-password-only', self.capture())

    def test_world_readable_foreign_or_unlisted_profile_refused_with_a_reason(self):
        self.profile.chmod(0o644)
        self.assertEqual(self.refused(), 'the keyfile is readable by others (mode 644).')
        self.profile.chmod(0o600)
        with self.assertRaises(WifiNotCopied) as refusal:
            capture_wifi(UUID, run=self.command, directories=(self.source,), owner=os.getuid() + 1)
        self.assertIn('the keyfile belongs to UID', refusal.exception.reason)
        self.kind = 'vpn'
        self.assertEqual(self.refused(), 'NetworkManager lists 0 Wi-Fi profiles with this UUID.')

    def test_keyfile_outside_the_connection_directories_or_in_memory_only_refused(self):
        with self.assertRaises(WifiNotCopied) as refusal:
            capture_wifi(UUID, run=self.command, directories=(self.root / 'elsewhere',), owner=os.getuid())
        self.assertIn('outside NetworkManager', refusal.exception.reason)
        self.profile = ''
        self.assertIn('only in memory', self.refused())

    @unittest.skipUnless(shutil.which('nmcli'), 'nmcli is required for offline keyfile validation')
    def test_secret_held_elsewhere_or_asked_each_time_is_refused_with_its_flags(self):
        for flags in ('1', '2'):
            self.profile.write_text(PROFILE.replace('psk=fixture-password-only', 'psk-flags=' + flags))
            self.assertIn(f'psk-flags {flags}', self.refused())
        self.profile.write_text(PROFILE.replace('psk=fixture-password-only\n', ''))
        self.assertEqual(self.refused(), 'the keyfile has no password.')

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
            self.refused()

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
            with self.assertRaises(WifiNotCopied) as error:
                private_command(['nmcli'])
        self.assertNotIn('private-', str(error.exception))
        self.assertEqual(error.exception.reason, 'nmcli failed (exit 1).')


A, B, C = ('aaaaaaaa-1111-4111-8111-111111111111', 'bbbbbbbb-2222-4222-8222-222222222222',
           'cccccccc-3333-4333-8333-333333333333')


def listing(*rows):
    """rows: (uuid, timestamp, active); a wired row and an odd file name ride along."""
    lines = ['e3b567bb-0d51-3cec-92aa-a5d7fa157f8b:802-3-ethernet:1791662916:yes:/run/NetworkManager/'
             'system-connections/Wired connection 1.nmconnection']
    lines += [f'{uuid}:{"wifi" if n % 2 else "802-11-wireless"}:{stamp}:{"yes" if active else "no"}:'
              f'/etc/NetworkManager/system-connections/Cafe: {n}.nmconnection'
              for n, (uuid, stamp, active) in enumerate(rows)]
    return lambda argv: '\n'.join(lines) + '\n'


class WifiChoiceTests(unittest.TestCase):
    def choose(self, *rows, joined=None):
        return choose_wifi(joined, run=listing(*rows))

    def test_listing_keeps_only_wifi_and_colons_in_file_names(self):
        profiles = saved_wifi(run=listing((A, 5, True), (B, 0, False)))
        self.assertEqual([(p.uuid, p.timestamp, p.active) for p in profiles], [(A, 5, True), (B, 0, False)])
        self.assertEqual(profiles[1].filename, '/etc/NetworkManager/system-connections/Cafe: 1.nmconnection')

    def test_one_active_connection_wins_wherever_it_was_joined(self):
        self.assertEqual(self.choose((A, 5, False), (B, 9, True), joined=A).uuid, B)
        self.assertEqual(self.choose((B, 9, True)).reason, 'the active Wi-Fi connection')

    def test_several_active_need_the_network_page_join(self):
        self.assertEqual(self.choose((A, 5, True), (B, 9, True), joined=A).uuid, A)
        choice = self.choose((A, 5, True), (B, 9, True), joined=C)
        self.assertIsNone(choice.uuid)
        self.assertEqual(choice.saved, 2)

    def test_dropped_link_keeps_the_network_page_join_or_the_only_saved_profile(self):
        choice = self.choose((A, 5, False), (B, 9, False), joined=B)
        self.assertEqual((choice.uuid, choice.reason), (B, 'joined on the Network page; no Wi-Fi is active now'))
        self.assertEqual(self.choose((A, 0, False), (B, 9, False), joined=A).uuid, A)
        self.assertEqual(self.choose((B, 9, False)).uuid, B)

    def test_missing_network_page_join_is_refused_not_replaced(self):
        # Review RW1: recorded join A is gone and an old inactive B remains.
        choice = self.choose((B, 7, False), joined=A)
        self.assertEqual((choice.uuid, choice.saved), (None, 1))
        self.assertEqual(choice.reason, f'the Network page join {A} is no longer saved and no Wi-Fi is active now')
        self.assertIsNone(self.choose(joined=A).uuid)

    def test_several_saved_without_a_join_choose_none(self):
        # Review RW1: old A connected, the newer attempted join B never did; neither is a safe guess.
        for rows in (((A, 7, False), (B, 0, False)), ((A, 5, False), (B, 9, False))):
            with self.subTest(rows=rows):
                choice = self.choose(*rows)
                self.assertEqual((choice.uuid, choice.saved), (None, 2))
                self.assertEqual(choice.reason, '2 saved Wi-Fi profiles, none is active and the Network '
                                                'page joined none of them')

    def test_nothing_chosen_explains_why(self):
        self.assertEqual(self.choose((A, 0, False)).reason, 'the only saved Wi-Fi profile has never connected')
        self.assertEqual(self.choose(), choose_wifi(None, run=listing()))
        self.assertEqual((self.choose().saved, self.choose().reason), (0, 'the live session has no Wi-Fi profile'))
