"""The text console types what the login screen types: the same keys give the same characters.

KEYMAP in /etc/vconsole.conf is render.CONSOLE's map for the first layout: the text console
(Ctrl+Alt+F2, the login without graphics) and the initramfs use it, while the login screen's
niri uses the XKB layout. A password typed by the same keys must come out the same in both.

For every CONSOLE entry, libxkbcommon and `loadkeys -m -u` (systemd-vconsole-setup loads maps in
Unicode mode) are compared on the main block of a 105-key keyboard:
- plain and Shift: every printable character and every dead key;
- AltGr and Shift+AltGr, where the XKB layout's Right Alt is AltGr: every ASCII symbol and the
  euro sign that the layout types only with AltGr, on every key that has it at the lowest such
  level (a repeat on Shift+AltGr is not typed by anyone).
Letters typed with AltGr (Polish ą, Romanian ș) are not compared. KNOWN lists, per entry, the
differences no kbd map avoids; any other difference fails, and so does a listed one that is gone.
Non-Latin entries (ru, ua) are compared with English (US): their console maps type ASCII on the
US keys until the console's own switch key, and a Cyrillic password is not typed there.
"""
import ctypes
import ctypes.util
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from emaki_installer.render import CONSOLE, US_VARIANTS, console_keymap

# Keycodes (evdev) of the main block: digits row, three letter rows, space, and the ISO key
# between left Shift and Z.
KEYS = list(range(2, 14)) + list(range(16, 28)) + list(range(30, 42)) + list(range(43, 54)) + [57, 86]
LEVELS = ('', 'Shift+', 'AltGr+', 'Shift+AltGr+')
# kbd's KT_DEAD values (linux/keyboard.h K_DGRAVE..K_DOGONEK) and the XKB dead keysyms they mean.
KBD_DEAD = ('grave', 'acute', 'circumflex', 'tilde', 'diaeresis', 'cedilla', 'macron', 'breve',
            'abovedot', 'abovering', 'doubleacute', 'caron', 'ogonek')
COMMON = {chr(c) for c in range(0x21, 0x7f)} | {'€'}
# The first layouts whose console map types ASCII on the US keys until switched.
NON_LATIN = {'ru', 'ua'}

