# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject unreadable or incorrect frames, even when the PNG is large enough."""
import copy
import importlib.util
import json
import contextlib
import io
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from render_content import validate_frame

SPEC = importlib.util.spec_from_file_location('installer_lint_checks', Path(__file__).with_name('check.py'))
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


class RequiredToolsTests(unittest.TestCase):
    def test_missing_checks_fail(self):
        for tool in ('qmllint', 'qmlformat', 'shellcheck', 'desktop-file-validate'):
            with self.subTest(tool=tool), patch.object(CHECKS.shutil, 'which', return_value=None):
                with self.assertRaisesRegex(RuntimeError, tool):
                    CHECKS.required_tool(tool)

    def test_main_fails_before_checks_when_tools_are_missing(self):
        with patch.object(CHECKS.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_file', return_value=False), patch.object(CHECKS, 'check_window_corners') as check:
            with self.assertRaisesRegex(RuntimeError, 'qmllint'):
                CHECKS.main()
            check.assert_not_called()

    def test_qt_fallback_requires_executable(self):
        with patch.object(CHECKS.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_file', return_value=True), patch.object(CHECKS.os, 'access', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'qmllint'):
                CHECKS.required_tool('qmllint', '/fixture/qmllint')


class RenderContentTests(unittest.TestCase):
    def setUp(self):
        evidence_root = Path(__file__).resolve().parents[3] / '.cache/evidence'
        evidence_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=evidence_root, prefix='render-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'frame.png'
        self.evidence = {
            'width': 400, 'height': 700, 'footerY': 660,
            'texts': [
                dict(text='Install · 0.3.1', x=10, y=10, width=160, height=20, body=False, clipped=False, truncated=False),
                dict(text='Welcome to Emaki', x=10, y=60, width=250, height=24, body=True, clipped=False, truncated=False),
                dict(text='Page details', x=10, y=110, width=250, height=24, body=True, clipped=False, truncated=False),
            ],
        }
        self.draw()

    def draw(self, blank=False):
        frame = Image.new('RGBA', (self.evidence['width'], self.evidence['height']), '#fff8f3')
        if not blank:
            draw = ImageDraw.Draw(frame)
            for row in self.evidence['texts']:
                draw.text((row['x'], row['y']), row['text'], fill='#222222')
        frame.save(self.path, compress_level=0)
        self.assertGreater(self.path.stat().st_size, 1000)

    def validate(self, evidence=None):
        log = 'CONTENT_FRAME welcome ' + json.dumps(evidence or self.evidence)
        validate_frame(self.path, log, 'welcome', self.evidence['width'], self.evidence['height'], '0.3.1')

    def test_correct_content_passes(self):
        self.validate()

    def test_fractional_scale_rounds_each_edge_independently(self):
        self.evidence.update(width=874, height=461, footerY=430)
        self.evidence['texts'][0]['text'] = 'EMAKI SETUP'
        self.draw()
        with Image.open(self.path) as logical:
            source = logical.copy()
        # Qt rounds 874 * 1.25 up and 461 * 1.25 down.
        source.resize((1093, 576)).save(self.path)
        self.validate()
        for dimensions in ((1093, 574), (1093, 578), (874, 576), (699, 369)):
            with self.subTest(dimensions=dimensions):
                source.resize(dimensions).save(self.path)
                with self.assertRaisesRegex(AssertionError, 'Wrong frame dimensions'):
                    self.validate()

    def test_wrong_version_fails(self):
        self.evidence['texts'][0]['text'] = 'Install · 9.9.9'
        with self.assertRaisesRegex(AssertionError, 'Expected visible content'):
            self.validate()

    def test_empty_page_fails(self):
        self.evidence['texts'] = self.evidence['texts'][:1]
        with self.assertRaisesRegex(AssertionError, 'No readable page'):
            self.validate()

    def test_missing_heading_fails(self):
        self.evidence['texts'][1]['text'] = ''
        with self.assertRaisesRegex(AssertionError, 'Expected visible content'):
            self.validate()

    def test_clipped_and_truncated_heading_fail(self):
        for key in ('clipped', 'truncated'):
            with self.subTest(key=key):
                evidence = copy.deepcopy(self.evidence)
                evidence['texts'][1][key] = True
                with self.assertRaisesRegex(AssertionError, 'Expected visible content'):
                    self.validate(evidence)

    def test_overlapping_heading_fails(self):
        self.evidence['texts'][2]['y'] = 65
        with self.assertRaisesRegex(AssertionError, 'heading overlaps'):
            self.validate()

    def test_footer_overlap_fails(self):
        self.evidence['texts'][2]['y'] = 650
        with self.assertRaisesRegex(AssertionError, 'Page overlaps footer'):
            self.validate()

    def test_blank_large_png_fails(self):
        self.draw(blank=True)
        with self.assertRaisesRegex(AssertionError, 'no rendered ink'):
            self.validate()

    def test_missing_evidence_fails(self):
        with self.assertRaisesRegex(AssertionError, 'Missing or repeated'):
            validate_frame(self.path, 'LAYOUT_OK SCREENSHOT_OK welcome', 'welcome', 400, 700, '0.3.1')


class RenderMutationTests(unittest.TestCase):
    def test_actual_window_rejects_missing_heading_and_wrong_version(self):
        import render
        evidence_root = render.ROOT / '.cache/evidence'
        evidence_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=evidence_root, prefix='render-mutation-') as temporary:
            root = Path(temporary)
            shutil.copytree(render.UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
            view = root / 'ui/InstallerView.qml'
            original = view.read_text()
            mutations = [('text: "Welcome to Emaki"', 'text: ""'),
                         ('"Install · "', '"Setup · "')]
            for before, after in mutations:
                with self.subTest(before=before):
                    self.assertIn(before, original)
                    view.write_text(original.replace(before, after))
                    arguments = ['render.py', '--repo-shell', '--screen', 'welcome', '--output', str(root / 'frames')]
                    with patch.object(render, 'UI', root / 'ui'), patch.object(sys, 'argv', arguments), \
                            contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaisesRegex(AssertionError, 'Expected visible content'):
                            render.main()
