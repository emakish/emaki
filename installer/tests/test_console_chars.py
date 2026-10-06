"""Which password characters the text console types with the same keys as the login screen.

latin_layouts.CONSOLE_CHARS is generated here and checked against what the tools give now:
- the login and lock screens type with libxkbcommon: the XKB layout (rules evdev, pc105) and,
  for dead keys, its compose table (the en_US.UTF-8 one; C and POSIX give the same sequences);
- the text console types with render.console_keymap's kbd map as `loadkeys -m -u` compiles it,
  dead keys as the kernel combines them (drivers/tty/vt/keyboard.c: handle_diacr) with the map's
  own compose table, or the kernel's built-in one (kbd's defkeymap) when the map has none,
  because loadkeys leaves the kernel's table alone then.

How a person types a character in a layout, on the main block of a 105-key keyboard: on every
key that has it at its lowest tier (without AltGr, that is plain or Shift; then AltGr; then
Shift+AltGr), at that key's lower level. A character on no key is typed as a dead key (placed
the same way) followed by a key without AltGr or by space; a dead key pressed twice only when
nothing else gives the character. The console types the character the same when every such way
gives exactly that character there.

Run this file with --write to regenerate the table after a change of render.CONSOLE or of
xkeyboard-config/kbd; the test fails until the committed table equals the generated one.
"""
import ast
import ctypes
import ctypes.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

from emaki_installer.latin_layouts import CONSOLE_CHARS
from emaki_installer.planner import console_unsafe_chars
from emaki_installer.render import CONSOLE, US_VARIANTS, console_keymap

# Keycodes (evdev) of the main block: digits row, three letter rows, space, the ISO key between
# left Shift and Z (the keys of test_console_keymaps.py), and the two extra keys of Japanese and
# Brazilian keyboards (ro/ABNT2 slash, yen), which type \ _ | in Japanese.
KEYS = list(range(2, 14)) + list(range(16, 28)) + list(range(30, 42)) + list(range(43, 54)) + [57, 86, 89, 124]
ASCII = ''.join(chr(c) for c in range(0x20, 0x7f))
# The kernel's ret_diacr: the character a KT_DEAD key (by its value) leaves for the next key.
RET_DIACR = '`\'^~",_U.*=cki#o!?+-)(:n;$@'
# Console maps that type the English (US) keys without AltGr (ru and ua-utf until switched).
US_POSITIONS = ('us', 'ru', 'ua-utf')
XKB_ROOT = Path('/usr/share/X11/xkb')
RULES = XKB_ROOT / 'rules/evdev.lst'
LATIN_LAYOUTS_PY = Path(__file__).resolve().parents[1] / 'emaki_installer/latin_layouts.py'


def available():
    return (bool(ctypes.util.find_library('xkbcommon')) and bool(shutil.which('loadkeys'))
            and RULES.is_file() and Path('/usr/share/X11/locale/en_US.UTF-8/Compose').is_file())


def tier(level):
    return 0 if level < 2 else level - 1


