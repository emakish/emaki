#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The short GRUB menu, its theme scale and the plain snapshot titles (N7G1)."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer import boot, grub_screen  # noqa: E402

loader = SourceFileLoader('snapshot_menu', str(ROOT / 'grub/emaki-snapshot-menu'))
snapshot_menu = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(snapshot_menu)

UUID = '442946be-2205-4306-89be-eb862636ab14'
GRUB_TOOLS = Path(os.environ.get('EMAKI_GRUB_TOOLS', '/'))
if GRUB_TOOLS == Path('/') and not shutil.which('grub-mkstandalone'):
    GRUB_TOOLS = Path('/tmp/emaki-grub-tools')
OVMF = Path('/usr/share/edk2/x64')


def entry(title, kind, initramfs):
    return (f"menuentry '{title}' --class emaki --class gnu-linux --class gnu --class os "
            f"$menuentry_id_option 'gnulinux-{kind}-{UUID}' {{\n"
            "\tload_video\n\tif [ x$feature_platform_search_hint = xy ]; then\n"
            f"\t  search --no-floppy --fs-uuid --set=root {UUID}\n\tfi\n"
            f"\tlinux\t/@/boot/vmlinuz-{kind.split('-advanced')[0].split('-fallback')[0]} root=UUID={UUID} rw\n"
            f"\tinitrd\t/@/boot/{initramfs}\n}}\n")


# The stock order and forms of the installed grub.cfg (VM, 2026-10-12, grub 2:2.16-1).
MENU = ('### BEGIN /etc/grub.d/00_header ###\nset default="0"\n### END /etc/grub.d/00_header ###\n\n'
        '### BEGIN /etc/grub.d/10_linux ###\n'
        + entry('Emaki, with Linux linux', 'linux-advanced', 'initramfs-linux.img')
        + entry('Emaki, with Linux linux (fallback initramfs)', 'linux-fallback', 'initramfs-linux-fallback.img')
        + entry('Emaki, with Linux linux-lts', 'linux-lts-advanced', 'initramfs-linux-lts.img')
        + entry('Emaki, with Linux linux-lts (fallback initramfs)', 'linux-lts-fallback',
                'initramfs-linux-lts-fallback.img')
        + '\n### END /etc/grub.d/10_linux ###\n\n'
        '### BEGIN /etc/grub.d/30_uefi-firmware ###\nif [ "$grub_platform" = "efi" ]; then\n'
        '\tfwsetup --is-supported\n\tif [ "$?" = 0 ]; then\n'
        "\t\tmenuentry 'UEFI Firmware Settings' $menuentry_id_option 'uefi-firmware' {\n"
        '\t\t\tfwsetup\n\t\t}\n\tfi\nfi\n### END /etc/grub.d/30_uefi-firmware ###\n\n'
        '### BEGIN /etc/grub.d/41_snapshots-btrfs ###\nif [ ! -e "${prefix}/grub-btrfs.cfg" ]; then\necho ""\n'
        "else\nsubmenu 'Restore from a snapshot' {\n    configfile \"${prefix}/grub-btrfs.cfg\"\n}\nfi\n"
        '### END /etc/grub.d/41_snapshots-btrfs ###\n')


# What grub 2:2.16-1's 41_custom writes: entries a custom.cfg adds are unknown until boot.
CUSTOM_CFG = ('### BEGIN /etc/grub.d/41_custom ###\nif [ -f  ${config_directory}/custom.cfg ]; then\n'
              '  source ${config_directory}/custom.cfg\nelif [ -z "${config_directory}" -a -f  $prefix/custom.cfg ]; then\n'
              '  source $prefix/custom.cfg\nfi\n### END /etc/grub.d/41_custom ###\n\n')


def titles(text):
    return [line.strip().split("'")[1] for line in text.splitlines()
            if line.lstrip().startswith(('menuentry ', 'submenu '))]


def menu_tree(text):
    """Every entry as GRUB sees it with all conditions true: [title, id, children]."""
    root, stack = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(('menuentry ', 'submenu ')) and stripped.endswith('{'):
            try:
                words = shlex.split(stripped[:-1].replace('$menuentry_id_option', '--id'))
            except ValueError:
                continue
            identifier = words[words.index('--id') + 1] if '--id' in words else None
            node = [words[1], identifier, []]
            (stack[-1][2] if stack else root).append(node)
            stack.append(node)
        elif stripped == '}' and stack:
            stack.pop()
    return root


def resolve(text, default):
    """The entry a default selects (GRUB: a number, an id or a title per '>' level)."""
    level, node = menu_tree(text), None
    for part in default.split('>'):
        node = next((entry for number, entry in enumerate(level)
                     if part in (str(number), entry[0], entry[1])), None)
        if node is None:
            return None
        level = node[2]
    return node[1] or node[0]


def remapped(text, default):
    """What the generated header leaves in $default after the remap commands."""
    header = text.split('### END /etc/grub.d/00_header ###')[0]
    pairs = re.findall(r'''if \[ "\$default" = '([^']*)' \]; then\n  set default='([^']*)'\n''', header)
    return dict(pairs).get(default, default)


def layout(text, old=False):
    """The part of the menu GRUB runs with the old order kept (old=True) or the short one."""
    wrappers, out, depth = [], [], 0
    for line in text.splitlines(keepends=True):
        word = line.strip()
        if word in ('if [ "$emaki_old_layout" ]; then', 'if [ -z "$emaki_old_layout" ]; then'):
            wrappers.append((depth, ('-z' not in word) == old))
            depth += 1
            continue
        if word == 'fi':
            depth -= 1
            if wrappers and wrappers[-1][0] == depth:
                wrappers.pop()
                continue
        elif word.startswith('if '):
            depth += 1
        if all(keep for _, keep in wrappers):
            out.append(line)
    return ''.join(out)


