#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check candidate provenance before release-gate consumes external evidence."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

from menu_mode import MissingMode, configured_size

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('boot_menu_capture', HERE / 'check-boot-menu.py')
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local_file(directory, name):
    if not isinstance(name, str) or not name.strip() or Path(name).is_absolute():
        raise ValueError('missing evidence file path')
    path = (directory / name).resolve(strict=True)
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('evidence file must be inside its record directory')
    return path


def rollback(args):
    manifest = json.loads(args.provenance.read_text())
    if not args.candidate.strip() or manifest.get('candidate') != args.candidate:
        raise ValueError('rollback candidate identity mismatch')
    hashes = manifest['sha256']
    for name, path in [('iso', args.iso), ('target.qcow2', args.base / 'target.qcow2'),
                       ('OVMF_VARS.4m.fd', args.base / 'OVMF_VARS.4m.fd'), ('plan', args.plan)]:
        if hashes.get(name) != digest(path):
            raise ValueError('rollback provenance checksum mismatch: ' + name)
    packages = manifest['packages']
    if not {'emaki', 'emaki-config', 'emaki-desktop'} <= packages.keys():
        raise ValueError('rollback provenance lacks required packages')
    if not all(isinstance(version, str) and version.strip() for version in packages.values()):
        raise ValueError('rollback provenance has invalid package versions')
    print('Candidate rollback fixture provenance matches; acceptance must still run')
    return 0


def boot_menu(args):
    record = args.directory / 'evidence.json'
    if not record.is_file():
        print('NOT TESTED: no boot-menu evidence.json')
        return 3
    manifest = json.loads(record.read_text())
    if manifest.get('sha256') != digest(args.iso):
        raise ValueError('boot-menu candidate ISO checksum mismatch')
    loader = menu.detect_bootloader(args.iso)
    cases = set(menu.boot_cases(loader))
    spec = importlib.util.spec_from_file_location('iso_shot', HERE / 'iso-shot.py')
    shot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shot)
    judged = set()
    for frame in manifest['frames']:
        path = local_file(args.directory, frame['file'])
        metrics = shot.frame_metrics(path)
        verdict = frame.get('judgment', 'NOT TESTED')
        if verdict not in ('PASS', 'CONFUSING', 'NOT TESTED'):
            raise ValueError('boot-menu frame judged ' + str(verdict) + ': ' + frame['file'])
        if verdict not in ('PASS', 'CONFUSING'):
            continue
        if frame.get('sha256') != digest(path):
            raise ValueError('reviewed boot-menu frame checksum mismatch: ' + frame['file'])
        size = tuple(frame['advertised_size'])
        firmware = frame.get('firmware', 'uefi' if loader == 'systemd-boot' else None)
        if (firmware, size) not in cases:
            raise ValueError('unknown boot-menu firmware/display case')
        if firmware == 'uefi':
            try:
                expected = configured_size(args.directory, frame, size)
            except MissingMode:
                continue
        else:
            expected = (metrics['width'], metrics['height'])
        if expected != (metrics['width'], metrics['height']):
            raise ValueError('boot-menu pixels differ from the configured mode: ' + frame['file'])
        if not isinstance(frame.get('reviewer'), str) or not frame['reviewer'].strip():
            raise ValueError('reviewed boot-menu frame lacks reviewer')
        if datetime.fromisoformat(frame.get('reviewed_at', '')).tzinfo is None:
            raise ValueError('boot-menu review time must include timezone')
        if verdict == 'CONFUSING' and not local_file(args.directory, frame.get('waiver')).read_text().strip():
            raise ValueError('boot-menu waiver is empty')
        if loader == 'grub' and firmware == 'uefi' and expected != size:
            raise ValueError('UEFI menu must use the native advertised mode')
        if loader == 'grub':
            if not frame.get('capitals'):
                continue
            measured = menu.measure_caps(path, frame)
            print(frame['file'] + ': ' + json.dumps(measured, sort_keys=True))
        judged.add((firmware, size))
    missing = cases - judged
    if missing:
        print('NOT TESTED: boot-menu lacks reviewed configured-mode frames for advertised displays ' +
              ', '.join(f'{firmware} {w}x{h}' for firmware, (w, h) in sorted(missing)))
        return 3
    print('Boot-menu candidate frames have recorded reviews for every required advertised display size')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='job', required=True)
    rb = sub.add_parser('rollback')
    for name in ('iso', 'base', 'plan', 'provenance'):
        rb.add_argument('--' + name, type=Path, required=True)
    rb.add_argument('--candidate', required=True)
    boot = sub.add_parser('boot-menu')
    boot.add_argument('--iso', type=Path, required=True)
    boot.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    try:
        return rollback(args) if args.job == 'rollback' else boot_menu(args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'FAIL: {args.job} evidence: {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
