#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installed menu's face: committed fonts, mode choice and real GRUB 2.16 output.

The faces and their rebuild notes are in grub/fonts/README.md.

Set EMAKI_GRUB_TOOLS to an extracted GRUB package root when GRUB is not installed;
without its 00_header the generated-menu checks are skipped.
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_installer import secureboot
sys.modules['emaki_boot.secureboot'] = secureboot
from emaki_boot import refresh

PACKAGE = Path(os.environ.get('EMAKI_GRUB_TOOLS', '/'))
if PACKAGE == Path('/') and not shutil.which('grub-mkimage'):
    PACKAGE = Path('/tmp/emaki-grub-tools')
FONTS = ROOT / 'grub/fonts'
UUID = 'abcdef01-2345-6789-abcd-ef0123456789'


def pf2(path):
    """Section values and glyph metrics (width, height, x, y, device width) of a PF2 file."""
    data = path.read_bytes()
    assert data[:12] == b'FILE\0\0\0\x04PFF2', path
    offset, sections = 12, {}
    while offset < len(data):
        name, size = data[offset:offset + 4], struct.unpack('>I', data[offset + 4:offset + 8])[0]
        if name == b'DATA':
            break
        sections[name.decode()] = data[offset + 8:offset + 8 + size]
        offset += 8 + size
    index = sections.pop('CHIX')
    glyphs = {}
    for start in range(0, len(index), 9):
        code, _, where = struct.unpack('>IBI', index[start:start + 9])
        glyphs[code] = struct.unpack('>HHhhh', data[where:where + 10])
    values = {key: struct.unpack('>H', value)[0] if len(value) == 2 else value.rstrip(b'\0').decode()
              for key, value in sections.items()}
    return values, glyphs


def screen_info(kind, width, height):
    info = bytearray(0x40)
    info[0x0f] = kind
    struct.pack_into('<HHH', info, 0x12, width, height, 32)
    return bytes(info)


