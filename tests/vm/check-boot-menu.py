#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the candidate ISO's actual UEFI and BIOS menus at the display-size matrix.

Exit 3 means NOT TESTED: saved frames still need visible-content/readability
judgment. An ISO digest is mandatory; no replacement bootloader is constructed.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
SIZES = ((1366, 768), (1920, 1080), (3840, 2160), (1280, 800), (2560, 1600), (1024, 768))
SCREENS = {(1366, 768): 15.6, (1920, 1080): 15.6, (3840, 2160): 27.0,
           (1280, 800): 13.3, (2560, 1600): 13.3, (1024, 768): 15.0}
MIN_CAP_MM = 2.4
CASES = tuple(('uefi', size) for size in SIZES) + (('bios', (1024, 768)),)
LEGACY_CASES = tuple(('uefi', size) for size in SIZES if size != (1024, 768))


def detect_bootloader(image):
    spec = importlib.util.spec_from_file_location('image_verifier', HERE.parents[1] / 'iso/verify-image.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier.detect_bootloader(image)


def boot_cases(loader):
    if loader == 'grub':
        return CASES
    if loader == 'systemd-boot':
        return LEGACY_CASES
    raise ValueError('unknown candidate boot loader')


def measure_caps(path, frame):
    """Recompute cap ink height from reviewed glyph regions, never a claimed font size.

    Reviewer identifies three separate capitals in actual menu entries. The fixed
    colour tolerance excludes antialias fringes; bounds include two pixels of
    background on all sides so clipped crops cannot inflate the result.
    """
    from PIL import Image
    samples = frame.get('capitals')
    if not isinstance(samples, list) or len(samples) < 3:
        raise ValueError('readability needs at least three reviewed capital crops')
    size = tuple(frame['advertised_size'])
    diagonal = SCREENS[size]
    physical_height = 25.4 * diagonal * size[1] / math.hypot(*size)
    results, boxes = [], []
    with Image.open(path) as source:
        pixels = source.convert('RGB')
        for sample in samples:
            if sample.get('letter') not in list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                raise ValueError('capital sample must identify one uppercase Latin letter')
            box = sample.get('box')
            rgb = sample.get('foreground')
            if (not isinstance(box, list) or len(box) != 4 or
                    any(type(v) is not int for v in box) or
                    not 0 <= box[0] < box[2] <= pixels.width or
                    not 0 <= box[1] < box[3] <= pixels.height):
                raise ValueError('invalid capital crop')
            if any(box[0] < other[2] and box[2] > other[0] and
                   box[1] < other[3] and box[3] > other[1] for other in boxes):
                raise ValueError('capital crops must be separate')
            boxes.append(box)
            if (not isinstance(rgb, list) or len(rgb) != 3 or
                    any(type(v) is not int or not 0 <= v <= 255 for v in rgb)):
                raise ValueError('invalid capital foreground colour')
            crop = pixels.crop(box)
            ink = [(x, y) for y in range(crop.height) for x in range(crop.width)
                   if max(abs(a - b) for a, b in zip(crop.getpixel((x, y)), rgb)) <= 32]
            if not ink:
                raise ValueError('capital crop contains no foreground pixels')
            left, right = min(x for x, y in ink), max(x for x, y in ink)
            top, bottom = min(y for x, y in ink), max(y for x, y in ink)
            if left < 2 or top < 2 or right >= crop.width - 2 or bottom >= crop.height - 2:
                raise ValueError('capital crop must include background padding')
            if set(y for x, y in ink) != set(range(top, bottom + 1)):
                raise ValueError('capital foreground must span consecutive rows')
            height = bottom - top + 1
            # Full-screen scaling of the captured framebuffer onto the reference panel.
            millimetres = height * physical_height / pixels.height
            results.append(dict(letter=sample['letter'], pixels=height, mm=millimetres))
    if min(item['mm'] for item in results) < MIN_CAP_MM:
        raise ValueError(f'capital height below {MIN_CAP_MM} mm: ' +
                         ', '.join(f"{item['mm']:.3f}" for item in results))
    return dict(diagonal_inches=diagonal, minimum_mm=MIN_CAP_MM, capitals=results)

spec = importlib.util.spec_from_file_location('iso_shot', HERE / 'iso-shot.py')
shot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shot)


def checked_iso(path, expected):
    path = Path(path).resolve(strict=True)
    if ',' in str(path):
        raise ValueError('ISO path must not contain commas')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected.lower():
        raise ValueError('candidate ISO SHA-256 mismatch')
    return path, actual