def selected(text, default):
    """The entry the rearranged menu selects: a remaining number >= the header's bound keeps the old order."""
    value = remapped(text, default)
    bound = re.search(r'if \[ "\$default" -ge ([0-9]+) \]', text)
    number = re.match(r'[0-9]+', value)
    return resolve(layout(text, bool(bound and number and int(number[0]) >= int(bound[1]))), value)


def grub_tool(name):
    packaged = GRUB_TOOLS / 'usr/bin' / name
    return str(packaged) if packaged.is_file() else shutil.which(name)


def grub_modules():
    """The x86_64-efi modules of the same GRUB as grub-mkstandalone (bin/../lib/grub)."""
    tool = grub_tool('grub-mkstandalone')
    return Path(tool).resolve().parents[1] / 'lib/grub/x86_64-efi' if tool else None


class MenuLayout(unittest.TestCase):
    def test_short_top_level_and_advanced_submenu(self):
        text = boot.menu_layout(MENU)
        self.assertEqual(titles(layout(text)), ['Emaki', 'Emaki (LTS kernel)', 'Restore from a snapshot',
                                                'Advanced options', 'Emaki (all drivers)',
                                                'Emaki (LTS kernel, all drivers)', 'UEFI Firmware Settings'])
        # An old default number GRUB cannot map keeps the old order, with the new titles.
        self.assertEqual(titles(layout(text, old=True)), ['Emaki', 'Emaki (all drivers)', 'Emaki (LTS kernel)',
                                                          'Emaki (LTS kernel, all drivers)',
                                                          'UEFI Firmware Settings', 'Restore from a snapshot'])
        self.assertNotIn('with Linux', ' '.join(titles(text)))
        # Boot commands move unchanged; the first entry is still the default kernel.
        for line in MENU.splitlines():
            if line.lstrip().startswith(('linux', 'initrd', 'fwsetup')):
                for old in (False, True):
                    self.assertEqual(layout(text, old).count(line + '\n'), MENU.count(line + '\n'))
        first = text[text.index("menuentry '"):]
        self.assertIn('/@/boot/vmlinuz-linux root=', first.split('\n}\n')[0])
        advanced = text[text.index("submenu 'Advanced options'"):]
        self.assertTrue(text.index('### END /etc/grub.d/41_snapshots-btrfs ###') < text.index(advanced))
        self.assertEqual(advanced.count('{'), advanced.count('}'))

    def test_idempotent_and_foreign_menus_unchanged(self):
        once = boot.menu_layout(MENU)
        self.assertEqual(boot.menu_layout(once), once)
        custom = "menuentry 'Arch Linux' {\n\tlinux /vmlinuz-linux\n}\n"
        self.assertEqual(boot.menu_layout(custom), custom)

    def test_menu_without_snapshot_section_appends_advanced(self):
        text = boot.menu_layout(MENU.split('### BEGIN /etc/grub.d/41_snapshots-btrfs')[0])
        self.assertTrue(text.rstrip().endswith('}\nfi'))
        self.assertTrue(text.index('### END /etc/grub.d/30_uefi-firmware') < text.index("submenu 'Advanced options'"))
        self.assertEqual(titles(layout(text))[:3], ['Emaki', 'Emaki (LTS kernel)', 'Advanced options'])

    def test_unknown_numbers_before_the_moved_entries_leave_the_menu(self):
        # Without the header, or when even entry 0 is unknown, old numbers cannot be kept.
        for menu in (MENU.replace('### END /etc/grub.d/00_header ###\n', ''),
                     MENU.replace('### BEGIN /etc/grub.d/10_linux ###\n',
                                  'for x in a; do\nmenuentry "$x" {\n\techo\n}\ndone\n### BEGIN /etc/grub.d/10_linux ###\n'),
                     # Only a conditional entry before the first moved one.
                     MENU.replace('### BEGIN /etc/grub.d/10_linux ###\n',
                                  'if true; then menuentry "A" {\n\techo\n}; fi\n### BEGIN /etc/grub.d/10_linux ###\n')
                     .replace(entry('Emaki, with Linux linux', 'linux-advanced', 'initramfs-linux.img'), '')):
            self.assertEqual(boot.menu_layout(menu), menu)

    def test_installed_menu_is_rearranged(self):
        # The whole grub.cfg grub-mkconfig wrote on the encrypted VM (2026-10-11, grub 2:2.16-1): the stock header
        # assigns variables from variables (`saved_entry="${chosen}"` in savedefault), which is not a command.
        menu = (ROOT / 'tests/fixtures/grub-2.16-encrypted.cfg').read_text()
        self.assertTrue(boot.top_level_entries(menu).complete)
        text = boot.menu_layout(menu)
        self.assertEqual(titles(layout(text)), ['Emaki', 'Emaki (LTS kernel)', 'Emaki snapshots', 'Advanced options',
                                                'Emaki (all drivers)', 'Emaki (LTS kernel, all drivers)',
                                                'UEFI Firmware Settings'])
        for value in ('0', '1', '2', '3', 'uefi-firmware', 'Emaki, with Linux linux (fallback initramfs)'):
            with self.subTest(value=value):
                self.assertEqual(selected(text, value), resolve(menu, value))
        # Only an assignment to a name is one: a word that starts with an expansion is a command.
        self.assertFalse(boot.changes_default('x="${y}"\nset a=$b\n'))
        for late in ('${cmd} default=1\n', '$x\n', 'default="${y}"\n'):
            self.assertTrue(boot.changes_default(late), late)
        found = boot.top_level_entries("menuentry A { echo; }\n${cmd}\nmenuentry B { echo; }\n")
        self.assertEqual([entry.title for entry in found.entries], ['A'])

    def test_late_default_changes_leave_the_menu(self):
        # Review N7Gv4: what runs after the header may change the default, which is then read in the
        # wrong order; only 41_custom's own block is followed at boot.
        for late in ('set default=1\n', "default='5'\n", 'set "default=1"\n', 'unset default\n', 'load_env\n',
                     'source $prefix/my.cfg\n', 'if true; then\n  set default=1\nfi\n', 'function f {\n  default=1\n}\nf\n',
                     'echo "unclosed\n', CUSTOM_CFG.replace('source $prefix', 'source ${prefix}'), CUSTOM_CFG * 2):
            menu = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', late + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
            with self.subTest(late=late):
                self.assertEqual(boot.menu_layout(menu), menu)
        # A default set inside an entry body only acts when that entry runs.
        inside = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs',
                              "submenu 'Tools' {\n  set default=1\n  menuentry 'A' { echo; }\n}\n### BEGIN /etc/grub.d/41_snapshots-btrfs")
        self.assertIn("submenu 'Advanced options'", boot.menu_layout(inside))

    def test_renamed_only_menu_keeps_title_defaults(self):
        # Review N7Gv5: without fallback entries and the firmware entry only the kernels are renamed; an old
        # title (GRUB_DEFAULT, the saved or next entry, or one a custom.cfg assigns) still selects its kernel.
        plain = boot.FIRMWARE.sub(r'\1\3', re.sub(r"(?ms)^menuentry '[^']*fallback initramfs\)'.*?^\}\n", '', MENU))
        text = boot.menu_layout(plain)
        self.assertEqual(titles(text), ['Emaki', 'Emaki (LTS kernel)', 'Restore from a snapshot'])
        for value in ('0', '1', 'Emaki, with Linux linux', 'Emaki, with Linux linux-lts', f'gnulinux-linux-lts-advanced-{UUID}'):
            with self.subTest(value=value):
                self.assertEqual(resolve(text, remapped(text, value)), resolve(plain, value))
        sourced = plain.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', CUSTOM_CFG + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        text = boot.menu_layout(sourced)
        after = text[text.index('### END /etc/grub.d/41_custom ###'):text.index('### BEGIN /etc/grub.d/41_snapshots-btrfs')]
        old = dict(re.findall(r'''if \[ "\$default" = '([^']*)' \]; then\n  set default='([^']*)'\n''', after))
        for value in ('Emaki, with Linux linux', 'Emaki, with Linux linux-lts'):
            with self.subTest(value=value, custom=True):
                self.assertEqual(resolve(text, old.get(value, value)), resolve(sourced, value))
        # Anything else after the header that may change the default keeps the old titles.
        late = plain.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', 'set default=1\n### BEGIN /etc/grub.d/41_snapshots-btrfs')
        self.assertEqual(boot.menu_layout(late), late)

    def test_custom_cfg_keeps_the_old_order(self):
        # 41_custom sources custom.cfg after the header: when it exists the old order stays, and
        # whatever default it leaves is pointed at the same entry right after it.
        menu = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', CUSTOM_CFG + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        text = boot.menu_layout(menu)
        header = text[:text.index('### END /etc/grub.d/00_header ###')]
        self.assertIn('if [ -f  ${config_directory}/custom.cfg ]; then\n  set emaki_old_layout=y\n', header)
        self.assertIn('elif [ -z "${config_directory}" -a -f  $prefix/custom.cfg ]; then\n  set emaki_old_layout=y\n', header)
        after = text[text.index('### END /etc/grub.d/41_custom ###'):text.index('### BEGIN /etc/grub.d/41_snapshots-btrfs')]
        self.assertTrue(after.startswith('### END /etc/grub.d/41_custom ###\nif [ "$emaki_old_layout" ]; then\n'))
        old = dict(re.findall(r'''if \[ "\$default" = '([^']*)' \]; then\n  set default='([^']*)'\n''', after))
        for value in ('Emaki, with Linux linux (fallback initramfs)', 'Emaki, with Linux linux-lts',
                      f'emaki-advanced>gnulinux-linux-fallback-{UUID}', 'Advanced options>Emaki (all drivers)',
                      'emaki-advanced>uefi-firmware', 'Advanced options>UEFI Firmware Settings'):
            with self.subTest(value=value):
                target = old[value]
                self.assertEqual(resolve(layout(text, old=True), target),
                                 resolve(layout(text), value if value.startswith(('emaki-', 'Advanced')) else remapped(text, value)))
        self.assertEqual(text.count('### END /etc/grub.d/41_custom ###'), 1)

    def test_old_defaults_select_the_same_entry(self):
        # GRUB_DEFAULT, a saved or a next entry may name an entry by number, title or id.
        windows = ('### BEGIN /etc/grub.d/30_os-prober ###\n'
                   "menuentry 'Windows Boot Manager (on /dev/nvme0n1p1)' --class windows "
                   "$menuentry_id_option 'osprober-efi-0A1B' {\n\tchainloader /efi/Microsoft/Boot/bootmgfw.efi\n}\n"
                   '### END /etc/grub.d/30_os-prober ###\n\n')
        alongside = MENU.replace('### BEGIN /etc/grub.d/30_uefi-firmware', windows + '### BEGIN /etc/grub.d/30_uefi-firmware')
        for menu, numbers in ((MENU, 4), (alongside, 5)):
            text = boot.menu_layout(menu)
            values = [str(number) for number in range(numbers)] + ['uefi-firmware', 'UEFI Firmware Settings']
            for name, identifier, _ in menu_tree(menu):
                values += [name, identifier] if identifier and identifier.startswith(('gnulinux', 'osprober')) else []
            for value in values:
                with self.subTest(value=value, alongside=menu is alongside):
                    self.assertIsNotNone(resolve(menu, value))
                    self.assertEqual(selected(text, value), resolve(menu, value))
            # Today's default and new-style selections are left alone.
            for value in ('0', 'emaki-advanced>uefi-firmware', f'gnulinux-linux-advanced-{UUID}'):
                self.assertEqual(remapped(text, value), value)

    def test_custom_entries_keep_earlier_numbers(self):
        # 40_custom may use any valid GRUB syntax; the stock entries before it still move.
        for custom in ('menuentry "Custom Linux" {\n\techo custom\n}\n',
                       'menuentry Custom --class os{\n\techo "}"\n}\n',
                       "menuentry 'Custom' { echo custom; }\n",
                       'if true; then menuentry "Custom" {\n\techo\n}; fi\n',
                       # Not followed: the entries before it are still known.
                       'for x in a b; do\nmenuentry "Custom $x" {\n\techo\n}\ndone\n',
                       'menuentry "Unclosed {\n'):
            menu = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', custom + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
            text = boot.menu_layout(menu)
            for value in ('1', '2', '3'):
                with self.subTest(custom=custom, value=value):
                    self.assertEqual(selected(text, value), resolve(MENU, value))

    def test_entry_syntax(self):
        found = boot.top_level_entries(
            "menuentry 'A' --class x $menuentry_id_option 'a' {\n\techo '}'\n}\n"
            'menuentry --class y "B \\"q\\"" {echo; }\n'
            '# menuentry "Comment" {\n'
            'if [ x ]; then\n  submenu C {\n    menuentry D { echo; }\n  }\nfi\n'
            'menuentry "E" --id=e { echo }\n')
        self.assertEqual([(entry.title, entry.id, entry.conditional) for entry in found.entries],
                         [('A', 'a', False), ('B "q"', None, False), ('C', None, True), ('E', 'e', False)])
        self.assertTrue(found.complete)
        sources = boot.top_level_entries(CUSTOM_CFG)
        self.assertEqual([(entry.source, entry.conditional) for entry in sources.entries], [(True, True)] * 2)
        partial = boot.top_level_entries('menuentry A { echo; }\nmenuentry "B\n')
        self.assertEqual([entry.title for entry in partial.entries], ['A'])
        self.assertFalse(partial.complete)

    def test_installed_kernels_still_verified(self):
        boot.verify_grub(boot.menu_layout(MENU))

    @unittest.skipUnless(subprocess.run(['sh', '-c', 'command -v grub-script-check'],
                                        capture_output=True).returncode == 0, 'grub-script-check is unavailable')
    def test_real_syntax_check(self):
        path = ROOT / '.cache/tmp/layout.cfg'
        path.parent.mkdir(parents=True, exist_ok=True)
        custom = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs',
                              'menuentry "Custom Linux" {\n\techo\n}\n' + CUSTOM_CFG
                              + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        for menu in (MENU, custom):
            path.write_text(boot.menu_layout(menu))
            self.assertEqual(subprocess.run(['grub-script-check', str(path)]).returncode, 0)


