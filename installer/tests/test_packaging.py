from pathlib import Path
import re
import unittest


class PackagingTests(unittest.TestCase):
    def test_every_python_module_is_explicitly_listed(self):
        root = Path(__file__).resolve().parents[2]
        recipe = (root / 'packaging/emaki-installer/PKGBUILD').read_text()
        modules = set(re.search(r'_modules=\(([^)]+)\)', recipe, re.S)[1].split())
        actual = {p.stem for p in (root / 'installer/emaki_installer').glob('*.py')}
        self.assertEqual(modules, actual)
        self.assertIn('archinstall=4.5-1', recipe)
        self.assertIn('pkgver=0.1.0', recipe)
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