def qemu_command(iso, variables, directory, size, firmware="uefi"):
    width, height = size
    device = (f'virtio-gpu-pci,xres={width},yres={height}' if firmware == 'uefi'
              else f'VGA,xres={width},yres={height},vgamem_mb=64')
    command = ['qemu-system-x86_64', '-accel', 'tcg', '-machine', 'q35',
            '-m', '2048', '-nodefaults', '-device', device,
            '-cdrom', str(iso), '-boot', 'order=d', '-display', 'none',
            '-monitor', 'unix:mon.sock,server=on,wait=off', '-net', 'none']
    if firmware == 'uefi':
        command += ['-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd',
                    '-drive', f'if=pflash,format=raw,file={variables}']
    elif firmware != 'bios':
        raise ValueError('unknown firmware')
    return command


def capture_matrix(iso, digest, output, menu_config=None, menu_size=None):
    loader = detect_bootloader(iso)
    cases = boot_cases(loader)
    output.mkdir(parents=True, exist_ok=True)
    mode = None
    if bool(menu_config) != bool(menu_size):
        raise ValueError('--menu-config and --menu-size must be supplied together')
    if menu_config:
        content = Path(menu_config).read_bytes()
        config_hash = hashlib.sha256(content).hexdigest()
        source = 'menu-config-' + config_hash + '.txt'
        (output / source).write_bytes(content)
        mode = dict(size=menu_size, source=source, sha256=config_hash)
    evidence = {'iso': str(iso), 'sha256': digest, 'bootloader': loader, 'judgment': 'NOT TESTED', 'frames': []}
    (output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    mismatches = []
    for firmware, size in cases:
        name = firmware + "-" + f'{size[0]}x{size[1]}'
        with tempfile.TemporaryDirectory(prefix='boot-menu-', dir=output) as temporary:
            directory = Path(temporary)
            variables = directory / 'vars.fd'
            if firmware == 'uefi':
                shutil.copy('/usr/share/edk2/x64/OVMF_VARS.4m.fd', variables)
            with (output / (name + '.log')).open('w') as log:
                proc = subprocess.Popen(qemu_command(iso, variables, directory, size, firmware),
                                        stdout=log, stderr=subprocess.STDOUT, cwd=directory,
                                        env=dict(os.environ, TMPDIR=temporary))
                try:
                    started = time.monotonic()
                    while not (directory / 'mon.sock').exists():
                        if proc.poll() is not None or time.monotonic() - started > 45:
                            raise RuntimeError('QEMU monitor did not start')
                        time.sleep(.1)
                    # Keep a timeline: elapsed time alone does not identify the menu.
                    for second in (5, 10, 15):
                        time.sleep(max(0, started + second - time.monotonic()))
                        destination = output / f'{name}-{second:02}.png'
                        args = argparse.Namespace(output=str(destination), dir=str(directory), monitor=True)
                        if not shot.screenshot(args):
                            raise RuntimeError(f'host display capture failed: {destination}')
                        metrics = shot.frame_metrics(destination)
                        evidence['frames'].append(dict(metrics, firmware=firmware, advertised_size=list(size),
                                                       observed_mode=f"{metrics['width']}x{metrics['height']}",
                                                       screen_diagonal_inches=SCREENS[size],
                                                       elapsed_seconds=second, file=destination.name,
                                                       sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
                        if mode:
                            evidence['frames'][-1]['menu_mode'] = mode
                        if loader == 'grub' and firmware == 'uefi' and (metrics['width'], metrics['height']) != size:
                            mismatches.append(f'{destination.name}: observed {metrics["width"]}x{metrics["height"]}, '
                                              f'requested mode {size[0]}x{size[1]}')
                        if firmware == 'bios':
                            evidence['frames'][-1]['mode_note'] = (
                                'BIOS uses VGA; the Syslinux menu configures 640x480 independently of the advertised panel.')
                        (output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
    if mismatches:
        raise RuntimeError('frame differs from requested mode: ' + '; '.join(mismatches))
    print('NOT TESTED: ISO menu identity, content and readability require judgment of the captured timeline; '
          'advertised display sizes do not prove the loader selected them.')
    return 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--sha256', required=True, help='expected candidate ISO SHA-256')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--menu-config', type=Path, help='configuration extracted from this candidate loader')
    parser.add_argument('--menu-size', help='configured mode being checked, WIDTHxHEIGHT')
    args = parser.parse_args(argv)
    iso, digest = checked_iso(args.iso, args.sha256)
    return capture_matrix(iso, digest, args.out.resolve(), args.menu_config, args.menu_size)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f'BAD: ISO boot-menu capture: {error}')
