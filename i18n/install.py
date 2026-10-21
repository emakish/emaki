#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy compiled catalogues with predictable package permissions."""
import argparse
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--destination', type=Path)
parser.add_argument('--check-built', action='store_true')
args = parser.parse_args()
base = Path(__file__).resolve().parent
source = base / 'locale'
if args.check_built:
    compiler_time = (base / 'catalogues.py').stat().st_mtime_ns
    for path in sorted(base.glob('*/*')):
        if path.suffix == '.ts':
            target = source / (path.parent.name + '_' + path.stem + '.qm')
        elif path.suffix == '.po':
            target = source / path.stem / 'LC_MESSAGES' / (path.parent.name + '.mo')
        else:
            continue
        if not target.is_file() or target.stat().st_mtime_ns < max(path.stat().st_mtime_ns, compiler_time):
            raise SystemExit('Missing or stale catalogue; run make build-i18n as your regular user: ' + str(target))
        if path.stem == 'pt_BR':
            alias = (source / (path.parent.name + '_pt.qm') if path.suffix == '.ts' else
                     source / 'pt' / 'LC_MESSAGES' / target.name)
            if not alias.is_file() or alias.read_bytes() != target.read_bytes():
                raise SystemExit('Missing or stale Portuguese fallback; run make build-i18n: ' + str(alias))
    raise SystemExit(0)
if args.destination is None:
    parser.error('--destination is required when copying catalogues')
for path in sorted(source.rglob('*')):
    if path.suffix not in ('.mo', '.qm'):
        continue
    target = args.destination / path.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    target.chmod(0o644)
