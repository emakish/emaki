#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Packaging and lock-capture rebase tripwires; not a replacement for VM proof.

Set NIRI_SOURCE_DIR to an unpacked tree with every packaged patch applied to
check the capture call graph and lock boundaries as well as patch packaging.
"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'packaging/niri-emaki'
PATCH = '0007-protect-session-pixels-while-locked.patch'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def array(text, name):
    match = re.search(r'^' + name + r'=\((.*?)\)', text, re.M | re.S)
    require(match, f'PKGBUILD: missing {name}')
    return shlex.split(match[1], comments=True)


def function(text, name):
    # Methods end at their declaration's indentation; nested blocks are deeper.
    match = re.search(r'^( *)[^\n]*\bfn ' + name + r'(?:\b|<)', text, re.M)
    require(match, f'missing function {name}; re-audit capture routing')
    end = re.search(r'^' + match[1] + r'}', text[match.end():], re.M)
    require(end, f'cannot delimit {name}')
    return text[match.start():match.end() + end.end()]


def ordered(text, *tokens):
    position = 0
    for token in tokens:
        found = text.find(token, position)
        require(found >= 0, f'missing/out-of-order security boundary: {token}')
        position = found + len(token)


def packaging():
    pkg = (PACKAGE / 'PKGBUILD').read_text()
    require(re.search(r'^pkgrel=11$', pkg, re.M), 'lock-capture package must be release 11')
    sources = array(pkg, 'source')
    require(sources.count(PATCH) == 1, f'{PATCH} must appear once in source')
    patches = [name for name in sources if name.endswith('.patch')]
    require(patches == sorted(patches), 'patches must retain their application order')
    prepare = pkg.split('prepare() {', 1)[1].split('\n}', 1)[0]
    applied = re.findall(r'patch -Np1 -i ../(\S+)', prepare)
    require(applied == patches, 'prepare() must apply every packaged patch in source order')
    ordered(prepare, f'patch -Np1 -i ../{PATCH}',
            'python "$srcdir/check-assumptions.py" . || return 1', 'cargo fetch')
    for name, digest in [('sha512sums', hashlib.sha512), ('b2sums', hashlib.blake2b)]:
        sums = array(pkg, name)
        require(len(sums) == len(sources), f'{name}: source/checksum count differs')
        for source, checksum in zip(sources, sums):
            if (PACKAGE / source).is_file():
                require(digest((PACKAGE / source).read_bytes()).hexdigest() == checksum,
                        f'{name}: checksum mismatch for {source}')
    patch = (PACKAGE / PATCH).read_text()
    require('--- a/src/niri.rs' in patch and '--- a/src/screencasting/mod.rs' in patch,
            'lock patch must cover output rendering and both window-cast callbacks')
    added = '\n'.join(line[1:] for line in patch.splitlines()
                      if line.startswith('+') and not line.startswith('+++'))
    for token in ('if is_locked {', 'if self.is_locked() {',
                  'same_client_as(&surface.id())', 'LockState::Locking',
                  'LockState::Locked', 'cannot screenshot a window while locked'):
        require(token in added, f'lock patch lost boundary: {token}')
    require(added.count('cast.dequeue_buffer_and_clear(renderer)') == 2,
            'both independent window-cast callbacks must clear while locked')


def source_assumptions(root):
    niri = (root / 'src/niri.rs').read_text()
    casts = (root / 'src/screencasting/mod.rs').read_text()
    lock_tests = (root / 'src/tests/lock_capture.rs').read_text()
    ordered(function(lock_tests, 'lock'), 'let id = f.add_client();',
            'f.roundtrip(id);', 'client.output("headless-1")')
    # New capture protocols require an explicit audit instead of silently inheriting
    # the claim that the current screencopy and PipeWire paths cover everything.
    unknown = re.compile(r'image_copy_capture|image_capture_source|export_dmabuf')
    for path in (root / 'src').rglob('*.rs'):
        require(not unknown.search(path.read_text()),
                f'{path}: new capture interface; audit its lock boundary and update this guard')
    inner = function(niri, 'render_inner')
    ordered(inner, 'self.exit_confirm_dialog', 'if self.is_locked()',
            'state.lock_surface', 'state.lock_color_buffer',
            'return;', 'self.config_error_notification',
            'state.screen_transition')
    wrapper = function(niri, 'render_with_startup_cover')
    ordered(wrapper, 'if self.is_locked()', 'self.render_inner(', 'return;', 'self.fill_')
    pointer = function(niri, 'render_pointer')
    ordered(pointer, 'RenderCursor::Surface', 'LockState::Locking', 'LockState::Locked',
            'same_client_as(&surface.id())', 'return;', 'push_elements_from_surface_tree')
    ordered(pointer, 'if self.is_locked()', 'return;', 'self.dnd_icon')
    for name, boundary, access, terminator in (
        ('redraw_cast', 'if is_locked {', 'self.niri.layout.windows()', 'return;'),
        ('render_windows_for_screen_cast', 'if self.is_locked() {',
         'self.layout.windows_for_output(output)', 'continue;'),
    ):
        body = function(casts, name)
        ordered(body, boundary, '!cast.is_blank()',
                'cast.dequeue_buffer_and_clear(renderer)', terminator, access)
    buffers = (root / 'src/screencasting/pw_utils.rs').read_text()
    ordered(function(buffers, 'dequeue_buffer_and_clear'), 'clear_dmabuf(',
            'Ok(sync_point)', 'is_blank = true', 'self.queue_after_sync(')
    ordered(function(buffers, 'dequeue_buffer_and_render'), 'render_to_dmabuf(',
            'Ok(sync_point)', 'is_blank = false', 'self.queue_after_sync(')
    ordered(function(niri, 'screenshot_window'), '!self.is_locked()', 'mapped.sizing_mode()')
    require('LockState::Locking(_) | LockState::Locked(_) => true' in function(niri, 'is_locked'),
            'is_locked changed; verify Locking and abandoned locks remain protected')
    for name in ('render_for_screencopy_with_damage', 'render_for_screencopy_without_damage'):
        body = function(niri, name)
        require('self.render(ctx, output,' in body, f'{name} bypasses central renderer')
    require('self.render(ctx, output,' in function(casts, 'render_for_screen_cast'),
            'output cast bypasses central renderer')
    for name in ('render', 'render_capture_to_vec'):
        require('self.render_with_startup_cover(' in function(niri, name),
                f'{name} bypasses locked render wrapper')
    require('.screenshot_all_outputs(' in function(niri, 'handle_take_screenshot'),
            'portal screenshot routing changed')
    require('self.render_to_vec(' in function(niri, 'screenshot_all_outputs'),
            'portal screenshot bypasses central renderer')
    require('self.render(ctx, output,' in function(niri, 'render_to_vec'),
            'render_to_vec bypasses locked render wrapper')
    require('self.render_capture_to_vec(' in function(niri, 'capture_screenshots'),
            'interactive screenshot snapshot bypasses locked render wrapper')


def main():
    packaging()
    source = os.environ.get('NIRI_SOURCE_DIR')
    if source:
        source_assumptions(Path(source))
        print('lock-capture assumptions: packaging and patched-source lock boundaries passed')
    else:
        print('lock-capture assumptions: packaging passed; source audit skipped (set NIRI_SOURCE_DIR)')


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, OSError, IndexError) as error:
        print(f'lock-capture assumptions: FAIL: {error}', file=sys.stderr)
        sys.exit(1)
