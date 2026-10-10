#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise package discovery, authorization boundaries and refusal handling offline."""
import copy
import fcntl
import hashlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'update-manager'))
import catalog
import backend
import fixtures

spec = importlib.util.spec_from_file_location('privileged_apply', ROOT / 'update-manager/apply.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class Parsing(unittest.TestCase):
    def test_pacman_formats_versions_sources_and_downloads(self):
        installed = catalog.packages(fixtures.INSTALLED)
        rows = catalog.transaction(fixtures.TRANSACTION, installed)
        self.assertEqual([(row['source'], row['name']) for row in rows],
                         [('Arch core', 'linux'), ('Emaki', 'emaki-config'), ('Arch extra', 'new-dependency')])
        self.assertEqual(rows[0]['old'], '6.16.8.arch3-1')
        self.assertEqual(rows[2]['old'], 'Not installed')
        self.assertEqual(rows[2]['new'], '1:2.0-1')
        self.assertEqual(rows[2]['downloadSize'], 0)
        self.assertEqual(catalog.summary(rows, [], [], '')['downloadSize'], 145909454)
        self.assertEqual(catalog.summary(rows, [], [], '')['groups'],
                         [dict(name=name, count=1) for name in ('Arch core', 'Arch extra', 'Emaki')])

    def test_checkupdates_ignored_and_epochs(self):
        rows = catalog.upgrade_lines(fixtures.AVAILABLE)
        self.assertEqual(rows['ffmpeg'], dict(old='2:8.0-1', new='2:8.0-2', ignored=True))
        self.assertFalse(rows['linux']['ignored'])
        self.assertEqual(catalog.upgrade_lines(''), {})

    def test_malformed_package_output_is_not_an_empty_success(self):
        for raw in ('garbage', 'extra|bash|1|-1', 'extra|bash|1|NaN', 'extra|--root|1|1',
                    'extra|bash|1|1\nextra|bash|1|2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                catalog.transaction(raw, {})
        with self.assertRaises(ValueError):
            catalog.upgrade_lines('linux 1 -> 2 [unexpected]')
        with self.assertRaises(ValueError):
            catalog.packages('bash\n')
        self.assertEqual(catalog.source_label('custom'), 'Repository custom')

    def test_aur_v5_missing_foreign_and_unknown_size(self):
        installed = {'paru': '2.0.4-1', 'example-local': '1-1'}
        rows, found = catalog.aur_updates(fixtures.AUR, installed, backend.compare)
        self.assertEqual(found, {'paru'})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['aur'])
        self.assertIsNone(rows[0]['downloadSize'])
        self.assertIn('update with your AUR tool', rows[0]['source'])
        self.assertEqual(catalog.aur_updates(fixtures.AUR, {'paru': '2.1.0-1'}, backend.compare)[0], [])
        # vercmp must retain pacman's epoch/pkgrel ordering, not string ordering.
        self.assertEqual(catalog.aur_updates(fixtures.AUR, {'paru': '1:1.0-1'}, backend.compare)[0], [])
        self.assertGreater(backend.compare('2:1.0-1', '1:9.0-9'), 0)

    def test_aur_errors_and_unrequested_results_rejected(self):
        for payload in ({'type': 'error', 'error': 'Too many requests'}, [],
                        dict(fixtures.AUR, version=4), dict(fixtures.AUR, resultcount=0),
                        dict(fixtures.AUR, results=[{'Name': '--root', 'Version': '1'}])):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                catalog.aur_updates(payload, {'paru': '1'}, backend.compare)
        with self.assertRaises(ValueError):
            catalog.aur_updates(fixtures.AUR, {}, backend.compare)

    def test_news_after_last_completed_full_upgrade(self):
        since = catalog.last_upgrade(fixtures.LOG)
        self.assertEqual(since, datetime(2026, 10, 1, 12, 2, tzinfo=timezone.utc))
        items = catalog.news_items(fixtures.NEWS, since)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['link'], 'https://archlinux.org/news/package-update/')
        self.assertEqual(len(catalog.news_items(fixtures.NEWS, None)), 3)
        self.assertEqual(catalog.news_items(fixtures.NEWS, datetime(2026, 10, 7, tzinfo=timezone.utc)), [])

    def test_news_rejects_entities_external_links_and_invalid_dates(self):
        with self.assertRaises(ValueError):
            catalog.news_items(b'<!DOCTYPE rss [<!ENTITY x "data">]><rss/>', None)
        for link in ('http://archlinux.org/news/', 'https://evil.example/news/',
                     'https://archlinux.org@evil.example/news/', 'file:///tmp/news', 'javascript:alert(1)'):
            changed = fixtures.NEWS.replace(b'https://archlinux.org/news/package-update/', link.encode())
            self.assertEqual(len(catalog.news_items(changed, catalog.last_upgrade(fixtures.LOG))), 1)

    def test_news_titles_are_plain_normalized_text(self):
        raw = fixtures.NEWS.replace(b'Package update requires manual intervention',
                                   b'&lt;b&gt;Package update&lt;/b&gt;  requires\n manual intervention')
        items = catalog.news_items(raw, catalog.last_upgrade(fixtures.LOG))
        self.assertEqual(items[0]['title'], 'Package update requires manual intervention')

    def test_warning_content_follows_actual_package_classes(self):
        rows = catalog.transaction(fixtures.TRANSACTION, catalog.packages(fixtures.INSTALLED))
        rows += catalog.aur_updates(fixtures.AUR, {'paru': '1'}, backend.compare)[0]
        text = '\n'.join(catalog.warnings_for(rows))
        for expected in ('cannot guarantee', 'Restart', 'sign out', 'not checked by Arch or Emaki', 'does not build'):
            self.assertIn(expected, text)


