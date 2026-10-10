#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise local diagnostics without reading a real journal or changing devices."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class About(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = types.ModuleType('emaki_paths')
        self.paths.LIBDIR = str(self.root / 'lib/emaki')
        loader = importlib.machinery.SourceFileLoader('settings_about', str(ROOT / 'scripts/emaki-settings-about'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.about = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, emaki_paths=self.paths):
            loader.exec_module(self.about)

    def tearDown(self):
        self.temp.cleanup()

    def test_release_uses_paths_and_never_executes_values(self):
        path = self.root / 'lib/emaki-release'
        path.parent.mkdir()
        path.write_text('VERSION="0.5.0"\nLABEL=alpha\nCHANNEL=testing\nIGNORE=bad\nEMAKI_COMMIT=$(false)\n')
        self.assertEqual(self.about.release(), {'VERSION': '0.5.0', 'LABEL': 'alpha',
                                              'CHANNEL': 'testing', 'EMAKI_COMMIT': '$(false)'})

    def test_private_unique_reports_and_fixed_read_only_commands(self):
        calls = []
        def fake(args):
            calls.append(args)
            return 'fixture output'
        with patch.dict(os.environ, XDG_STATE_HOME=str(self.root)), patch.object(self.about, 'command', fake):
            first = Path(self.about.report({'version': '0.5.0', 'processor': 'Fixture CPU'}))
            second = Path(self.about.report({'version': '0.5.0'}))
        self.assertNotEqual(first, second)
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
        text = first.read_text()
        for expected in ['0.5.0', 'Fixture CPU', 'Recent journal errors', 'Failed system units',
                         'Failed user units', 'Block devices', 'not been uploaded']:
            self.assertIn(expected, text)
        self.assertEqual(calls[:4], [
            ['journalctl', '-b', '-p', 'err', '-n', '200', '--no-pager'],
            ['systemctl', '--failed', '--no-legend', '--no-pager'],
            ['systemctl', '--user', '--failed', '--no-legend', '--no-pager'],
            ['lsblk', '-o', 'NAME,TYPE,SIZE,MODEL', '--json']])

    def test_report_save_failure_returns_json(self):
        output = io.StringIO()
        with patch.object(self.about, 'details', return_value={}), patch.object(self.about, 'report', side_effect=OSError), contextlib.redirect_stdout(output):
            code = self.about.main(['report', '--json'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'report_save_failed')

    def test_unknown_command_does_not_collect(self):
        with patch.object(self.about, 'details') as details, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.about.main(['upload', '--json']), 2)
            details.assert_not_called()

    def test_missing_commands_and_timeouts_are_visible(self):
        import subprocess
        for error in [FileNotFoundError(), subprocess.TimeoutExpired('fixture', 10)]:
            with patch.object(self.about.subprocess, 'run', side_effect=error):
                self.assertEqual(self.about.command(['fixture']), 'Unavailable')

    def test_graphics_vendor_and_model_without_pci_metadata(self):
        output = '\n'.join([
            '00:01.0 "VGA compatible controller" "Red Hat, Inc." "Virtio 1.0 GPU" -r01 "Red Hat, Inc." "Device 1100"',
            '01:00.0 "3D controller" "NVIDIA Corporation" "GA106 [GeForce RTX 3060]" -ra1 "Vendor" "Adapter"',
            'c4:00.0 "Display controller" "Advanced Micro Devices, Inc. [AMD/ATI]" "Krackan [Radeon 840M / 860M Graphics]" -rf2 -p00 "Lenovo" "Device 5144"',
            '00:02.0 "VGA compatible controller" "Intel Corporation" "UHD Graphics 620" -r07 "" ""',
            '00:03.0 "Host bridge" "Fixture" "Ignored"',
        ])
        self.assertEqual(self.about.graphics_names(output),
                         'Red Hat Virtio 1.0 GPU\nNVIDIA GeForce RTX 3060\nAMD Radeon 840M / 860M Graphics\nIntel UHD Graphics 620')

    def test_graphics_unknown_vendor_and_invalid_output(self):
        self.assertEqual(self.about.graphics_names('00:01.0 "Display controller" "Other Vendor" "Adapter 1"'),
                         'Other Vendor Adapter 1')
        for output in ('Unavailable', '', '00:01.0 "VGA compatible controller"',
                       '00:01.0 "VGA compatible controller" "Broken',
                       '00:01.0 "VGA compatible controller" "" "Adapter"'):
            with self.subTest(output=output):
                self.assertEqual(self.about.graphics_names(output), 'Unavailable')

    def test_details_requests_machine_readable_graphics(self):
        def command(args):
            return ('00:01.0 "VGA compatible controller" "Red Hat, Inc." "Virtio 1.0 GPU" -r01'
                    if args == ['lspci', '-mm'] else 'Unavailable')
        with patch.object(self.about, 'read', return_value=''), patch.object(self.about, 'command', side_effect=command):
            self.assertEqual(self.about.details()['graphics'], 'Red Hat Virtio 1.0 GPU')

    def test_partial_hardware_is_reported(self):
        files = {'/proc/cpuinfo': 'model name : Fixture CPU', '/proc/meminfo': 'MemTotal: 6051736 kB',
                 '/sys/class/dmi/id/bios_vendor': 'Fixture firmware', '/sys/class/dmi/id/bios_version': '1.0'}
        with patch.object(self.about, 'read', side_effect=lambda path: files.get(str(path), '')), patch.object(self.about, 'command', return_value='Unavailable'), patch.object(self.about.shutil, 'disk_usage', side_effect=OSError):
            info = self.about.details()
        self.assertEqual(info['processor'], 'Fixture CPU')
        self.assertEqual(info['memory'], '5.8 GiB')
        self.assertEqual(info['firmware'], 'Fixture firmware 1.0')
        self.assertEqual(info['disk'], 'Unavailable')
        self.assertEqual(info['graphics'], 'Unavailable')

    def test_missing_or_invalid_memory_is_unavailable(self):
        for value in ('', 'MemTotal: unknown kB', 'MemTotal: 10 bytes', 'MemTotal: -1 kB'):
            with self.subTest(value=value), patch.object(self.about, 'read', return_value=value), patch.object(self.about, 'command', return_value='Unavailable'):
                self.assertEqual(self.about.details()['memory'], 'Unavailable')


if __name__ == '__main__':
    unittest.main()
