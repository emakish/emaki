# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""One disk for the partition editor's window tests: Windows, an old Linux and free space.

inventory() is the worker's view (with the private probe fields the planner reads); the window
gets public(inventory()), as the worker sends it. plan_ack() runs the real planner on a plan
the window sent, so a frame or an interaction proves what the review would really say.
"""
import copy
import os
from pathlib import Path
import sys

MIB = 1024 ** 2
GIB = 1024 ** 3
ESP_GUID = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
BASIC_DATA = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
LINUX = '0fc63daf-8483-4772-8e79-3d69d8477de4'


def checkout():
    """The installer package of this checkout; the renderer copies the window elsewhere."""
    root = Path(os.environ.get('EMAKI_INSTALLER_CHECKOUT') or Path(__file__).resolve().parents[3])
    if str(root / 'installer') not in sys.path:
        sys.path.insert(0, str(root / 'installer'))


def _part(number, start, size, fs, parttype, **extra):
    return {'id': f'/dev/vda{number}', 'path': f'/dev/vda{number}', 'number': number,
            'uuid': f'FIXTURE-{number}', 'fs': fs, 'size_bytes': size, 'start_bytes': start,
            'mountpoint': None, 'label': None, 'os_hint': None, 'esp': parttype == ESP_GUID,
            'other_system': None, '_geometry_known': True, '_read_only': False,
            '_partuuid': f'00000000-0000-4000-8000-00000000000{number}', '_parttype': parttype,
            '_partlabel': '', '_esp_free_bytes': None, '_esp_cluster_bytes': 4096,
            '_btrfs_devices': None, '_subvolumes': None, '_busy': False, **extra}


def inventory(empty=False):
    """A 128 GiB disk: Windows' ESP, Windows, an old Linux system and 27.5 GiB free at the end.

    empty: the same disk with an old MBR table and no partitions (a new GPT table first)."""
    checkout()
    from emaki_installer.inventory import other_system
    disk = {'id': '/dev/vda', 'path': '/dev/vda', 'model': 'Samsung SSD 990', 'size_bytes': 128 * GIB,
            'bus': 'nvme', 'removable': False, 'is_boot_medium': False, 'partitions': [],
            'closed_encrypted': [], 'free_extents': [], 'shrink': None, 'partition_table': 'gpt',
            '_busy': False, '_read_only': False, '_sector_size': 512, '_pttype': 'gpt', '_fs': None,
            '_id_fallback': False, '_identity': {'serial': 'fixture', 'disk-seq': 7}}
    if empty:
        disk.update(partition_table='dos', _pttype='dos')
    else:
        disk['partitions'] = [
            _part(1, MIB, 512 * MIB, 'vfat', ESP_GUID, os_hint='windows', _esp_free_bytes=420 * MIB,
                  _efi_loaders={'EFI/Microsoft/Boot/bootmgfw.efi': '0' * 64}),
            _part(2, 513 * MIB, 60 * GIB, 'ntfs', BASIC_DATA, os_hint='windows'),
            _part(3, 513 * MIB + 60 * GIB, 40 * GIB, 'ext4', LINUX, os_hint='linux'),
        ]
        for part in disk['partitions']:
            part['other_system'] = other_system(part)
    return {'disks': [disk], 'gpu': 'Fixture GPU', 'cpu': 'Fixture CPU', 'memory_bytes': 16 * GIB,
            'uefi': True, 'uefi_bits': 64, 'network': {'online': False}, 'tz_guess': 'UTC',
            'scale_guess': None, 'hardware': {}, '_boot_medium_known': True}


def public(value):
    checkout()
    from emaki_installer.inventory import public_inventory
    return public_inventory(copy.deepcopy(value))


def plan_ack(config, value):
    """The worker's plan_ack for the window's config on this disk (the real planner)."""
    checkout()
    from emaki_installer.errors import InstallError
    from emaki_installer.planner import make_plan
    try:
        plan = make_plan(copy.deepcopy(config), copy.deepcopy(value))
    except InstallError as exc:
        return {'type': 'plan_ack', 'plan_id': 'fixture-plan', 'expires_s': 600, 'summary': [],
                'errors': [{'code': exc.code.value, 'msg': exc.message}], 'warnings': []}
    return {'type': 'plan_ack', 'plan_id': 'fixture-plan', 'token': 'mock-capability', 'expires_s': 600,
            'summary': plan.summary, 'errors': [], 'warnings': plan.warnings}
