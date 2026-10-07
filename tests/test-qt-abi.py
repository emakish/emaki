#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Qt build checks and shell startup/recovery with fake versions."""
from contextlib import redirect_stderr
import configparser
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader('qt_check', str(ROOT / 'scripts/emaki-qt-check'))
spec = importlib.util.spec_from_loader(loader.name, loader)
guard = importlib.util.module_from_spec(spec)
loader.exec_module(guard)


class QtAbiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-qt-abi-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        # Wrapper startup records its selection through the real health helper.
        # Keep that state out of the running desktop's login runtime.
        runtime = self.work / 'runtime'
        runtime.mkdir(mode=0o700)
        environment = patch.dict(os.environ, XDG_RUNTIME_DIR=str(runtime))
        environment.start()
        self.addCleanup(environment.stop)
        self.stamp = self.work / 'qt-build-version'
        self.stamp.write_text('6.11.2\n')

    def check(self, output, failure=None, *, hook=False):
        result = subprocess.CompletedProcess([], 0, output, '')
        with patch.object(guard.subprocess, 'run', return_value=result, side_effect=failure), redirect_stderr(io.StringIO()) as log:
            status = guard.main(self.stamp, hook=hook)
        return status, log.getvalue()

    def test_matching_releases_ignore_epoch_and_package_rebuild(self):
        self.assertEqual(self.check('qt6-base 1:6.11.2-9\nqt6-declarative 6.11.2-1.2\n'), (0, ''))

    def test_each_component_patch_minor_and_older_release_warn(self):
        for changed in guard.COMPONENTS:
            for version in ('6.11.3-1', '6.12.0-1', '6.11.1-1'):
                with self.subTest(component=changed, version=version):
                    output = ''.join(f'{name} {version if name == changed else "6.11.2-1"}\n'
                                     for name in guard.COMPONENTS)
                    status, log = self.check(output)
                    self.assertEqual(status, 1)
                    self.assertIn('built with Qt 6.11.2', log)
                    self.assertIn(changed + ' ' + version, log)
                    self.assertIn('The panel can still start', log)
                    self.assertIn('sudo pacman -Syu', log)

    def test_missing_invalid_or_unreadable_metadata_warns(self):
        for stamp in ('', '6.11', '6.11.2\n6.11.3', None):
            if stamp is None:
                self.stamp.unlink()
            else:
                self.stamp.write_text(stamp)
            self.assertEqual(self.check('')[0], 1)

    def test_hook_warns_successfully_without_changing_diagnostic_status(self):
        output = 'qt6-base 6.11.3-1\nqt6-declarative 6.11.2-1\n'
        diagnostic_status, diagnostic_log = self.check(output)
        hook_status, hook_log = self.check(output, hook=True)
        self.assertEqual(diagnostic_status, 1)
        self.assertEqual(hook_status, 0)
        self.assertEqual(hook_log, diagnostic_log)
        self.assertIn('built with Qt 6.11.2', hook_log)
        self.assertEqual(self.check('qt6-base 6.11.2-1\nqt6-declarative 6.11.2-1\n', hook=True), (0, ''))

    def test_hook_command_uses_successful_warning_mode(self):
        hook = configparser.ConfigParser(interpolation=None, strict=False)
        hook.read(ROOT / 'upkeep/99-emaki-qt-check.hook')
        command = hook['Action']['Exec'].split()
        self.assertEqual(command, ['/usr/bin/emaki-qt-check', '--hook'])
        with patch.object(sys, 'argv', command), \
                patch.object(guard.subprocess, 'run', side_effect=OSError('unavailable package metadata')), \
                redirect_stderr(io.StringIO()) as log, self.assertRaises(SystemExit) as result:
            runpy.run_path(str(ROOT / 'scripts/emaki-qt-check'), run_name='__main__')
        self.assertEqual(result.exception.code, 0)
        self.assertIn('WARNING:', log.getvalue())

    def test_old_package_uses_only_its_installed_build_provide(self):
        self.stamp.unlink()
        for version in ('6.11.2', '6.11.3'):
            metadata = subprocess.CompletedProcess([], 0,
                'Name            : quickshell-emaki\n'
                'Provides        : quickshell=0.3.1\n'
                f'                  emaki-quickshell-qt-build={version}\n'
                'Depends On      : qt6-base>=6.11.2\n', '')
            installed = subprocess.CompletedProcess([], 0,
                'qt6-base 6.11.2-1\nqt6-declarative 6.11.2-1\n', '')
            with patch.object(guard.subprocess, 'run', side_effect=[metadata, installed]) as run, redirect_stderr(io.StringIO()):
                self.assertEqual(guard.main(self.stamp), 0 if version == '6.11.2' else 1)
            self.assertEqual(run.call_args_list[0].args[0],
                             ['/usr/bin/pacman', '-Qi', 'quickshell-emaki'])
            self.assertEqual(run.call_args_list[0].kwargs['env']['LC_ALL'], 'C')

    def test_old_package_missing_duplicate_and_malformed_provides_warn(self):
        self.stamp.unlink()
        for provides in ('None', 'emaki-quickshell-qt-build', 'emaki-quickshell-qt-build=6.11',
                         'emaki-quickshell-qt-build=6.11.2 emaki-quickshell-qt-build=6.11.3'):
            self.assertEqual(self.check('Provides : ' + provides + '\n')[0], 1)

    def test_invalid_existing_stamp_never_falls_back(self):
        self.stamp.write_text('6.11')
        with patch.object(guard.subprocess, 'run') as run, redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(self.stamp), 1)
        run.assert_not_called()

    def test_incomplete_or_invalid_installed_versions_warn(self):
        for output in ('qt6-base 6.11.2-1\n', 'qt6-base 6.11.2-1\nqt6-base 6.11.2-1\n',
                       'qt6-base 6.11-1\nqt6-declarative 6.11.2-1\n'):
            self.assertEqual(self.check(output)[0], 1)
        for failure in (OSError('missing pacman'), subprocess.CalledProcessError(1, 'pacman'),
                        subprocess.TimeoutExpired('pacman', 5)):
            self.assertEqual(self.check('', failure)[0], 1)

    def test_old_package_without_metadata_starts_panel_with_visible_warning(self):
        self.stamp.unlink()
        # Exercise the actual diagnostic and wrapper together with an installed
        # pre-metadata package; neither its absent file nor Provides blocks qs.
        diagnostic = self.work / 'emaki-qt-check'
        diagnostic.write_text(
            '#!/usr/bin/env python3\n'
            'import importlib.machinery, subprocess, sys\n'
            'from pathlib import Path\n'
            'from unittest.mock import patch\n'
            f'guard = importlib.machinery.SourceFileLoader("qt_check", '
            f'{str(ROOT / "scripts/emaki-qt-check")!r}).load_module()\n'
            'result = subprocess.CompletedProcess([], 0, '
            '"Name : quickshell-emaki\\nVersion : 0.3.1-1\\nProvides : quickshell=0.3.1\\n", "")\n'
            'with patch.object(guard.subprocess, "run", return_value=result):\n'
            f'    sys.exit(guard.main(Path({str(self.stamp)!r})))\n')
        diagnostic.chmod(0o755)
        for name, body in {
            'qs': 'echo panel-started',
            'kitty': 'echo visible-warning; shift 2; exec "$@"',
        }.items():
            script = self.work / name
            script.write_text('#!/bin/sh\n' + body + '\n')
            script.chmod(0o755)
        result = subprocess.run(['sh', str(ROOT / 'scripts/emaki-shell')],
                                env=dict(os.environ, PATH=str(self.work) + ':' + os.environ['PATH']),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('panel-started', result.stdout)
        self.assertIn('visible-warning', result.stdout)
        self.assertIn('missing or ambiguous Qt build metadata', result.stderr)
        self.assertIn('The panel can still start', result.stderr)
        self.assertIn('The panel will still start', result.stdout)

    def test_shell_starts_despite_qt_warning_and_recovery_diagnoses(self):
        log = self.work / 'calls'
        for name, body in {'emaki-qt-check': 'echo qt-check >> "$CALLS"; exit "$QT_STATUS"',
                           'qs': 'echo qs >> "$CALLS"',
                           'kitty': 'echo warning > "$WARNING"',
                           'systemctl': 'echo systemctl >> "$CALLS"'}.items():
            script = self.work / name
            script.write_text('#!/bin/sh\n' + body + '\n')
            script.chmod(0o755)
        env = dict(os.environ, PATH=str(self.work) + ':' + os.environ['PATH'], CALLS=str(log),
                   WARNING=str(self.work / 'warning'))
        for status in ('0', '1'):
            env['QT_STATUS'] = status
            log.unlink(missing_ok=True)
            result = subprocess.run(['sh', str(ROOT / 'scripts/emaki-shell')], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(), ['qt-check', 'qs'])
            self.assertEqual((self.work / 'warning').exists(), status == '1')
        log.unlink()
        result = subprocess.run(['sh', str(ROOT / 'scripts/emaki-shell'), 'recover'], env=env,
                                input='', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['qt-check'])
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / 'systemd/emaki-shell.service')
        self.assertNotIn('78', unit['Service']['RestartPreventExitStatus'].split())


if __name__ == '__main__':
    unittest.main()
