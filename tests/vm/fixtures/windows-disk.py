#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only inside a disposable test guest, as root. Never on the host.

prepare emits the immutable host-side baseline; check reads it from stdin.
This is a synthetic Windows system volume with a diagnostic EFI target, not Windows.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
import zlib

DISK = '/dev/disk/by-id/virtio-emaki-target'
MIB = 1024**2
GIB = 1024**3
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
# Alongside Windows, Emaki creates its own ESP in the freed extent and mounts it rw at
# /efi; Windows' ESP is never mounted or written (owner decision for 0.9.0).
EMAKI_ESP_LABEL = 'EMAKI'
EMAKI_ESP_SIZE = GIB
MARKER = Path('/var/lib/emaki/alongside-boot.json')


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True,
                            env=dict(os.environ, LC_ALL='C'))
    print('$ ' + ' '.join(argv) + '\n' + result.stdout + result.stderr, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def disk_info():
    return json.loads(run('lsblk', '--json', '--tree', '--bytes', '--output',
                         'PATH,TYPE,SIZE,START,PARTN,PARTUUID,PARTTYPE,PARTLABEL,PTTYPE,FSTYPE,LABEL,UUID,SERIAL,'
                         'MOUNTPOINTS', DISK))['blockdevices'][0]


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


def partition_digest(device):
    digest = hashlib.sha256()
    with open(device, 'rb', buffering=0) as stream:
        while chunk := stream.read(MIB):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(no_fallback=False, esp_mib=100):
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
    recovery_start = end + 1 - 1024 * MIB // 512
    # Windows' own default ESP is 100 MiB (260 MiB on 4Kn disks); MSR is 16 MiB.
    esp_end = 2048 + esp_mib * MIB // 512 - 1
    msr_end = esp_end + 16 * MIB // 512
    run('sgdisk', '--clear', f'--new=1:2048:{esp_end}', '--typecode=1:ef00', '--change-name=1:Windows ESP',
        f'--new=2:{esp_end + 1}:{msr_end}', '--typecode=2:0c01', '--change-name=2:Microsoft reserved',
        f'--new=3:{msr_end + 1}:{recovery_start - 1}', '--typecode=3:0700', '--change-name=3:Windows',
        '--attributes=3:set:63',
        f'--new=4:{recovery_start}:{end}', '--typecode=4:2700', '--change-name=4:Windows RE tools',
        '--attributes=4:set:0', '--attributes=4:set:63', DISK)
    run('partprobe', DISK)
    run('udevadm', 'settle', '--timeout=30')
    run('mkfs.fat', '-F', '32', '-n', 'WINESP', DISK + '-part1')
    run('mkntfs', '-Q', '-L', 'Windows', DISK + '-part3')
    loader = Path('/tmp/windows-fixture.efi').read_bytes()
    esp_files = {'EFI/Microsoft/Boot/bootmgfw.efi': loader,
                 'EFI/Microsoft/Boot/BCD': b'Synthetic BCD presence fixture; no Windows installation.\n',
                 'EFI/BOOT/BOOTX64.EFI': b'Emaki VM foreign fallback - preserve exactly\n'}
    if no_fallback:
        del esp_files['EFI/BOOT/BOOTX64.EFI']
    ntfs_files = {'Users/Demo/Documents/keep.txt': b'Windows data must survive the Emaki installer.\n',
                  'Users/Demo/Pictures/pattern.bin': bytes(range(256)) * 16384,
                  'Windows/System32/config/SYSTEM': b'Synthetic Windows fixture; not a real Windows install.\n'}
    # A trailing recovery partition forces the new root into the middle extent.
    # Raw sentinels avoid depending on a recovery filesystem or Windows binaries.
    with open(DISK + '-part4', 'r+b', buffering=0) as recovery:
        for offset in (0, 512 * MIB, 1024 * MIB - 4096):
            recovery.seek(offset)
            recovery.write(b'Windows recovery fixture: preserve every byte.\n'.ljust(4096, b'\0'))
    baseline = {'recovery_sha256': partition_digest(DISK + '-part4'),
                'no_fallback': no_fallback}
    for key, part, fs, files in [('esp', 1, 'vfat', esp_files), ('ntfs', 3, 'ntfs-3g', ntfs_files)]:
        with mounted(DISK + f'-part{part}', fs, readonly=False) as root:
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            baseline[key] = hashes(root, files)
    # Fail if even this clean synthetic volume cannot be probed without force.
    run('ntfsresize', '--info', '--no-action', DISK + '-part3')
    # Register the preserved target through firmware, not only os-prober.
    run('efibootmgr', '--create', '--disk', os.path.realpath(DISK), '--part', '1',
        '--label', 'Windows Boot Manager', '--loader', r'\EFI\Microsoft\Boot\bootmgfw.efi')
    inventory = run('efibootmgr', '-v')
    partuuid = run('blkid', '-s', 'PARTUUID', '-o', 'value', DISK + '-part1').strip()
    entries = re.findall(
        r'^Boot([0-9A-Fa-f]{4})\* Windows Boot Manager\s+HD\(1,GPT,'
        + re.escape(partuuid) + r',[^)]*\)/(?:File\(\\EFI\\Microsoft\\Boot\\bootmgfw\.efi\)|'
        r'\\EFI\\Microsoft\\Boot\\bootmgfw\.efi)[ \t]*$',
        inventory, re.MULTILINE | re.IGNORECASE)
    if len(entries) != 1:
        raise RuntimeError('Synthetic Windows firmware entry is absent or ambiguous')
    baseline['windows_boot_number'] = entries[0].upper()
    baseline['windows_esp_partuuid'] = partuuid.lower()
    baseline['firmware_inventory'] = inventory
    baseline['disk'] = disk_info()
    baseline['gpt'] = {str(n): run('sgdisk', f'--info={n}', DISK) for n in (1, 2, 3, 4)}
    # FAT volume id of Windows' ESP: alongside-boot.json names it as windows_esp_uuid.
    baseline['windows_esp_uuid'] = run('blkid', '-s', 'UUID', '-o', 'value', DISK + '-part1').strip()
    # Emaki never mounts or writes Windows' ESP: every byte must stay as prepared here.
    os.sync()
    baseline['esp_sha256'] = partition_digest(DISK + '-part1')
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


def check_removable_loader(root):
    """Emaki's own ESP carries its removable loader, like the ESP of an erase install."""
    if (root / 'EFI/Microsoft').exists():
        raise RuntimeError("Emaki's ESP holds EFI/Microsoft: it would be taken for a shared ESP")
    fallback = root / 'EFI/BOOT/BOOTX64.EFI'
    if not fallback.is_file():
        raise RuntimeError("installer did not create the removable EFI loader on Emaki's ESP")
    loader = fallback.read_bytes()
    offset = int.from_bytes(loader[60:64], 'little')
    if (len(loader) < 64 or not loader.startswith(b'MZ') or offset < 64
            or loader[offset:offset + 6] != b'PE\0\0\x64\x86'):
        raise RuntimeError('new removable loader is not an x86-64 PE image')
    print("PASS: removable EFI loader on Emaki's own ESP verified; sha256=" + hashlib.sha256(loader).hexdigest())


def emaki_esp(before, after, freed):
    """Emaki's ESP: one of exactly two new partitions that together fill the freed extent."""
    new = sorted((p for n, p in after.items() if n not in before), key=lambda p: int(p['start']))
    if len(new) != 2:
        raise RuntimeError('expected exactly two new partitions (Emaki ESP and root), found '
                           + str(len(new)))
    edge = int(after[3]['start']) * 512 + int(after[3]['size'])
    for part in new:
        if int(part['start']) * 512 != edge:
            raise RuntimeError('the new partitions are not exactly inside the freed extent')
        edge += int(part['size'])
    if edge != int(after[3]['start']) * 512 + int(after[3]['size']) + freed:
        raise RuntimeError('the new partitions do not exactly fill the freed extent')
    esps = [p for p in new if str(p.get('parttype')).lower() == ESP_TYPE]
    if len(esps) != 1:
        raise RuntimeError('Emaki did not create exactly one ESP (partition type EF00) of its own')
    esp = esps[0]
    version = run('blkid', '-p', '-o', 'value', '-s', 'VERSION', esp['path']).strip()
    if (esp['fstype'] != 'vfat' or version != 'FAT32' or esp['label'] != EMAKI_ESP_LABEL
            or int(esp['size']) != EMAKI_ESP_SIZE):
        raise RuntimeError(f"Emaki's ESP is not a 1 GiB FAT32 volume labelled {EMAKI_ESP_LABEL}: "
                           + json.dumps(dict(esp, version=version)))
    return esp


def firmware_entries(text):
    """{number: (label, partition GUID)} for efibootmgr -v entries with an HD() device path."""
    entries = {}
    for number, label, guid in re.findall(r'^Boot([0-9A-Fa-f]{4})\*? ([^\t]+?)\s+HD\(\d+,\w+,([0-9A-Fa-f-]{36}),',
                                          text, re.MULTILINE):
        entries[number.upper()] = (label.strip(), guid.lower())
    return entries


def check_installed_layout(baseline, esp):
    """On the installed system: /efi is Emaki's own ESP, rw, and the marker names both ESPs."""
    source, fstype, options = run('findmnt', '-nr', '--mountpoint', '/efi', '-o', 'SOURCE,FSTYPE,OPTIONS').split()
    if os.path.realpath(source) != os.path.realpath(esp['path']) or fstype != 'vfat':
        raise RuntimeError("/efi is not Emaki's own ESP: " + source)
    if 'rw' not in options.split(','):
        raise RuntimeError("Emaki's own ESP is not mounted read-write: " + options)
    rows = [line.split() for line in Path('/etc/fstab').read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    efi = [row for row in rows if len(row) == 6 and row[1] == '/efi']
    if (len(efi) != 1 or efi[0][0] != 'UUID=' + esp['uuid'] or efi[0][2] != 'vfat'
            or 'ro' in efi[0][3].split(',') or efi[0][5] != '2'):
        raise RuntimeError("fstab's /efi row is not Emaki's ESP, read-write, pass 2: " + repr(efi))
    status = MARKER.stat()
    if status.st_uid != 0 or status.st_gid != 0 or status.st_mode & 0o7777 != 0o644:
        raise RuntimeError(f'{MARKER} is not root-owned 0644')
    marker = json.loads(MARKER.read_text())
    expected = {'version': 1, 'esp_partuuid': baseline['windows_esp_partuuid'].lower(),
                'emaki_esp_partuuid': esp['partuuid'].lower(),
                'windows_esp_uuid': baseline['windows_esp_uuid'].upper()}
    found = {key: (value.upper() if key == 'windows_esp_uuid' else value.lower())
             if isinstance(value, str) else value for key, value in marker.items()}
    if found != expected:
        raise RuntimeError(f'{MARKER} does not describe the two ESPs: {marker!r}, expected {expected!r}')
    text = run('efibootmgr', '-v')
    entries = firmware_entries(text)
    order = re.search(r'^BootOrder: (\S+)$', text, re.MULTILINE)
    first = entries.get(order[1].split(',')[0].upper()) if order else None
    if first != ('Emaki', esp['partuuid'].lower()):
        raise RuntimeError("the first firmware entry is not Emaki on Emaki's own ESP: " + repr(first))
    if entries.get(baseline['windows_boot_number']) != ('Windows Boot Manager',
                                                        baseline['windows_esp_partuuid'].lower()):
        raise RuntimeError("Windows Boot Manager's firmware entry no longer names Windows' ESP")
    if any(guid == baseline['windows_esp_partuuid'].lower() and label != 'Windows Boot Manager'
           for label, guid in entries.values()):
        raise RuntimeError("a firmware entry other than Windows Boot Manager names Windows' ESP")
    print("PASS: /efi is Emaki's own ESP (rw, fstab pass 2); alongside-boot.json names both ESPs; "
          'Emaki first in BootOrder on its ESP, Windows Boot Manager kept on Windows ESP.')


def check(baseline, freed):
    current = disk_info()
    before = {int(p['partn']): p for p in baseline['disk']['children']}
    after = {int(p['partn']): p for p in current['children']}
    if len(after) != len(before) + (2 if freed else 0):
        raise RuntimeError('unexpected partition count')
    for number in before:
        for key in ('start', 'partuuid', 'parttype', 'partlabel', 'fstype'):
            if after[number][key] != before[number][key]:
                raise RuntimeError(f'partition {number} {key} changed')
        expected = int(before[number]['size']) - (freed if number == 3 else 0)
        if int(after[number]['size']) != expected:
            raise RuntimeError(f'partition {number} size mismatch')
    # Windows' ESP: never mounted, every byte as prepared (no mount here either).
    if any(after[1].get('mountpoints') or []):
        raise RuntimeError("Windows' ESP is mounted: " + repr(after[1]['mountpoints']))
    if 'esp_sha256' not in baseline:
        raise RuntimeError('the baseline has no Windows ESP digest; prepare the fixture again')
    if partition_digest(DISK + '-part1') != baseline['esp_sha256']:
        raise RuntimeError("Windows' ESP bytes changed")
    print("PASS: Windows' ESP not mounted and byte-identical; sha256=" + baseline['esp_sha256'])
    if freed:
        esp = emaki_esp(before, after, freed)
        with mounted(esp['path'], 'vfat') as root:
            check_removable_loader(root)
        if MARKER.is_file() and Path('/efi').is_mount():
            check_installed_layout(baseline, esp)
    with mounted(DISK + '-part3', 'ntfs-3g') as root:
        if hashes(root, baseline['ntfs']) != baseline['ntfs']:
            raise RuntimeError('ntfs file hashes changed')
    metadata = raw_gpt()
    for n in before:
        expected = gpt_metadata(baseline['gpt'][str(n)])
        if n == 3:
            expected['Last sector'] -= freed // 512
        assert metadata[n] == expected
    if 'recovery_sha256' in baseline:
        if partition_digest(DISK + '-part4') != baseline['recovery_sha256']:
            raise RuntimeError('recovery partition bytes changed')
    if freed and Path('/run/archiso/bootmnt').exists():
        dirty = subprocess.run(['ntfsresize', '--info', '--no-action', DISK + '-part3'],
                               capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
        assert dirty.returncode != 0 and 'Volume is scheduled for check' in dirty.stdout + dirty.stderr
        print('PASS: post-resize dirty flag remains set for Windows chkdsk; read-only metadata/data checks succeeded.')
    print('PASS: NTFS mounted read-only; files, Windows ESP bytes, partition GUID/type/name/start preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check'])
    parser.add_argument('--freed', type=int, default=0)
    parser.add_argument('--esp-mib', type=int, choices=(100, 260, 512), default=100,
                        help="size of the Windows ESP (Windows uses 260 MiB on 4Kn disks); Emaki never mounts or writes it")
    parser.add_argument('--no-fallback', action='store_true',
                        help='prepare an ESP without a removable loader')
    args = parser.parse_args()
    if os.geteuid() != 0 or run('systemd-detect-virt', '--vm').strip() not in ('qemu', 'kvm'):
        raise RuntimeError('requires root inside the disposable QEMU/KVM guest')
    if args.action == 'prepare':
        if 'emaki.test=1' not in Path('/proc/cmdline').read_text().split() or not Path('/run/archiso/bootmnt').exists():
            raise RuntimeError('requires the live test ISO')
        print(json.dumps(prepare(no_fallback=args.no_fallback, esp_mib=args.esp_mib), indent=2))
    else:
        check(json.load(sys.stdin), args.freed)


if __name__ == '__main__':
    main()
