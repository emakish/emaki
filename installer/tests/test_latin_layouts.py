from pathlib import Path
import re
import shutil
import string
import subprocess
import unittest

from emaki_installer.latin_layouts import LATIN_LAYOUTS, LATIN_VARIANTS, is_latin

RULES = Path('/usr/share/X11/xkb/rules/evdev.lst')


def first_level(layout, variant=''):
    """Level-1 keysyms of the first group, or None when the layout does not compile."""
    argv = ['xkbcli', 'compile-keymap', '--layout', layout]
    if variant:
        argv += ['--variant', variant]
    run = subprocess.run(argv, capture_output=True, text=True)
    if run.returncode:
        return None
    found = set()
    for key in re.finditer(r'key\s+<\w+>\s*\{(.*?)\};', run.stdout, re.S):
        first = (re.search(r'symbols\[(?:Group)?1\]\s*=\s*\[\s*([^,\]\s]+)', key.group(1))
                 or re.search(r'\[\s*([^,\]\s]+)', key.group(1)))
        if first:
            found.add(first.group(1))
    return found


class LatinLayoutTests(unittest.TestCase):
    def test_known_members(self):
        self.assertEqual(len(LATIN_LAYOUTS), 57)
        for layout in ('fr', 'be', 'cz', 'de', 'us', 'gb', 'it', 'dvorak', 'colemak'):
            self.assertTrue(is_latin(layout), layout)
        # az, epo and tm have a on the home row but lack other Latin letters.
        for layout in ('ru', 'ua', 'gr', 'il', 'az', 'epo', 'tm', 'by', 'custom', ''):
            self.assertFalse(is_latin(layout), layout)

    @unittest.skipUnless(shutil.which('xkbcli') and RULES.is_file(), 'xkbcli or evdev.lst is not installed')
    def test_list_equals_the_layouts_that_type_a_to_z(self):
        letters, codes, section = set(string.ascii_lowercase), [], ''
        for line in RULES.read_text().splitlines():
            if line.startswith('!'):
                section = line[1:].strip()
            elif line.strip() and section == 'layout':
                codes.append(line.split()[0])
        self.assertGreater(len(codes), 50)
        latin = {code for code in codes if letters <= (first_level(code) or set())}
        self.assertEqual(latin, set(LATIN_LAYOUTS))
        for variant in LATIN_VARIANTS:
            self.assertLessEqual(letters, first_level('us', variant), variant)


if __name__ == '__main__':
    unittest.main()
