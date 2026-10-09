#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stop a rebase until changes to non-exhaustive fork assumptions are reviewed.

Run against the source AFTER applying every package patch. These fingerprints
are review gates, not behavioral proofs. Do not regenerate them automatically:
follow the corresponding section of packaging/REBASE.md, extend the Rust tests,
and only then record the reviewed source hashes here.
"""
from hashlib import sha256
from pathlib import Path
import re
import sys

# Reviewed source: v26.04 with this package's nine patches. Whole-file hashes
# deliberately reject even benign edits: a new width-changing layout path or
# a new action/render path must not slip past a narrow text-pattern check.
REVIEWED = {
    'src/handlers/layer_shell.rs': (
        '3c0136bcf386537c49551fc33045599dbd57dfe7123e7c8274b1f066476a4a03',
        'persistent layer keyboard focus (N6c)'),
    'src/layer/mapped.rs': (
        '94a3c9fb9c39f3828dba0860bf5423e950855435c4bc530d4f9872f55801b561',
        'persistent layer keyboard focus (N6c)'),

    'src/layout/scrolling.rs': (
        '96205fdf0b376d3321648b083f91ca89df380cd5d05b06d19b3b2fa0e397984d',
        'wallpaper camera assignments and width changes (465)'),
    'src/layout/tests/emaki_wallpaper.rs': (
        '783df0bc03ece88a6632466353671dab954f8970da7b2a05dedd6bc4fcf78df3',
        'wallpaper camera regression coverage (465)'),
    'niri-config/src/binds.rs': (
        'd049c1b8a5c6c1b94ab8b6da128686134e90cc2e1d2c9418c21ae93517d19f7a',
        'new navigation actions and the wildcard match (753)'),
    'src/input/mod.rs': (
        '121c83be940ad84ef1d5b7c357ccff3cb88c5f373e67f0669263cb5b899d232e',
        'navigation action classification (753)'),
    'src/cursor.rs': (
        'e9cc29129eafa86517afe0a2d6e90038dfe7678da9d9d37d2e2f1afa0c75240f',
        'geometry, damage and opaque-region output scales (754)'),
    'src/niri.rs': (
        'f7198fba63a335443c03e443e32ef659300522b33324c72b80915985f2ffa4c2',
        'own-layer exclusion, cursor scales, locked rendering static blur and keyboard arbitration (230, 464, 754, N6c)'),
    'src/render_helpers/xray.rs': (
        'dac33e39cf6f79142cab756d6f93f2b61aab5a6ed980f49bbd32a114707d22c1',
        'static blur opaque coverage and geometry tests (230)'),
    'src/render_helpers/shaders/postprocess.frag': (
        '98f8e0727ba175e38986f689c5e315ddf31b14140e42091e391f41e3c47f2c1b',
        'static blur background alpha composition (230)'),
    'src/screencasting/mod.rs': (
        '9913848744951389449df9d67f73458d73a4d642e3abc5661b2929c415492abb',
        'cursor capture scales and locked window blanking (754)'),
}
CURSOR_PATHS = {
    'src/cursor.rs', 'src/niri.rs', 'src/screencasting/mod.rs',
    'src/tests/lock_capture.rs',
}
CAPTURE_PROTOCOL = re.compile(r'image[_-]copy[_-]capture')
CURSOR_PATH = re.compile(r'\bCursorRenderElement\b|\brender_pointer\s*\(')


def check(source):
    failures = []
    for relative, (expected, reason) in REVIEWED.items():
        path = source / relative
        if not path.is_file():
            failures.append(f'{relative}: missing reviewed source ({reason})')
        elif sha256(path.read_bytes()).hexdigest() != expected:
            failures.append(f'{relative}: source changed; review {reason} and extend its tests')

    cursor_paths = set()
    for path in sorted((source / 'src').rglob('*.rs')):
        text = path.read_text()
        relative = path.relative_to(source).as_posix()
        if CAPTURE_PROTOCOL.search(text):
            failures.append(f'{relative}: new image-copy capture protocol; propagate the requesting '
                            'client through every capture path and test own-layer exclusion')
        if CURSOR_PATH.search(text):
            cursor_paths.add(relative)
    if cursor_paths != CURSOR_PATHS:
        failures.append('cursor render path inventory changed: review output scale at every caller '
                        f'(found {sorted(cursor_paths)})')
    return failures


def main(argv):
    if len(argv) != 2:
        print(f'Usage: {argv[0]} PATCHED_NIRI_SOURCE', file=sys.stderr)
        return 2
    failures = check(Path(argv[1]))
    if failures:
        for failure in failures:
            print(f'niri rebase guard: {failure}', file=sys.stderr)
        print('See packaging/REBASE.md, Silent-break places; do not refresh hashes without review.',
              file=sys.stderr)
        return 1
    print('niri rebase guards passed (capture, wallpaper camera, navigation actions, cursor scale, layer focus)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
