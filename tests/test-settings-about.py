#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise local diagnostics without reading a real journal or changing devices."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import gettext
import importlib.machinery
import importlib.util
import io
import json
import locale
import os
from pathlib import Path
import stat
import subprocess
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

    def test_saved_diagnostics_redact_details_and_command_output(self):
        planted = ('Authorization: Bearer C8R_SYNTHETIC_SECRET password=C8R_SYNTHETIC_PASSWORD\n'
                   'person@example.invalid 192.0.2.44 2001:db8::44 aa:bb:cc:dd:ee:ff '
                   'serial=FIXTURE_SERIAL')
        with patch.dict(os.environ, XDG_STATE_HOME=str(self.root)), \
                patch.object(self.about, 'command', return_value=planted):
            path = Path(self.about.report({'processor': planted, 'commit': 'PRIVATE_BUILD_HASH'}))
        text = path.read_text()
        for value in ('C8R_SYNTHETIC_SECRET', 'C8R_SYNTHETIC_PASSWORD', 'person@example.invalid',
                      '192.0.2.44', '2001:db8::44', 'aa:bb:cc:dd:ee:ff', 'FIXTURE_SERIAL'):
            self.assertNotIn(value, text)
        # A line that names a credential is dropped whole; the others keep their markers.
        self.assertNotIn('Authorization', text)
        self.assertIn('[redacted email] [redacted ip] [redacted ip] [redacted mac] serial=[redacted serial]', text)
        self.assertNotIn('PRIVATE_BUILD_HASH', text)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

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

    def emaki_os_release(self, url='https://github.com/emakish/emaki/issues'):
        path = self.root / 'lib/emaki/os-release'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'NAME="Emaki"\nID=arch\nVARIANT_ID=emaki\nBUG_REPORT_URL="{url}"\n')

    def installed(self, text):
        self.paths.LOCALSTATEDIR = str(self.root / 'var/lib/emaki')
        path = self.root / 'var/lib/emaki/installed.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_issue_address_comes_from_emaki_os_release(self):
        self.emaki_os_release()
        self.assertEqual(self.about.issue_target(), 'https://github.com/emakish/emaki/issues/new')
        self.emaki_os_release('https://github.com/emakish/emaki/issues/')
        self.assertEqual(self.about.issue_target(), 'https://github.com/emakish/emaki/issues/new')
        for url in ('https://bugs.archlinux.org/', 'http://github.com/emakish/emaki/issues',
                    'https://github.com.example/emakish/emaki/issues', 'javascript:alert(1)',
                    'https://github.com/emakish/emaki/issues?x=1', ''):
            with self.subTest(url=url):
                self.emaki_os_release(url)
                self.assertIsNone(self.about.issue_target())
        # The host distribution's file (no Emaki variant) never chooses the tracker.
        (self.root / 'lib/emaki/os-release').write_text(
            'NAME="Arch Linux"\nID=arch\nBUG_REPORT_URL="https://github.com/emakish/emaki/issues"\n')
        self.assertIsNone(self.about.issue_target())
        (self.root / 'lib/emaki/os-release').unlink()
        self.assertIsNone(self.about.issue_target())

    def test_installation_record(self):
        self.installed('{"version": 1, "emaki_version": "0.8.0"}\n')
        self.assertEqual(self.about.installation('0.8.0'), 'Installed with Emaki 0.8.0; current release matches')
        self.assertEqual(self.about.installation('0.8.1'), 'Installed with Emaki 0.8.0; now on 0.8.1')
        for text in ('', 'not json', '[]', '{"version": 2, "emaki_version": "0.8.0"}',
                     '{"version": 1, "emaki_version": "0.8.0\\n- Injected: yes"}', '{"version": 1}'):
            with self.subTest(text=text):
                self.installed(text)
                self.assertTrue(self.about.installation('0.8.0').startswith('Not recorded'))

    def test_issue_url_carries_only_the_named_fields_encoded(self):
        from urllib.parse import parse_qs, urlsplit
        self.emaki_os_release()
        self.installed('{"version": 1, "emaki_version": "0.7.0"}\n')
        private = {'computer': 'Jane Doe Laptop 14', 'processor': 'Fixture CPU 9000',
                   'graphics': 'Fixture GPU', 'memory': '31.2 GiB', 'disk': '953.9 GiB · 120.4 GiB free',
                   'firmware': 'LENOVO N3HET88W', 'window_manager': 'niri 26.04', 'commit': 'abc1234'}
        info = dict(private, version='0.8.0', label='alpha', channel='testing & more #1',
                    kernel='6.17.1-arch1-1')
        url = self.about.issue(info)
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ('https', 'github.com', '/emakish/emaki/issues/new'))
        # Only the body is prefilled: no title, labels, template or anything GitHub would submit by itself.
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
        self.assertEqual(list(query), ['body'])
        for raw in (' ', '\n', '&more', '#1', '+'):
            self.assertNotIn(raw, parts.query)
        body = query['body'][0]
        self.assertEqual(body.split('### System\n')[1].split('\n\n')[0].splitlines(), [
            '- Emaki: 0.8.0 alpha',
            '- Update channel: testing & more #1',
            '- Linux kernel: 6.17.1-arch1-1',
            '- Installation: Installed with Emaki 0.7.0; now on 0.8.0'])
        for heading in ('### What happened', '### What I expected', '### Steps to reproduce'):
            self.assertIn(heading, body)
        for value in private.values():
            self.assertNotIn(value, body)
        # The real account and machine must not leak in. Skip a value that the given fields already contain
        # (the build VM's user is "arch", which is inside the fixture kernel "6.17.1-arch1-1").
        for value in (os.environ.get('USER'), str(Path.home()), os.uname().nodename):
            if value and not any(value in str(field) for field in info.values()):
                self.assertNotIn(value, body)

    def test_issue_values_cannot_add_lines(self):
        self.emaki_os_release()
        self.paths.LOCALSTATEDIR = str(self.root / 'missing')
        body = self.about.issue_body({'version': '0.8.0', 'channel': 'stable\n- Extra: line', 'kernel': ''})
        system = body.split('### System\n')[1].split('\n\n')[0].splitlines()
        self.assertEqual(system, ['- Emaki: 0.8.0', '- Update channel: stable - Extra: line',
                                  '- Linux kernel: Unavailable',
                                  '- Installation: Not recorded (please say whether this is a fresh '
                                  'installation or an updated one)'])

    def test_issue_command_reads_no_hardware_and_writes_nothing(self):
        from urllib.parse import parse_qs, urlsplit
        self.emaki_os_release()
        self.paths.LOCALSTATEDIR = str(self.root / 'missing')
        (self.root / 'lib/emaki-release').write_text('VERSION=0.8.0\nLABEL=alpha\nEMAKI_COMMIT=abc1234\n')
        reads, commands = [], []
        original = self.about.read
        def read(path):
            reads.append(str(path))
            return original(path)
        def command(args):
            commands.append(args)
            return 'stable'
        output = io.StringIO()
        with patch.object(self.about, 'read', read), patch.object(self.about, 'command', command), \
                patch.object(self.about, 'report') as report, patch.object(self.about, 'details') as details, \
                patch.object(self.about.platform, 'release', return_value='6.17.1-arch1-1'), \
                contextlib.redirect_stdout(output):
            self.assertEqual(self.about.main(['issue', '--json']), 0)
        report.assert_not_called()
        details.assert_not_called()
        self.assertEqual(commands, [['emaki-update-channel']])
        self.assertEqual(sorted(reads), sorted([str(self.root / 'lib/emaki-release'),
                                                str(self.root / 'lib/emaki/os-release'),
                                                str(self.root / 'missing/installed.json')]))
        reply = json.loads(output.getvalue())
        self.assertEqual(set(reply), {'schema_version', 'status', 'url'})
        self.assertEqual(reply['status'], 'issue')
        body = parse_qs(urlsplit(reply['url']).query)['body'][0]
        self.assertIn('- Emaki: 0.8.0 alpha\n- Update channel: stable\n- Linux kernel: 6.17.1-arch1-1\n', body)
        self.assertNotIn('abc1234', reply['url'])
        (self.root / 'lib/emaki/os-release').unlink()
        output = io.StringIO()
        with patch.object(self.about, 'identity', return_value={}), contextlib.redirect_stdout(output):
            self.assertEqual(self.about.main(['issue', '--json']), 1)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'issue_target_unavailable')

    def test_missing_or_invalid_memory_is_unavailable(self):
        for value in ('', 'MemTotal: unknown kB', 'MemTotal: 10 bytes', 'MemTotal: -1 kB'):
            with self.subTest(value=value), patch.object(self.about, 'read', return_value=value), patch.object(self.about, 'command', return_value='Unavailable'):
                self.assertEqual(self.about.details()['memory'], 'Unavailable')

    def test_size_numbers_and_units_use_compiled_session_catalogues(self):
        expected = {
            'en': ('15.5 GiB', '476.9 GiB · 120.3 GiB free'),
            'es': ('15,5 GiB', '476,9 GiB · 120,3 GiB libres'),
            'pt_BR': ('15,5 GiB', '476,9 GiB · 120,3 GiB livres'),
            'de': ('15,5 GiB', '476,9 GiB · 120,3 GiB frei'),
            'fr': ('15,5\u00a0Gio', '476,9\u00a0Gio · 120,3\u00a0Gio libres'),
            'ru': ('15,5 GiB', '476,9 GiB · свободно 120,3 GiB'),
        }
        disk = types.SimpleNamespace(total=476.9 * 2**30, free=120.3 * 2**30)
        def read(path):
            return 'MemTotal: 16252928 kB' if path == '/proc/meminfo' else ''
        for language, values in expected.items():
            with self.subTest(language=language):
                compiled = self.root / (language + '.mo')
                subprocess.run(['msgfmt', '-o', str(compiled),
                                str(ROOT / 'i18n/shell' / (language + '.po'))], check=True)
                with compiled.open('rb') as stream:
                    catalogue = gettext.GNUTranslations(stream)
                with patch.dict(os.environ, EMAKI_LANGUAGE=language), \
                        patch.object(self.about, '_', catalogue.gettext), \
                        patch.object(self.about, 'read', side_effect=read), \
                        patch.object(self.about, 'command', return_value='Unavailable'), \
                        patch.object(self.about, 'identity', return_value={}), \
                        patch.object(self.about.shutil, 'disk_usage', return_value=disk):
                    details = self.about.details()
                self.assertEqual((details['memory'], details['disk']), values)
                if language == 'en':
                    self.assertEqual(details['memory'], f'{16252928 / 2**20:.1f} GiB')
                    self.assertEqual(details['disk'],
                                     f'{disk.total / 2**30:.1f} GiB · {disk.free / 2**30:.1f} GiB free')

    def test_number_precision_grouping_and_request_isolation(self):
        number = self.about.format_number
        cases = [('en_US.UTF-8', '12,345.67'), ('es_MX.UTF-8', '12.345,67'),
                 ('pt_BR.UTF-8', '12.345,67'), ('de_DE.UTF-8', '12.345,67'),
                 ('fr_FR.UTF-8', '12\u202f345,67'), ('ru_RU.UTF-8', '12\u00a0345,67')]
        before = locale.setlocale(locale.LC_NUMERIC)
        with patch.object(locale, 'setlocale', side_effect=AssertionError('Locale mutation')):
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(number, 12345.67, 2, language=language, grouping=True)
                           for language, _ in cases * 8]
                self.assertEqual([future.result() for future in futures],
                                 [expected for _, expected in cases * 8])
            self.assertEqual(number(9007199254740993, language='fr', grouping=True),
                             '9\u202f007\u202f199\u202f254\u202f740\u202f993')
            self.assertEqual(number(-1.25, 1, language='de'), '-1,2')
            self.assertEqual(number(1, 2, language='fr'), '1,00')
            self.assertEqual(number(12345.67, 1, language='en'), '12345.7')
        self.assertEqual(locale.setlocale(locale.LC_NUMERIC), before)

    def test_number_language_follows_message_preferences(self):
        cases = [({'EMAKI_LANGUAGE': 'de', 'LC_ALL': 'C'}, '1,5'),
                 ({'LANG': 'fr_FR.UTF-8'}, '1,5'),
                 ({'LC_ALL': 'C', 'LANGUAGE': 'de'}, '1.5'),
                 ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'xx:ru'}, '1,5'),
                 ({'EMAKI_LANGUAGE': 'en_XA'}, '1.5')]
        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(self.about.format_number(1.5, 1), expected)


if __name__ == '__main__':
    unittest.main()
