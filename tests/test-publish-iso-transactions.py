#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The live installer resolves only against the packages retained on its image."""
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/mirror'))
spec = importlib.util.spec_from_file_location('publisher', ROOT / 'packaging/mirror/publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as target:
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
    return output.getvalue()


def database(packages):
    entries = {}
    for name, version, dependencies in packages:
        fields = {'NAME': [name], 'VERSION': [version], 'BASE': [name], 'ARCH': ['any'],
                  'FILENAME': [f'{name}-{version}-any.pkg.tar.zst'], 'CSIZE': ['100'],
                  'ISIZE': ['100'], 'DESC': ['Image package fixture'], 'DEPENDS': dependencies}
        entries[f'{name}-{version}/desc'] = ''.join(
            f'%{key}%\n' + '\n'.join(value) + '\n\n' for key, value in fields.items() if value).encode()
    return archive(entries)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('pacman', 'fakeroot', 'bsdtar')),
                     'pacman, fakeroot and bsdtar required')
class ImageTransactions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='iso-transaction-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = self.root / 'emaki-1.0.0-x86_64.iso'

    def image_with(self, archinstall='4.5-1'):
        packages = [('emaki-installer', '1-1', ['archinstall=4.5-1']),
                    ('archinstall', archinstall, [])]
        filenames = [f'{name}-{version}-any.pkg.tar.zst' for name, version, _ in packages]
        entries = {f'emaki/repo/{name}': b'package fixture' for name in filenames}
        entries['emaki/repo/closure.txt'] = ('\n'.join(filenames) + '\n').encode()
        entries['emaki/repo/emaki-offline.db.tar.gz'] = database(packages)
        self.image.write_bytes(archive(entries))
        return filenames

    def test_old_archinstall_pin_resolves_from_image_without_current_arch(self):
        filenames = self.image_with()
        publisher.check_iso_transactions(self.image, filenames)

    def test_broken_iso_pin_is_refused_before_source_checks_or_upload(self):
        self.image_with('4.6-1')
        publish = mock.Mock()
        publish.args = SimpleNamespace(arch_sources=None)
        publish.dry_run = False
        publish.pointer.return_value = ('snapshot', None)
        publish.snapshot.return_value = {'MANIFEST': b''}
        with self.assertRaisesRegex(publisher.Refused, 'archinstall=4.5-1'):
            publisher.iso_publish(publish, self.image, False)
        publish.backend.put_new.assert_not_called()
        publish.put_or_same.assert_not_called()

    def test_image_database_must_describe_exact_image_package_set(self):
        filenames = self.image_with()
        with self.assertRaisesRegex(publisher.Refused, 'database.*closure'):
            publisher.check_iso_transactions(self.image, filenames + ['unlisted-1-1-any.pkg.tar.zst'])


if __name__ == '__main__':
    unittest.main()
