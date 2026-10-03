#!/usr/bin/env python3
"""Inspect the finished ISO, including appended EFI images, without mounting it."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def check_loaders(directory, test_mode=False):
    count = 0
    for path in directory.rglob('*'):
        if not path.is_file() or path.suffix not in ('.cfg', '.conf'):
            continue
        text = path.read_text()
        if not test_mode and 'emaki.test' in text:
            raise ValueError(f'test kernel argument in release loader: {path}')
        for line in text.splitlines():
            # systemd-boot's linux and SYSLINUX's LINUX only select a kernel;
            # parameters live on options/APPEND. GRUB combines them in .cfg.
            pattern = r'^\s*(?:options|APPEND' + (r'|linux(?:efi)?' if path.suffix == '.cfg' else '') + r')\s+'
            if re.match(pattern, line) and (
                    'archiso' in line or 'vmlinuz-linux' in line):
                count += 1
                params = line.split()
                if [p for p in params if p.startswith('copytoram=')] != ['copytoram=n']:
                    raise ValueError(f'live entry must retain copytoram=n: {path}')
                if test_mode and 'emaki.test=1' not in params:
                    raise ValueError(f'test entry missing emaki.test=1: {path}')
    if not count:
        raise ValueError(f'no live loader entries extracted: {directory}')


def check_root_listing(listing, test_mode=False):
    paths = [line.split(' -> ', 1)[0] for line in listing.splitlines()]
    if not any(p.startswith('squashfs-root/') for p in paths):
        raise ValueError('empty or unrecognized squashfs listing')
    for name in ('home/live/.ssh', 'etc/emaki-test'):
        prefix = 'squashfs-root/' + name
        present = any(p == prefix or p.startswith(prefix + '/') for p in paths)
        if present != test_mode:
            raise ValueError(f'unexpected test material state in airootfs: /{name}')


def verify(image, test_mode=False):
    # Keep squashfs/EFI extraction on the build volume, not the live /tmp tmpfs.
    with tempfile.TemporaryDirectory(prefix='.emaki-image-check-', dir=image.parent) as temporary:
        work = Path(temporary)
        command = ['xorriso', '-abort_on', 'FAILURE', '-osirrox', 'on', '-indev', str(image)]
        for source, destination in (('/boot', 'boot'), ('/loader', 'loader'),
                                    ('/emaki/x86_64/airootfs.sfs', 'airootfs.sfs'),
                                    ('/emaki/repo/emaki-offline.db', 'emaki-offline.db'),
                                    ('/emaki/repo/emaki-offline.db.tar.gz', 'emaki-offline.db.tar.gz')):
            command += ['-extract', source, str(work / destination)]
        command += ['-extract_boot_images', str(work / 'boot-images')]
        subprocess.run(command, check=True)
        # repo-add makes emaki-offline.db a relative symlink to the .tar.gz; xorriso restores
        # the link itself, so check the link exists and read the size of its target file.
        database = work / 'emaki-offline.db'
        if not (database.is_symlink() or database.is_file()):
            raise ValueError('mastered offline repository DB missing')
        if not (work / 'emaki-offline.db.tar.gz').stat().st_size:
            raise ValueError('empty mastered offline repository DB')
        check_loaders(work / 'boot', test_mode)
        check_loaders(work / 'loader', test_mode)
        # xorriso documents these names for El Torito and MBR/GPT EFI images:
        # https://man.archlinux.org/man/xorriso.1#extract_boot_images
        images = sorted(set((work / 'boot-images').glob('*_efi.img')) |
                        set((work / 'boot-images').glob('*_uefi.img')))
        if not images:
            raise ValueError('no mastered EFI boot image extracted')
        for index, efi in enumerate(images):
            destination = work / f'efi-{index}'
            destination.mkdir()
            subprocess.run(['mcopy', '-s', '-i', str(efi), '::/loader', str(destination)], check=True)
            check_loaders(destination, test_mode)
        result = subprocess.run(['unsquashfs', '-l', str(work / 'airootfs.sfs')],
                                check=True, text=True, capture_output=True)
        check_root_listing(result.stdout, test_mode)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    try:
        verify(args.image, args.test)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'ERROR: mastered image verification failed: {error}')
