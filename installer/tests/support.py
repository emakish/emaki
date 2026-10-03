import copy
import json
from pathlib import Path

from emaki_installer.constants import GIB, MIB


def config(mode='erase', fs='btrfs'):
    return {'mode': mode, 'disk_id': '/dev/vda', 'fs': fs, 'hostname': 'emaki',
            'timezone': 'UTC', 'layouts': ['us', 'ru'], 'online_update': False,
            'user': {'name': 'VM User', 'login': 'vmuser', 'password': 'a-secret-for-tests'}}


def inventory(size=40 * GIB, partitions=False):
    disk = {'id': '/dev/vda', 'path': '/dev/vda', 'size_bytes': size, 'model': 'VM disk',
            'bus': 'virtio', 'removable': False, 'is_boot_medium': False, 'partitions': [],
            'free_extents': [], 'shrink': None, '_busy': False, '_sector_size': 512,
            '_pttype': 'gpt' if partitions else None, '_read_only': False,
            '_identity': {'serial': 'test-disk', 'diskseq': 42}}
    if partitions:
        for number, fs, start, length, esp in [(1, 'vfat', MIB, GIB, True),
                                              (2, 'btrfs', GIB + MIB, size - GIB - 2 * MIB, False)]:
            disk['partitions'].append({'id': f'/dev/vda{number}', 'path': f'/dev/vda{number}',
                                       'number': number, 'uuid': f'uuid-{number}', 'fs': fs,
                                       'size_bytes': length, 'start_bytes': start, 'mountpoint': None,
                                       'label': None, 'os_hint': None, 'esp': esp,
                                       '_geometry_known': True, '_read_only': False,
                                       '_partuuid': f'partuuid-{number}', '_esp_free_bytes': 700 * MIB,
                                       '_btrfs_devices': 1, '_subvolumes': []})
    return {'disks': [disk], 'gpu': 'test', 'cpu': 'test', 'memory_bytes': 4 * GIB,
            'uefi': True, 'network': {'online': False}, 'tz_guess': None, 'scale_guess': None,
            '_boot_medium_known': True}


def manual(fs='btrfs', format=True):
    c = config('manual', fs)
    c['mounts'] = [{'partition_id': '/dev/vda1', 'mountpoint': '/efi', 'fs': 'vfat', 'format': False},
                   {'partition_id': '/dev/vda2', 'mountpoint': '/', 'fs': fs, 'format': format}]
    return c


class FakeInventory:
    def __init__(self, data=None):
        self.data = data or inventory()

    def probe(self):
        return copy.deepcopy(self.data)


class RecordingRunner:
    def __init__(self, log=None, redactor=None, progress=None):
        self.commands = []
        self.log = log or (lambda line: None)
        self.responses = {}
        self.fail = None

    def run(self, argv, **kwargs):
        from emaki_installer.errors import Code, InstallError
        argv = [str(x) for x in argv]
        self.commands.append((argv, kwargs))
        if self.fail and self.fail(argv):
            raise InstallError(Code.COMMAND_FAILED, 'injected command failure')
        return self.responses.get(tuple(argv), '')

    def chroot(self, argv, target, **kwargs):
        return self.run(['arch-chroot', str(target), *argv], **kwargs)


def fixture(name):
    return json.loads((Path(__file__).parents[1] / 'fixtures' / name).read_text())