class Faces(unittest.TestCase):
    def test_committed_faces_match_the_cell_table(self):
        self.assertEqual(sorted(path.name for path in FONTS.glob('*.pf2')),
                         sorted(f'menu-{size}.pf2' for size, _, _ in boot.MENU_FONTS))
        for size, width, height in boot.MENU_FONTS:
            with self.subTest(size=size):
                values, glyphs = pf2(FONTS / f'menu-{size}.pf2')
                self.assertEqual(values['NAME'], f'DejaVu Sans Mono Regular {size}')
                # gfxterm: cell width is the widest printable ASCII advance, height is MAXH.
                self.assertEqual(max(glyphs[code][4] for code in range(32, 127)), width)
                self.assertEqual(values['MAXH'], height)
                # gfxterm puts the baseline ASCE below the cell top: the frame's vertical
                # line must fill the cell exactly, or the menu border shows gaps.
                _, line, _, bottom, _ = glyphs[0x2502]
                self.assertEqual((line, line + bottom, -bottom), (height, values['ASCE'], values['DESC']))
                # Menu frame, scroll and truncation marks, and non-English snapshot notes.
                for code in (*range(32, 127), 0x2190, 0x2191, 0x2192, 0x2193, *range(0x2500, 0x2570),
                             0x00e9, 0x0436, 0x044f, 0x03a9):
                    self.assertIn(code, glyphs, hex(code))
        self.assertEqual([size for size, _, _ in boot.MENU_FONTS],
                         sorted((size for size, _, _ in boot.MENU_FONTS), reverse=True))

    def test_face_follows_the_mode(self):
        cases = {(640, 480): None, (800, 600): None, (1024, 768): None, (1280, 800): None,
                 (1366, 768): None, (1280, 1024): None, (1080, 1920): None,
                 (1600, 900): 24, (1920, 1080): 24, (1920, 1200): 32, (2256, 1504): 32,
                 (2560, 1440): 32, (2560, 1600): 32, (2880, 1800): 46,
                 (3000, 2000): 46, (3840, 2160): 46, (5120, 2880): 46}
        for mode, size in cases.items():
            with self.subTest(mode=mode):
                expected = None if size is None else f'/usr/share/emaki/grub/fonts/menu-{size}.pf2'
                self.assertEqual(boot.menu_font(mode), expected)
                if size is not None:
                    _, width, height = next(face for face in boot.MENU_FONTS if face[0] == size)
                    self.assertGreaterEqual(mode[0] // width, 100)
                    self.assertGreaterEqual(mode[1] // height, 30)
        self.assertIsNone(boot.menu_font(None))

    def test_boot_framebuffer_reads_only_an_efi_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data'
            for blob, expected in ((screen_info(0x70, 2560, 1600), (2560, 1600)),
                                   (screen_info(0x70, 1024, 768) + b'\0' * 4000, (1024, 768)),
                                   (screen_info(0x23, 1920, 1080), None),  # VESA, not EFI
                                   (screen_info(0x70, 0, 0), None),
                                   (screen_info(0x70, 2560, 1600)[:0x15], None)):
                with self.subTest(blob=blob[:0x18].hex()):
                    path.write_bytes(blob)
                    self.assertEqual(boot.boot_framebuffer(path), expected)
            self.assertIsNone(boot.boot_framebuffer(Path(directory) / 'missing'))

    def test_generated_menu_font_reads_the_header(self):
        self.assertIsNone(boot.generated_menu_font('if loadfont $font ; then\n  set gfxmode=auto\nfi\n'))
        for prefix in ('', '/@'):
            self.assertEqual(boot.generated_menu_font(
                f'if loadfont {prefix}/usr/share/emaki/grub/fonts/menu-32.pf2 ; then\n'),
                '/usr/share/emaki/grub/fonts/menu-32.pf2')
        for unknown in ('', 'if loadfont /boot/grub/fonts/custom.pf2 ; then\n',
                        'if loadfont $font ; then\nif loadfont $font ; then\n'):
            self.assertEqual(boot.generated_menu_font(unknown), '')


def tool(name):
    for candidate in (PACKAGE / 'usr/bin' / name, PACKAGE / 'etc/grub.d' / name):
        if candidate.is_file():
            return candidate
    raise unittest.SkipTest('Missing real GRUB file: ' + name)


class GeneratedHeader(unittest.TestCase):
    """Run the packaged 00_header with Emaki's defaults; only disk probing is a fixture."""

    def setUp(self):
        self.header = tool('00_header')
        self.library = PACKAGE / 'usr/share/grub'
        if not (self.library / 'grub-mkconfig_lib').is_file():
            self.skipTest('Missing real grub-mkconfig_lib')
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='menu-font-', dir=evidence)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        probe = self.work / 'grub-probe'
        probe.write_text('#!/bin/sh\nfor arg; do case $arg in\n'
                         '  --target=device|-t\\ device) echo /dev/fixture2; exit 0 ;;\n'
                         '  --target=fs) echo btrfs; exit 0 ;;\n'
                         f'  --target=fs_uuid) echo {UUID}; exit 0 ;;\n'
                         '  --target=partmap) echo gpt; exit 0 ;;\n'
                         '  --target=abstraction|--target=hints_string|--target=compatibility_hint) exit 0 ;;\n'
                         '  --target=drive) echo "(hd0,gpt2)"; exit 0 ;;\nesac; done\nexit 0\n')
        relpath = self.work / 'grub-mkrelpath'
        relpath.write_text('#!/bin/sh\necho "/@$1"\n')
        for script in (probe, relpath):
            script.chmod(0o755)

    def header_for(self, font):
        defaults = refresh.assignments((ROOT / 'grub/defaults.cfg').read_text())
        env = {'PATH': os.environ['PATH'], 'pkgdatadir': str(self.library),
               'grub_probe': str(self.work / 'grub-probe'), 'grub_mkrelpath': str(self.work / 'grub-mkrelpath'),
               'GRUB_FS': 'btrfs', 'GRUB_DEVICE': '/dev/fixture2', 'GRUB_DEVICE_UUID': UUID,
               **{key: value for key, value in defaults.items() if key != 'GRUB_BACKGROUND'}}
        if font:
            env['GRUB_FONT'] = font
        result = subprocess.run(['sh', str(self.header)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.work / ('menu-' + (Path(font).stem if font else 'default') + '.cfg')
        path.write_text(result.stdout)
        check = subprocess.run([str(tool('grub-script-check')), str(path)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        return result.stdout

    def test_only_the_face_changes_in_the_generated_menu(self):
        default = self.header_for(None)
        self.assertIsNone(boot.generated_menu_font(default))
        for size, _, _ in boot.MENU_FONTS:
            font = f'{FONTS}/menu-{size}.pf2'
            with self.subTest(size=size):
                text = self.header_for(font)
                self.assertIn(f'if loadfont /@{font} ; then\n', text)
                self.assertEqual(boot.generated_menu_font(text.replace(str(FONTS), boot.MENU_FONT_DIR)),
                                 f'{boot.MENU_FONT_DIR}/menu-{size}.pf2')
                # Mode, terminal and timeouts are the same lines in both menus.
                for line in ('  set gfxmode=1024x768,800x600,640x480,auto\n', 'terminal_output gfxterm\n',
                             '  set timeout_style=menu\n', '  set timeout=5\n', '   set default="0"\n'):
                    self.assertEqual(text.count(line), default.count(line), line)
                    self.assertGreater(default.count(line), 0, line)
                kept = [line for line in default.splitlines() if 'font' not in line]
                self.assertTrue(set(kept) <= set(text.splitlines()))


    def test_generated_header_retries_a_stock_face_without_changing_modes(self):
        for size, width, height in boot.MENU_FONTS:
            raw = self.header_for(f'{FONTS}/menu-{size}.pf2').replace(str(FONTS), boot.MENU_FONT_DIR)
            safe = boot.menu_font_fallback(raw)
            with self.subTest(size=size):
                self.assertIn(f'set gfxterm_font="DejaVu Sans Mono Regular {size}"', safe)
                self.assertIn('if terminal_output gfxterm; then', safe)
                self.assertIn('  set gfxterm_font="Unifont Regular 16"\n'
                              '  if loadfont unicode; then\n    terminal_output gfxterm\n', safe)
                self.assertEqual(boot.generated_menu_font(safe), boot.generated_menu_font(raw))
                self.assertEqual(boot.menu_font_fallback(safe), safe)
                self.assertIn('set gfxmode=1024x768,800x600,640x480,auto', safe)
                path = self.work / 'safe.cfg'
                path.write_text(safe)
                result = subprocess.run([str(tool('grub-script-check')), str(path)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

        # Execute the emitted retry branches with gfxterm's real minimum-grid rule and
        # the committed 46 px face metrics: each smaller mode needs the stock face.
        raw = self.header_for(f'{FONTS}/menu-46.pf2').replace(str(FONTS), boot.MENU_FONT_DIR)
        safe = boot.menu_font_fallback(raw)
        block = safe[safe.index('set gfxterm_font='):safe.index('\nfi\n', safe.index('set gfxterm_font=')) + 4]
        block = block.replace('set gfxterm_font=', 'gfxterm_font=')
        _, width, height = boot.MENU_FONTS[0]
        for mode in ((3840, 2160), (1024, 768), (800, 600), (640, 480)):
            fits = mode[0] // width >= 40 and mode[1] // height >= 12
            script = ('loadfont() { test "$1" = unicode; }\n'
                      'terminal_output() {\n'
                      '  echo "$gfxterm_font"\n'
                      '  test "$gfxterm_font" = "Unifont Regular 16" || test "$fits" = yes\n'
                      '}\n' + ('fits=yes\n' if fits else 'fits=no\n') + block)
            result = subprocess.run(['sh', '-c', script], text=True, capture_output=True)
            with self.subTest(previous=(3840, 2160), next=mode):
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ['DejaVu Sans Mono Regular 46'] + ([] if fits else ['Unifont Regular 16'])
                self.assertEqual(result.stdout.splitlines(), expected)

    def test_custom_fonts_and_text_terminals_are_preserved(self):
        default = self.header_for(None)
        self.assertEqual(boot.menu_font_fallback(default), default)
        raw = self.header_for(f'{FONTS}/menu-46.pf2').replace(str(FONTS), boot.MENU_FONT_DIR)
        for terminal in ('console', 'serial', 'serial console'):
            text = raw.replace('terminal_output gfxterm', 'terminal_output ' + terminal)
            self.assertEqual(boot.menu_font_fallback(text), text)
        custom = raw.replace(boot.MENU_FONT_DIR + '/menu-46.pf2', '/local/font.pf2')
        self.assertEqual(boot.menu_font_fallback(custom), custom)

    def test_generated_menu_exports_the_effective_face_to_snapshot_configfiles(self):
        for size, _, _ in boot.MENU_FONTS:
            raw = self.header_for(f'{FONTS}/menu-{size}.pf2').replace(str(FONTS), boot.MENU_FONT_DIR)
            generated = boot.menu_font_fallback(raw)
            self.assertEqual(generated.count('\nexport gfxterm_font\n'), 1)
            start = generated.index('set gfxterm_font=')
            end = generated.index('\nexport gfxterm_font\n', start) + len('\nexport gfxterm_font\n')
            block = generated[start:end]
            # Both assignments precede export: configfile inherits the effective
            # face even when the preferred face did not fit this boot's mode.
            self.assertTrue(block.endswith('fi\nexport gfxterm_font\n'))
            path = self.work / 'snapshot-parent.cfg'
            path.write_text(generated + 'menuentry "Emaki snapshots" {\n'
                            '  configfile "($root)/@/boot/grub/grub-btrfs.cfg"\n}\n')
            check = subprocess.run([str(tool('grub-script-check')), str(path)],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            # A fresh shell environment exercises configfile's export boundary; it
            # does not source the parent and cannot see unexported assignments.
            for fits in (True, False):
                script = ('unset gfxterm_font\n'
                          'loadfont() { return 0; }\n'
                          'terminal_output() {\n'
                          '  test "$gfxterm_font" = "Unifont Regular 16" || test "$fits" = yes\n'
                          '}\n' + ('fits=yes\n' if fits else 'fits=no\n') +
                          block.replace('set gfxterm_font=', 'gfxterm_font=') +
                          'sh -c \'printf "%s\\n" "$gfxterm_font"\'\n')
                result = subprocess.run(['sh', '-c', script], capture_output=True, text=True)
                with self.subTest(size=size, fits=fits):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = f'DejaVu Sans Mono Regular {size}' if fits else 'Unifont Regular 16'
                    self.assertEqual(result.stdout.strip(), expected)


    def test_snapshot_description_keeps_stock_unicode_coverage(self):
        unicode = self.library / 'unicode.pf2'
        if not unicode.is_file():
            self.skipTest('Missing packaged Unifont')
        stock, fallback = pf2(unicode)
        self.assertEqual(stock['NAME'], 'Unifont Regular 16')
        description = 'Before update: 日本中文한국'
        for size, _, _ in boot.MENU_FONTS:
            with self.subTest(size=size):
                values, primary = pf2(FONTS / f'menu-{size}.pf2')
                missing = set(map(ord, '日本中文한국')) - primary.keys()
                self.assertEqual(missing, set(map(ord, '日本中文한국')))
                self.assertTrue(missing <= fallback.keys())
                raw = self.header_for(f'{FONTS}/menu-{size}.pf2').replace(str(FONTS), boot.MENU_FONT_DIR)
                raw += f"menuentry '{description}' {{ true; }}\n"
                safe = boot.menu_font_fallback(raw)
                # An unconditional stock load precedes terminal activation, including
                # when the primary succeeds. The primary's exact PF2 name wins for ASCII.
                self.assertIn('loadfont unicode\nset gfxterm_font="' + values['NAME'] + '"\n'
                              'if terminal_output gfxterm; then', safe)
                self.assertTrue(safe.endswith(f"menuentry '{description}' {{ true; }}\n"))
                for character in description:
                    self.assertIn(ord(character), primary if ord(character) in primary else fallback)
                path = self.work / 'unicode-menu.cfg'
                path.write_text(safe)
                result = subprocess.run([str(tool('grub-script-check')), str(path)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class Refresh(unittest.TestCase):
    def test_generator_passes_the_face_only_when_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def mapped(value):
                path = Path(value)
                return root / str(path).lstrip('/') if path.is_absolute() and not path.is_relative_to(root) else path

            params = mapped(boot.BOOT_PARAMS)
            params.parent.mkdir(parents=True)
            stage = Path('/boot/emaki') / ('b' * 32)
            mapped(stage / 'grub').mkdir(parents=True)
            menu = mapped('/boot/grub/grub.cfg')
            menu.parent.mkdir(parents=True)
            calls = []
            with patch.object(refresh, 'Path', side_effect=mapped), \
                    patch.object(refresh, 'bind'), \
                    patch.object(refresh, 'run', side_effect=lambda *args: calls.append(args)):
                for mode, installed, expected in (((2560, 1600), True, {'GRUB_FONT': boot.MENU_FONT_DIR + '/menu-32.pf2'}),
                                                  ((2560, 1600), False, None),
                                                  ((1024, 768), True, None)):
                    with self.subTest(mode=mode, installed=installed):
                        params.write_bytes(screen_info(0x70, *mode))
                        font = mapped(boot.MENU_FONT_DIR + '/menu-32.pf2')
                        if installed:
                            font.parent.mkdir(parents=True, exist_ok=True)
                            font.write_bytes(b'font')
                        elif font.exists():
                            font.unlink()
                        calls.clear()
                        header = ('if loadfont /@' + boot.MENU_FONT_DIR + '/menu-32.pf2 ; then\nfi\n'
                                  'terminal_output gfxterm\n') if expected else 'if loadfont $font ; then\nfi\n'
                        menu.write_text(header)
                        refresh.generate(stage)
                        self.assertEqual(calls, [(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], expected)])
                        self.assertEqual(menu.read_text(), boot.menu_font_fallback(header))


if __name__ == '__main__':
    unittest.main()
