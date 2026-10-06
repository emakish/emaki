#!/usr/bin/env python3
"""Run only inside a disposable test guest, as root. Never on the host.

prepare emits the immutable host-side baseline; check reads it from stdin.
This is a synthetic Windows system volume with a diagnostic EFI target, not Windows.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
import zlib

DISK = '/dev/disk/by-id/virtio-emaki-target'
MIB = 1024**2


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True,
                            env=dict(os.environ, LC_ALL='C'))
    print('$ ' + ' '.join(argv) + '\n' + result.stdout + result.stderr, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def disk_info():
    return json.loads(run('lsblk', '--json', '--tree', '--bytes', '--output',
                         'PATH,TYPE,SIZE,START,PARTN,PARTUUID,PARTTYPE,PARTLABEL,PTTYPE,FSTYPE,SERIAL,MOUNTPOINTS', DISK))['blockdevices'][0]


@contextmanager
def mounted(dev, fs, readonly=True):
    if readonly and fs == 'vfat' and Path('/efi').is_mount():
        source = run('findmnt', '-nr', '--mountpoint', '/efi', '-o', 'SOURCE')
        if os.path.realpath(source.strip()) != os.path.realpath(dev):
            raise RuntimeError('installed ESP source does not match the fixture')
        yield Path('/efi')
        return
    with tempfile.TemporaryDirectory(prefix='emaki-windows-') as directory:
        options = 'ro,norecover,nodev,nosuid,noexec' if fs == 'ntfs-3g' and readonly else 'ro,nodev,nosuid,noexec' if readonly else 'rw,nodev,nosuid,noexec'
        run('mount', '-t', fs, '-o', options, dev, directory)
        try:
            yield Path(directory)
        finally:
            run('umount', directory)


def hashes(root, names):
    result = {}
    for name in names:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise RuntimeError('unsafe manifest path')
        p = root / name
        if p.is_symlink():
            raise RuntimeError('unexpected symlink in fixture')
        result[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def prepare():
    info = disk_info()
    if (info.get('children') or info.get('pttype') or info.get('fstype') or
            any(info.get('mountpoints') or []) or info.get('serial') != 'emaki-target'):
        raise RuntimeError('preparation requires an empty, unmounted emaki-target disk')
    size = int(info['size'])
    if size < 80 * 1024**3:
        raise RuntimeError('synthetic Windows disk needs at least 80 GiB')
    # Fixture is deliberately 512-sector virtio, unlike the production planner
    # which also validates 4Kn layouts in unit tests.
    if run('blockdev', '--getss', DISK).strip() != '512':
        raise RuntimeError('fixture requires 512-byte logical sectors')
    end = (size // MIB - 1) * MIB // 512 - 1
    run('sgdisk', '--clear', '--new=1:2048:206847', '--typecode=1:ef00', '--change-name=1:Windows ESP',
        '--new=2:206848:239615', '--typecode=2:0c01', '--change-name=2:Microsoft reserved',
        f'--new=3:239616:{end}', '--typecode=3:0700', '--change-name=3:Windows',
        '--attributes=3:set:63', DISK)
    run('partprobe', DISK)
    run('udevadm', 'settle', '--timeout=30')
    run('mkfs.fat', '-F', '32', '-n', 'WINESP', DISK + '-part1')
    run('mkntfs', '-Q', '-L', 'Windows', DISK + '-part3')
    loader = Path('/tmp/windows-fixture.efi').read_bytes()
    esp_files = {'EFI/Microsoft/Boot/bootmgfw.efi': loader,
                 'EFI/Microsoft/Boot/BCD': b'Synthetic BCD presence fixture; no Windows installation.\n',
                 'EFI/BOOT/BOOTX64.EFI': b'Emaki VM foreign fallback - preserve exactly\n'}
    ntfs_files = {'Users/Demo/Documents/keep.txt': b'Windows data must survive the Emaki installer.\n',
                  'Users/Demo/Pictures/pattern.bin': bytes(range(256)) * 16384,
                  'Windows/System32/config/SYSTEM': b'Synthetic Windows fixture; not a real Windows install.\n'}
    baseline = {}
    for key, part, fs, files in [('esp', 1, 'vfat', esp_files), ('ntfs', 3, 'ntfs-3g', ntfs_files)]:
        with mounted(DISK + f'-part{part}', fs, readonly=False) as root:
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            baseline[key] = hashes(root, files)
    # Fail if even this clean synthetic volume cannot be probed without force.
    run('ntfsresize', '--info', '--no-action', DISK + '-part3')
    baseline['disk'] = disk_info()
    baseline['gpt'] = {str(n): run('sgdisk', f'--info={n}', DISK) for n in (1, 2, 3)}
    return baseline


def gpt_metadata(text):
    fields = dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line)
    return {
        'Partition GUID code': fields['Partition GUID code'].split()[0].lower(),
        'Partition unique GUID': fields['Partition unique GUID'].lower(),
        'First sector': int(fields['First sector'].split()[0]),
        'Last sector': int(fields['Last sector'].split()[0]),
        'Attribute flags': fields['Attribute flags'].lower(),
        'Partition name': fields['Partition name'][1:-1],
    }


def raw_gpt():
    """Independent read-only oracle; installed targets need no gptfdisk package."""
    size = int(run('blockdev', '--getsize64', DISK))
    with open(DISK, 'rb', buffering=0) as stream:
        tables = []
        for lba in (1, size // 512 - 1):
            stream.seek(lba * 512)
            header = bytearray(stream.read(512))
            assert header[:8] == b'EFI PART'
            length = int.from_bytes(header[12:16], 'little')
            assert 92 <= length <= 512
            checksum = int.from_bytes(header[16:20], 'little')
            header[16:20] = bytes(4)
            assert zlib.crc32(header[:length]) == checksum, 'GPT header CRC mismatch'
            location = int.from_bytes(header[72:80], 'little')
            count = int.from_bytes(header[80:84], 'little')
            entry_size = int.from_bytes(header[84:88], 'little')
            assert 1 <= count <= 128 and 128 <= entry_size <= 4096
            stream.seek(location * 512)
            table = stream.read(count * entry_size)
            assert zlib.crc32(table) == int.from_bytes(header[88:92], 'little'), 'GPT entries CRC mismatch'
            tables.append(table)
        assert tables[0] == tables[1], 'Primary/backup GPT tables differ'
    result = {}
    for index in range(count):
        entry = table[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == bytes(16):
            continue
        result[index + 1] = {
            'Partition GUID code': str(uuid.UUID(bytes_le=entry[:16])),
            'Partition unique GUID': str(uuid.UUID(bytes_le=entry[16:32])),
            'First sector': int.from_bytes(entry[32:40], 'little'),
            'Last sector': int.from_bytes(entry[40:48], 'little'),
            'Attribute flags': format(int.from_bytes(entry[48:56], 'little'), '016x'),
            'Partition name': entry[56:128].decode('utf-16le').rstrip('\0'),
        }
    return result


def check(baseline, freed):
    current = disk_info()
    before = {int(p['partn']): p for p in baseline['disk']['children']}
    after = {int(p['partn']): p for p in current['children']}
    if len(after) != len(before) + (1 if freed else 0):
        raise RuntimeError('unexpected partition count')
    for number in (1, 2, 3):
        for key in ('start', 'partuuid', 'parttype', 'partlabel', 'fstype'):
            if after[number][key] != before[number][key]:
                raise RuntimeError(f'partition {number} {key} changed')
        expected = int(before[number]['size']) - (freed if number == 3 else 0)
        if int(after[number]['size']) != expected:
            raise RuntimeError(f'partition {number} size mismatch')
    if freed:
        root = next(p for n, p in after.items() if n not in before)
        if int(root['start']) * 512 != int(after[3]['start']) * 512 + int(after[3]['size']) or int(root['size']) != freed:
            raise RuntimeError('root is not exactly inside the freed extent')
    for key, part, fs in [('esp', 1, 'vfat'), ('ntfs', 3, 'ntfs-3g')]:
        with mounted(DISK + f'-part{part}', fs) as root:
            if hashes(root, baseline[key]) != baseline[key]:
                raise RuntimeError(key + ' file hashes changed')
    metadata = raw_gpt()
    for n in (1, 2, 3):
        expected = gpt_metadata(baseline['gpt'][str(n)])
        if n == 3:
            expected['Last sector'] -= freed // 512
        assert metadata[n] == expected
    if freed and Path('/run/archiso/bootmnt').exists():
        dirty = subprocess.run(['ntfsresize', '--info', '--no-action', DISK + '-part3'],
                               capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
        assert dirty.returncode != 0 and 'Volume is scheduled for check' in dirty.stdout + dirty.stderr
        print('PASS: post-resize dirty flag remains set for Windows chkdsk; read-only metadata/data checks succeeded.')
    print('PASS: NTFS mounted read-only; files, Microsoft/fallback EFI loaders, partition GUID/type/name/start preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check'])
    parser.add_argument('--freed', type=int, default=0)
    args = parser.parse_args()
    if os.geteuid() != 0 or run('systemd-detect-virt', '--vm').strip() not in ('qemu', 'kvm'):
        raise RuntimeError('requires root inside the disposable QEMU/KVM guest')
    if args.action == 'prepare':
        if 'emaki.test=1' not in Path('/proc/cmdline').read_text().split() or not Path('/run/archiso/bootmnt').exists():
            raise RuntimeError('requires the live test ISO')
        print(json.dumps(prepare(), indent=2))
    else:
        check(json.load(sys.stdin), args.freed)


if __name__ == '__main__':
    main()
