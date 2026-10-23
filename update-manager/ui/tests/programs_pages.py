#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise website and development pages with isolated, offscreen input."""
import argparse
from pathlib import Path
import tempfile
import interactions
import layout

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', help='Run against UI sources at this revision')
parser.add_argument('cases', nargs='*')
args = parser.parse_args()
failures = []
with tempfile.TemporaryDirectory(prefix='programs-pages-') as directory:
    source = layout.checkout(args.source, Path(directory) / 'source') if args.source else interactions.UI
    for scenario in args.cases or ('confirmation', 'add', 'guidance', 'style', 'loading', 'present', 'finish'):
        try:
            interactions.run(entry='ProgramsPagesTest.qml', source=source,
                             extra={'TEST_PAGE_CASE': scenario}, allow_restore_binding_warning=True)
            print('PASS programs pages', scenario)
        except AssertionError as error:
            failures.append(scenario)
            print('FAIL programs pages', scenario, str(error)[-1500:])
if failures:
    raise SystemExit(1)