class Checking(unittest.TestCase):
    def test_real_pacman_private_sync_and_print_formats(self):
        with tempfile.TemporaryDirectory(prefix='emaki-pacman-fixture-') as directory:
            root = Path(directory)
            local = root / 'db/local/bash-1-1'
            local.mkdir(parents=True)
            (local.parent / 'ALPM_DB_VERSION').write_text('9\n')
            (local / 'desc').write_text('%NAME%\nbash\n\n%VERSION%\n1-1\n\n%DESC%\nCommand shell\n\n%ARCH%\nx86_64\n\n')
            (local / 'files').write_text('%FILES%\nusr/bin/bash\n\n')
            (root / 'repo').mkdir()
            (root / 'cache').mkdir()
            raw = (b'%FILENAME%\nbash-2-1-x86_64.pkg.tar.zst\n\n%NAME%\nbash\n\n%VERSION%\n2-1\n\n'
                   b'%DESC%\nCommand shell\n\n%CSIZE%\n12345\n\n%ISIZE%\n23456\n\n%ARCH%\nx86_64\n\n')
            with tarfile.open(root / 'repo/fixture.db', 'w:gz') as database:
                entry = tarfile.TarInfo('bash-2-1/desc')
                entry.size = len(raw)
                database.addfile(entry, io.BytesIO(raw))
            config = root / 'pacman.conf'
            # Only this locally generated test database is unsigned; production
            # checks use the installed configuration and its signature policy.
            config.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n[fixture]\nServer = file://{root}/repo\n')
            base = ['--config', str(config), '--dbpath', str(root / 'db'),
                    '--cachedir', str(root / 'cache'), '--logfile', '/dev/null']
            backend.command(['/usr/bin/fakeroot', '--', '/usr/bin/pacman', '-Sy', '--disable-sandbox-filesystem', *base], timeout=15)
            installed = catalog.packages(backend.command(['/usr/bin/pacman', '-Q', *base]))
            available = catalog.upgrade_lines(backend.command(['/usr/bin/pacman', '-Qu', *base]))
            printed = backend.command(['/usr/bin/pacman', '-Sup', '--noconfirm', '--print-format', '%r|%n|%v|%s', *base])
            self.assertEqual(installed, {'bash': '1-1'})
            self.assertEqual(available['bash']['new'], '2-1')
            self.assertEqual(catalog.transaction(printed, installed), [dict(
                source='Repository fixture', name='bash', old='1-1', new='2-1', downloadSize=12345, aur=False)])

    def test_discovery_copies_local_database_and_never_syncs_system_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / 'system'
            (system / 'local').mkdir(parents=True)
            (system / 'sync').mkdir()
            (system / 'sync/untouched').write_text('original')
            log = root / 'pacman.log'
            log.write_text(fixtures.LOG)
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                self.assertIn('--dbpath', args)
                private = Path(args[args.index('--dbpath') + 1])
                self.assertNotEqual(private, system)
                self.assertFalse((private / 'local').is_symlink())
                self.assertTrue((private / 'local').is_dir())
                self.assertEqual(args[args.index('--logfile') + 1], '/dev/null')
                if '-Q' in args:
                    return fixtures.INSTALLED
                if '-Qu' in args:
                    return fixtures.AVAILABLE
                if '-Sup' in args:
                    self.assertIn('%r|%n|%v|%s', args)
                    return fixtures.TRANSACTION
                if '-Qm' in args:
                    return 'paru 2.0.4-1\nexample-local 1-1\n'
                return ''
            def fetch(url, cache, validator):
                return validator(json.dumps(fixtures.AUR).encode() if '/rpc/' in url else fixtures.NEWS), False
            with patch.object(backend, 'package_paths', return_value=(system, log)), \
                    patch.object(backend, 'command', side_effect=command), \
                    patch.object(backend, 'compare', return_value=1), \
                    patch.object(backend, 'fetch', side_effect=fetch), patch.object(backend, 'emit'):
                data = backend.discover(root)
            self.assertEqual((system / 'sync/untouched').read_text(), 'original')
            self.assertEqual(len(data['updates']), 4)
            self.assertTrue(any('hold back' in message for message in data['warnings']))
            self.assertTrue(any('example-local' in message for message in data['warnings']))
            sync = next(args for args in calls if '-Sy' in args)
            self.assertEqual(sync[:3], ['/usr/bin/fakeroot', '--', '/usr/bin/pacman'])
            self.assertNotIn('/usr/bin/pkexec', str(calls))
            self.assertEqual(list(root.glob('database-*')), [])

    def test_active_package_lock_refuses_before_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'local').mkdir()
            (root / 'db.lck').touch()
            with patch.object(backend, 'package_paths', return_value=(root, root / 'log')), \
                    patch.object(backend, 'command') as command, self.assertRaises(RuntimeError):
                backend.discover(root)
            command.assert_not_called()
            self.assertTrue((root / 'db.lck').exists())

    def test_fetch_is_cached_bounded_and_uses_stale_data_on_network_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = Mock()
            response.url = 'https://archlinux.org/feeds/news/'
            response.read.return_value = b'{"ok":true}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch.object(backend, 'urlopen', return_value=response) as opening:
                self.assertEqual(backend.fetch(response.url, root, json.loads), ({'ok': True}, False))
                self.assertEqual(backend.fetch(response.url, root, json.loads), ({'ok': True}, False))
                self.assertEqual(opening.call_count, 1)
                self.assertEqual(opening.call_args.kwargs['timeout'], 15)
                request = opening.call_args.args[0]
                self.assertEqual(request.get_method(), 'GET')
                self.assertIsNone(request.data)
                self.assertEqual(response.read.call_args.args, (4 * 1024 * 1024 + 1,))
            with patch.object(backend.time, 'time', return_value=10**12), \
                    patch.object(backend, 'urlopen', side_effect=OSError('offline')):
                self.assertEqual(backend.fetch(response.url, root, json.loads), ({'ok': True}, True))

    def test_fetch_rejects_insecure_redirect_before_reading_or_caching(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            response = Mock(url='http://archlinux.org/feeds/news/')
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'{"ok":true}'
            with patch.object(backend, 'urlopen', return_value=response), self.assertRaises(RuntimeError):
                backend.fetch('https://archlinux.org/feeds/news/', cache, json.loads)
            response.read.assert_not_called()
            self.assertEqual(list(cache.iterdir()), [])

    def test_check_rejects_database_change_or_new_lock_before_network(self):
        for change in ('metadata', 'lock'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                system = cache / 'system'
                (system / 'local').mkdir(parents=True)
                def command(args, **kwargs):
                    if '-Qm' in args:
                        if change == 'lock':
                            (system / 'db.lck').touch()
                        else:
                            stamp = (system / 'local').stat().st_mtime_ns
                            os.utime(system / 'local', ns=(stamp + 1000000, stamp + 1000000))
                    return ''
                with patch.object(backend, 'package_paths', return_value=(system, cache / 'log')), \
                        patch.object(backend, 'command', side_effect=command), \
                        patch.object(backend, 'fetch') as fetch, patch.object(backend, 'emit'):
                    with self.assertRaisesRegex(RuntimeError, 'changed during the check'):
                        backend.discover(cache)
                    fetch.assert_not_called()
                self.assertEqual(list(cache.glob('database-*')), [])

    def test_aur_batches_are_complete_bounded_and_throttled(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            system = cache / 'system'
            (system / 'local').mkdir(parents=True)
            log = cache / 'log'
            log.write_text(fixtures.LOG)
            names = ['package-%03d' % index for index in range(205)]
            requests, events = [], []
            def command(args, **kwargs):
                return ''.join(name + ' 1-1\n' for name in names) if '-Qm' in args else ''
            def fetch(url, cache, validator):
                if '/rpc/' not in url:
                    return validator(fixtures.NEWS), False
                batch = parse_qs(urlparse(url).query)['arg[]']
                requests.append(batch)
                events.append('request')
                payload = dict(version=5, type='multiinfo', resultcount=len(batch),
                               results=[dict(Name=name, Version='1-1') for name in batch])
                return validator(json.dumps(payload).encode()), True
            with patch.object(backend, 'package_paths', return_value=(system, log)), \
                    patch.object(backend, 'command', side_effect=command), \
                    patch.object(backend, 'compare', return_value=0), \
                    patch.object(backend, 'fetch', side_effect=fetch), patch.object(backend, 'emit'), \
                    patch.object(backend.time, 'sleep', side_effect=lambda seconds: events.append(seconds)):
                data = backend.discover(cache)
            self.assertEqual(len(data['warnings']), len(set(data['warnings'])))
            self.assertEqual([len(batch) for batch in requests], [100, 100, 5])
            self.assertEqual([name for batch in requests for name in batch], names)
            self.assertEqual(events, ['request', 1, 'request', 1, 'request'])

    def test_apply_uses_absolute_authorization_and_helper_paths(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.stdout = []
        process.wait.return_value = 126
        with patch.object(backend.subprocess, 'Popen', return_value=process) as opening, \
                patch.object(backend, 'emit') as emit:
            backend.apply(Path('/unused'))
            self.assertTrue(emit.call_args.kwargs['notStarted'])
            self.assertEqual(emit.call_args.kwargs['message'], 'The update was not started.')
        self.assertEqual(opening.call_args.args[0],
                         ['/usr/bin/pkexec', '/usr/libexec/emaki/emaki-update-apply', 'apply'])
        self.assertNotIn('shell', opening.call_args.kwargs)

    def test_fetch_recovers_from_cache_without_a_valid_body(self):
        url = 'https://archlinux.org/feeds/news/'
        for previous in ({'time': time.time()}, {'time': time.time(), 'body': None},
                         {'time': time.time(), 'body': 'invalid'}, {'time': 'invalid', 'body': '{}'}):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                backend.save_json(cache / (hashlib.sha256(url.encode()).hexdigest() + '.json'), previous)
                response = Mock(url=url)
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.read.return_value = b'{"fresh":true}'
                with patch.object(backend, 'urlopen', return_value=response):
                    self.assertEqual(backend.fetch(url, cache, json.loads), ({'fresh': True}, False))
                backend.save_json(cache / (hashlib.sha256(url.encode()).hexdigest() + '.json'), previous)
                with patch.object(backend, 'urlopen', side_effect=OSError('offline')):
                    if previous.get('body') == '{}':
                        self.assertEqual(backend.fetch(url, cache, json.loads), ({}, True))
                    else:
                        with self.assertRaises(RuntimeError):
                            backend.fetch(url, cache, json.loads)

    def test_install_date_is_oldest_valid_record_and_bounds_news(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            for name in ('install-20261001T120000Z.log', 'install-20261003T120000Z.log',
                         'install-invalid.log', 'install-20269999T120000Z.log'):
                (cache / name).touch()
            expected = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
            self.assertEqual(backend.install_date(cache), expected)
            (cache / 'local').mkdir()
            with patch.object(backend, 'package_paths', return_value=(cache, cache / 'missing-log')), \
                    patch.object(backend, 'command', return_value=''), \
                    patch.object(backend, 'install_date', return_value=expected), \
                    patch.object(backend, 'fetch', side_effect=lambda url, cache, validator: (validator(fixtures.NEWS), False)), \
                    patch.object(backend, 'emit'):
                data = backend.discover(cache)
            self.assertEqual(len(data['news']), 2)
            self.assertFalse(any('unknown' in line for line in data['warnings']))

    def test_stale_databases_are_cleaned_only_after_check_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            stale = cache / 'database-stale'
            stale.mkdir()
            (stale / 'payload').write_text('old')
            target = cache / 'unrelated'
            target.mkdir()
            (target / 'keep').touch()
            (cache / 'database-link').symlink_to(target, target_is_directory=True)
            with (cache / 'check.lock').open('w') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(backend, 'discover') as discover:
                    backend.check(cache, True)
                    discover.assert_not_called()
                self.assertTrue(stale.exists())
            def discover(_):
                self.assertEqual(list(cache.glob('database-*')), [])
                return catalog.summary([], [], [], '')
            with patch.object(backend, 'package_paths', return_value=(cache, cache / 'log')), \
                    patch.object(backend, 'discover', side_effect=discover):
                backend.check(cache, True)
            self.assertTrue((target / 'keep').exists())

    def test_signals_remove_private_databases_and_stop_the_writer(self):
        code = """
import os, pathlib, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import backend
root = pathlib.Path(sys.argv[2])
original = backend.command
def command(args, **kwargs):
    if '-Sy' in args:
        private = pathlib.Path(args[args.index('--dbpath') + 1])
        child = 'import os,pathlib,time; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)' % str(root / 'ready')
        return original([sys.executable, '-c', child])
    return ''
os.environ['XDG_CACHE_HOME'] = str(root)
sys.argv = ['backend', '--refresh']
with patch.object(backend, 'package_paths', return_value=(root / 'system', root / 'log')), patch.object(backend, 'command', side_effect=command), patch.object(backend.os, 'geteuid', return_value=1000):
    backend.main()
"""
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'system/local').mkdir(parents=True)
                process = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'update-manager'), str(root)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                writer = None
                try:
                    deadline = time.monotonic() + 5
                    while not (root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue((root / 'ready').exists(), 'package writer did not start')
                    writer = int((root / 'ready').read_text())
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + signum, (stdout, stderr))
                    self.assertEqual(list((root / 'emaki-update-manager').glob('database-*')), [])
                    with self.assertRaises(ProcessLookupError):
                        os.kill(writer, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    if writer is not None:
                        try:
                            os.kill(writer, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_check_failure_preserves_old_list_with_plain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            old = catalog.summary([{'name': 'linux', 'source': 'Arch core', 'downloadSize': 1, 'aur': False}], [], [], '2026-01-01T00:00:00+00:00')
            backend.save_json(cache / 'updates.json', old)
            with patch.object(backend, 'package_paths', return_value=(cache, cache)), \
                    patch.object(backend, 'discover', side_effect=RuntimeError('Check the connection.')):
                data = backend.check(cache, True)
            self.assertEqual(data['updates'], old['updates'])
            self.assertIn('previous check', data['error'])

    def test_indicator_clears_after_terminal_update_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            backend.save_json(cache / 'updates.json', dict(updates=[dict(name='linux', old='1', new='2')]))
            with patch.object(backend, 'command', return_value='linux 2\n') as command:
                self.assertEqual(backend.status(cache)['pending'], 0)
                command.assert_called_once_with(['/usr/bin/pacman', '-Q'])

    def test_reopened_window_observes_running_service_without_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            backend.save_json(cache / 'updates.json', dict(updates=[dict(name='linux', old='1', new='2', aur=False)]))
            states = [dict(ActiveState='activating'),
                      dict(ActiveState='inactive', Result='success', ExecMainStatus='0')]
            with patch.object(backend, 'service_state', side_effect=states), \
                    patch.object(backend.time, 'sleep'), patch.object(backend, 'emit') as emit, \
                    patch.object(backend, 'command', return_value='linux 2\n') as command:
                self.assertTrue(backend.observe_existing(cache))
                command.assert_called_once_with(['/usr/bin/pacman', '-Q'])
                self.assertEqual(emit.call_args.args, ('finished',))
                self.assertTrue(emit.call_args.kwargs['ok'])
                self.assertTrue(emit.call_args.kwargs['restart'])

    def test_unknown_service_result_is_never_success(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(backend, 'service_state', side_effect=[dict(ActiveState='activating'), {}]), \
                patch.object(backend.time, 'sleep'), patch.object(backend, 'emit') as emit, \
                patch.object(backend, 'command', return_value=''):
            self.assertTrue(backend.observe_existing(Path(directory)))
            self.assertFalse(emit.call_args.kwargs['ok'])


class Authorization(unittest.TestCase):
    def test_only_exact_apply_argument_is_accepted(self):
        helper.validate(['apply'])
        for args in ([], ['apply', 'bash'], ['apply', '--root', '/tmp'], ['--help'],
                     ['apply;id'], ['sh', '-c', 'id'], ['restart'], ['apply', '--ignore=linux']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                helper.validate(args)

    def test_rejected_arguments_execute_nothing(self):
        with patch.object(helper.sys, 'argv', ['helper', 'apply', '--root=/tmp']), \
                patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper, 'apply') as apply, patch.object(helper, 'emit') as emit:
            self.assertEqual(helper.main(), 1)
            apply.assert_not_called()
            self.assertTrue(emit.call_args.kwargs['notStarted'])
            self.assertEqual(emit.call_args.kwargs['message'], 'Only a full repository update is accepted.')

    def test_nonroot_helper_executes_nothing(self):
        with patch.object(helper.sys, 'argv', ['helper', 'apply']), \
                patch.object(helper.os, 'geteuid', return_value=1000), \
                patch.object(helper, 'apply', return_value=0) as apply, patch.object(helper, 'emit'):
            self.assertEqual(helper.main(), 1)
            apply.assert_not_called()

    def test_concurrent_helper_refuses_before_inspecting_or_starting_service(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'helper.lock'
            with path.open('w') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                descriptor = os.open(path, os.O_RDWR)
                with patch.object(helper.os, 'open', return_value=descriptor) as opening, \
                        patch.object(helper, 'versions', side_effect=AssertionError('inspected packages')) as versions, \
                        patch.object(helper.subprocess, 'Popen', side_effect=AssertionError('started service')) as starting, \
                        patch.object(helper, 'emit') as emit:
                    self.assertEqual(helper.apply(), 1)
                    versions.assert_not_called()
                    starting.assert_not_called()
                    opening.assert_called_once_with('/run/emaki-update-manager.lock',
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                    self.assertFalse(emit.call_args.kwargs['ok'])
                    self.assertIn('already running', emit.call_args.kwargs['message'])

    def test_service_and_policy_bind_fixed_root_owned_paths(self):
        service = (ROOT / 'update-manager/emaki-update.service').read_text()
        self.assertIn('ExecStart=/usr/bin/emaki-update --noninteractive\n', service)
        directives = [line for line in service.splitlines() if line.startswith('Exec')]
        self.assertEqual(directives, ['ExecStart=/usr/bin/emaki-update --noninteractive'])
        self.assertIn('TimeoutStartSec=infinity', service)
        self.assertNotIn('User=', service)
        policy = ET.parse(ROOT / 'update-manager/org.emaki.update.policy').getroot()
        action = policy.find('action')
        self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin')
        self.assertEqual(action.findtext('defaults/allow_inactive'), 'no')
        self.assertEqual(action.findtext('defaults/allow_any'), 'no')
        self.assertEqual(action.findtext('annotate'), '/usr/libexec/emaki/emaki-update-apply')

    def test_helper_environment_does_not_inherit_caller_commands(self):
        with patch.object(helper.subprocess, 'run') as run:
            helper.run(['/usr/bin/systemctl', 'show', helper.SERVICE])
            arguments = run.call_args.kwargs
            self.assertEqual(arguments['cwd'], '/')
            self.assertEqual(arguments['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
            self.assertNotIn('shell', arguments)

    def test_restart_and_signout_follow_changed_versions(self):
        self.assertEqual(helper.changed({'linux': '1'}, {'linux': '1'}), (False, False))
        self.assertEqual(helper.changed({'linux': '1'}, {'linux': '2'}), (True, False))
        self.assertEqual(helper.changed({}, {'emaki-config': '2'}), (False, True))

    def test_failed_transaction_reports_journal_once_and_actual_package_changes(self):
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                descriptor = os.open(Path(directory) / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
                process = Mock()
                process.__enter__ = Mock(return_value=process)
                process.__exit__ = Mock(return_value=False)
                process.poll.return_value = 1
                process.wait.return_value = 1
                line = 'error: unable to lock database'
                journal = json.dumps(dict(__CURSOR='one', MESSAGE=line)) + '\n'
                with patch.object(helper.os, 'open', return_value=descriptor), \
                        patch.object(helper, 'versions', side_effect=[{'bash': '1'}, {'bash': '2' if changed else '1'}]), \
                        patch.object(helper.subprocess, 'Popen', return_value=process), \
                        patch.object(helper, 'run', side_effect=[Mock(stdout=journal), Mock(stdout='')]), \
                        patch.object(helper, 'emit') as emit:
                    self.assertEqual(helper.apply(), 1)
                messages = [call.kwargs['message'] for call in emit.call_args_list]
                self.assertEqual(sum(line in message for message in messages), 1)
                summary = emit.call_args.kwargs['message']
                self.assertNotIn(line, summary)
                self.assertEqual('Some packages changed.' in summary, changed)
                self.assertNotIn('may have changed', summary)

    def test_session_notices_match_the_package_hook_exactly(self):
        hook = ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook'
        targets = tuple(line.removeprefix('Target = ') for line in hook.read_text().splitlines()
                        if line.startswith('Target = '))
        self.assertEqual(catalog.SESSION_TARGETS, targets)
        for name in ('emaki-config', 'niri', 'niri-emaki', 'quickshell-emaki', 'qt6-base', 'qt6-wayland'):
            with self.subTest(name=name):
                self.assertEqual(helper.changed({name: '1'}, {name: '2'}), (False, True))
                self.assertTrue(any('sign out' in warning for warning in catalog.warnings_for([dict(name=name, aur=False)])))
                self.assertEqual(helper.changed({name: '1'}, {name: '1'}), (False, False))
        for name in ('emaki', 'emaki-keyring', 'emaki-mirrorlist', 'qt5-base', 'niri-extra'):
            with self.subTest(name=name):
                self.assertEqual(helper.changed({name: '1'}, {name: '2'}), (False, False))
                self.assertFalse(any('sign out' in warning for warning in catalog.warnings_for([dict(name=name, aur=False)])))

    def test_space_refusal_is_the_final_message_even_after_service_diagnostics(self):
        refusal = ('Not enough free space for this update: it needs about 3.4 GB, '
                   '500.0 MB is free. Free some space, then try again.')
        with tempfile.TemporaryDirectory() as directory:
            descriptor = os.open(Path(directory) / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
            process = Mock()
            process.__enter__ = Mock(return_value=process)
            process.__exit__ = Mock(return_value=False)
            process.poll.return_value = 1
            process.wait.return_value = 1
            journal = '\n'.join(json.dumps(dict(__CURSOR=str(index), MESSAGE=line))
                                for index, line in enumerate((refusal, 'service failed with exit status 1')))
            with patch.object(helper.os, 'open', return_value=descriptor), \
                    patch.object(helper, 'versions', return_value={'bash': '1'}), \
                    patch.object(helper.subprocess, 'Popen', return_value=process), \
                    patch.object(helper, 'run', side_effect=[Mock(stdout=journal), Mock(stdout='')]), \
                    patch.object(helper, 'emit') as emit:
                self.assertEqual(helper.apply(), 1)
            self.assertEqual(emit.call_args.args, ('finished',))
            self.assertFalse(emit.call_args.kwargs['ok'])
            self.assertEqual(emit.call_args.kwargs['message'], refusal)

    def test_nonroot_wrapper_leaves_root_refusal_to_pacman(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        for arguments in ([], ['--noninteractive']):
            with self.subTest(arguments=arguments), \
                    patch.dict(module['main'].__globals__, run=Mock(return_value=1), preflight=Mock()), \
                    patch.object(os, 'geteuid', return_value=1000), \
                    patch.object(sys, 'argv', ['emaki-update', *arguments]):
                self.assertEqual(module['main'](), 1)
                module['main'].__globals__['preflight'].assert_not_called()
                module['main'].__globals__['run'].assert_called_once_with(
                    ['/usr/bin/pacman', '-Syu'] + (['--noconfirm'] if arguments else []),
                    fail_on_hook_error=bool(arguments))

    def test_noninteractive_wrapper_keeps_full_upgrade_and_failed_hooks(self):
        module = runpy.run_path(str(ROOT / 'scripts/emaki-update'))
        with patch.dict(module['main'].__globals__, run=Mock(return_value=0), preflight=Mock()), \
                patch.object(os, 'geteuid', return_value=0):
            mocked = module['main'].__globals__['run']
            with patch.object(sys, 'argv', ['emaki-update', '--noninteractive']):
                self.assertEqual(module['main'](), 0)
            mocked.assert_called_once_with(['/usr/bin/pacman', '-Syu', '--noconfirm'], fail_on_hook_error=True)
        code = ('import runpy,sys; m=runpy.run_path(sys.argv[1]); '
                'sys.exit(m["run"]([sys.executable,"-c",\'print("error: command failed to execute correctly")\'], True))')
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'scripts/emaki-update')], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Some packages may already have changed', result.stderr)


class Launcher(unittest.TestCase):
    def test_window_starts_without_quickshell_crash_handler(self):
        # The real launcher against a stub qs that records the environment it receives.
        with tempfile.TemporaryDirectory(prefix='update-qs-') as temporary:
            root = Path(temporary)
            (root / 'bin').mkdir()
            stub = root / 'bin/qs'
            stub.write_text('#!/bin/sh\nprintf %s "${QS_DISABLE_CRASH_HANDLER-unset}" > "$QS_STUB_OUT"\n')
            stub.chmod(0o755)
            env = {key: value for key, value in os.environ.items() if key != 'QS_DISABLE_CRASH_HANDLER'}
            env.update(PATH=f'{root / "bin"}:/usr/bin', XDG_RUNTIME_DIR=str(root), QS_STUB_OUT=str(root / 'out'))
            result = subprocess.run(['bash', str(ROOT / 'update-manager/emaki-update-manager')],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'out').read_text(), '1')


if __name__ == '__main__':
    unittest.main()
