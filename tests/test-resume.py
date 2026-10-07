#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the initramfs wrapper without accessing a resume device."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResumeTests(unittest.TestCase):
    def run_wrapper(self, setting=None):
        with tempfile.TemporaryDirectory() as directory:
            upstream = Path(directory) / 'resume'
            upstream.write_text('''run_hook() {
    printf 'level=%s arg=%s\\n' "$SYSTEMD_LOG_LEVEL" "$1"
    printf 'warning remains visible\\nerror remains visible\\n' >&2
    return 17
}
''')
            wrapper = (ROOT / 'initcpio/hooks/emaki-resume').read_text().replace(
                '/hooks/emaki-resume-upstream', str(upstream))
            script = wrapper + '''
run_hook marker
status=$?
printf 'status=%s parent=%s\\n' "$status" "${SYSTEMD_LOG_LEVEL-unset}"
'''
            env = os.environ.copy()
            env.pop('SYSTEMD_LOG_LEVEL', None)
            if setting is not None:
                env['SYSTEMD_LOG_LEVEL'] = setting
            return subprocess.run(['bash', '-c', script], env=env, text=True,
                                  capture_output=True, check=True)

    def test_expected_messages_filtered_without_losing_status_or_errors(self):
        result = self.run_wrapper()
        self.assertEqual(result.stdout, 'level=warning arg=marker\nstatus=17 parent=unset\n')
        self.assertEqual(result.stderr, 'warning remains visible\nerror remains visible\n')

    def test_explicit_diagnostic_level_preserved(self):
        result = self.run_wrapper('debug')
        self.assertIn('level=debug arg=marker\nstatus=17 parent=debug\n', result.stdout)

    def test_build_copies_stock_runtime_without_registering_it_twice(self):
        script = '''
map() { printf 'map:%s\\n' "$*"; }
add_binary() { printf 'binary:%s\\n' "$*"; }
add_file() { printf 'file:%s\\n' "$*"; }
add_runscript() { printf 'runscript\\n'; }
. "$1"
build
'''
        result = subprocess.run(['bash', '-c', script, 'test',
                                 str(ROOT / 'initcpio/install/emaki-resume')],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            'map:add_module crypto-lzo crypto-lz4',
            'binary:/usr/lib/systemd/systemd-hibernate-resume',
            'file:/usr/lib/initcpio/hooks/resume /hooks/emaki-resume-upstream',
            'runscript',
        ])


if __name__ == '__main__':
    unittest.main()
