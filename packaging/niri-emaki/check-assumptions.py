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

# Reviewed source: v26.04 with this package's fourteen patches. Whole-file hashes
# deliberately reject even benign edits: a new width-changing layout path or
# a new action/render path must not slip past a narrow text-pattern check.
REVIEWED = {
    'src/trusted_keyboard.rs': (
        '558841f99d0eb5b32bfa26cd66337028e024c6418f384d7416f205aa133e61f3',
        'startup keymap snapshot and physical safety matching (B2b2)'),
    'src/tests/trusted_binds.rs': (
        '814e96be2eb42f0a73f2a9007fc73e34d686e1cf2d083f4b533d44fcc9f33988',
        'physical keymap reload, binding and capture regressions (B2b2)'),
    'src/trusted_binds.rs': (
        '0b200bab287266d2d03ed090abafba62de33b24ec838d13ef734136d959795b4',
        'startup safety capture, exact packaged identity and missing-bind recovery (B2b2)'),
    'src/trusted_client.rs': (
        'c686a83241bba6c26355b3796e3514513ca0c268ac5466306d4491e1c69d68ec',
        'private connection, immutable launch environment, bounded IPC heartbeat restart and the shell PID on its casts and one-shot login delivery (A6, N81S, FX9, FX19)'),
    'src/trusted_prompt.rs': (
        'd1ee11d459499068f9d93efae92c93014f896dc92238f3a8f8fd81d11b6643b5',
        'trusted role input boundary, lock gating and proof rendering (A6)'),
    'src/handlers/mod.rs': (
        '3f6d54cf1a7d3fbfc0a8c6877d759eda58eb784d045bf278d0ac5930efb191f1',
        'virtual pointer, selection and drag handlers (A6)'),
    'src/handlers/xdg_shell.rs': (
        'e0570bb9984804994a7a3213d66a253167bdf3772f4843a8d57ba6174cdb1eff',
        'popup, move and resize grab refusal (A6)'),
    'src/ipc/server.rs': (
        '8f9a23881f40d9aace0641bd79cb31813c77e2ceb06fb2e08f0ca0e048b65738',
        'fixed restart, trusted state and input-picking refusal (A6)'),
    'src/utils/spawning.rs': (
        '376613abb6f8078c4386988525ed80009c8ca84c66c727f1b47dad99423d7995',
        'child environment snapshot inputs (A6)'),
    'src/main.rs': (
        '6d14ddbb1e7d902815caa3a221dc6fea6f998fcf4e7b70d342a5aa4dcf1aff25',
        'startup-only trusted command and private login handoff capture (A6, FX19)'),
    'niri-config/src/lib.rs': (
        '2dce509c5d6d41a9a09806f5fc47c9b5e71436a010be118876a0d23cd3a5c212',
        'singleton trusted startup configuration (A6)'),
    'niri-ipc/src/lib.rs': (
        '831c00763bab005c1a2a3346f5a165463fba33c679aace839557c5ce190d5ab1',
        'fixed restart and trusted status protocol without a proof action (A6); the optional '
        'emaki_tile_pos_in_view and emaki_capture_safe window layout fields (X9)'),
    'src/tests/trusted_prompt.rs': (
        'bbe138c46e0ce6e7ec63e7eb8705fe60959e5f3d135af78879e3c22152f5f9b9',
        'identity, input, proof pixel, lock and cast PID regressions (A6, N81S)'),
    'src/handlers/layer_shell.rs': (
        '2f7a7d1462de5c7abd8ffd1d987929e9c49c0f6e7b41b9bb99f8e1a29b246e14',
        'persistent layer keyboard focus (N6c)'),
    'src/layer/mapped.rs': (
        '94a3c9fb9c39f3828dba0860bf5423e950855435c4bc530d4f9872f55801b561',
        'persistent layer keyboard focus (N6c)'),

    'src/layout/scrolling.rs': (
        '6244b4212cc3f6989a46c136bb2893b023647a17db9d6da6bf0af20b870f6046',
        'wallpaper camera assignments and width changes (465); fullscreen inset origin, '
        'height and IPC position (N81M4); tiled IPC positions at the target view (X9)'),
    'src/layout/workspace.rs': (
        'e9fc0ded2b22d6515f11f622666acab4e9dc0ad6edf722046404857024b6ba0c',
        'fullscreen inset marker accepted only from the trusted shell connection (N81M5)'),
    'src/layout/tests/emaki_wallpaper.rs': (
        '783df0bc03ece88a6632466353671dab954f8970da7b2a05dedd6bc4fcf78df3',
        'wallpaper camera regression coverage (465)'),
    'niri-config/src/binds.rs': (
        '9752be2505d6d8e8c83dc1ad8ea7b225a17115c0ee46f69d235dd6e337858cf0',
        'new navigation actions and the wildcard match (753)'),
    'src/input/mod.rs': (
        'b054b35957ac067ec606d9f2720d97de2455a9cb56116aa540ea94b3d2a96a90',
        'navigation action classification, safety dispatch and atomic window keyboard handover (753, B2b, X9c)'),
    'src/tests/emaki_switcher_handover.rs': (
        '47e7a5e26113077ac59bd458ae5f9dbbbba08735caddf204e0612e2d8d59bdbc',
        'window and previous-window keyboard delivery, both layer commit orders and focus boundaries (X9c)'),
    'src/cursor.rs': (
        'e9cc29129eafa86517afe0a2d6e90038dfe7678da9d9d37d2e2f1afa0c75240f',
        'geometry, damage and opaque-region output scales (754)'),
    'src/niri.rs': (
        'e7d90c681356183bc3909cfa8a22d76157dbaa532d263e1db407f2b04315724b',
        'own-layer exclusion, cursor scales, locked rendering static blur and keyboard arbitration, frozen bindings and the trusted shell PID (230, 464, 754, N6c, B2b, N81S)'),
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
    'src/tests/lock_capture.rs', 'src/trusted_prompt.rs',
}
CAPTURE_PROTOCOL = re.compile(r'image[_-]copy[_-]capture')
CURSOR_PATH = re.compile(r'\bCursorRenderElement\b|\brender_pointer\s*\(')
# Release code reads the fullscreen inset (workspace.rs); a #[cfg(test)] above its accessor
# compiles under `cargo test` but breaks `cargo build --release` (N81M5).
TEST_ONLY_INSET_ACCESSOR = re.compile(r'#\[cfg\(test\)\]\s*pub fn fullscreen_top_inset\b')


def check(source):
    failures = []
    for relative, (expected, reason) in REVIEWED.items():
        path = source / relative
        if not path.is_file():
            failures.append(f'{relative}: missing reviewed source ({reason})')
        elif sha256(path.read_bytes()).hexdigest() != expected:
            failures.append(f'{relative}: source changed; review {reason} and extend its tests')

    scrolling = source / 'src/layout/scrolling.rs'
    if scrolling.is_file() and TEST_ONLY_INSET_ACCESSOR.search(scrolling.read_text()):
        failures.append('src/layout/scrolling.rs: fullscreen_top_inset() is test-only but release '
                        'code calls it; a release build cannot compile (N81M5)')

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
    print('niri rebase guards passed (capture, wallpaper camera, navigation actions, cursor scale, layer focus, trusted prompt)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
