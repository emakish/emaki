#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unsupported roots cannot reach AUR planning or privileged mutations."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine
import programs_aur_build as helper
import emaki_update_root as guard


class SnapshotRefusal(unittest.TestCase):
    def test_local_receipt_details_cannot_bypass_install_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = engine.Engine(Mock(), temporary)
            row = dict(kind='aur', package='paint', installed=True, installedVersion='1', version='2')
            with patch.object(manager, 'details', return_value=dict(ok=True, program=row)), \
                    patch.object(engine.aur, 'build_plan') as build, \
                    patch.object(engine.subprocess, 'Popen') as authorize:
                self.assertEqual(manager.plan('install', 'aur:paint')['code'], 'aur-closed')
                self.assertEqual(manager.execute('install', 'aur:paint', 'a' * 64, Mock())['code'], 'aur-closed')
                build.assert_not_called()
                authorize.assert_not_called()

    def test_engine_refuses_ext4_before_planning_or_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = engine.Engine(Mock(), temporary)
            engine.aur.set_consent(manager.consent_file, True)
            row = dict(kind='aur', package='paint', installed=False)
            with patch.object(manager, 'details', return_value=dict(ok=True, program=row)), \
                    patch.object(engine.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout='{"mode":"unsupported","filesystem":"ext4"}')) as status, \
                    patch.object(engine.aur, 'build_plan') as build, \
                    patch.object(engine.subprocess, 'Popen') as authorize:
                for operation in (lambda: manager.plan('install', 'aur:paint'),
                                  lambda: manager.execute('install', 'aur:paint', 'a' * 64, Mock())):
                    answer = operation()
                    self.assertFalse(answer['ok'])
                    self.assertIn('snapshot', answer['message'].lower())
                self.assertEqual(status.call_count, 2)
                build.assert_not_called()
                authorize.assert_not_called()

    def test_privileged_worker_refuses_ext4_before_any_mutation(self):
        output = io.StringIO()
        with tempfile.TemporaryFile(mode='w') as lock, \
                contextlib.redirect_stdout(output), \
                patch.object(helper, 'modules', return_value=(None, engine.aur, Mock(), guard)), \
                patch.object(helper.os, 'open', return_value=10), \
                patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), \
                patch.object(helper.fcntl, 'flock'), \
                patch.object(helper, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout='{"mode":"unsupported","filesystem":"ext4"}')) as status, \
                patch.object(helper, 'reviewed_plan') as plan, \
                patch.object(helper, 'versions') as versions, \
                patch.object(helper, 'preinstall_snapshot') as snapshot, \
                patch.object(helper, 'stream') as packages, \
                patch.object(helper, 'sandbox_build') as build:
            self.assertNotEqual(helper.worker('paint', 'a' * 64), 0)
            result = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(result['code'], 'refused')
            self.assertTrue(result['notStarted'])
            self.assertIn('snapshot', result['message'].lower())
            status.assert_called_once_with([guard.ROLLBACK, 'status', '--json'], timeout=20)
            for mutation in (plan, versions, snapshot, packages, build):
                mutation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
