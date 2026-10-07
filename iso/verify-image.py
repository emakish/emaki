#!/usr/bin/env python3
"""Inspect the finished ISO, including appended EFI images, without mounting it."""
import argparse
import json
from pathlib import Path
import posixpath
import re
import shlex
import subprocess
import tempfile


def iso_files(image):
    """Keep exact ISO paths: boot payload lookup is case-sensitive."""
    result = subprocess.run(['xorriso', '-abort_on', 'FAILURE', '-indev', str(image),
                             '-find', '/', '-type', 'f', '-exec', 'echo', '--'],
                            check=True, text=True, capture_output=True)
    return set(shlex.split(result.stdout))


def detect_bootloader(image, paths=None):
    """Select acceptance policy from the candidate filesystem, never its filename."""
    paths = {path.lower() for path in (iso_files(image) if paths is None else paths)}
    fallback = '/efi/boot/bootx64.efi' in paths
    systemd = any(re.fullmatch(r'/loader/entries/[^/]+\.conf', path) for path in paths)
    grub = '/boot/grub/grub.cfg' in paths
    if not fallback or systemd == grub:
        raise ValueError('unknown or ambiguous mastered UEFI boot loader')
    return 'systemd-boot' if systemd else 'grub'


def check_graphics_entries(text, path, require_safe=False):
    """Only explicit troubleshooting entries may disable kernel modesetting."""
    safe = False
    count = 0
    for line in text.splitlines():
        if re.match(r'^\s*(?:menuentry |LABEL )', line):
            safe = bool(re.search(r"--id 'archlinux-safe'|^LABEL arch(?:_(?:nbd|nfs|http))?_safe$", line))
        if re.match(r'^\s*(?:linux(?:efi)?|APPEND)\s+', line) and (
                'archiso' in line or 'vmlinuz-linux' in line):
            disabled = line.split().count('nomodeset')
            if disabled != int(safe):
                raise ValueError(f'nomodeset must appear only in safe graphics entries: {path}')
            count += int(safe)
    expected = {'grub.cfg': 1, 'loopback.cfg': 1,
                'archiso_sys-linux.cfg': 1, 'archiso_pxe-linux.cfg': 3}
    if require_safe and path.name in expected and count != expected[path.name]:
        raise ValueError(f'missing or duplicate safe graphics entries: {path}')


def check_boot_payload(directory, paths, prefix='/boot'):
    """Check mastered loader references against files in the ISO, not the host."""
    count = 0
    for config in sorted(directory.rglob('*')):
        if not config.is_file() or config.suffix not in ('.cfg', '.conf'):
            continue
        location = posixpath.join(prefix, config.relative_to(directory).as_posix())
        for number, line in enumerate(config.read_text().splitlines(), 1):
            match = re.match(r'^\s*(linux(?:efi|16)?|kernel|initrd(?:efi|16)?|append)(?:\s+(.*))?$',
                             line, re.IGNORECASE)
            if not match:
                continue
            command, arguments = match.groups()
            command = command.lower()
            words = shlex.split(arguments or '', comments=True)
            if command == 'append':
                names = [name for word in words if word.lower().startswith('initrd=')
                         for name in word.split('=', 1)[1].split(',')]
            elif command.startswith('initrd'):
                names = [name for word in words for name in word.split(',')]
            else:
                names = [word for word in words if not word.startswith('--')][:1]
            if not names and command != 'append':
                raise ValueError(f'empty mastered boot reference: {location}:{number}')
            for name in names:
                # SYSLINUX PXE uses ::/ for the same payload served from the ISO.
                name = name.removeprefix('::')
                if not name or re.search(r'[$%{}]', name):
                    raise ValueError(f'unresolved mastered boot reference: {location}:{number}: {name}')
                target = posixpath.normpath(posixpath.join(posixpath.dirname(location), name))
                if target not in paths:
                    raise ValueError(f'missing mastered boot payload: {location}:{number}: {target}')
                count += 1
    return count


def check_loaders(directory, test_mode=False, check_assets=False, require_safe=False):
    count = 0
    for path in directory.rglob('*'):
        if not path.is_file() or path.suffix not in ('.cfg', '.conf'):
            continue
        text = path.read_text()
        check_graphics_entries(text, path, require_safe)
        if check_assets and path.name == 'loopback.cfg':
            pattern = r'^\s*background_image\s+(?:(?:--mode(?:=|\s+)|-m\s*)(?:stretch|normal)\s+)?(/boot/\S+)\s*$'
            for name in re.findall(pattern, text, re.M):
                asset = directory / name.removeprefix('/boot/')
                if not asset.is_file() or not asset.stat().st_size:
                    raise ValueError(f'missing or empty loopback background: {name}')
        if not test_mode and 'emaki.test' in text:
            raise ValueError(f'test kernel argument in release loader: {path}')
        for line in text.splitlines():
            # SYSLINUX puts parameters on APPEND; GRUB combines them with linux.
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


