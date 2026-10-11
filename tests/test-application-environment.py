#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real launched children with restored session environments."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'shell/helpers/app_scope.py'
spec = importlib.util.spec_from_file_location('app_scope', HELPER)
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ApplicationEnvironmentTests(unittest.TestCase):
    def test_real_child_receives_session_in_scope_and_fallback(self):
        with tempfile.TemporaryDirectory(prefix='application-environment-') as directory:
            base = Path(directory)
            runner = base / 'systemd-run'
            runner.write_text(f'#!{sys.executable} -I\n'
                              'import os,sys\n'
                              "if os.environ['SCOPE_MODE'] == 'fail': sys.exit(1)\n"
                              "args = sys.argv[sys.argv.index('--') + 1:]\n"
                              'os.execv(args[0], args)\n')
            runner.chmod(0o755)
            session = dict(os.environ, PATH=str(base) + os.pathsep + os.defpath,
                           QT_QPA_PLATFORMTHEME='personal-theme',
                           QT_PLUGIN_PATH=str(base / 'personal-plugins'),
                           QML_IMPORT_PATH=str(base / 'personal-qml'),
                           PYTHONPATH=str(base / 'personal-python'),
                           PYTHONHOME=sys.base_prefix,
                           LD_LIBRARY_PATH=str(base / 'personal-libraries'),
                           LD_PRELOAD='', MALLOC_CONF='personal-malloc',
                           SESSION_ONLY='kept', WAYLAND_SOCKET='77',
                           EMAKI_SHELL_PROCESS_ID='private',
                           EMAKI_SHELL_ORIGINAL_MALLOC_CONF='private',
                           EMAKI_SHELL_SESSION_ENV='must not escape')
            for mode in ('scope', 'fail'):
                with self.subTest(mode=mode):
                    output = base / (mode + '.json')
                    session['SCOPE_MODE'] = mode
                    with patch.dict(os.environ, {}, clear=True):
                        trusted = runpy.run_path(str(ROOT / 'scripts/emaki-shell-trusted'))
                    restricted = trusted['environment'](base / 'generation',
                        dict(session, EMAKI_SHELL_SESSION_ENV=json.dumps(session)))
                    restricted['WAYLAND_SOCKET'] = '91'
                    restricted['SHELL_ONLY'] = 'must not escape'
                    self.assertNotEqual(restricted['PATH'], session['PATH'])
                    self.assertNotIn('QT_QPA_PLATFORMTHEME', restricted)
                    self.assertNotIn('PYTHONPATH', restricted)
                    self.assertNotIn('LD_PRELOAD', restricted)
                    command = [sys.executable, '-I', str(HELPER), '--exec', 'env-test',
                               sys.executable, '-I', '-c',
                               'import json,os,sys; '
                               'open(sys.argv[1], "w").write(json.dumps(dict(os.environ)))',
                               str(output)]
                    result = subprocess.run(command, env=restricted, capture_output=True,
                                            text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    child = json.loads(output.read_text())
                    expected = {key: value for key, value in session.items()
                                if key not in ('WAYLAND_SOCKET', *scope.INTERNAL_MARKERS)}
                    self.assertEqual(child, expected)
                    self.assertEqual(scope.WARNING in result.stderr, mode == 'fail')

    def test_direct_trusted_launch_captures_environment_before_filtering(self):
        with patch.dict(os.environ, {}, clear=True):
            trusted = runpy.run_path(str(ROOT / 'scripts/emaki-shell-trusted'))
        for malloc in ({}, {'MALLOC_CONF': ''}, {'MALLOC_CONF': 'personal'}):
            session = dict(PATH='personal-path', QT_QPA_PLATFORMTHEME='qt6ct',
                           QT_DISABLE_SHADER_DISK_CACHE='0', QSG_RHI_DISABLE_SHADER_DISK_CACHE='0',
                           MESA_SHADER_CACHE_DISABLE='false',
                           WAYLAND_SOCKET='81', **malloc)
            restricted = trusted['environment'](ROOT / 'generation', session)
            self.assertEqual(restricted['QT_DISABLE_SHADER_DISK_CACHE'], '1')
            self.assertEqual(restricted['QSG_RHI_DISABLE_SHADER_DISK_CACHE'], '1')
            self.assertEqual(restricted['MESA_SHADER_CACHE_DISABLE'], 'true')
            with patch.dict(os.environ, restricted, clear=True):
                child = scope.application_environment()
            self.assertEqual(child, {key: value for key, value in session.items()
                                     if key != 'WAYLAND_SOCKET'})

    def test_ordinary_launch_preserves_malloc_unset_empty_and_value(self):
        for marker, value in (('0', None), ('1', ''), ('1custom', 'custom')):
            with self.subTest(marker=marker), patch.dict(
                    os.environ, {'MALLOC_CONF': 'shell', scope.MALLOC_MARKER: marker,
                                 'WAYLAND_SOCKET': '12', 'PATH': 'personal'}, clear=True):
                environment = scope.application_environment()
                self.assertEqual(environment.get('MALLOC_CONF'), value)
                self.assertEqual(environment['PATH'], 'personal')
                self.assertNotIn('WAYLAND_SOCKET', environment)
                self.assertNotIn(scope.MALLOC_MARKER, environment)

    def test_malformed_snapshot_cannot_create_invalid_child_environment(self):
        for invalid in ('invalid', '[]', '{"A": 1}', '{"A=B":"x"}',
                        '{"":"x"}', '{"A":"\\u0000"}'):
            with self.subTest(invalid=invalid), patch.dict(
                    os.environ, {scope.SESSION_MARKER: invalid, 'PATH': 'fallback'}, clear=True):
                self.assertEqual(scope.application_environment(), {'PATH': 'fallback'})


if __name__ == '__main__':
    unittest.main()