class Xkb:
    """Per key and level: ('char', character, keysym), ('dead', keysym) or None."""

    class Names(ctypes.Structure):
        # struct xkb_rule_names, in its order.
        _fields_ = [(n, ctypes.c_char_p) for n in ('rules', 'keyboard', 'layout', 'variant', 'options')]

    def __init__(self):
        x = self.x = ctypes.CDLL(ctypes.util.find_library('xkbcommon'))
        x.xkb_context_new.restype = ctypes.c_void_p
        x.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        x.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        x.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        x.xkb_state_new.restype = ctypes.c_void_p
        x.xkb_state_new.argtypes = [ctypes.c_void_p]
        x.xkb_state_unref.argtypes = [ctypes.c_void_p]
        x.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        x.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
        x.xkb_state_update_mask.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 6
        x.xkb_state_key_get_utf32.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        x.xkb_state_key_get_utf32.restype = ctypes.c_uint32
        x.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        x.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
        x.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        x.xkb_compose_table_new_from_locale.restype = ctypes.c_void_p
        x.xkb_compose_table_new_from_locale.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.xkb_compose_state_new.restype = ctypes.c_void_p
        x.xkb_compose_state_new.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.xkb_compose_state_feed.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        x.xkb_compose_state_reset.argtypes = [ctypes.c_void_p]
        x.xkb_compose_state_get_status.argtypes = [ctypes.c_void_p]
        x.xkb_compose_state_get_utf8.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        x.xkb_context_include_path_append.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        x.xkb_context_set_log_level.argtypes = [ctypes.c_void_p, ctypes.c_int]
        # Only the system's XKB data: no ~/.config/xkb, no XKB_DEFAULT_* from the environment.
        self.context = x.xkb_context_new(1 | 2)
        x.xkb_context_include_path_append(self.context, str(XKB_ROOT).encode())
        x.xkb_context_set_log_level(self.context, 10)  # XKB_LOG_LEVEL_CRITICAL: 'custom' has no file
        table = x.xkb_compose_table_new_from_locale(self.context, b'en_US.UTF-8', 0)
        assert table, 'no compose table for en_US.UTF-8'
        self.compose_state = x.xkb_compose_state_new(table, 0)

    def name(self, sym):
        buffer = ctypes.create_string_buffer(64)
        self.x.xkb_keysym_get_name(sym, buffer, 64)
        return buffer.value.decode()

    def compose(self, syms):
        """The text a sequence of keysyms composes, or None."""
        x, state = self.x, self.compose_state
        x.xkb_compose_state_reset(state)
        for sym in syms:
            x.xkb_compose_state_feed(state, sym)
        if x.xkb_compose_state_get_status(state) != 2:  # XKB_COMPOSE_COMPOSED
            return None
        buffer = ctypes.create_string_buffer(64)
        x.xkb_compose_state_get_utf8(state, buffer, 64)
        return buffer.value.decode()

    def places(self, layout, variant=''):
        x = self.x
        names = self.Names(b'evdev', b'pc105', layout.encode(), variant.encode(), b'')
        keymap = x.xkb_keymap_new_from_names(self.context, ctypes.byref(names), 0)
        if not keymap:
            return None
        state = x.xkb_state_new(keymap)
        shift = 1 << x.xkb_keymap_mod_get_index(keymap, b'Shift')
        mod5 = 1 << x.xkb_keymap_mod_get_index(keymap, b'Mod5')
        # Right Alt (evdev 100) is AltGr only where the layout makes it ISO_Level3_Shift.
        altgr = self.name(x.xkb_state_key_get_one_sym(state, 100 + 8)) == 'ISO_Level3_Shift'
        out = {}
        for level, mask in enumerate((0, shift, mod5, shift | mod5)):
            x.xkb_state_update_mask(state, mask, 0, 0, 0, 0, 0)
            for key in KEYS:
                out[key, level] = None
                if level >= 2 and not altgr:
                    continue
                sym = x.xkb_state_key_get_one_sym(state, key + 8)
                code = x.xkb_state_key_get_utf32(state, key + 8)
                if self.name(sym).startswith('dead_'):
                    out[key, level] = ('dead', sym)
                elif code >= 0x20 and code != 0x7f:
                    out[key, level] = ('char', chr(code), sym)
        x.xkb_state_unref(state)
        x.xkb_keymap_unref(keymap)
        return out