def requires_hygiene(version):
    """The frozen releases through 0.2.0 predate live-root pruning."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('invalid mastered image version')
    return tuple(map(int, version.split('.'))) > (0, 2, 0)


def check_root_listing(listing, test_mode=False, check_hygiene=True):
    # Long listings include symlink targets; retain plain listings for callers
    # which only check path policy.
    entries = {}
    for line in listing.splitlines():
        if not line.startswith('squashfs-root'):
            fields = line.split(None, 5)
            if len(fields) != 6:
                continue
            line = fields[5]
        if line == 'squashfs-root' or line.startswith('squashfs-root/'):
            name, separator, target = line.partition(' -> ')
            entries['/' + name.removeprefix('squashfs-root').lstrip('/')] = target if separator else None
    paths = ['squashfs-root' + name for name in entries]
    if check_hygiene:
        check_unit_links(entries)
    if not any(p.startswith('squashfs-root/') for p in paths):
        raise ValueError('empty or unrecognized squashfs listing')
    # Runtime typelibs and documentation-backed license links remain intact.
    for name in ('usr/include', 'usr/share/gtk-doc', 'usr/share/gir-1.0'):
        if check_hygiene and any(p.startswith('squashfs-root/' + name + '/') for p in paths):
            raise ValueError(f'development data remains in live root: /{name}')
    # pacstrap runs the live /etc/pacman.d/hooks for the installed target; any hook there
    # shadows or adds to the target's own (a 0.3.0 candidate made no kernel presets).
    hooks = [p for p in paths if p.startswith('squashfs-root/etc/pacman.d/hooks/')]
    if check_hygiene and entries.get('/etc/pacman.d/hooks') is not None:
        raise ValueError('the live /etc/pacman.d/hooks is a symlink')
    if check_hygiene and hooks:
        raise ValueError('the live /etc/pacman.d/hooks is not empty (pacstrap reads it for the target): '
                         + ', '.join(p.removeprefix('squashfs-root') for p in hooks))
    for name in ('home/live/.ssh', 'etc/emaki-test'):
        prefix = 'squashfs-root/' + name
        present = any(p == prefix or p.startswith(prefix + '/') for p in paths)
        if present != test_mode:
            raise ValueError(f'unexpected test material state in airootfs: /{name}')


def check_unit_links(entries):
    """Resolve unit links inside the image, including directory aliases."""
    def present(name):
        for _ in range(40):
            name = posixpath.normpath(name)
            if name == '/dev/null':
                return True  # A masked unit; /dev is populated when booting.
            parts = name.lstrip('/').split('/')
            for index in range(len(parts)):
                prefix = '/' + '/'.join(parts[:index + 1])
                target = entries.get(prefix)
                if target is not None:
                    name = posixpath.join(posixpath.dirname(prefix), target, *parts[index + 1:])
                    break
            else:
                return name in entries
        return False

    for name, target in entries.items():
        if target is not None and re.match(r'^/(?:etc|usr/lib|usr/local/lib)/systemd/(?:system|user)/', name):
            if not present(name):
                raise ValueError(f'dangling unit link in live root: {name} -> {target}')


def check_grub_assets(boot):
    grub = boot / 'grub'
    for name in ('grub.cfg', 'loopback.cfg', 'background.png'):
        path = grub / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f'mastered GRUB asset missing or empty: {path}')
    # Require the exact fonts requested by either menu, not an unrelated PF2.
    references = set()
    for name in ('grub.cfg', 'loopback.cfg'):
        references.update(re.findall(r'/boot/grub/([\w./-]+\.pf2)',
                                    (grub / name).read_text()))
    if not references:
        raise ValueError('mastered GRUB menus have no staged font references')
    for name in references:
        font = grub / name
        if not font.is_file() or not font.stat().st_size:
            raise ValueError(f'mastered GRUB font missing or empty: {font}')


def efi_images(directory):
    images = sorted(set(directory.glob('*_efi.img')) |
                    set(directory.glob('*_uefi.img')))
    if not images:
        raise ValueError('no mastered EFI boot image extracted')
    return images


def inspect_efi(image, reject_payload=True):
    result = subprocess.run(['mdir', '-s', '-b', '-i', str(image), '::/'],
                            check=True, text=True, capture_output=True)
    paths = sorted(line.strip().removeprefix('::').lstrip('/')
                   for line in result.stdout.splitlines() if line.strip())
    if 'efi/boot/bootx64.efi' not in {p.lower() for p in paths}:
        raise ValueError(f'EFI fallback binary missing: {image}')
    payload = [p for p in paths if re.match(r'(?:vmlinuz|initramfs)',
                                           Path(p).name, re.IGNORECASE)]
    if reject_payload and payload:
        raise ValueError(f'kernel/initramfs duplicated in EFI image: {payload}')
    record = {'image': image.name, 'bytes': image.stat().st_size, 'contents': paths}
    print(f"EFI image {image.name}: {record['bytes']} bytes")
    for path in paths:
        print('  /' + path)
    return record


def verify(image, test_mode=False, efi_report=None, compare_efi=None):
    paths = iso_files(image)
    loader = detect_bootloader(image, paths)
    # Large extracted images stay out of /tmp, including when the input ISO lives there.
    evidence = Path(__file__).resolve().parent.parent / '.cache/evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='image-check-', dir=evidence) as temporary:
        work = Path(temporary)
        command = ['xorriso', '-abort_on', 'FAILURE', '-osirrox', 'on', '-indev', str(image)]
        for source, destination in (('/boot', 'boot'),
                                    ('/emaki/version', 'version'),
                                    ('/emaki/x86_64/airootfs.sfs', 'airootfs.sfs'),
                                    ('/emaki/repo/emaki-offline.db', 'emaki-offline.db'),
                                    ('/emaki/repo/emaki-offline.db.tar.gz', 'emaki-offline.db.tar.gz')):
            command += ['-extract', source, str(work / destination)]
        if loader == 'systemd-boot':
            command += ['-extract', '/loader', str(work / 'loader')]
        command += ['-extract_boot_images', str(work / 'boot-images')]
        subprocess.run(command, check=True)
        # xorriso restores the repo-add symlink; check its target separately.
        database = work / 'emaki-offline.db'
        if not (database.is_symlink() or database.is_file()):
            raise ValueError('mastered offline repository DB missing')
        if not (work / 'emaki-offline.db.tar.gz').stat().st_size:
            raise ValueError('empty mastered offline repository DB')
        # Read the mastered version, never the filename or caller-supplied metadata.
        check_hygiene = requires_hygiene((work / 'version').read_text().strip())
        check_loaders(work / 'boot', test_mode,
                      check_assets=check_hygiene or loader == 'grub',
                      require_safe=check_hygiene and loader == 'grub')
        check_boot_payload(work / 'boot', paths)
        if loader == 'grub':
            check_grub_assets(work / 'boot')
        else:
            check_loaders(work / 'loader', test_mode)
            check_boot_payload(work / 'loader', paths, '/loader')
        images = []
        for index, efi in enumerate(efi_images(work / 'boot-images')):
            record = inspect_efi(efi, reject_payload=loader == 'grub')
            entries = any(re.fullmatch(r'loader/entries/[^/]+\.conf', path.lower())
                          for path in record['contents'])
            if entries != (loader == 'systemd-boot'):
                raise ValueError('ISO and EFI image boot loaders disagree')
            if loader == 'systemd-boot':
                destination = work / f'efi-{index}'
                destination.mkdir()
                subprocess.run(['mcopy', '-s', '-i', str(efi), '::/loader', str(destination)], check=True)
                check_loaders(destination, test_mode)
                check_boot_payload(destination, paths, '/')
            images.append(record)
        report = {'after': {'iso': str(image.resolve()), 'loader': loader, 'images': images}}
        if compare_efi is not None:
            subprocess.run(['xorriso', '-abort_on', 'FAILURE', '-osirrox', 'on',
                            '-indev', str(compare_efi), '-extract_boot_images',
                            str(work / 'baseline-images')], check=True)
            report['before'] = {'iso': str(compare_efi.resolve()), 'images': [
                inspect_efi(efi, reject_payload=False)
                for efi in efi_images(work / 'baseline-images')]}
        result = subprocess.run(['unsquashfs', '-ll', str(work / 'airootfs.sfs')],
                                check=True, text=True, capture_output=True)
        check_root_listing(result.stdout, test_mode, check_hygiene=check_hygiene)
        if efi_report is not None:
            efi_report.write_text(json.dumps(report, indent=2) + '\n')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--efi-report', type=Path, help='write exact EFI byte sizes and full listings as JSON')
    parser.add_argument('--compare-efi', type=Path, help='also inspect EFI images from a baseline ISO')
    args = parser.parse_args()
    try:
        verify(args.image, args.test, args.efi_report, args.compare_efi)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'ERROR: mastered image verification failed: {error}')