class QuietKernelEntries(unittest.TestCase):
    """B9: no "Loading Linux ..." lines between the menu and the startup logo."""

    def setUp(self):
        self.menu = (ROOT / 'tests/fixtures/grub-2.16-encrypted.cfg').read_text()

    @staticmethod
    def section(text):
        return text.split('### BEGIN /etc/grub.d/10_linux ###')[1].split('### END /etc/grub.d/10_linux ###')[0]

    def test_stock_entries_clear_the_screen_and_print_nothing(self):
        text = boot.quiet_linux_entries(self.menu)
        section = self.section(text)
        self.assertNotIn('echo', section)
        bodies = re.findall(r"^menuentry [^\n]*\{\n([^\n]*)\n", section, re.M)
        self.assertEqual(bodies, ['\tclear'] * 4)
        # Nothing else changed: dropping the inserted and removed lines gives the stock menu back.
        stock = [line for line in self.menu.splitlines() if not re.fullmatch(r"\techo\t'Loading [^']*'", line)]
        self.assertEqual([line for line in text.splitlines() if line != '\tclear'], stock)
        self.assertIn('echo ""', text)  # the snapshot section's own line stays

    def test_translated_messages_are_found_by_place_not_by_words(self):
        russian = self.menu.replace("'Loading Linux linux ...'", "'Загрузка Linux linux…'").replace(
            "'Loading initial ramdisk ...'", "'Загружается начальный виртуальный диск…'")
        self.assertNotIn('echo', self.section(boot.quiet_linux_entries(russian)))

    def test_idempotent_and_other_entries_untouched(self):
        once = boot.quiet_linux_entries(self.menu)
        self.assertEqual(boot.quiet_linux_entries(once), once)
        windows = ("### BEGIN /etc/grub.d/30_os-prober ###\nmenuentry 'Windows Boot Manager' {\n"
                   "\techo\t'Starting Windows'\n\tchainloader /EFI/Microsoft/Boot/bootmgfw.efi\n}\n"
                   "### END /etc/grub.d/30_os-prober ###\n")
        self.assertEqual(boot.quiet_linux_entries(windows), windows)
        own = MENU.replace('\tload_video\n', "\techo\t'My own note'\n\tload_video\n", 1)
        self.assertIn("\techo\t'My own note'\n", boot.quiet_linux_entries(own))
        self.assertEqual(boot.quiet_linux_entries('menuentry A { echo; }\n'), 'menuentry A { echo; }\n')

    def test_moved_fallback_entries_stay_quiet(self):
        text = boot.menu_layout(boot.quiet_linux_entries(self.menu))
        self.assertIn("submenu 'Advanced options'", text)
        self.assertNotIn("'Loading ", text)
        self.assertEqual(text.count('\n\tclear\n'), 4 + 2)  # the moved entries are in both orders
        boot.verify_grub(text)

    @unittest.skipUnless(subprocess.run(['sh', '-c', 'command -v grub-script-check'],
                                        capture_output=True).returncode == 0, 'grub-script-check is unavailable')
    def test_real_syntax_check(self):
        path = ROOT / '.cache/tmp/quiet.cfg'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(boot.menu_layout(boot.quiet_linux_entries(self.menu)))
        self.assertEqual(subprocess.run(['grub-script-check', str(path)]).returncode, 0)


