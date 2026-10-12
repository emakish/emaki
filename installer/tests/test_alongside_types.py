# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows-managed partition maps must never reach a resize operation."""

import copy
import unittest
from unittest.mock import Mock

from emaki_installer import inventory
from emaki_installer.errors import InstallError
from emaki_installer.planner import make_plan
from support import alongside_on
from test_alongside import windows


class AlongsidePartitionTypesTests(unittest.TestCase):
    def assert_refused(self, config, inv, reason):
        disk = inv['disks'][0]
        part = next(p for p in disk['partitions'] if p['id'] == config['partition_id'])
        runner = Mock()
        probe = inventory.Inventory(runner)
        with alongside_on():
            probe._alongside(disk, True)
            self.assertIn(reason, part['shrink']['reason'])
            self.assertEqual(part['shrink']['max_free_bytes'], 0)
            runner.run.assert_not_called()
            # A forged successful inventory offer cannot bypass planner validation.
            part['shrink'] = inventory.shrink_offer(20 * inventory.GIB,
                                                   part['size_bytes'], part['start_bytes'])
            with self.assertRaisesRegex(InstallError, reason):
                make_plan(config, inv)

    def test_managed_type_anywhere_on_disk_refuses_alongside(self):
        for guid in sorted(inventory.WINDOWS_MANAGED_GUIDS):
            for candidate in (True, False):
                for spelling in (guid, guid.upper()):
                    with self.subTest(guid=spelling, candidate=candidate):
                        config, inv = windows()
                        disk = inv['disks'][0]
                        if candidate:
                            part = disk['partitions'][1]
                        else:
                            part = copy.deepcopy(disk['partitions'][1])
                            part.update(id='managed-volume', number=3, fs=None)
                            disk['partitions'].append(part)
                        part['_parttype'] = spelling
                        part['_gpt']['type'] = spelling
                        self.assert_refused(config, inv, 'dynamic disk or in Storage Spaces')

    def test_candidate_requires_basic_data_type(self):
        for guid in ('', None, inventory.ESP_GUID, '0x07',
                     'de94bba4-06d1-4d40-a16a-bfd50179d6ac'):
            with self.subTest(guid=guid):
                config, inv = windows()
                part = inv['disks'][0]['partitions'][1]
                part['_parttype'] = guid
                part['_gpt']['type'] = guid
                self.assert_refused(config, inv, 'set up in a way Emaki cannot safely shrink')

    def test_basic_data_still_plans(self):
        config, inv = windows()
        disk = inv['disks'][0]
        self.assertIsNone(inventory.alongside_type_refusal(disk, disk['partitions'][1]))
        with alongside_on():
            make_plan(config, inv)


if __name__ == '__main__':
    unittest.main()
