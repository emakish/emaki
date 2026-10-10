import copy
import unittest
from unittest.mock import patch

from emaki_installer.constants import BTRFS_OPTIONS, GIB, MIB, SUBVOLUMES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import RESERVED_LOGINS, erase_partitions, fingerprint, make_plan, shrink_bounds, validate_config
from support import alongside_on, config, inventory, manual


class PlannerTests(unittest.TestCase):
    def rejects(self, c, inv, code):
        with self.assertRaises(InstallError) as raised:
            make_plan(c, inv)
        self.assertEqual(raised.exception.code, code)

    def test_session_output_scales_are_validated(self):
        c = config()
        c['output_scales'] = {'eDP-1': 1.25, 'HDMI-A-1': 1.5, 'DP-1': 1}
        self.assertEqual(validate_config(c)['output_scales'], c['output_scales'])
        for scales in ([], {'eDP-1': True}, {'eDP-1': float('nan')}, {'eDP-1': 0},
                       {'eDP-1': 4.1}, {'bad\nname': 1.25}, {'': 1}, {'x' * 257: 1}):
            with self.subTest(scales=scales), self.assertRaises(InstallError):
                validate_config(dict(c, output_scales=scales))

    def test_encrypted_confirmation_matches_path_type_and_uuid(self):
        from emaki_installer.inventory import encrypted_warning, unconfirmed_identity_warning
        for kind in ('crypto_LUKS', 'BitLocker', 'cs_fvault2', 'apfs'):
            for mode in ('erase', 'manual'):
                with self.subTest(kind=kind, mode=mode):
                    inv = inventory(partitions=True)
                    disk = inv['disks'][0]
                    volume = {'path': '/dev/vda2', 'type': kind, 'uuid': 'locked-uuid',
                              'warning': encrypted_warning('/dev/vda2', kind)}
                    disk['partitions'][1]['fs'] = kind
                    disk['closed_encrypted'] = [volume]
                    c = config() if mode == 'erase' else manual()
                    self.rejects(c, inv, Code.ENCRYPTED_CONFIRMATION)
                    identity = {k: volume[k] for k in ('path', 'type', 'uuid')}
                    for key in ('path', 'type', 'uuid'):
                        stale = dict(identity, **{key: 'BitLocker' if key == 'type' and kind != 'BitLocker' else
                                                 'crypto_LUKS' if key == 'type' else 'stale'})
                        self.rejects(dict(c, confirmed_encrypted=[stale]), inv, Code.ENCRYPTED_CONFIRMATION)
                    plan = make_plan(dict(c, confirmed_encrypted=[identity]), inv)
                    self.assertEqual(plan.config['confirmed_encrypted'], [identity])
                    volume['uuid'] = None
                    for confirmations in ([], [identity]):
                        with self.assertRaises(InstallError) as raised:
                            make_plan(dict(c, confirmed_encrypted=confirmations), inv)
                        self.assertEqual(raised.exception.code, Code.ENCRYPTED_CONFIRMATION)
                        self.assertEqual(raised.exception.message, unconfirmed_identity_warning(volume['path']))
                        self.assertNotIn('Type ERASE', raised.exception.message)

    def test_manual_preserves_unassigned_locked_volume_without_confirmation(self):
        for uuid in ('locked', None):
            with self.subTest(uuid=uuid):
                inv = inventory(partitions=True)
                disk = inv['disks'][0]
                disk['closed_encrypted'] = [{'path': '/dev/vda3', 'type': 'crypto_LUKS', 'uuid': uuid, 'warning': 'locked'}]
                plan = make_plan(manual(), inv)
                self.assertNotIn('/dev/vda3', [part.path for part in plan.partitions])
                self.assertEqual(plan.config['confirmed_encrypted'], [])

    def test_alongside_preserves_unrelated_locked_volume(self):
        from test_alongside import windows
        c, inv = windows()
        disk = inv['disks'][0]
        extra = dict(disk['partitions'][1], id='/dev/vda3', path='/dev/vda3', number=3,
                     start_bytes=98 * GIB, size_bytes=GIB, fs='crypto_LUKS')
        disk['partitions'].append(extra)
        disk['closed_encrypted'] = [{'path': '/dev/vda3', 'type': 'crypto_LUKS', 'uuid': 'locked', 'warning': 'locked'}]
        with alongside_on():
            plan = make_plan(c, inv)
        self.assertNotIn('/dev/vda3', [p.path for p in plan.partitions])
        self.assertEqual(plan.config['confirmed_encrypted'], [])

    def test_closed_encrypted_identity_changes_fingerprint(self):
        inv = inventory()
        disk = inv['disks'][0]
        disk['closed_encrypted'] = [{'path': '/dev/vda', 'type': 'crypto_LUKS', 'uuid': 'first', 'warning': 'locked'}]
        before = fingerprint(disk)
        disk['closed_encrypted'][0]['uuid'] = 'second'
        self.assertNotEqual(before, fingerprint(disk))

    def test_review_names_the_final_update_choice(self):
        for online_update, expected in (
            (True, 'Update Emaki at the end: when connected.'),
            (False, 'Update Emaki at the end: off.'),
        ):
            with self.subTest(online_update=online_update):
                summary = make_plan(dict(config(), online_update=online_update), inventory()).summary
                self.assertEqual([line for line in summary if line.startswith('Update Emaki at the end:')],
                                 [expected])

    def test_review_names_the_keyboard_layouts(self):
        rules = ('! model\n  pc105 Generic\n! layout\n  us              English (US)\n  cz              Czech\n'
                 '  ru              Russian\n! variant\n  dvorak          us: English (Dvorak)\n'
                 '  dvorak          gb: English (UK, Dvorak)\n! option\n  grp Switching\n')

        def line(layouts, text=rules):
            with patch('emaki_installer.planner.xkb_rules', return_value=text):
                found = [x for x in make_plan(dict(config(), layouts=layouts), inventory()).summary
                         if x.startswith('Keyboard:')]
            self.assertEqual(len(found), 1)
            return found[0]

        self.assertEqual(line(['cz', 'us']), 'Keyboard: Czech (default), English (US). Super+Space switches.')
        self.assertEqual(line(['us', 'ru', 'cz']),
                         'Keyboard: English (US) (default), Russian, Czech. Super+Space switches.')
        # One layout: nothing to switch, nothing to call the default.
        self.assertEqual(line(['ru']), 'Keyboard: Russian.')
        self.assertEqual(line(['dvorak', 'ru']), 'Keyboard: English (Dvorak) (default), Russian. Super+Space switches.')
        # Without the rules file the codes are still shown.
        self.assertEqual(line(['cz', 'us'], ''), 'Keyboard: cz (default), us. Super+Space switches.')

    def test_latin_first_layout_rule_is_a_switch(self):
        # Off: any first layout. On: a first layout outside the Latin list is refused.
        for layouts in (['ru', 'us'], ['ua'], ['cz', 'ru'], ['dvorak', 'ru']):
            make_plan(dict(config(), layouts=layouts), inventory())
        with patch('emaki_installer.planner.REQUIRE_LATIN_FIRST', True):
            for layouts in (['cz', 'ru'], ['fr'], ['dvorak', 'ru'], ['us', 'ua']):
                make_plan(dict(config(), layouts=layouts), inventory())
            for layouts in (['ru', 'us'], ['ua'], ['gr', 'cz']):
                self.rejects(dict(config(), layouts=layouts), inventory(), Code.BAD_CONFIG)

    def test_erase_alignment_and_backup_gpt(self):
        for size in (24 * GIB, 256 * GIB, 2 * 1024 * GIB, 24 * GIB + 512):
            for fs in ('btrfs', 'ext4'):
                with self.subTest(size=size, fs=fs):
                    esp, root = erase_partitions(size, fs)
                    self.assertEqual((esp.start, esp.size, esp.mountpoint), (MIB, GIB, '/efi'))
                    self.assertEqual(esp.start + esp.size, root.start)
                    self.assertLessEqual(root.start + root.size, size - MIB)
                    self.assertLess(size - root.start - root.size, 2 * MIB)
                    for p in (esp, root):
                        self.assertEqual(p.start % MIB, 0)
                        self.assertEqual(p.size % MIB, 0)
                    self.assertEqual(root.subvolumes, SUBVOLUMES if fs == 'btrfs' else {})

    def test_erase_layout_keeps_boot_and_pacman_in_root(self):
        p = make_plan(config(), inventory())
        self.assertEqual(p.root.options, BTRFS_OPTIONS)
        self.assertEqual({m for _, m, _ in p.mounts}, {'/', '/efi', '/home', '/var/log', '/var/cache/pacman/pkg', '/.snapshots'})
        self.assertNotIn('a-secret-for-tests', repr(p))

    def test_disk_refusals(self):
        cases = [('uefi', False, Code.UEFI_REQUIRED), ('_boot_medium_known', False, Code.UNSAFE_DISK)]
        for key, value, code in cases:
            inv = inventory()
            inv[key] = value
            self.rejects(config(), inv, code)
        for key, value, code in [('is_boot_medium', True, Code.BOOT_MEDIUM),
                                 ('_busy', True, Code.DISK_BUSY), ('_read_only', True, Code.UNSAFE_DISK),
                                 ('size_bytes', 24 * GIB - 1, Code.DISK_TOO_SMALL)]:
            inv = inventory()
            inv['disks'][0][key] = value
            self.rejects(config(), inv, code)

    def test_refused_disk_reports_its_own_reason(self):
        inv = inventory()
        inv['disks'][0].update(_busy=True, reason='/dev/vda is one of 2 devices of a RAID array.')
        with self.assertRaises(InstallError) as raised:
            make_plan(config(), inv)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         (Code.DISK_BUSY, '/dev/vda is one of 2 devices of a RAID array.'))

    def test_whole_disk_signature_changes_the_fingerprint(self):
        disk = inventory()['disks'][0]
        self.assertNotEqual(fingerprint(dict(disk, _fs=None)), fingerprint(dict(disk, _fs='LVM2_member')))

    def test_manual_format_flags_and_implicit_subvolumes(self):
        p = make_plan(manual(), inventory(partitions=True))
        by_path = {r.path: r for r in p.partitions}
        self.assertFalse(by_path['/dev/vda1'].format)
        self.assertTrue(by_path['/dev/vda2'].format)
        self.assertEqual(p.root.subvolumes, SUBVOLUMES)
        self.assertEqual(p.partitions[0], p.root)

    def test_manual_preserve_root_never_becomes_format(self):
        p = make_plan(manual(format=False), inventory(partitions=True))
        self.assertFalse(p.root.format)

    def test_manual_missing_or_duplicate_root_esp(self):
        for change in ('missing_root', 'missing_esp', 'duplicate_root'):
            c = manual()
            if change == 'missing_root':
                c['mounts'][1]['mountpoint'] = '/home'
            elif change == 'missing_esp':
                c['mounts'][0]['mountpoint'] = '/home'
            else:
                c['mounts'].append(copy.deepcopy(c['mounts'][1]))
            self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_manual_esp_free_not_total_size(self):
        for free in (None, 32 * MIB - 1):
            inv = inventory(partitions=True)
            inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = free
            self.rejects(manual(), inv, Code.ESP_SPACE)
        inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = 32 * MIB
        make_plan(manual(), inv)

    def test_manual_reject_type_change_without_format(self):
        self.rejects(manual('ext4', False), inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_manual_reject_unknown_geometry_and_readonly(self):
        for key, value in [('_geometry_known', False), ('_read_only', True)]:
            inv = inventory(partitions=True)
            inv['disks'][0]['partitions'][1][key] = value
            self.rejects(manual(), inv, Code.MANUAL_LAYOUT)

    def test_manual_reject_unsupported_mounts_and_injection(self):
        for mp in ('/boot', '/boot/efi', '/usr', '/var', '/var/lib/pacman', '/../etc', '/home/../../etc', '/home//x'):
            c = manual()
            c['mounts'][1]['mountpoint'] = mp
            self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_home_cannot_be_fat(self):
        inv = inventory(partitions=True)
        parts = inv['disks'][0]['partitions']
        parts[1]['size_bytes'] -= 5 * GIB
        parts.append(dict(parts[1], id='/dev/vda3', path='/dev/vda3', number=3, uuid='uuid-3', fs='ext4',
                          start_bytes=parts[1]['start_bytes'] + parts[1]['size_bytes'], size_bytes=5 * GIB,
                          _partuuid='partuuid-3'))

        def plan(mountpoint, fs):
            c = manual('ext4')
            c['mounts'].append({'partition_id': '/dev/vda3', 'mountpoint': mountpoint, 'fs': fs, 'format': True})
            return make_plan(c, inv)

        for mountpoint in ('/home', '/home/shared'):
            with self.assertRaises(InstallError) as raised:
                plan(mountpoint, 'vfat')
            self.assertEqual(raised.exception.code, Code.MANUAL_LAYOUT)
            self.assertIn('FAT', raised.exception.message)
            self.assertEqual(plan(mountpoint, 'ext4').partitions[-1].fs, 'ext4')
        for mountpoint in ('/mnt/data', '/homework'):
            self.assertEqual(plan(mountpoint, 'vfat').partitions[-1].fs, 'vfat')

    def test_explicit_root_subvolumes(self):
        c = manual()
        c['mounts'] = c['mounts'][:1] + [
            {'partition_id': '/dev/vda2', 'mountpoint': mp, 'fs': 'btrfs', 'format': False, 'subvolume': 'custom' + name}
            for name, mp in SUBVOLUMES.items()]
        p = make_plan(c, inventory(partitions=True))
        self.assertEqual(set(p.root.subvolumes), {'custom' + name for name in SUBVOLUMES})
        c['mounts'][-1]['format'] = True
        self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_root_and_data_mount_order(self):
        p = make_plan(manual(), inventory(partitions=True))
        self.assertEqual(p.mounts[0][1], '/')
        self.assertLess([m for _, m, _ in p.mounts].index('/'), [m for _, m, _ in p.mounts].index('/var/log'))

    def test_multidevice_btrfs_preservation_refused(self):
        inv = inventory(partitions=True)
        inv['disks'][0]['partitions'][1]['_btrfs_devices'] = 2
        self.rejects(manual(format=False), inv, Code.MANUAL_LAYOUT)

    def test_alongside_bounds_and_unsuitable_disk(self):
        shrink_bounds(42 * GIB, 40 * GIB, 80 * GIB)
        for new in (40 * GIB, 41 * GIB, 48 * GIB + 1, 80 * GIB, True):
            with self.assertRaises(InstallError):
                shrink_bounds(new, 40 * GIB, 80 * GIB)
        c = config('alongside')
        c['partition_id'] = '/dev/vda2'
        c['shrink_bytes'] = 41 * GIB
        self.rejects(c, inventory(), Code.UNSUPPORTED_MODE)
        with alongside_on():
            self.rejects(c, inventory(), Code.SHRINK_BOUNDS)

    def test_logins_of_system_users_and_groups_are_refused(self):
        for login in ('mail', 'games', 'ftp', 'http', 'bin', 'daemon', 'wheel', 'audio', 'video',
                      'root', 'greeter', 'live', 'nobody'):
            with self.subTest(login=login):
                c = config()
                c['user']['login'] = login
                self.rejects(c, inventory(), Code.BAD_CONFIG)
        self.assertEqual(make_plan(config(), inventory()).config['user']['login'], 'vmuser')
        self.assertEqual(list(RESERVED_LOGINS), sorted(set(RESERVED_LOGINS)))

    def test_config_rejects_command_and_password_line_injection(self):
        for key, value in [('hostname', 'host\nBAD=1'), ('timezone', '../../etc'),
                           ('layouts', ['us"}']), ('online_update', 'false'), ('scale_guess', float('nan')),
                           ('repo_server', 'http://user:secret@example.com/repo'),
                           ('repo_server', 'https://example.com\nSigLevel=Never')]:
            c = config()
            c[key] = value
            with self.assertRaises(InstallError):
                validate_config(c)
        c = config()
        c['user']['password'] = 'secret\nroot:injected'
        with self.assertRaises(InstallError):
            validate_config(c)

    @staticmethod
    def encrypted(encryption, password, layouts):
        c = config()
        c['layouts'] = layouts
        if encryption == 'account':
            c['encryption'] = 'account'
            c['user']['password'] = password
        else:
            c.update(encryption='separate', disk_password=password)
        return c

    def test_disk_password_must_type_on_the_startup_prompts_layout(self):
        # GRUB asks with US key positions whatever the chosen layouts are (keyboard contract K5:
        # unlock_layout(layouts, 'grub') is 'us'), so the password is printable ASCII.
        good = ('Zebra-Yacht1', ' ~!@#$%^&*()_+`-=[]{}|;:",./<>?' + "'", 'x' * 1024)
        # Control characters are refused before this rule (test_passwords_refuse_control_characters).
        bad = ('пароль', 'passé', 'ümlaut')
        # The account password also needs English (US) first (the test below).
        for encryption, layout_lists in (('account', (['us'], ['us', 'ru'])),
                                         ('separate', (['us'], ['ru', 'us'], ['de'], ['dvorak', 'ua']))):
            for layouts in layout_lists:
                for password in good:
                    validate_config(self.encrypted(encryption, password, layouts))
                for password in bad:
                    with self.subTest(encryption=encryption, layouts=layouts, password=password):
                        with self.assertRaises(InstallError) as raised:
                            validate_config(self.encrypted(encryption, password, layouts))
                        self.assertEqual(raised.exception.code, Code.BAD_CONFIG)
                        self.assertIn('English (US)', raised.exception.message)
                        if encryption == 'separate':
                            self.assertEqual(raised.exception.message, 'The startup password must use '
                                             'characters available on an English (US) keyboard.')
        # The rule follows the contract's answer for the GRUB prompt, not a constant of its own.
        with patch('emaki_installer.planner.unlock_layout', return_value='de') as asked:
            with self.assertRaises(InstallError):
                validate_config(self.encrypted('separate', good[0], ['de', 'us']))
        asked.assert_called_with(['de', 'us'], 'grub')
    def test_passwords_refuse_control_characters(self):
        # No layout types a tab, an escape or DEL into the login screen's password field (Tab
        # moves the focus there): a password holding one could only have been pasted.
        controls = [chr(x) for x in range(0, 0x20)] + ['\x7f']
        for char in controls:
            with self.subTest(char=hex(ord(char))):
                c = config()
                c['user']['password'] = 'Zebra' + char + 'Yacht'
                with self.assertRaises(InstallError) as raised:
                    validate_config(c)
                self.assertEqual(raised.exception.code, Code.BAD_CONFIG)
                c = dict(config(), encryption='separate', disk_password='Zebra' + char + 'Yacht')
                with self.assertRaises(InstallError) as raised:
                    validate_config(c)
                self.assertEqual(raised.exception.code, Code.BAD_CONFIG)

    def test_account_password_uses_printable_ascii_with_or_without_encryption(self):
        for encryption in ('none', 'account', 'separate'):
            c = config()
            c['encryption'] = encryption
            if encryption == 'separate':
                c['disk_password'] = 'Disk-secret1'
            for password in (' ', ''.join(chr(x) for x in range(32, 127)), 'x' * 1024):
                with self.subTest(encryption=encryption, length=len(password)):
                    c['user']['password'] = password
                    if encryption == 'account' and len(password) < 8:
                        with self.assertRaisesRegex(InstallError, 'at least 8 characters'):
                            validate_config(c)
                    else:
                        self.assertEqual(validate_config(c)['user']['password'], password)
            for password in ('пароль', 'secret🔑', 'passé', 'naïve x', '\x80x', 'a\tb', '', 'x' * 1025):
                with self.subTest(encryption=encryption, password=password):
                    c['user']['password'] = password
                    with self.assertRaises(InstallError) as raised:
                        validate_config(c)
                    self.assertEqual(raised.exception.code, Code.BAD_CONFIG)
                    if password and len(password) <= 1024:
                        self.assertEqual(raised.exception.message,
                                         'Use only letters, digits and symbols of the English (US) keyboard.')

    def test_one_password_for_everything_needs_the_startup_layout_first(self):
        # The account password is typed at the login screen in the first layout and at the
        # startup prompt in unlock_layout(layouts, 'grub'). The window offers the choice only
        # when the two agree (InstallerController.accountUnlocks); a plan given to the CLI too.
        for layouts in (['us'], ['us', 'ru'], ['us', 'de', 'cz']):
            validate_config(self.encrypted('account', 'Zebra-Yacht1', layouts))
        for layouts in (['de'], ['cz', 'us'], ['ru', 'us'], ['dvorak', 'ua']):
            with self.subTest(layouts=layouts):
                with self.assertRaises(InstallError) as raised:
                    validate_config(self.encrypted('account', 'Zebra-Yacht1', layouts))
                self.assertEqual(raised.exception.code, Code.BAD_CONFIG)
                self.assertEqual(raised.exception.message, 'One password for everything is not available: the login '
                                 f'screen starts in {layouts[0]}, but the disk is unlocked at startup in us.')
                # A separate disk password is the way with these layouts.
                validate_config(self.encrypted('separate', 'Zebra-Yacht1', layouts))


if __name__ == '__main__':
    unittest.main()
