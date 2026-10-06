"""The window's console warning (Protocol.js consoleUnsafeChars) is the planner's
console_unsafe_chars on the table the worker's hello carries."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

UI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI.parent))
from emaki_installer.latin_layouts import CONSOLE_CHARS  # noqa: E402
from emaki_installer.planner import console_unsafe_chars  # noqa: E402

# Evaluates consoleUnsafeChars for every case with the window's own Protocol.js; the cases and
# the table travel in a file, not in argv.
EVALUATE = ('const vm = require("node:vm"); const fs = require("node:fs"); const context = vm.createContext({});'
            'vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);'
            'const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));'
            'process.stdout.write(JSON.stringify(input.cases.map(c => context.consoleUnsafeChars(input.table, c[0], c[1]))));')


def cases():
    """Every record as the first layout, alone and before every other kind of record."""
    samples = ['Secret-2026', 'v1.0 ~@#{}|\\', 'пароль', 'Grüße €', 'kůň ąę', 'ñ œ ß ð', '😀 naïve']
    firsts = sorted(CONSOLE_CHARS) + ['zz']
    seconds = ['us', 'de', 'ru', 'fr', 'gr', 'jp', 'sk', 'zz']
    result = []
    for first in firsts:
        record = CONSOLE_CHARS.get(first, (False, '', '', '', ''))
        # What this layout types the same, and what it does not, as passwords.
        own = [record[2], ''.join(chr(c) for c in range(0x20, 0x7f))]
        for password in samples + own:
            result.append([[first], password])
            for second in seconds:
                if second != first:
                    result.append([[first, second], password])
    return result


@unittest.skipUnless(shutil.which('node'), 'node is unavailable')
class ConsoleWarning(unittest.TestCase):
    def test_the_window_names_the_planners_characters(self):
        table = {name: list(record) for name, record in CONSOLE_CHARS.items()}
        checks = cases()
        with tempfile.TemporaryDirectory(prefix='emaki-console-chars-') as temporary:
            path = Path(temporary) / 'input.json'
            path.write_text(json.dumps({'table': table, 'cases': checks}, ensure_ascii=False))
            result = subprocess.run(['node', '-e', EVALUATE, str(UI / 'Protocol.js'), str(path)],
                                    check=True, capture_output=True, text=True, timeout=60)
        window = json.loads(result.stdout)
        self.assertEqual(len(window), len(checks))
        self.assertGreater(len(checks), 5000)
        unsafe = 0
        for (layouts, password), seen in zip(checks, window):
            expected = console_unsafe_chars(layouts, password)
            unsafe += bool(expected)
            self.assertEqual(seen, expected, (layouts, password))
        # Both outcomes occur; a check that always agreed on "" would prove nothing.
        self.assertGreater(unsafe, 1000)
        self.assertLess(unsafe, len(checks) - 1000)


def window(expression):
    """Evaluate an expression with the window's own Protocol.js."""
    script = ('const vm = require("node:vm"); const fs = require("node:fs"); const context = vm.createContext({});'
              'vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);'
              'process.stdout.write(JSON.stringify(vm.runInContext(process.argv[2], context)));')
    result = subprocess.run(['node', '-e', script, str(UI / 'Protocol.js'), expression],
                            check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def unseen(char):
    """A character a list cannot show as itself: not the space, and a separator, a format or
    control character, or an unassigned or private-use code point."""
    return char != ' ' and unicodedata.category(char) in ('Zs', 'Zl', 'Zp', 'Cc', 'Cf', 'Cn', 'Co')


def typed_characters():
    """Every character an offered layout types, as installer/tests/test_console_chars.py counts
    how a person types; None without libxkbcommon or xkeyboard-config."""
    spec = importlib.util.spec_from_file_location('console_chars_generator', UI.parent / 'tests/test_console_chars.py')
    generator = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(UI.parent / 'tests'))
    spec.loader.exec_module(generator)
    if not generator.available():
        return None
    from emaki_installer.render import US_VARIANTS
    xkb, chars = generator.Xkb(), set()
    for name in generator.offered():
        layout, variant = ('us', name) if name in US_VARIANTS else (name, '')
        chars.update(generator.ways(xkb, layout, variant) or {})
    return chars


@unittest.skipUnless(shutil.which('node'), 'node is unavailable')
class UnseenCharacters(unittest.TestCase):
    def test_the_window_names_what_a_list_cannot_show(self):
        names = {int(code): name for code, name in window('UNSEEN_NAMES').items()}
        # The names are Unicode's (a tab has none there); unassigned and private-use code points have none.
        for code, name in names.items():
            with self.subTest(code=hex(code)):
                self.assertTrue(unseen(chr(code)))
                self.assertEqual(name, 'tab' if code == 9 else unicodedata.name(chr(code), '').lower())
        typed = typed_characters()
        if typed is None:
            self.skipTest('libxkbcommon or xkeyboard-config is missing')
        # Every such character a layout the window offers types has its entry.
        self.assertEqual(sorted(hex(ord(c)) for c in typed if unseen(c) and ord(c) not in names), [])
        self.assertGreater(len([c for c in typed if unseen(c)]), 10)

    def test_the_console_sentence_names_them(self):
        table = {name: list(record) for name, record in CONSOLE_CHARS.items()}
        sentence = window('consoleWarning(' + json.dumps(table) + ', ["us"], "Pa\\u20ac\\u0444\\u00a0\\t\\u200d1")')
        self.assertEqual(sentence, 'The text console types these characters differently: U+0009 tab U+00A0 no-break space '
                         'ф U+200D zero width joiner €. Choose a password without them if you may need the console.')


if __name__ == '__main__':
    unittest.main()
