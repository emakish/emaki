#!/usr/bin/env python3
"""Keyboard layouts have one source: /etc/vconsole.conf through localed.

Static check over the shipped niri configs and the installer's generated files: no xkb
section anywhere Emaki writes (it would cut that compositor off from the system list), no
grp: switch option next to the Mod+Space switch-layout bind (the layout would switch twice),
and a switch-layout bind on the login screen (a password typed in a second layout must be
typeable there). Repo-level: it reads niri/ and greetd/, which the installer package does
not carry, so it lives here and not in installer/tests.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer.render import niri_config, vconsole_conf  # noqa: E402

SHIPPED = sorted(ROOT.glob('niri/*.kdl')) + [ROOT / 'greetd/niri.kdl',
                                             ROOT / 'iso/profile/airootfs/home/live/.config/emaki/niri-emaki.kdl']
SWITCH_BIND = re.compile(r'(?m)^\s*Mod\+Space\s*\{\s*switch-layout\s+"next"\s*;\s*\}')


def without_comments(text):
    return re.sub(r'//[^\n]*', '', text)


class KeyboardSingleSourceTests(unittest.TestCase):
    def test_shipped_configs_exist(self):
        self.assertGreaterEqual(len(SHIPPED), 4)
        for path in SHIPPED:
            self.assertTrue(path.is_file(), path)

    def test_no_xkb_section_and_no_group_switch_option_in_shipped_configs(self):
        for path in SHIPPED:
            text = without_comments(path.read_text())
            self.assertNotRegex(text, r'\bxkb\s*\{', path)
            self.assertNotIn('grp:', text, path)
            self.assertNotIn('XKB', text, path)

    def test_generated_files_carry_no_xkb_section_and_no_group_switch_option(self):
        # The defaults bind Mod+Space to switch-layout; an xkb group option in a generated
        # file would switch a second time on every press.
        for text in (niri_config(), niri_config(1.5, ['eDP-1']), vconsole_conf(['cz', 'us']),
                     vconsole_conf(['dvorak', 'ru'])):
            self.assertNotRegex(text, r'\bxkb\b')
            self.assertNotIn('grp:', text)
            self.assertNotIn('XKBOPTIONS', text)

    def test_session_and_login_screen_switch_layouts_with_the_same_key(self):
        # The session defaults bind it once; the login screen's own compositor must bind it
        # too, or a password typed in the second layout cannot be typed at login.
        defaults = without_comments((ROOT / 'niri/default.kdl').read_text())
        self.assertEqual(len(SWITCH_BIND.findall(defaults)), 1)
        self.assertRegex(without_comments((ROOT / 'greetd/niri.kdl').read_text()), SWITCH_BIND)

    def test_login_screen_launches_nothing_else_from_binds(self):
        text = without_comments((ROOT / 'greetd/niri.kdl').read_text())
        binds = re.search(r'(?s)\bbinds\s*\{(.*)\}', text)
        self.assertIsNotNone(binds)
        self.assertNotIn('spawn', binds[1])


if __name__ == '__main__':
    unittest.main()
