# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic mount and kernel dependency graphs never inspect host devices."""
import json
from pathlib import Path
import tempfile
import unittest

from emaki_installer.boot_media import BootMedia

BOOT_MOUNT = "/run/archiso/bootmnt"


def device(path, kind, number, **fields):
    return {'path': path, 'kname': Path(path).name, 'type': kind,
            'maj:min': number, 'rm': False, **fields}


class MountRunner:
    def __init__(self, mounts):
        self.mounts = mounts
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if (len(argv) != 6 or argv[:2] != ['findmnt', '--json']
                or argv[2] not in {'--mountpoint', '--target'}
                or argv[4:] != ['--output', 'SOURCE,MAJ:MIN']):
            raise AssertionError(f'Unexpected command: {argv!r}')
        mount = self.mounts[(argv[2], argv[3])]
        if isinstance(mount, Exception):
            raise mount
        if isinstance(mount, str):
            return mount
        return json.dumps({'filesystems': mount})


class BootMediaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def resolver(self, nodes, source='/dev/mapper/ventoy', number='253:0', mounts=None):
        values = {('--mountpoint', BOOT_MOUNT): [{'source': source, 'maj:min': number}]}
        values.update(mounts or {})
        runner = MountRunner(values)
        return BootMedia(runner, nodes, root=self.root)

    def slaves(self, number, *names):
        directory = self.root / 'sys/dev/block' / number / 'slaves'
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            # Kernel slave entries are links named after the dependency's KNAME.
            (directory / name).symlink_to(self.root / 'sys/class/block' / name)

    def backing(self, number, filename):
        path = self.root / 'sys/dev/block' / number / 'loop/backing_file'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(filename + '\n')

    def test_ventoy_mapper_tree_on_exfat_or_ntfs(self):
        for filesystem in ('exfat', 'ntfs'):
            with self.subTest(filesystem=filesystem):
                mapper = device('/dev/mapper/ventoy', 'dm', '253:0', kname='dm-0')
                partition = device('/dev/sdb1', 'part', '8:17', fstype=filesystem,
                                   children=[mapper])
                stick = device('/dev/sdb', 'disk', '8:16', tran='usb', rm=True,
                               children=[partition])
                media = self.resolver([stick])
                sources = media.physical(media.mounted_source())
                self.assertEqual(sources, {'/dev/sdb'})
                self.assertTrue(media.removable(sources))

    def test_flat_mapper_uses_kernel_slaves_and_kernel_alias(self):
        self.slaves('253:0', 'sdb1')
        nodes = [device('/dev/sdb', 'disk', '8:16', tran='usb'),
                 device('/dev/sdb1', 'part', '8:17', pkname='sdb'),
                 device('/dev/mapper/ventoy', 'dm', '253:0', kname='dm-0')]
        media = self.resolver(nodes, source='/dev/dm-0', number=None)
        sources = media.physical(media.mounted_source())
        self.assertEqual(sources, {'/dev/sdb'})
        # USB bridges may report RM=0 even for the live stick.
        self.assertTrue(media.removable(sources))

    def test_flat_mapper_uses_pkname_without_sysfs(self):
        nodes = [device('/dev/sdb', 'disk', '8:16', tran='usb'),
                 device('/dev/sdb1', 'part', '8:17', pkname='/dev/sdb'),
                 device('/dev/mapper/ventoy', 'dm', '253:0',
                        kname='dm-0', pkname='sdb1')]
        media = self.resolver(nodes)
        self.assertEqual(media.physical(media.mounted_source()), {'/dev/sdb'})

    def test_loop_backing_filename_with_spaces_finds_outer_partition(self):
        filename = '/run/media/live/Ventoy/My Images/Emaki 0.5.0.iso'
        self.backing('7:0', filename)
        nodes = [device('/dev/sdb', 'disk', '8:16', tran='usb'),
                 device('/dev/sdb1', 'part', '8:17', pkname='sdb'),
                 device('/dev/loop0', 'loop', '7:0')]
        media = self.resolver(nodes, source='/dev/loop0', number='7:0', mounts={
            ('--target', filename): [{'source': '/dev/sdb1', 'maj:min': '8:17'}]})
        sources = media.physical(media.mounted_source())
        self.assertEqual(sources, {'/dev/sdb'})
        self.assertTrue(media.removable(sources))
        self.assertIn(['findmnt', '--json', '--target', filename,
                       '--output', 'SOURCE,MAJ:MIN'], media.run.calls)

    def test_mount_device_number_resolves_alternate_source_name(self):
        nodes = [device('/dev/sdb', 'disk', '8:16', tran='usb'),
                 device('/dev/sdb1', 'part', '8:17', pkname='sdb')]
        media = self.resolver(nodes, source='/dev/disk/by-label/Ventoy[/images]', number='8:17')
        self.assertEqual(media.physical(media.mounted_source()), {'/dev/sdb'})

    def test_every_mapper_dependency_must_resolve(self):
        self.slaves('253:0', 'sdb1', 'sdc1')
        nodes = [device('/dev/sdb', 'disk', '8:16', tran='usb'),
                 device('/dev/sdb1', 'part', '8:17', pkname='sdb'),
                 device('/dev/sdc', 'disk', '8:32', tran='sata'),
                 device('/dev/sdc1', 'part', '8:33', pkname='sdc'),
                 device('/dev/mapper/ventoy', 'dm', '253:0', kname='dm-0')]
        media = self.resolver(nodes)
        sources = media.physical(media.mounted_source())
        self.assertEqual(sources, {'/dev/sdb', '/dev/sdc'})
        self.assertTrue(media.removable(sources))
        incomplete = self.resolver([node for node in nodes if node['path'] != '/dev/sdc'])
        self.assertEqual(incomplete.physical(incomplete.mounted_source()), set())

    def test_orphan_mapper_and_dependency_cycle_fail_closed(self):
        mapper = device('/dev/mapper/ventoy', 'dm', '253:0', kname='dm-0')
        media = self.resolver([mapper])
        self.assertEqual(media.physical(media.mounted_source()), set())
        self.slaves('253:0', 'dm-1')
        self.slaves('253:1', 'dm-0')
        media = self.resolver([mapper, device('/dev/mapper/second', 'dm', '253:1', kname='dm-1')])
        self.assertEqual(media.physical(media.mounted_source()), set())

    def test_missing_relative_or_unresolvable_loop_backing_fails_closed(self):
        nodes = [device('/dev/loop0', 'loop', '7:0')]
        media = self.resolver(nodes, source='/dev/loop0', number='7:0')
        self.assertEqual(media.physical(media.mounted_source()), set())
        for filename in ('', 'relative/image.iso', '/missing/image.iso'):
            with self.subTest(filename=filename):
                self.backing('7:0', filename)
                media = self.resolver(nodes, source='/dev/loop0', number='7:0', mounts={
                    ('--target', filename): RuntimeError('mount not found')})
                self.assertEqual(media.physical(media.mounted_source()), set())

    def test_loop_backed_by_itself_fails_closed(self):
        filename = '/run/media/image.iso'
        self.backing('7:0', filename)
        media = self.resolver([device('/dev/loop0', 'loop', '7:0')],
                              source='/dev/loop0', number='7:0', mounts={
            ('--target', filename): [{'source': '/dev/loop0', 'maj:min': '7:0'}]})
        self.assertEqual(media.physical(media.mounted_source()), set())

    def test_direct_optical_and_internal_disk(self):
        for node, removable in ((device('/dev/sr0', 'rom', '11:0', rm=True), True),
                                (device('/dev/nvme0n1', 'disk', '259:0', tran='nvme'), False)):
            with self.subTest(path=node['path']):
                media = self.resolver([node], source=node['path'], number=node['maj:min'])
                sources = media.physical(media.mounted_source())
                self.assertEqual(sources, {node['path']})
                self.assertEqual(media.removable(sources), removable)

    def test_unknown_or_ambiguous_mount_fails_closed(self):
        for output in ([], [{'source': '/dev/unknown'}],
                       [{'source': '/dev/sdb'}, {'source': '/dev/sdc'}],
                       'not json', RuntimeError('findmnt failed'), OSError('findmnt unavailable')):
            with self.subTest(output=output):
                media = self.resolver([device('/dev/sdb', 'disk', '8:16')], mounts={
                    ('--mountpoint', BOOT_MOUNT): output})
                self.assertEqual(media.physical(media.mounted_source()), set())


if __name__ == '__main__':
    unittest.main()