def installed_menu():
    """The VM's whole grub.cfg with its stock header; only the commands that reach that machine's disk are dropped."""
    menu = re.sub(r'(?m)^(insmod (part_gpt|cryptodisk|luks2|gcry_\w+|btrfs|png)|cryptomount .*|set root=.*|'
                  r'background_image .*)\n', '',
                  (ROOT / 'tests/fixtures/grub-2.16-encrypted.cfg').read_text())
    menu = re.sub(r'(?m)^if \[ x\$feature_platform_search_hint = xy \]; then\n  search .*\nelse\n  search .*\nfi\n', '', menu)
    # And the graphical terminal and the countdown, which the serial console under test does not use.
    menu = re.sub(r'(?ms)^if loadfont \$font ; then\n.*?^fi\nterminal_output gfxterm\n', '', menu)
    return menu.replace('set timeout=5\n', 'set timeout=0\n')


class RealGrubSelection(unittest.TestCase):
    """Real GRUB 2.16 under OVMF boots the default of the rearranged menu.

    Set EMAKI_GRUB_TOOLS to an extracted GRUB package root when GRUB is not installed.
    """

    def boot(self, folder, menu, before, firmware=True, snapshots=False, custom=None):
        # Every entry reports its id instead of booting; the firmware entry exists unless firmware=False,
        # the snapshot submenu only with snapshots=True (opening it reports "snapshots"), and a
        # custom.cfg for 41_custom only when `custom` gives its text.
        menu = re.sub(r"(?ms)^([ \t]*)(menuentry [^\n]*\$menuentry_id_option '([^']+)' \{\n).*?^\1\}\n",
                      lambda match: f"{match[1]}{match[2]}{match[1]}  echo 'SELECTED={match[3]}'\n"
                                    f"{match[1]}  halt\n{match[1]}}}\n",
                      menu.replace('fwsetup --is-supported', 'true' if firmware else 'false'))
        # The stock 00_header default block, fed by GRUB_DEFAULT, saved_entry or next_entry.
        header = ('serial --unit=0 --speed=115200\nterminal_input serial\nterminal_output serial\n'
                  'set timeout=0\nset menuentry_id_option=--id\nexport menuentry_id_option\n' + before
                  + 'if [ "${next_entry}" ] ; then\n   set default="${next_entry}"\n   set next_entry=\nelse\n'
                  '   set default="${emaki_test_default}"\nfi\n')
        config = folder / 'grub.cfg'
        (folder / 'fat/EFI/BOOT').mkdir(parents=True)
        config.write_text(menu.replace('set default="0"\n', header))
        (folder / 'grub-btrfs.cfg').write_text('echo SELECTED=snapshots\nhalt\n')
        (folder / 'custom.cfg').write_text(custom or '')
        subprocess.run([grub_tool('grub-mkstandalone'), '-O', 'x86_64-efi', '-d', str(grub_modules()),
                        '--fonts=', '--locales=', '--themes=', '-o', str(folder / 'fat/EFI/BOOT/BOOTX64.EFI'),
                        f'boot/grub/grub.cfg={config}']
                       + ([f'boot/grub/grub-btrfs.cfg={folder / "grub-btrfs.cfg"}'] if snapshots else [])
                       + ([f'boot/grub/custom.cfg={folder / "custom.cfg"}'] if custom is not None else []), check=True)
        shutil.copyfile(OVMF / 'OVMF_VARS.4m.fd', folder / 'vars.fd')
        serial = folder / 'serial.log'
        command = ['qemu-system-x86_64', '-machine', 'q35', '-accel', 'kvm', '-accel', 'tcg', '-m', '256',
                   '-nodefaults', '-display', 'none', '-monitor', 'none', '-net', 'none', '-no-reboot',
                   '-serial', f'file:{serial}',
                   '-drive', f'if=pflash,format=raw,readonly=on,file={OVMF / "OVMF_CODE.4m.fd"}',
                   '-drive', f'if=pflash,format=raw,file={folder / "vars.fd"}',
                   '-drive', f'format=raw,file=fat:rw:{folder / "fat"}']
        for attempt in range(3):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                break
            except subprocess.TimeoutExpired:
                log = serial.read_text(errors='replace')
                # OVMF under KVM now and then stalls before it starts any loader; GRUB never ran.
                if 'BdsDxe: starting' in log or attempt == 2:
                    self.fail('QEMU did not power off; serial: ' + log[-600:])
        self.assertEqual(result.returncode, 0, result.stderr)
        log = serial.read_text(errors='replace')
        self.assertNotIn('error:', log)
        found = re.findall(r'SELECTED=(\S+)', log)
        return found[0] if found else None

    def test_configured_saved_and_next_defaults(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        text = boot.menu_layout(MENU)
        fallback, lts_fallback = f'gnulinux-linux-fallback-{UUID}', f'gnulinux-linux-lts-fallback-{UUID}'
        cases = [('set emaki_test_default=1\n', '1'),
                 ('set emaki_test_default=2\n', '2'),
                 (f"set emaki_test_default='{fallback}'\n", fallback),
                 ("set emaki_test_default='Emaki, with Linux linux-lts (fallback initramfs)'\n",
                  'Emaki, with Linux linux-lts (fallback initramfs)'),
                 ('set emaki_test_default=uefi-firmware\n', 'uefi-firmware'),
                 # GRUB_DEFAULT=saved reads saved_entry; grub-reboot sets next_entry.
                 (f"set saved_entry='{fallback}'\nset emaki_test_default=\"${{saved_entry}}\"\n", fallback),
                 (f"set next_entry='{lts_fallback}'\nset emaki_test_default=0\n", lts_fallback),
                 ('set emaki_test_default=0\n', '0')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (before, value) in enumerate(cases):
                with self.subTest(value=value, before=before):
                    self.assertEqual(self.boot(Path(directory, str(number)), text, before), resolve(MENU, value))

    def test_custom_and_conditional_numbers(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        # Review N7Gv2: a custom entry without an id in double quotes, and numbers at and after the
        # firmware entry, which exists only when the firmware supports it.
        custom = ('menuentry "Custom Linux" {\n echo SELECTED=custom\n halt\n}\n'
                  "menuentry 'Other' $menuentry_id_option 'other' {\n echo\n}\n" + CUSTOM_CFG)
        menu = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', custom + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        text = boot.menu_layout(menu)
        # (firmware entry exists, snapshot submenu exists, old default, entry it selected)
        cases = [(True, False, '1', f'gnulinux-linux-fallback-{UUID}'), (True, False, '3', f'gnulinux-linux-lts-fallback-{UUID}'),
                 (True, False, '4', 'uefi-firmware'), (True, False, '5', 'custom'), (True, False, '6', 'other'),
                 (False, False, '4', 'custom'), (False, False, '5', 'other'), (False, False, 'Custom Linux', 'custom'),
                 # The snapshot submenu comes after 41_custom's `source`, which did not run.
                 (True, True, '7', 'snapshots'), (False, True, '6', 'snapshots')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (firmware, snapshots, value, expected) in enumerate(cases):
                with self.subTest(firmware=firmware, snapshots=snapshots, value=value):
                    before = f"set emaki_test_default='{value}'\n"
                    for stage, config in (('before', menu), ('after', text)):
                        self.assertEqual(self.boot(Path(directory, f'{number}-{stage}'), config, before, firmware, snapshots),
                                         expected, stage)

    def test_numbers_after_custom_cfg_and_submenu_paths(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        # Review N7Gv3: entries a custom.cfg adds through 41_custom are unknown until boot, and a
        # number may start a path into a submenu.
        sourced = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', CUSTOM_CFG + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        rescue = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', "submenu 'Rescue' --id rescue {\n" + ''.join(
            f" menuentry 'Rescue {name}' --id rescue-{name} {{\n  echo SELECTED=rescue-{name}\n  halt\n }}\n"
            for name in 'ab') + '}\n### BEGIN /etc/grub.d/41_snapshots-btrfs')
        one = "menuentry 'Custom Linux' --id custom {\n echo SELECTED=custom\n halt\n}\n"
        # (menu, firmware entry exists, snapshot submenu exists, custom.cfg, old default, entry it selected)
        cases = [(sourced, True, False, one, '5', 'custom'), (sourced, True, True, one, '6', 'snapshots'),
                 (sourced, True, True, '# empty\n', '5', 'snapshots'), (sourced, False, True, one, '4', 'custom'),
                 (sourced, True, True, one, '2', f'gnulinux-linux-lts-advanced-{UUID}'),
                 (rescue, True, False, None, '5>1', 'rescue-b'), (rescue, False, False, None, '4>0', 'rescue-a'),
                 (MENU, True, True, None, '5>0', 'snapshots'), (rescue, True, False, None, 'rescue>rescue-b', 'rescue-b'),
                 (sourced, True, True, one, 'Custom Linux', 'custom'), (MENU, True, False, None, '', f'gnulinux-linux-advanced-{UUID}')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (menu, firmware, snapshots, custom, value, expected) in enumerate(cases):
                text = boot.menu_layout(menu)
                # GRUB_DEFAULT (or saved_entry) and grub-reboot's next_entry.
                for way, before in (('default', f"set emaki_test_default='{value}'\n"),
                                    ('next', f"set next_entry='{value}'\nset emaki_test_default=0\n")):
                    for stage, config in (('before', menu), ('after', text)):
                        with self.subTest(value=value, firmware=firmware, snapshots=snapshots, custom=custom,
                                          way=way, stage=stage):
                            self.assertEqual(self.boot(Path(directory, f'{number}-{way}-{stage}'), config, before,
                                                       firmware, snapshots, custom), expected)

    def test_installed_menu(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        menu = installed_menu()
        text = boot.menu_layout(menu)
        self.assertIn("submenu 'Advanced options'", text)
        fallback, lts_fallback = f'gnulinux-linux-fallback-{UUID}', f'gnulinux-linux-lts-fallback-{UUID}'
        cases = [('1', fallback), ('3', lts_fallback), ('4', 'uefi-firmware'), ('5', 'snapshots'),
                 ('Emaki, with Linux linux-lts (fallback initramfs)', lts_fallback), ('0', f'gnulinux-linux-advanced-{UUID}')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (value, expected) in enumerate(cases):
                for stage, config in (('before', menu), ('after', text)):
                    with self.subTest(value=value, stage=stage):
                        self.assertEqual(self.boot(Path(directory, f'{number}-{stage}'), config,
                                                   f"set emaki_test_default='{value}'\n", True, True), expected)

    def test_renamed_only_installed_menu(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        # Review N7Gv5: the installed menu without fallback entries and the firmware entry; only the kernels are renamed.
        menu = installed_menu()
        for found in reversed(boot.top_level_entries(menu).entries):
            if found.title and 'fallback initramfs' in found.title:
                menu = menu[:found.start] + menu[found.end:]
        menu = boot.FIRMWARE.sub(r'\1\3', menu)
        text = boot.menu_layout(menu)
        self.assertNotIn("submenu 'Advanced options'", text)
        self.assertNotIn('with Linux', ' '.join(titles(text)))
        lts, linux = f'gnulinux-linux-lts-advanced-{UUID}', f'gnulinux-linux-advanced-{UUID}'
        # (how the old default is given, custom.cfg or None, entry it selected)
        cases = [("set emaki_test_default='Emaki, with Linux linux-lts'\n", None, lts),
                 ("set next_entry='Emaki, with Linux linux-lts'\nset emaki_test_default=0\n", None, lts),
                 ('set emaki_test_default=0\n', "set default='Emaki, with Linux linux-lts'\n", lts),
                 ("set emaki_test_default='Emaki, with Linux linux'\n", None, linux),
                 ('set emaki_test_default=1\n', None, lts), ('set emaki_test_default=2\n', None, 'snapshots')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (before, custom, expected) in enumerate(cases):
                for stage, config in (('before', menu), ('after', text)):
                    with self.subTest(before=before, custom=custom, stage=stage):
                        self.assertEqual(self.boot(Path(directory, f'{number}-{stage}'), config, before, False, True, custom),
                                         expected)

    def test_default_assigned_by_custom_cfg(self):
        if not grub_modules() or not grub_modules().is_dir() \
                or not shutil.which('qemu-system-x86_64') or not (OVMF / 'OVMF_CODE.4m.fd').is_file():
            self.skipTest('real GRUB, QEMU or OVMF is unavailable')
        # Review N7Gv4: a custom.cfg runs after the header and may assign the default itself.
        menu = MENU.replace('### BEGIN /etc/grub.d/41_snapshots-btrfs', CUSTOM_CFG + '### BEGIN /etc/grub.d/41_snapshots-btrfs')
        text = boot.menu_layout(menu)
        fallback, lts_fallback = f'gnulinux-linux-fallback-{UUID}', f'gnulinux-linux-lts-fallback-{UUID}'
        # (firmware entry exists, value custom.cfg assigns, entry it selected)
        cases = [(True, '1', fallback), (True, '5', 'snapshots'), (True, '5>0', 'snapshots'), (True, fallback, fallback),
                 (False, '4', 'snapshots'), (True, 'Emaki, with Linux linux-lts (fallback initramfs)', lts_fallback),
                 (True, 'Emaki, with Linux linux-lts', f'gnulinux-linux-lts-advanced-{UUID}'),
                 (True, 'UEFI Firmware Settings', 'uefi-firmware')]
        with tempfile.TemporaryDirectory() as directory:
            for number, (firmware, value, expected) in enumerate(cases):
                for stage, config in (('before', menu), ('after', text)):
                    with self.subTest(value=value, firmware=firmware, stage=stage):
                        self.assertEqual(self.boot(Path(directory, f'{number}-{stage}'), config, 'set emaki_test_default=0\n',
                                                   firmware, True, f"set default='{value}'\n"), expected)
            # A custom.cfg that leaves the default alone: names from the header, also of the short menu, still work.
            for number, (value, expected) in enumerate((('1', fallback), ('Emaki, with Linux linux (fallback initramfs)', fallback),
                                                        (f'emaki-advanced>{lts_fallback}', lts_fallback),
                                                        ('Advanced options>UEFI Firmware Settings', 'uefi-firmware'),
                                                        ('0', f'gnulinux-linux-advanced-{UUID}'))):
                for way, before in (('default', f"set emaki_test_default='{value}'\n"),
                                    ('next', f"set next_entry='{value}'\nset emaki_test_default=0\n")):
                    with self.subTest(value=value, way=way):
                        self.assertEqual(self.boot(Path(directory, f'kept-{number}-{way}'), text, before, True, True,
                                                   '# no default here\n'), expected)


class MenuTheme(unittest.TestCase):
    def test_scale_follows_grub_mode_height(self):
        for mode, scale in ((None, 100), ((1024, 768), 100), ((1280, 800), 100), ((1920, 1080), 150),
                            ((1920, 1200), 150), ((2560, 1600), 200), ((2560, 1440), 200),
                            ((3840, 2160), 300)):
            with self.subTest(mode=mode):
                self.assertEqual(boot.menu_theme(mode), f'{boot.MENU_THEME_DIR}/theme-{scale}.txt')

    def test_every_scale_is_shipped_with_its_fonts(self):
        for scale, sizes in grub_screen.SCALES.items():
            text = (ROOT / f'grub/theme/theme-{scale}.txt').read_text()
            for size in (sizes['item'], sizes['hint']):
                self.assertIn(f'"{grub_screen.FONT_FAMILY} {size}"', text)
                self.assertTrue((ROOT / f'grub/theme/noto-sans-{size}.pf2').is_file())
            self.assertTrue((ROOT / f'grub/theme/arrows-{sizes["hint"]}.pf2').is_file())
            self.assertIn('title-text: ""', text)
            self.assertIn(grub_screen.HINT, text)
            for prefix in ('menu', 'select'):
                for piece in ('nw', 'n', 'ne', 'w', 'c', 'e', 'sw', 's', 'se'):
                    self.assertTrue((ROOT / f'grub/theme/{scale}/{prefix}_{piece}.png').is_file())

    def test_selection_is_one_row_high(self):
        # GRUB draws the selection's corners outside the content box (gui_list.c).
        for scale, sizes in grub_screen.SCALES.items():
            text = (ROOT / f'grub/theme/theme-{scale}.txt').read_text()
            height = int(text.split('item_height = ')[1].split()[0])
            spacing = int(text.split('item_spacing = ')[1].split()[0])
            self.assertEqual(height + 2 * sizes['select_radius'], sizes['row'])
            self.assertEqual(height + spacing, sizes['row'] + sizes['gap'])

    def test_generated_theme_is_read_back(self):
        line = 'set theme=($root)/@/usr/share/emaki/grub/theme/theme-200.txt\n'
        self.assertEqual(boot.generated_menu_theme(line), '/usr/share/emaki/grub/theme/theme-200.txt')
        self.assertIsNone(boot.generated_menu_theme('terminal_output gfxterm\n'))
        self.assertEqual(boot.generated_menu_theme('set theme=($root)/boot/grub/themes/x/theme.txt\n'), '')

    def test_terminal_window_covers_the_screen(self):
        # GRUB's default terminal window is a black box over the middle 70 % of the picture
        # (gfxmenu/view.c); what GRUB printed after the menu showed there (B9).
        for scale in grub_screen.SCALES:
            text = (ROOT / f'grub/theme/theme-{scale}.txt').read_text()
            for line in ('terminal-left: "0"', 'terminal-top: "0"', 'terminal-width: "100%"',
                         'terminal-height: "100%"', 'terminal-border: "0"'):
                with self.subTest(scale=scale, line=line):
                    self.assertIn('\n' + line + '\n', text)

    def test_committed_theme_is_current(self):
        subprocess.run([sys.executable, str(ROOT / 'grub/theme/generate-theme.py'), '--check'], check=True)


def block(date, number, kind, description):
    return (f"submenu '| {date} | @snapshots/{number}/snapshot | {kind} | {description} |' {{\n\n"
            + ''.join(f"    menuentry '  vmlinuz-{k} & initramfs-{k}{f}.img' --class snapshots $menuentry_id_option "
                      f"'gnulinux-snapshots-{UUID}' {{\n"
                      f"        linux \"/@snapshots/{number}/snapshot/boot/vmlinuz-{k}\" root=UUID={UUID}\n"
                      f"        initrd \"/@snapshots/{number}/snapshot/boot/initramfs-{k}{f}.img\"\n    }}\n\n"
                      for k in ('linux', 'linux-lts') for f in ('-fallback', ''))
            + '}\n')


class SnapshotTitles(unittest.TestCase):
    def setUp(self):
        self.menu = (block('2026-10-11 17:13:04', 1, 'single', 'Fresh Emaki 0.6.0 install')
                     + ''.join(block(f'2026-10-11 {12 + i}:00:0{i}', 2 + i, 'single', 'timeline') for i in range(7))
                     + block('2026-10-11 21:30:00', 9, 'pre', 'pacman -Syu')
                     + block('2026-10-10 09:00:00', 20, 'single', 'my own'))

    def test_plain_titles_newest_first_with_five_automatic(self):
        text = snapshot_menu.render(self.menu)
        self.assertEqual([title for title in titles(text) if not title.startswith('Emaki')], [
            '11 Oct 2026, 21:30 · before update · pacman -Syu', '11 Oct 2026, 18:00 · automatic',
            '11 Oct 2026, 17:13 · fresh install', '11 Oct 2026, 17:00 · automatic',
            '11 Oct 2026, 16:00 · automatic', '11 Oct 2026, 15:00 · automatic',
            '11 Oct 2026, 14:00 · automatic', '10 Oct 2026, 09:00 · my own'])
        # Ordinary initramfs first, fallback ones after, inside every snapshot.
        self.assertEqual(titles(text)[1:5], ['Emaki', 'Emaki (LTS kernel)', 'Emaki (all drivers)',
                                             'Emaki (LTS kernel, all drivers)'])

    def test_boot_commands_survive_and_render_is_stable(self):
        text = snapshot_menu.render(self.menu)
        for number in (1, 6, 7, 8, 9, 20):
            for line in self.menu.splitlines():
                if f'/@snapshots/{number}/' in line:
                    self.assertIn(line, text)
        self.assertEqual(snapshot_menu.render(text), text)

    def test_long_update_command_is_shortened(self):
        title = snapshot_menu.plain_title('2026-10-12 04:37:52', 'pre',
                                          'pacman -U --noconfirm /var/cache/pacman/pkg/aalib-1.4rc5-19')
        self.assertEqual(title, '12 Oct 2026, 04:37 · before update · pacman -U --noconfirm /…')

    def test_unknown_format_is_left_alone(self):
        custom = "submenu '|2026-10-11|single|' {\n}\n"
        self.assertEqual(snapshot_menu.render(custom), custom)


if __name__ == '__main__':
    unittest.main()