def ways(xkb, layout, variant=''):
    """character -> the key sequences (tuples of places) a person types it with; None when the
    layout does not compile."""
    places = xkb.places(layout, variant)
    if places is None:
        return None
    found, deads = {}, {}
    for place, value in places.items():
        if value:
            (found if value[0] == 'char' else deads).setdefault(value[1], []).append(place)

    def lowest(spots):
        # The lowest tier, and on each key its lower level there (nobody adds Shift to get the
        # same thing: Icelandic has its dead acute on plain and Shift of one key).
        best, keys = min(tier(level) for _, level in spots), {}
        for key, level in sorted(spots):
            if tier(level) == best:
                keys.setdefault(key, level)
        return sorted(keys.items())
    result = {char: [(p,) for p in lowest(spots)] for char, spots in found.items()}
    bases = [(places[p][2], p) for spots in found.values() for p in lowest(spots) if tier(p[1]) == 0]
    composed, doubled = {}, {}
    deads = {sym: lowest(spots) for sym, spots in deads.items()}
    for sym, spots in sorted(deads.items()):
        for base_sym, base in bases:
            char = xkb.compose([sym, base_sym])
            if char and len(char) == 1 and char not in result:
                composed.setdefault(char, []).extend((spot, base) for spot in spots)
        char = xkb.compose([sym, sym])
        if char and len(char) == 1 and char not in result:
            doubled.setdefault(char, []).extend((spot, spot) for spot in spots)
    for char, sequences in list(composed.items()) + list(doubled.items()):
        if char in result:
            continue
        # The dead keys at the lowest tier that give the character.
        best = min(tier(sequence[0][1]) for sequence in sequences)
        result[char] = [sequence for sequence in sequences if tier(sequence[0][1]) == best]
    return result


def console_places(keymap):
    """Per key and level: ('char', character), ('dead', diacritic) or None; and the compose table."""
    text = subprocess.run(['loadkeys', '-m', '-u', keymap], capture_output=True, text=True, check=True).stdout
    tables = {name: [int(v, 16) for v in re.findall(r'0x[0-9a-f]+', body)]
              for name, body in re.findall(r'(\w+_map)\[NR_KEYS\] = \{(.*?)\};', text, re.S)}
    order = re.search(r'key_maps\[MAX_NR_KEYMAPS\] = \{(.*?)\};', text, re.S)[1]
    index = [t.strip() for t in order.replace('\n', ' ').split(',') if t.strip()]
    accents = {}
    body = re.search(r'accent_table\[MAX_DIACR\] = \{(.*?)\n\};', text, re.S)
    for diacr, base, result in re.findall(r"\{('(?:[^'\\]|\\.)*'), ('(?:[^'\\]|\\.)*'), (0x[0-9a-f]+)\}",
                                          body[1] if body else ''):
        accents[c_char(diacr), c_char(base)] = chr(int(result, 16))

    def modifier(key):
        # The modifier bit a key sets (KT_SHIFT), or 0.
        value = tables['plain_map'][key] ^ 0xf000
        return 1 << (value & 0xff) if value >> 8 == 7 else 0
    shift, altgr = modifier(42), modifier(100)
    out = {}
    for level, mods in enumerate((0, shift, altgr, shift | altgr)):
        values = tables.get(index[mods] if mods < len(index) else '0') if level < 2 or altgr else None
        for key in KEYS:
            value = values[key] if values else None
            out[key, level] = None
            if value is None:
                continue
            if value < 0xf000:
                out[key, level] = ('char', chr(value)) if value >= 0x20 else None
                continue
            kind, code = (value ^ 0xf000) >> 8, value & 0xff
            if kind in (0, 11) and code >= 0x20 and code != 0x7f:  # KT_LATIN, KT_LETTER
                out[key, level] = ('char', chr(code))
            elif kind == 4 and code < len(RET_DIACR):  # KT_DEAD
                out[key, level] = ('dead', RET_DIACR[code])
            elif kind == 13:  # KT_DEAD2: the diacritic itself
                out[key, level] = ('dead', chr(code))
    return out, accents


def c_char(token):
    inner = token[1:-1]
    if inner.startswith('\\'):
        return chr(int(inner[1:], 8)) if inner[1:2].isdigit() else inner[1:]
    return inner


