#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check channel diagnostics through native pacman configuration expansion."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/emaki-update-channel'


class ChannelDetection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='emaki-channel-read-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'pacman.conf'

    def invoke(self, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), '--config', str(self.config)],
                              env=env, capture_output=True, text=True)

    def assert_result(self, result, channel):
        self.assertEqual(result.stdout, channel + '\n', result.stderr)
        self.assertEqual(result.returncode, 1 if channel == 'unknown' else 0, result.stderr)

    def test_native_includes_comments_and_architecture_expansion(self):
        if shutil.which('pacman-conf') is None:
            self.skipTest('pacman-conf is not installed')
        selector = self.root / 'selector'
        servers = self.root / 'servers'
        self.config.write_text(f'[options]\nArchitecture = x86_64\n[emaki]\nInclude = {selector}\n')
        selector.write_text(f'# Server = https://pkgs.emaki.sh/stable/$arch\nInclude = {servers}\n')
        servers.write_text('# Server = https://pkgs.emaki.sh/stable/$arch\n'
                           'Server = https://pkgs.emaki.sh/testing/$arch\n')
        before = {path: path.read_bytes() for path in (self.config, selector, servers)}
        self.assert_result(self.invoke(), 'testing')
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        servers.write_text('Server = https://pkgs.emaki.sh/stable/$arch\n')
        self.assert_result(self.invoke(), 'stable')
        self.config.write_text('[options]\nArchitecture = x86_64\n'
                               '# [emaki]\n# Server = https://pkgs.emaki.sh/testing/$arch\n')
        self.assert_result(self.invoke(), 'disabled')

    def test_native_legacy_custom_mixed_and_empty(self):
        if shutil.which('pacman-conf') is None:
            self.skipTest('pacman-conf is not installed')
        for entries, expected in [
                (['https://github.com/emakish/packages/releases/download/testing'], 'testing'),
                (['https://github.com/emakish/packages/releases/download/stable'], 'stable'),
                (['https://mirror.example/testing/x86_64'], 'custom'),
                (['https://pkgs.emaki.sh/stable/$arch', 'https://pkgs.emaki.sh/testing/$arch'], 'mixed'),
                (['https://pkgs.emaki.sh/stable/$arch', 'https://mirror.example/stable'], 'mixed'),
                ([], 'unknown')]:
            with self.subTest(entries=entries):
                self.config.write_text('[options]\nArchitecture = x86_64\n[emaki]\n' +
                                       ''.join(f'Server = {url}\n' for url in entries))
                self.assert_result(self.invoke(), expected)

    def test_fake_results_reject_lookalikes_and_preserve_consistent_channels(self):
        fake = self.root / 'pacman-conf'
        fake.write_text('#!/bin/sh\ncase "$*" in\n'
                        '  *--repo-list*) printf "emaki\\n" ;;\n'
                        '  *) printf "%s\\n" "$CHANNEL_TEST_SERVERS" ;;\nesac\n')
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(self.root))
        cases = [
            ('https://pkgs.emaki.sh/testing/x86_64', 'testing'),
            ('https://pkgs.emaki.sh/testing/x86_64\n'
             'https://github.com/emakish/packages/releases/download/testing', 'testing'),
            ('https://pkgs.emaki.sh.evil.example/stable/x86_64', 'custom'),
            ('https://user@pkgs.emaki.sh/stable/x86_64', 'custom'),
            ('https://pkgs.emaki.sh/stable/x86_64?other=testing', 'custom'),
            ('http://pkgs.emaki.sh/stable/x86_64', 'custom'),
            ('https://pkgs.emaki.sh/other/x86_64', 'custom'),
            ('https://github.com/emakish/packages/releases/download/testing/extra', 'custom'),
            ('', 'unknown'),
        ]
        for servers, expected in cases:
            with self.subTest(servers=servers):
                env['CHANNEL_TEST_SERVERS'] = servers
                self.assert_result(self.invoke(env), expected)

    def test_failed_or_missing_parser_is_unknown(self):
        env = dict(os.environ, PATH=str(self.root))
        self.assert_result(self.invoke(env), 'unknown')
        fake = self.root / 'pacman-conf'
        fake.write_text('#!/bin/sh\nprintf "testing\\n"\nexit 1\n')
        fake.chmod(0o755)
        self.assert_result(self.invoke(env), 'unknown')

    def test_release_metadata_does_not_claim_a_build_time_channel(self):
        metadata = (ROOT / 'packaging/emaki-config/emaki-release').read_text()
        self.assertFalse(any(line.startswith('CHANNEL=') for line in metadata.splitlines()))


if __name__ == '__main__':
    unittest.main()
