#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the update menu with literal snapshot fixtures and persistent states."""

import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_menu', ROOT / 'grub/emaki_boot/update_menu.py')
menu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(menu)
TOKEN = 'a' * 32
UUID = '01234567-89ab-cdef-0123-456789abcdef'
PREFIX = '/@snapshots/42/snapshot/boot/'
BODY = ('linux "' + PREFIX + 'vmlinuz-linux" root=UUID=' + UUID
        + ' quiet cryptdevice=UUID=' + UUID + ':emaki-root resume=UUID=' + UUID
        + ' resume_offset=123 rootflags=subvol="@snapshots/42/snapshot",rw\n'
        + 'initrd "' + PREFIX + 'intel-ucode.img" \\\n  "' + PREFIX + 'initramfs-linux.img"\n')


class UpdateMenu(unittest.TestCase):
    def config(self):
        return menu.render(BODY, TOKEN, 42, '2026-10-07', UUID)

    def execute(self, config, attempt='0', token=TOKEN, suppress='', next_entry='',
                action='normal', save_status=0, files=True, load_status=0, missing=None, search_status=0, manual=False, timeout='5'):
        """Run generated command flow with disk I/O and GRUB builtins substituted.

        This checks the state decisions; the real parser check below separately
        checks GRUB syntax. Errors accumulate until the menu would prompt; an
        explicit boot exits before that path, as grub-core/normal/menu.c does.
        Manual keyboard input clears fallback in that source's run_menu. Neither
        substitutes for firmware and power-cut checks.
        """
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            if files:
                for name in ('vmlinuz-linux', 'intel-ucode.img', 'initramfs-linux.img'):
                    if name == missing:
                        continue
                    path = root / (PREFIX + name).lstrip('/')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
            wrapped = menu.wrap_menu('menuentry Emaki {\n linux /@/boot/vmlinuz-linux root=UUID='
                                     + UUID + '\n echo Loading initramfs\n initrd /@/boot/initramfs-linux.img\n}\n', 'E0A1-0001')
            unprotected = wrapped.split(menu.BEGIN)[1].split('set emaki_update_ready=\nset emaki_update_esp=')[0]
            source = (unprotected + config).replace('($emaki_update_esp)', str(root)).replace('($root)', str(root))
            source = re.sub(r'(?m)^(\s*)set (\w+)=(.*)$', r'\1\2=\3', source)
            source = source.replace("menuentry 'Emaki recovery' --id emaki-auto-recovery {", 'recovery() {')
            setup = f'''default=normal
errors=0
timeout={shlex.quote(timeout)}
loaded=
fallback=
load_env() {{
  emaki_update={shlex.quote(token)}
  emaki_attempt={shlex.quote(attempt)}
  emaki_suppress={shlex.quote(suppress)}
  next_entry={shlex.quote(next_entry)}
  return {load_status}
}}
save_env() {{
  echo "saved:$emaki_attempt:$next_entry:$emaki_suppress"
  if [ {save_status} != 0 ]; then errors=$((errors + 1)); fi
  return {save_status}
}}
search() {{ return {search_status}; }}
linux() {{
  echo linux
  echo "arguments:$*"
  loaded=1
}}
initrd() {{ echo initrd; }}
boot() {{ echo explicit-boot; exit 0; }}
'''
            execute = (menu.ATTEMPT.replace('set ', '') + '\necho normal-linux' if action == 'normal'
                       else 'recovery' if action == 'recovery' else ':')
            result = subprocess.run(['bash', '-c', setup + source + ('\nfallback=\n' if manual else '\n') + execute
                                     + '\nif [ $errors != 0 ]; then echo interactive-prompt; fi'
                                     + '\necho "fallback:$fallback"'
                                     + '\necho "state:$emaki_update_ready:$emaki_attempt:$next_entry:$emaki_suppress:$default"'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.splitlines()

    def assert_contract(self, config):
        kernel = re.search(r'(?m)^\s*linux (.+)$', config)[1].split()
        self.assertEqual(kernel.count('noresume'), 1)
        normal = self.execute(config)
        self.assertIn('saved:1:emaki-auto-recovery:', normal)
        self.assertIn('normal-linux', normal)
        self.assertLess(normal.index('saved:1:emaki-auto-recovery:'), normal.index('normal-linux'))
        returned = self.execute(config, attempt='1', next_entry='emaki-auto-recovery', action='recovery')
        self.assertIn('state:1:2:::emaki-auto-recovery', returned)
        self.assertIn('saved:2::', returned)
        self.assertIn('linux', returned)
        self.assertLess(returned.index('saved:2::'), returned.index('linux'))
        self.assertIn('initrd', returned)
        self.assertIn('fallback:0', returned)
        self.assertIn('emaki.auto_return=' + TOKEN, '\n'.join(returned))
        for attempt in ('0', '2', 'broken'):
            state = self.execute(config, attempt=attempt, next_entry='emaki-auto-recovery', action='none')[-1]
            self.assertTrue(state.endswith(':normal'), state)
        for overrides in ({'token': 'b' * 32}, {'load_status': 1}):
            state = self.execute(config, attempt='1', next_entry='emaki-auto-recovery', action='none', **overrides)[-1]
            self.assertTrue(state.endswith(':normal'), state)
        no_selection = self.execute(config, attempt='1', next_entry='', action='none')[-1]
        self.assertTrue(no_selection.endswith(':normal'), no_selection)
        sleeping = self.execute(config, attempt='1', suppress='1', next_entry='emaki-auto-recovery')
        self.assertIn('saved:1:emaki-auto-recovery:', sleeping)
        self.assertIn('state:1:1:emaki-auto-recovery::normal', sleeping)
        # A failed resume may hang before userspace restores the saved state.
        # Persist the attempt first; only a completed resume may return it.
        first_resume = self.execute(config, attempt='0', suppress='1')
        self.assertIn('saved:1:emaki-auto-recovery:', first_resume)
        self.assertIn('state:1:1:emaki-auto-recovery::normal', first_resume)
        self.assertLess(first_resume.index('saved:1:emaki-auto-recovery:'), first_resume.index('normal-linux'))
        failed_arm = self.execute(config, save_status=1)
        self.assertIn('explicit-boot', failed_arm)
        self.assertNotIn('interactive-prompt', failed_arm)
        self.assertIn('emaki.update_unprotected=1', '\n'.join(failed_arm))
        failed_return = self.execute(config, attempt='1', next_entry='emaki-auto-recovery',
                                     action='recovery', save_status=1)
        self.assertIn('explicit-boot', failed_return)
        self.assertNotIn('interactive-prompt', failed_return)
        self.assertIn('emaki.update_unprotected=1', '\n'.join(failed_return))
        self.assertNotIn('emaki.auto_return=', '\n'.join(failed_return))
        for options in ({'files': False}, {'missing': 'vmlinuz-linux'},
                        {'missing': 'intel-ucode.img'}, {'missing': 'initramfs-linux.img'},
                        {'search_status': 1}):
            failure = self.execute(config, attempt='1', next_entry='emaki-auto-recovery', action='recovery', **options)
            self.assertNotIn('linux', failure)
            self.assertNotIn('initrd', failure)

    def test_state_machine_and_power_loss_boundaries(self):
        self.assert_contract(self.config())

    def test_mutants_at_state_decisions_are_rejected(self):
        config = self.config()
        mutations = [
            ('"$emaki_update" = "' + TOKEN + '"', '"1" = "1"'),
            ('"$emaki_attempt" = "0" ]; then', '"$emaki_attempt" = "2" ]; then'),
            ('"$emaki_attempt" = "1" -a', '"$emaki_attempt" = "0" -a'),
            ('"$next_entry" = "emaki-auto-recovery"', '"1" = "1"'),
            ('"$emaki_suppress" != "1"', '"$emaki_suppress" = "1"'),
            ('set emaki_attempt=2', 'set emaki_attempt=1'),
            ('          set fallback=0\n', ''),
            ('if save_env -f', 'if true; then true; elif save_env -f'),
            ('if [ -f ($root)' + PREFIX + 'vmlinuz-linux -a -f ($root)' + PREFIX
             + 'intel-ucode.img -a -f ($root)' + PREFIX + 'initramfs-linux.img ]', 'if [ -n "yes" ]'),
            ('-f ($root)' + PREFIX + 'vmlinuz-linux', '-n "yes"'),
            ('-f ($root)' + PREFIX + 'intel-ucode.img', '-n "yes"'),
            ('-f ($root)' + PREFIX + 'initramfs-linux.img', '-n "yes"'),
            ('if search --no-floppy', 'if ! search --no-floppy'),
            (' noresume ', ' '),
            ('function emaki_update_attempt {\n',
             'function emaki_update_attempt {\n'
             '  if [ "$emaki_suppress" = "1" ]; then\n'
             '    set emaki_suppress=\n'
             '    save_env -f ($emaki_update_esp)/EFI/Emaki/update.env emaki_suppress\n'
             '    return\n'
             '  fi\n'),
        ]
        for before, after in mutations:
            with self.subTest(before=before):
                self.assertIn(before, config)
                with self.assertRaises(AssertionError):
                    self.assert_contract(config.replace(before, after))

    def test_real_parser(self):
        checker = (os.environ.get('EMAKI_TEST_GRUB_SCRIPT_CHECK') or shutil.which('grub-script-check')
                   or '/tmp/emaki-grub-tools/usr/bin/grub-script-check')
        if not Path(checker).is_file():
            self.skipTest('Real grub-script-check is unavailable')
        normal = ('menuentry Emaki {\n linux /@/boot/vmlinuz-linux root=UUID=' + UUID
                  + '\n initrd /@/boot/initramfs-linux.img\n}\n')
        for text in (self.config(), menu.wrap_menu(normal, 'E0A1-0001')):
            result = subprocess.run([checker], input=text, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrap_is_idempotent_and_arms_only_normal_linux_after_trial(self):
        text = ('set default=0\nmenuentry Emaki {\n if chainloader /loader.efi; then\n boot\n fi\n'
                ' linux /@/boot/vmlinuz-linux root=UUID=' + UUID
                + '\n initrd /@/boot/initramfs-linux.img\n}\n'
                'menuentry Snapshot {\n' + BODY + '}\n')
        wrapped = menu.wrap_menu(text, 'E0A1-0001')
        self.assertEqual(wrapped, menu.wrap_menu(wrapped, 'E0A1-0001'))
        legacy = wrapped.replace(menu.ATTEMPT, menu.LEGACY_ATTEMPT)
        self.assertEqual(wrapped, menu.wrap_menu(legacy, 'E0A1-0001'))
        self.assertEqual(wrapped.count('    emaki_update_attempt'), 1)
        self.assertLess(wrapped.index('chainloader'), wrapped.index('    emaki_update_attempt'))
        self.assertLess(wrapped.index('    emaki_update_attempt'), wrapped.index('linux /@/boot/'))
        self.assertTrue(wrapped.endswith(menu.END))
        self.assertIn(BODY, wrapped)

    def test_snapshot_identity_not_shared_entry_id_or_position(self):
        fixture = ('submenu Snapshots {\n'
                   "    menuentry duplicate --id gnulinux-snapshots-shared {\n"
                   + BODY.replace('/42/', '/41/') + '    }\n'
                   "    menuentry duplicate --id gnulinux-snapshots-shared {\n" + BODY + '    }\n}\n')
        body = menu.extract_snapshot_body(fixture, 42, UUID)
        self.assertIn('/42/', body)
        self.assertNotIn('/41/', body)
        self.assertIn('intel-ucode.img', body)
        config = menu.render(body, TOKEN, 42, '2026-10-07', UUID)
        self.assertNotIn('resume=', config)
        self.assertNotIn('resume_offset=', config)
        self.assertEqual(config.split().count('noresume'), 1)
        self.assertNotIn('chainloader', config)
        self.assertNotIn('cryptomount', config)
        self.assertIn('cryptdevice=UUID=' + UUID + ':emaki-root', config)
        self.assertIn('emaki.auto_return=' + TOKEN, config)

    def test_manual_recovery_has_no_automatic_return_claim(self):
        for attempt, next_entry, suppress in (('0', '', ''), ('1', 'emaki-auto-recovery', ''),
                                              ('1', 'emaki-auto-recovery', '1'), ('2', '', '')):
            lines = self.execute(self.config(), attempt=attempt, next_entry=next_entry,
                                 suppress=suppress, action='recovery', manual=True)
            self.assertIn('linux', lines)
            self.assertNotIn('emaki.auto_return=', '\n'.join(lines))
            self.assertIn('emaki.snapshot_date=2026-10-07', '\n'.join(lines))

    def test_untimed_menu_cannot_claim_automatic_return(self):
        for timeout in ('', '-1'):
            lines = self.execute(self.config(), attempt='1', next_entry='emaki-auto-recovery',
                                 action='recovery', timeout=timeout)
            self.assertIn('linux', lines)
            self.assertNotIn('emaki.auto_return=', '\n'.join(lines))

    def test_default_initramfs_selected_even_when_fallback_entry_is_first(self):
        fallback = BODY.replace('initramfs-linux.img', 'initramfs-linux-fallback.img')
        fixture = ('menuentry Fallback {\n' + fallback + '}\n'
                   + 'menuentry Normal {\n' + BODY + '}\n')
        self.assertNotIn('-fallback.img', menu.extract_snapshot_body(fixture, 42, UUID))
        with self.assertRaises(ValueError):
            menu.extract_snapshot_body('menuentry Fallback {\n' + fallback + '}\n', 42, UUID)

    def test_snapshot_kernel_and_initramfs_pairs_and_existing_noresume(self):
        for kernel in ('linux', 'linux-lts'):
            for suffix in ('', '-fallback'):
                body = BODY.replace('vmlinuz-linux', 'vmlinuz-' + kernel).replace(
                    'initramfs-linux.img', 'initramfs-' + kernel + suffix + '.img')
                body = body.replace(' quiet ', ' quiet noresume ')
                config = menu.render(body, TOKEN, 42, '2026-10-07', UUID)
                self.assertEqual(config.split().count('noresume'), 1)
                other = 'linux-lts' if kernel == 'linux' else 'linux'
                with self.assertRaises(ValueError):
                    menu.render(body.replace('initramfs-' + kernel + suffix + '.img',
                                             'initramfs-' + other + suffix + '.img'),
                                TOKEN, 42, '2026-10-07', UUID)

    def test_real_upstream_entry_renderer(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            for name in ('vmlinuz-linux', 'initramfs-linux.img', 'initramfs-linux-fallback.img', 'intel-ucode.img'):
                (root / name).touch()
            script = '''source "$1"
grub_btrfs_directory=$2
boot_dir=$2
boot_uuid=$3
LINUX_ROOT_DEVICE=UUID=$3
name_kernel=(vmlinuz-linux)
name_initramfs=(initramfs-linux-fallback.img initramfs-linux.img)
name_microcode=(intel-ucode.img)
insmods=('insmod btrfs')
rootflags=rootflags=
snap_date='2026-10-07 12:00:00'
snap_type=pre
snap_description='pacman -Syu'
count_warning_menuentries=0
for snap_snapshot in 41 42; do
  snap_date_trim=$snap_date
  snap_dir_name_trim=@snapshots/$snap_snapshot/snapshot
  boot_dir_root_grub=/$snap_dir_name_trim/boot
  title_format
  make_menu_entries
done
'''
            result = subprocess.run(['bash', '-c', script, 'fixture',
                                     str(ROOT / 'grub/tests/grub-btrfs-4.14.fixture'), work, UUID],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = menu.extract_snapshot_body((root / 'grub-btrfs.new').read_text(), 42, UUID)
            self.assertIn('/42/snapshot/boot/intel-ucode.img', body)
            self.assertNotIn('/41/', body)
            self.assertNotIn('-fallback.img', body)
            self.assertIn('/42/snapshot/boot/initramfs-linux.img', body)
            menu.render(body, TOKEN, 42, '2026-10-07', UUID)

    def test_invalid_or_deleted_snapshot_and_injection_refused(self):
        changes = [('/42/', '/43/'), ('root=UUID=' + UUID, 'root=UUID=' + 'f' * 36),
                   ('quiet', 'quiet; reboot'), ('quiet', '$(reboot)'),
                   ('subvol="@snapshots/42/snapshot",rw', 'subvolid=99'),
                   ('initramfs-linux.img', '../initramfs-linux.img'),
                   ('initramfs-linux.img', 'initramfs-linux-lts.img'),
                   ('initrd ', 'echo ')]
        for old, new in changes:
            with self.subTest(old=old):
                with self.assertRaises(ValueError):
                    menu.render(BODY.replace(old, new), TOKEN, 42, '2026-10-07', UUID)
        with self.assertRaises(ValueError):
            menu.extract_snapshot_body('', 42, UUID)
        with self.assertRaises(ValueError):
            menu.render(BODY, TOKEN, 42, '2026-02-30', UUID)


if __name__ == '__main__':
    unittest.main()
