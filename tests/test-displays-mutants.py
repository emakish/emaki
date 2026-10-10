#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that display transaction tests reject missing safety boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / 'shell/helpers/displays.py').read_text()
mutants = [
    ('last-output guard', "if key == 'enabled' and not value and not any(item['enabled'] for item in before if item['name'] != name):", "if False:", 'test_output_controls_and_last_output'),
    ('risky confirmation', "if key == 'main':\n                    self.publish(pending)", "if key not in ('mode', 'scale'):\n                    self.publish(pending)", 'test_every_risky_change_waits_for_keep_and_reverts'),
    ('remaining display recovery', '        if any(item[\'enabled\'] for item in current):', '        if True:', 'test_disabled_state_is_never_saved_and_undock_restores_last_screen'),
    ('live acknowledgement', '    def verify(self, name, key, value):\n', '    def verify(self, name, key, value):\n        return\n', 'test_ack_without_actual_change_is_refused'),
    ('confirmation deadline', "if self.pending and time.monotonic() >= self.pending['deadline']:", 'if False:', 'test_deadline_rejects_late_keep'),
    ('KDL validation', "            run(['validate', '--config', name])", '            pass', 'test_validation_failure_changes_nothing'),
]
with tempfile.TemporaryDirectory(prefix='display-mutants-') as directory:
    for index, (name, old, new, test) in enumerate(mutants):
        if source.count(old) != 1:
            raise SystemExit(f'{name}: mutation anchor changed')
        helper = Path(directory) / f'displays-{index}.py'
        helper.write_text(source.replace(old, new))
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/test-displays.py'), f'DisplaysTest.{test}'],
                                env=dict(os.environ, DISPLAY_TEST_HELPER=str(helper)), capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or 'FAIL:' not in result.stderr:
            raise SystemExit(f'{name}: not rejected by an assertion\n{result.stdout}{result.stderr}')
        print(f'{name}: rejected')
