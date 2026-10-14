import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import sysconfig
import tempfile
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

    def test_scalable_unlock_artwork_is_shipped_beside_the_module(self):
        from emaki_installer import grub_screen
        root = Path(__file__).resolve().parents[2]
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        self.assertIn('for variant in A B C D; do', recipe)
        self.assertIn('$site/emaki_installer/grub_artwork/$variant/${picture##*/}', recipe)
        for variant in ('A', 'B', 'C', 'D'):
            paths = list((grub_screen.ARTWORK / variant).glob('*.png'))
            self.assertEqual({path.name for path in paths}, {
                f'{state}-{width}x{height}.png'
                for state in ('unlock', 'wrong', 'checking')
                for width, height in grub_screen.SIZES
            })

    def test_release_package_has_no_socket_override_or_test_fixtures(self):
        root = Path(__file__).resolve().parents[2]
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        self.assertNotRegex(recipe.split('package() {', 1)[1], r'install [^\n]*installer/fixtures')
        listed = (root / 'packaging/emaki-installer/expected-files.list').read_text()
        self.assertNotIn('/fixtures/', listed)
        source = (root / 'installer/ui/InstallerController.qml').read_text()
        self.assertEqual(source.count('EMAKI_INSTALLER_SOCKET'), 1)
        step = re.search(r'^    # The socket override.*?^    fi\n', recipe, re.M | re.S)
        self.assertIsNotNone(step)
        with tempfile.TemporaryDirectory() as directory:
            ui = Path(directory) / 'usr/share/emaki-installer/ui'
            ui.mkdir(parents=True)
            (ui / 'InstallerController.qml').write_text(source)
            result = subprocess.run(['bash', '-euc', 'package() {\n' + step[0] + '}\npackage', 'recipe'],
                                    env={'pkgdir': directory, 'PATH': '/usr/bin:/bin'},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            packaged = (ui / 'InstallerController.qml').read_text()
            self.assertNotIn('EMAKI_INSTALLER_SOCKET', packaged)
            self.assertIn('path: "/run/emaki-installer/sock"', packaged)
            # A changed source line must stop the build, not ship the override.
            (ui / 'InstallerController.qml').write_text(source.replace('|| "/run', '||  "/run'))
            result = subprocess.run(['bash', '-euc', 'package() {\n' + step[0] + '}\npackage', 'recipe'],
                                    env={'pkgdir': directory, 'PATH': '/usr/bin:/bin'},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_staged_release_refuses_repository_override_even_in_test_mode(self):
        from support import config
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'src/emaki-installer'
            source.mkdir(parents=True)
            (source / 'installer').symlink_to(root / 'installer', target_is_directory=True)
            (source / 'LICENSE').symlink_to(root / 'LICENSE')
            destination = base / 'pkg'
            recipe = base / 'recipe'
            recipe.mkdir()
            (recipe / 'PKGBUILD').write_text((root / 'packaging/emaki-installer/PKGBUILD').read_text())
            (recipe / 'SOURCE-COMMIT').write_text('0' * 40)
            env = dict(os.environ, EMAKI_SOURCE_COMMIT='', startdir=str(recipe),
                       srcdir=str(base / 'src'), pkgdir=str(destination))
            result = subprocess.run(['bash', '-euc', 'source "$startdir/PKGBUILD"; package'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            site = destination / Path(sysconfig.get_path('purelib')).relative_to('/')
            env['PYTHONPATH'] = str(site)
            # Import only the actual package payload, in a new process outside the source tree.
            probe = """
import json
from pathlib import Path
from emaki_installer.constants import TEST_REPO_OVERRIDE
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import validate_config
from emaki_installer.protocol import read_test_mode
assert TEST_REPO_OVERRIDE is False
Path('cmdline').write_text('quiet emaki.test=1')
assert read_test_mode(Path('cmdline')) is True
config = json.loads(FIXTURE_JSON)
for mode in (False, read_test_mode(Path('cmdline'))):
    validate_config(config, mode)
    for server in ('https://pkgs.example/stable', 'http://10.0.2.2/stable', '', None):
        try:
            validate_config(dict(config, repo_server=server), mode)
        except InstallError as error:
            assert error.code == Code.BAD_CONFIG
            assert 'repository' in str(error).lower()
        else:
            raise AssertionError((mode, server))
""".replace('FIXTURE_JSON', repr(json.dumps(config())))
            result = subprocess.run([sys.executable, '-c', probe], cwd=base, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_offline_vm_plans_do_not_override_the_packaged_repository(self):
        root = Path(__file__).resolve().parents[2]
        for directory in ('installer/fixtures', 'tests/vm/fixtures', 'tests/vm/night/fixtures'):
            for path in (root / directory).glob('plan-*.json'):
                with self.subTest(path=path):
                    plan = json.loads(path.read_text())
                    self.assertNotIn('repo_server', plan)

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
        self.assertIn('pkgver=0.8.0', recipe)
        self.assertIn('pkgrel=4', recipe)
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
                      'PrivateMounts=yes', 'KillMode=mixed', 'LimitMEMLOCK=infinity'):
            self.assertIn(field, service)
        self.assertNotIn('Group=', service)
        self.assertEqual((root / 'sysusers.d/emaki-installer.conf').read_text(), 'g emaki-install -\n')


if __name__ == '__main__':
    unittest.main()
