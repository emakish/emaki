#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutation tests for the package's conservative niri rebase review gates."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'packaging/niri-emaki/check-assumptions.py'
spec = importlib.util.spec_from_file_location('fork_assumptions', CHECKER)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class RebaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        reviewed = {}
        for relative, (_, reason) in guard.REVIEWED.items():
            content = '// reviewed source\n'
            if relative in guard.CURSOR_PATHS:
                content += 'fn render_pointer() {}\n'
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            reviewed[relative] = (hashlib.sha256(content.encode()).hexdigest(), reason)
        for relative in guard.CURSOR_PATHS - guard.REVIEWED.keys():
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fn render_pointer() {}\n')
        mock = patch.object(guard, 'REVIEWED', reviewed)
        mock.start()
        self.addCleanup(mock.stop)
        self.assertEqual(guard.check(self.source), [])

    def change(self, relative, addition):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as stream:
            stream.write(addition)

    def test_new_capture_protocol_requires_filter_review(self):
        self.change('src/handlers/image_copy_capture.rs', 'mod ext_image_copy_capture;\n')
        self.assertTrue(any('requesting client' in failure for failure in guard.check(self.source)))

    def test_capture_render_change_requires_review(self):
        self.change('src/niri.rs', 'let capture_client = None;\n')
        self.assertTrue(any('464' in failure for failure in guard.check(self.source)))

    def test_static_blur_occlusion_change_requires_review(self):
        self.change('src/render_helpers/xray.rs', '// changed effect coverage\n')
        self.assertTrue(any('230' in failure for failure in guard.check(self.source)))

    def test_static_blur_alpha_change_requires_review(self):
        self.change('src/render_helpers/shaders/postprocess.frag', '// changed alpha\n')
        self.assertTrue(any('230' in failure for failure in guard.check(self.source)))

    def test_new_column_assignment_requires_review(self):
        self.change('src/layout/scrolling.rs', 'self.active_column_idx += 1;\n')
        self.assertTrue(any('465' in failure for failure in guard.check(self.source)))

    def test_new_width_path_requires_review(self):
        self.change('src/layout/scrolling.rs', 'columns[0].set_width(width);\n')
        self.assertTrue(any('465' in failure for failure in guard.check(self.source)))

    def test_new_action_requires_navigation_review(self):
        self.change('niri-config/src/binds.rs', 'enum Action { NewNavigationAction }\n')
        self.assertTrue(any('753' in failure for failure in guard.check(self.source)))

    def test_existing_cursor_call_scale_change_requires_review(self):
        self.change('src/screencasting/mod.rs', 'element.geometry(other_scale);\n')
        self.assertTrue(any('754' in failure for failure in guard.check(self.source)))

    def test_new_cursor_render_file_requires_review(self):
        self.change('src/new_capture.rs', 'niri.render_pointer(renderer);\n')
        self.assertTrue(any('inventory changed' in failure for failure in guard.check(self.source)))

    def test_missing_source_is_not_a_pass(self):
        (self.source / 'src/layout/scrolling.rs').unlink()
        self.assertTrue(any('missing reviewed source' in failure for failure in guard.check(self.source)))

    def test_command_fails_on_unreviewed_source(self):
        result = subprocess.run(['python3', str(CHECKER), str(self.source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('do not refresh hashes without review', result.stderr)

    def test_package_runs_guard_before_fetch_and_build(self):
        recipe = (ROOT / 'packaging/niri-emaki/PKGBUILD').read_text()
        invocation = 'python "$srcdir/check-assumptions.py" . || return 1'
        self.assertIn(invocation, recipe)
        self.assertLess(recipe.index('patch -Np1 -i ../0008'), recipe.index(invocation))
        self.assertLess(recipe.index(invocation), recipe.index('cargo fetch'))
        self.assertIn('        check-assumptions.py)', recipe)
        self.assertIn('  python\n', recipe)
        data = CHECKER.read_bytes()
        self.assertIn(hashlib.sha512(data).hexdigest(), recipe)
        self.assertIn(hashlib.blake2b(data).hexdigest(), recipe)


if __name__ == '__main__':
    unittest.main()
