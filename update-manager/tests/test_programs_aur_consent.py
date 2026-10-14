#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Consent checks must reject special files without blocking the service."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ConsentFileTests(unittest.TestCase):
    def test_fifo_without_writer_is_refused_promptly(self):
        with tempfile.TemporaryDirectory(prefix='aur-consent-') as directory:
            consent = Path(directory) / 'consent'
            os.mkfifo(consent, 0o600)
            script = '''
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import programs_aur_build as helper
aur = SimpleNamespace(consent_path=lambda root: Path(sys.argv[2]), WARNING_VERSION=1)
person = SimpleNamespace(pw_uid=os.getuid(), pw_dir=sys.argv[3])
with patch.object(helper, 'modules', return_value=(None, aur, None, None)), \
        patch.object(helper.pwd, 'getpwuid', return_value=person), \
        patch.dict(os.environ, {'PKEXEC_UID': '1000'}):
    try:
        helper.require_consent()
    except ValueError:
        print('refused')
    else:
        raise AssertionError('A FIFO must never establish consent')
'''
            try:
                result = subprocess.run(
                    [sys.executable, '-B', '-c', script,
                     str(Path(__file__).resolve().parents[1]), str(consent), directory],
                    capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired:
                self.fail('The consent FIFO blocked the privileged service')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'refused')


if __name__ == '__main__':
    unittest.main()
