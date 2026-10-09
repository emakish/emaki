"""The window repeats planner limits before the review; these tests hold both to one number."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

UI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI.parent))
from emaki_installer.constants import GIB, MIB  # noqa: E402
from emaki_installer.errors import Code, InstallError  # noqa: E402
from emaki_installer.planner import Partition, storage_layout, validate_config, xkb_rules  # noqa: E402
from emaki_installer.render import keyboard_summary  # noqa: E402

EVALUATE = ('const vm = require("node:vm"); const fs = require("node:fs"); const context = vm.createContext({});'
            'vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);'
            'process.stdout.write(JSON.stringify(vm.runInContext(process.argv[2], context)));')


def window(expression):
    """Evaluate an expression with the window's own Protocol.js."""
    result = subprocess.run(['node', '-e', EVALUATE, str(UI / 'Protocol.js'), expression],
                            check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which('node'), 'node is unavailable')
class RootMinimum(unittest.TestCase):
    def test_root_minimum_is_the_planners(self):
        for ram in (4 * GIB, 16 * GIB, 64 * GIB):
            for hibernation in (False, True):
                for encryption in ('none', 'account'):
                    with self.subTest(ram=ram, hibernation=hibernation, encryption=encryption):
                        minimum = window(f'rootMinimum({ram}, {json.dumps(hibernation)}, {json.dumps(encryption != "none")})')
                        config = {'encryption': encryption, 'hibernation': hibernation}
                        inventory = {'memory_bytes': ram}
                        storage_layout(config, [Partition(None, 2, MIB, minimum, 'ext4', True, '/')], inventory)
                        with self.assertRaises(InstallError) as caught:
                            storage_layout(config, [Partition(None, 2, MIB, minimum - MIB, 'ext4', True, '/')], inventory)
                        self.assertEqual(caught.exception.code, Code.ROOT_TOO_SMALL)
                        # A manual root also needs the planner's plain 20 GiB.
                        self.assertGreaterEqual(minimum, 20 * GIB)


@unittest.skipUnless(shutil.which('node'), 'node is unavailable')
class PasswordCharacters(unittest.TestCase):
    def test_the_window_refuses_the_account_passwords_the_planner_refuses(self):
        # One character in an otherwise good password: every code point to U+00FF, and a few more.
        chars = [chr(x) for x in range(0x100)] + ['’', '€', 'ф', ' ', '\U0001f600']
        passwords = ['Zebra' + char + 'Yacht' for char in chars]
        refused = window('JSON.parse(' + json.dumps(json.dumps(passwords)) + ').map(p => accountErrors("Alex", "alex", p, p, "emaki").password !== "")')
        for password, by_window in zip(passwords, refused):
            config = {'mode': 'erase', 'disk_id': 'x', 'fs': 'btrfs', 'encryption': 'none',
                      'user': {'login': 'alex', 'password': password}}
            try:
                validate_config(config)
                by_planner = False
            except InstallError:
                by_planner = True
            with self.subTest(char=hex(ord(password[5]))):
                self.assertEqual(by_window, by_planner)
        self.assertEqual([hex(ord(p[5])) for p, r in zip(passwords, refused) if r],
                         [hex(ord(char)) for char in chars if not " " <= char <= "~"])

    def test_password_length_is_checked_before_keyboard_characters(self):
        keyboard = "Use only letters, digits and symbols of the English (US) keyboard."
        window_length = "Enter a password without line breaks, tabs or other control characters (at most 1024 UTF-8 bytes)."
        planner_length = "Password must be nonempty and contain no control characters (line breaks, tabs)."
        for password in ('', 'a' * 1024, 'a' * 1025, 'ф' * 512, 'ф' * 513,
                         '😀' * 256, '😀' * 257, 'a' * 1024 + '\t'):
            with self.subTest(bytes=len(password.encode())):
                too_long_or_empty = not password or len(password.encode()) > 1024
                characters = not (password.isascii() and password.isprintable())
                actual = window(f'accountErrors("Alex", "alex", {json.dumps(password)}, "", "emaki").password')
                self.assertEqual(actual, window_length if too_long_or_empty else keyboard if characters else '')
                config = {'mode': 'erase', 'disk_id': 'x', 'fs': 'btrfs', 'encryption': 'none',
                          'user': {'login': 'alex', 'password': password}}
                if too_long_or_empty or characters:
                    with self.assertRaises(InstallError) as caught:
                        validate_config(config)
                    self.assertEqual(caught.exception.code, Code.BAD_CONFIG)
                    self.assertEqual(str(caught.exception), planner_length if too_long_or_empty else keyboard)
                else:
                    validate_config(config)


class ReviewKeyboardLine(unittest.TestCase):
    def test_the_review_finds_the_planners_keyboard_line_by_its_first_word(self):
        # InstallerView's Review page lifts this line to the top of the plan list.
        self.assertIn('modelData.startsWith("Keyboard: ")', (UI / 'InstallerView.qml').read_text())
        for layouts in (['us'], ['de'], ['cz', 'us'], ['us', 'ru', 'de']):
            with self.subTest(layouts=layouts):
                self.assertTrue(keyboard_summary(layouts, xkb_rules()).startswith('Keyboard: '))


if __name__ == '__main__':
    unittest.main()