def console_types(places, accents, sequence):
    """What the console puts out for a key sequence (keyboard.c: k_dead, k_unicode, handle_diacr)."""
    out, diacr = [], None

    def handle(d, ch):
        if (d, ch) in accents:
            return accents[d, ch]
        if ch == ' ' or ch == d:
            return d
        out.append(d)
        return ch
    for place in sequence:
        value = places[place]
        if value is None:
            return None
        if value[0] == 'dead':
            diacr = handle(diacr, value[1]) if diacr else value[1]
        else:
            out.append(handle(diacr, value[1]) if diacr else value[1])
            diacr = None
    return None if diacr else ''.join(out)


class Generator:
    def __init__(self):
        self.xkb = Xkb()
        self.maps = {}
        self.kernel = console_places('defkeymap')[1]

    def console(self, keymap):
        if keymap not in self.maps:
            places, accents = console_places(keymap)
            self.maps[keymap] = places, accents or self.kernel
        return self.maps[keymap]

    def record(self, name):
        """latin_layouts.CONSOLE_CHARS's (us, differs, more, moved, absent) for one offered layout;
        None when it does not compile."""
        layout, variant = ('us', name) if name in US_VARIANTS else (name, '')
        typed = ways(self.xkb, layout, variant)
        if typed is None:
            return None
        keymap = console_keymap(name)
        places, accents = self.console(keymap)
        same = {c for c, sequences in typed.items()
                if all(console_types(places, accents, s) == c for s in sequences)}
        us = self.console('us')
        # Behind a first layout whose console types the English (US) keys: AltGr counts as moved,
        # because us, ru and ua-utf differ there.
        moved = ''.join(c for c in ASCII if c in typed and not all(
            all(tier(p[1]) == 0 for p in s) and console_types(*us, s) == c for s in typed[c]))
        return (keymap in US_POSITIONS, ''.join(c for c in ASCII if c not in same),
                ''.join(sorted(c for c in same if c not in ASCII)), moved,
                ''.join(c for c in ASCII if c not in typed))

    def table(self, names):
        records = {name: self.record(name) for name in sorted(names)}
        return {name: record for name, record in records.items() if record}


def offered():
    """Every layout the installer window offers (evdev.lst), the planner's US variants and CONSOLE."""
    codes, section = set(), ''
    for line in RULES.read_text().splitlines():
        if line.startswith('!'):
            section = line[1:].strip()
        elif line.strip() and section == 'layout':
            codes.add(line.split()[0])
    return codes | set(US_VARIANTS) | set(CONSOLE)


def literal(table):
    lines = ['CONSOLE_CHARS = {']
    for name, (us, differs, more, moved, absent) in sorted(table.items()):
        lines.append(f'    {name!r}: ({us!r}, {differs!r},')
        lines.append(f'        {more!r},')
        lines.append(f'        {moved!r}, {absent!r}),')
    lines.append('}')
    return '\n'.join(lines) + '\n'


BEGIN = '# BEGIN generated by installer/tests/test_console_chars.py --write\n'
END = '# END generated\n'


def write():
    text = LATIN_LAYOUTS_PY.read_text()
    head, rest = text.split(BEGIN)
    _, tail = rest.split(END)
    LATIN_LAYOUTS_PY.write_text(head + BEGIN + literal(Generator().table(offered())) + END + tail)