# Per CONSOLE entry: its kbd map, and every key on which that map still types something else than
# the XKB layout (a password using that character cannot be typed at the text console the same way).
KNOWN = {
    'at': ('de-latin1', (
        "'~' with AltGr on AltGr+key27: console 'dead tilde'",
    )),
    'ba': ('croat', (
        "key41: XKB '`', console '¸'",
        "Shift+key41: XKB '~', console '¨'",
        "'|' with AltGr on AltGr+key17, AltGr+key86: console '|', None",
        "'€' with AltGr on AltGr+key18: console 'e'",
        "'^' with AltGr on Shift+AltGr+key4: console None",
    )),
    'be': ('be-latin1', (
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'a'",
        "'{' with AltGr on AltGr+key8, AltGr+key10: console None, '{'",
        "'[' with AltGr on AltGr+key9, AltGr+key26: console None, '['",
        "'\\\\' with AltGr on AltGr+key12, AltGr+key86: console None, '\\\\'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'br': ('br-abnt2', (
        "'~' with AltGr on AltGr+key40: console None",
        "'`' with AltGr on Shift+AltGr+key26: console None",
        "'^' with AltGr on Shift+AltGr+key40: console None",
    )),
    'ca': ('cf', (
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'ch': ('de_CH-latin1', (
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
        "']' with AltGr on AltGr+key10, AltGr+key27: console None, ']'",
        "'}' with AltGr on AltGr+key11, AltGr+key43: console None, '}'",
        "'€' with AltGr on AltGr+key18: console '¤'",
    )),
    'colemak': ('colemak', ()),
    'cz': ('cz-qwertz', (
        "Shift+key13: XKB 'dead caron', console 'dead circumflex'",
        "Shift+key41: XKB 'dead abovering', console 'dead grave'",
        "'@' with AltGr on AltGr+key3, AltGr+key47: console 'dead circumflex', '@'",
        "'#' with AltGr on AltGr+key4, AltGr+key45: console 'dead circumflex', '#'",
        "'$' with AltGr on AltGr+key5, AltGr+key39: console 'dead tilde', '$'",
        "'^' with AltGr on AltGr+key7, AltGr+key50: console 'dead cedilla', '^'",
        "'&' with AltGr on AltGr+key8, AltGr+key46: console None, '&'",
        "'*' with AltGr on AltGr+key9, AltGr+key53: console 'dead grave', '*'",
        "'{' with AltGr on AltGr+key10, AltGr+key48: console 'dead acute', '{'",
        "'}' with AltGr on AltGr+key11, AltGr+key49: console 'dead tilde', '}'",
        "'€' with AltGr on AltGr+key18: console None",
        "'[' with AltGr on AltGr+key26, AltGr+key33: console '÷', '['",
        "']' with AltGr on AltGr+key27, AltGr+key34: console '×', ']'",
        "'`' with AltGr on AltGr+key35, AltGr+key41: console '`', None",
    )),
    'de': ('de-latin1', (
        "'~' with AltGr on AltGr+key27: console 'dead tilde'",
    )),
    'dk': ('dk-latin1', (
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'dvorak': ('dvorak', ()),
    'dz': ('fr-pc', (
        "Shift+key41: XKB '³', console '²'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'ee': ('et', (
        "key41: XKB 'dead caron', console 'dead circumflex'",
        "Shift+key5: XKB '¤', console '€'",
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
        "'€' with AltGr on AltGr+key6, AltGr+key18: console None, '€'",
        "'`' with AltGr on AltGr+key13: console 'dead acute'",
        "'|' with AltGr on AltGr+key53, AltGr+key86: console None, '|'",
    )),
    'es': ('es', (
        "'|' with AltGr on AltGr+key2, AltGr+key86: console '|', None",
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'fi': ('fi', ()),
    'fo': ('dk-latin1', (
        "key27: XKB 'ð', console 'dead diaeresis'",
        "Shift+key27: XKB 'Ð', console 'dead circumflex'",
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'fr': ('fr-pc', (
        "Shift+key41: XKB '~', console '²'",
        "'@' with AltGr on AltGr+key11, AltGr+key30: console '@', 'q'",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'gb': ('ie', ()),
    'hr': ('croat', (
        "key41: XKB '`', console '¸'",
        "Shift+key41: XKB '~', console '¨'",
        "'|' with AltGr on AltGr+key17, AltGr+key86: console '|', None",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'hu': ('hu', (
        "'€' with AltGr on AltGr+key22: console 'u'",
        "'>' with AltGr on AltGr+key44, AltGr+key52: console '>', None",
        "'<' with AltGr on AltGr+key50, AltGr+key86: console 'm', '<'",
    )),
    'ie': ('ie', ()),
    'is': ('is-latin1', (
        "key41: XKB 'dead abovering', console '°'",
        "Shift+key40: XKB 'dead acute', console '^'",
        "'€' with AltGr on AltGr+key18: console 'e'",
        "'|' with AltGr on AltGr+key25, AltGr+key86: console 'p', '|'",
        "'^' with AltGr on AltGr+key39: console None",
    )),
    'it': ('it', (
        "'{' with AltGr on AltGr+key8: console '7'",
        "'[' with AltGr on AltGr+key9, AltGr+key26: console '{', '['",
        "']' with AltGr on AltGr+key10, AltGr+key27: console '}', ']'",
        "'}' with AltGr on AltGr+key11: console '~'",
        "'~' with AltGr on AltGr+key13: console 'í'",
        "'@' with AltGr on AltGr+key16, AltGr+key39: console 'q', '@'",
        "'€' with AltGr on AltGr+key18: console '¤'",
    )),
    'jp': ('jp106', ()),
    'latam': ('la-latin1', (
        "'@' with AltGr on AltGr+key3, AltGr+key16: console None, '@'",
        "'~' with AltGr on AltGr+key5, AltGr+key27, AltGr+key39: console None, '~', None",
        "'\\\\' with AltGr on AltGr+key12, AltGr+key86: console '\\\\', None",
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'md': ('ro', (
        "key86: XKB '\\\\', console '<'",
        "Shift+key86: XKB '|', console '>'",
    )),
    'me': ('croat', (
        "key41: XKB '`', console '¸'",
        "Shift+key41: XKB '~', console '¨'",
        "'|' with AltGr on AltGr+key17, AltGr+key86: console '|', None",
        "'€' with AltGr on AltGr+key18: console 'e'",
        "'^' with AltGr on Shift+AltGr+key4: console None",
    )),
    'ml': ('fr-pc', (
        "Shift+key41: XKB '~', console '²'",
        "'@' with AltGr on AltGr+key11, AltGr+key30: console '@', 'q'",
    )),
    'nl': ('nl2', (
        "Shift+key13: XKB 'dead tilde', console '~'",
        "'~' with AltGr on AltGr+key26: console None",
        "'^' with AltGr on Shift+AltGr+key26: console None",
        "'`' with AltGr on Shift+AltGr+key40: console None",
    )),
    'no': ('se-lat6', (
        "Shift+key5: XKB '¤', console '$'",
        "Shift+key13: XKB 'dead grave', console '`'",
        "Shift+key27: XKB 'dead circumflex', console 'dead tilde'",
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'ä'",
        "'$' with AltGr on AltGr+key5: console '<'",
        "'€' with AltGr on AltGr+key18: console 'é'",
    )),
    'pl': ('pl', (
        "'€' with AltGr on AltGr+key6: console None",
    )),
    'pt': ('pt-latin9', (
        "'@' with AltGr on AltGr+key3, AltGr+key16: console '@', 'q'",
    )),
    'ro': ('ro', (
        "key86: XKB '\\\\', console '<'",
        "Shift+key86: XKB '|', console '>'",
    )),
    'ru': ('ru', ()),
    'se': ('fi', ()),
    'si': ('slovene', (
        "'|' with AltGr on AltGr+key17, AltGr+key86: console '|', None",
        "'€' with AltGr on AltGr+key18: console 'e'",
        "'~' with AltGr on Shift+AltGr+key2: console None",
        "'^' with AltGr on Shift+AltGr+key4: console None",
        "'`' with AltGr on Shift+AltGr+key8, Shift+AltGr+key47: console None, None",
    )),
    'tg': ('fr-pc', (
        "'€' with AltGr on AltGr+key18: console 'e'",
    )),
    'tr': ('trq', ()),
    'ua': ('ua-utf', ()),
    'us': ('us', ()),
}

XKB_RULES = Path('/usr/share/X11/xkb/rules/evdev')


def available():
    return bool(ctypes.util.find_library('xkbcommon')) and bool(shutil.which('loadkeys')) and XKB_RULES.is_file()


class Xkb:
    """The characters libxkbcommon gives per key and level (rules evdev, keyboard pc105)."""

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
        self.context = x.xkb_context_new(0)

    def sym(self, state, key):
        name = ctypes.create_string_buffer(64)
        self.x.xkb_keysym_get_name(self.x.xkb_state_key_get_one_sym(state, key + 8), name, 64)
        return name.value.decode()

    def table(self, layout, variant=''):
        x = self.x
        names = self.Names(b'evdev', b'pc105', layout.encode(), variant.encode(), b'')
        keymap = x.xkb_keymap_new_from_names(self.context, ctypes.byref(names), 0)
        assert keymap, layout
        state = x.xkb_state_new(keymap)
        shift = 1 << x.xkb_keymap_mod_get_index(keymap, b'Shift')
        mod5 = 1 << x.xkb_keymap_mod_get_index(keymap, b'Mod5')
        altgr = self.sym(state, 100) == 'ISO_Level3_Shift'
        out = {}
        for level, mask in enumerate((0, shift, mod5, shift | mod5)):
            x.xkb_state_update_mask(state, mask, 0, 0, 0, 0, 0)
            for key in KEYS:
                if level >= 2 and not altgr:
                    out[key, level] = None
                    continue
                name = self.sym(state, key)
                code = x.xkb_state_key_get_utf32(state, key + 8)
                out[key, level] = ('dead ' + name[5:] if name.startswith('dead_') else
                                   chr(code) if code >= 0x20 and code != 0x7f else None)
        x.xkb_state_unref(state)
        x.xkb_keymap_unref(keymap)
        return out


def kbd_table(keymap):
    """The characters a kbd map gives per key and level, as `loadkeys -m -u` compiles it."""
    text = subprocess.run(['loadkeys', '-m', '-u', keymap], capture_output=True, text=True, check=True).stdout
    tables = {name: [int(v, 16) for v in re.findall(r'0x[0-9a-f]+', body)]
              for name, body in re.findall(r'(\w+_map)\[NR_KEYS\] = \{(.*?)\};', text, re.S)}
    order = re.search(r'key_maps\[MAX_NR_KEYMAPS\] = \{(.*?)\};', text, re.S)[1]
    index = [t.strip() for t in order.replace('\n', ' ').split(',') if t.strip()]

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
            if value is None:
                out[key, level] = None
            elif value < 0xf000:
                out[key, level] = chr(value) if value >= 0x20 else None
            else:
                kind, code = (value ^ 0xf000) >> 8, value & 0xff
                out[key, level] = (chr(code) if kind in (0, 11) and code >= 0x20 and code != 0x7f else
                                   'dead ' + KBD_DEAD[code] if kind == 4 and code < len(KBD_DEAD) else
                                   'kbd type %d value %d' % (kind, code) if kind in (4, 13) else None)
    return out


def differences(xkb, console):
    out, seen = [], set()
    for (key, level), char in sorted(xkb.items(), key=lambda item: (item[0][1], item[0][0])):
        if char is None:
            continue
        if level < 2:
            if console[key, level] != char:
                out.append('%skey%d: XKB %r, console %r' % (LEVELS[level], key, char, console[key, level]))
            continue
        # Typed with AltGr: common symbols only, and only those the layout has nowhere else.
        if char in seen or char not in COMMON or any(c == char for (k, lv), c in xkb.items() if lv < 2):
            continue
        seen.add(char)
        # Every key with it at the lowest AltGr level it has (AltGr before Shift+AltGr).
        places = sorted(place for place, c in xkb.items() if c == char and place[1] == level)
        if any(console[place] != char for place in places):
            out.append('%r with AltGr on %s: console %s' % (
                char, ', '.join(LEVELS[lv] + 'key%d' % k for k, lv in places),
                ', '.join(repr(console[place]) for place in places)))
    return out


@unittest.skipUnless(available(), 'libxkbcommon, loadkeys or xkeyboard-config is missing')
class ConsoleKeymapTests(unittest.TestCase):
    def test_the_table_is_the_vetted_one(self):
        self.assertEqual(CONSOLE, {layout: keymap for layout, (keymap, _) in KNOWN.items()})

    def test_console_and_login_screen_type_the_same_characters(self):
        xkb = Xkb()
        for layout, (_, known) in sorted(KNOWN.items()):
            keymap = console_keymap(layout)
            with self.subTest(layout=layout, keymap=keymap):
                if layout in NON_LATIN:
                    table = xkb.table('us')
                elif layout in US_VARIANTS:
                    table = xkb.table('us', layout)
                else:
                    table = xkb.table(layout)
                self.assertEqual(differences(table, kbd_table(keymap)), list(known))


if __name__ == '__main__':
    unittest.main()
