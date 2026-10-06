import importlib.util
from pathlib import Path
import re
import struct
import unittest

from emaki_installer.constants import GRUB_VISIBLE_FONT


class PackagingTests(unittest.TestCase):
    def test_grub_unlock_fonts_are_shipped_and_well_formed(self):
        root = Path(__file__).resolve().parents[2]
        assets = root / 'installer/assets/grub'
        spec = importlib.util.spec_from_file_location('subset_pf2', assets / 'subset-pf2.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        sizes = (24,)
        for size in sizes:
            with self.subTest(size=size):
                data = (assets / f'unlock-{size}.pf2').read_bytes()
                self.assertTrue(data.startswith(tool.MAGIC))
                fields = dict(tool.read_sections(data))
                self.assertEqual(fields['NAME'], f'DejaVu Sans Mono Regular {size}\0'.encode())
                self.assertEqual(struct.unpack('>H', fields['PTSZ'])[0], size)
                glyphs = tool.glyphs(data, fields['CHIX'])
                # Exactly the printable ASCII range: GRUB shows nothing else before the unlock.
                self.assertEqual(sorted(glyphs), list(range(0x20, 0x7f)))
                max_width, max_height = (struct.unpack('>H', fields[k])[0] for k in ('MAXW', 'MAXH'))
                widths = set()
                for record in glyphs.values():
                    width, height, _, _, device_width = tool.GLYPH.unpack_from(record)
                    self.assertLessEqual(width, max_width)
                    self.assertLessEqual(height, max_height)
                    self.assertEqual(len(record), tool.GLYPH.size + (width * height + 7) // 8)
                    widths.add(device_width)
                self.assertEqual(len(widths), 1)  # monospace: one cell width for the prompt
                self.assertEqual(len(fields['DATA']), sum(len(r) for r in glyphs.values()))
        self.assertIn(GRUB_VISIBLE_FONT.name, {f'unlock-{size}.pf2' for size in sizes})
        self.assertEqual(GRUB_VISIBLE_FONT.parent, Path('/usr/share/emaki-installer/grub'))
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        self.assertIn('installer/assets/grub/unlock-24.pf2', recipe)
        self.assertIn('/usr/share/emaki-installer/grub/unlock-24.pf2', recipe)
        self.assertIn('installer/assets/grub/LICENSE-DejaVu.txt', recipe)
        self.assertIn('Bitstream-Vera', re.search(r'license=\(([^)]+)\)', recipe)[1])
        self.assertTrue((assets / 'LICENSE-DejaVu.txt').is_file())

    def test_native_unlock_artwork_is_shipped_beside_the_module(self):
        from emaki_installer import grub_screen
        root = Path(__file__).resolve().parents[2]
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        self.assertIn('for variant in A B C; do', recipe)
        self.assertIn('$site/emaki_installer/grub_artwork/$variant/${picture##*/}', recipe)
        for variant in ('A', 'B', 'C'):
            paths = list((grub_screen.ARTWORK / variant).glob('*.png'))
            self.assertEqual(len(paths), 9)

    def test_every_python_module_is_explicitly_listed(self):
        root = Path(__file__).resolve().parents[2]
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        modules = set(re.search(r'_modules=\(([^)]+)\)', recipe, re.S)[1].split())
        actual = {p.stem for p in (root / 'installer/emaki_installer').glob('*.py')}
        self.assertEqual(modules, actual)
        self.assertIn('archinstall=4.5-1', recipe)
        # The launcher icon lives in hicolor; its index.theme names the scalable/apps directory.
        self.assertIn('hicolor-icon-theme', re.search(r'depends=\(([^)]+)\)', recipe, re.S)[1].split())
        self.assertIn('/usr/share/icons/hicolor/scalable/apps/emaki-install.svg', recipe)
        self.assertIn('pkgver=0.2.0', recipe)
        self.assertIn('pkgrel=1', recipe)
        for path in ('bin/emaki-installerd', 'bin/emaki-install-cli',
                     'systemd/emaki-installerd.service', 'sysusers.d/emaki-installer.conf',
                     'tmpfiles.d/emaki-installer.conf', 'README.md'):
            self.assertTrue((root / 'installer' / path).is_file())
            self.assertIn('installer/' + path, recipe)

    def test_iso_condition_socket_group_and_runtime_permissions(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / 'systemd/emaki-installerd.service').read_text()
        for field in ('ConditionPathExists=/run/archiso/bootmnt', 'UMask=0022',
                      'RuntimeDirectory=emaki-installer', 'RuntimeDirectoryMode=0750',
                      'PrivateMounts=yes', 'KillMode=mixed'):
            self.assertIn(field, service)
        self.assertNotIn('Group=', service)
        self.assertEqual((root / 'sysusers.d/emaki-installer.conf').read_text(), 'g emaki-install -\n')


if __name__ == '__main__':
    unittest.main()