@unittest.skipUnless(available(), 'libxkbcommon, its compose data, loadkeys or xkeyboard-config is missing')
class ConsoleCharsTableTests(unittest.TestCase):
    def test_the_committed_table_is_the_generated_one(self):
        generated = Generator().table(offered())
        self.assertEqual(sorted(generated), sorted(CONSOLE_CHARS))
        for name, record in sorted(generated.items()):
            with self.subTest(layout=name):
                self.assertEqual(CONSOLE_CHARS[name], record)

    def test_every_layout_the_window_offers_has_a_record(self):
        # 'custom' has no symbols file and compiles to no keymap; console_unsafe_chars then
        # treats it as unknown.
        missing = offered() - set(CONSOLE_CHARS)
        self.assertEqual(missing, {'custom'} & offered())
        self.assertGreaterEqual(len(CONSOLE_CHARS), 90)

    def test_us_position_maps_type_the_english_keys_without_altgr(self):
        # `moved` is measured on us; ru and ua-utf type the same until their own switch key.
        us = console_places('us')[0]
        for keymap in US_POSITIONS:
            places = console_places(keymap)[0]
            with self.subTest(keymap=keymap):
                self.assertEqual({p: v for p, v in places.items() if p[1] < 2}, {p: v for p, v in us.items() if p[1] < 2})
        for name, record in CONSOLE_CHARS.items():
            self.assertEqual(record[0], console_keymap(name) in US_POSITIONS, name)

    def test_every_difference_the_key_comparison_lists_is_unsafe(self):
        # test_console_keymaps.KNOWN, the vetted key-by-key comparison: each character it lists
        # as typed differently is one the warning names. Its dead-key rows are compared by what
        # they compose here (cz's console composes carons with its circumflex key).
        from test_console_keymaps import KNOWN
        for layout, (_, rows) in KNOWN.items():
            for row in rows:
                char = ast.literal_eval(re.match(r"(?:(?:Shift\+)?key\d+: XKB )?('(?:[^'\\]|\\.)*'|\"[^\"]*\")", row)[1])
                if char.startswith('dead '):
                    continue
                with self.subTest(layout=layout, row=row):
                    self.assertEqual(console_unsafe_chars([layout], char), char)


class ConsoleUnsafeCharsTests(unittest.TestCase):
    """What the window warns about, from the committed table (latin_layouts.CONSOLE_CHARS)."""

    def check(self, layouts, password, unsafe):
        self.assertEqual(console_unsafe_chars(layouts, password), unsafe, (layouts, password))

    def test_latin_layouts_with_their_own_console_map(self):
        self.check(['de'], 'Grüße-aus-Köln', '')
        self.check(['de'], 'café€', '')  # dead acute then e; AltGr+E
        self.check(['de', 'us'], 'a~b', '~')  # AltGr++ is a dead tilde on the console
        self.check(['fr'], 'été@~', '@~')
        self.check(['fr'], 'œuvre', 'œ')
        self.check(['pl'], 'zażółć', '')
        self.check(['pl'], '5€', '€')
        self.check(['cz'], 'kůň@', '@')  # the console composes ň with its own dead key there

    def test_layouts_without_a_console_map_of_their_own(self):
        self.check(['lv'], 'labdien!', '')
        self.check(['lv'], 'ābols', 'ā')
        self.check(['sk'], 'zima1', '1z')  # QWERTZ and digits on Shift; the console types US keys

    def test_a_password_typed_in_another_layout(self):
        self.check(['us', 'ru'], 'пароль-1', 'алопрь')
        self.check(['us', 'de'], 'zä', 'ä')
        self.check(['ru', 'us'], 'Secret-2026', '')
        self.check(['ru', 'us'], 'Пароль', 'Палорь')
        # Typed in Russian, where the login screen starts, the full stop is on another key.
        self.check(['ru', 'us'], 'v1.0', '.')
        self.check(['ru', 'de'], 'zebra', 'z')

    def test_unknown_layouts_count_only_ascii_as_safe(self):
        self.check(['zz'], 'plain ASCII ~', '')
        self.check(['zz'], 'naïve', 'ï')
        self.check(['ru', 'zz'], 'abc', '')

    def test_each_character_once_in_code_point_order(self):
        self.check(['us'], '€é€éa😀', 'é€😀')
        self.check(['us'], '', '')


if __name__ == '__main__' and sys.argv[1:] == ['--write']:
    write()
    sys.exit(0)


if __name__ == '__main__':
    unittest.main()
