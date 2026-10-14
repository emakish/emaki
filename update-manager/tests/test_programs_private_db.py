#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh-system repository catalog and transaction planning integration checks."""
import io
import os
from pathlib import Path
import subprocess
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_arch as arch
import programs_aur as aur
import programs_engine as engine


class PrivateDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-private-db-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.system = self.root / 'system'
        (self.system / 'local' / 'paint-1').mkdir(parents=True)
        (self.system / 'local' / 'paint-1' / 'desc').write_text('installed sentinel')
        self.cache = self.root / 'cache'
        self.configuration = '[options]\n[extra]\nServer = https://example.org/stable\n'
        self.repositories = ['extra']
        self.version = '2'
        self.download = True
        self.commands = []
        self.sync_paths = []
        self.enterContext(patch.object(arch.subprocess, 'run', side_effect=self.run_command))
        self.real_sync = arch.sync_database
        self.sync = self.enterContext(patch.object(arch, 'sync_database', side_effect=self.synchronize))
        self.enterContext(patch.object(aur, 'head_commit', return_value='a' * 40))
        metadata = [dict(id='org.example.Paint', name='Paint', packages=['paint'], origin='Arch',
                         summary='Drawing tool', description='', icons=[], categories=[],
                         screenshots=[], keywords=[], sourcePath='')]
        self.enterContext(patch.object(engine.appstream, 'components', return_value=(metadata, [])))
        self.enterContext(patch.object(engine.flatpak, 'discover', return_value=([], [])))
        self.enterContext(patch.object(engine.receipts, 'receipts', return_value=[]))
        self.enterContext(patch.object(engine, 'syslog'))
        self.before = self.system_snapshot()
        self.addCleanup(self.assert_system_unchanged)

    def system_snapshot(self):
        return {str(path.relative_to(self.system)): path.read_bytes()
                for path in self.system.rglob('*') if path.is_file()}

    def assert_system_unchanged(self):
        self.assertEqual(self.system_snapshot(), self.before)
        self.assertFalse((self.system / 'sync').exists())

    def write_repository(self, directory):
        with tarfile.open(directory / 'extra.db', 'w:gz') as archive:
            for name in ('paint', 'ink'):
                raw = ('%NAME%\n' + name + '\n\n%VERSION%\n' + self.version
                       + '\n\n%DESC%\nDrawing tool\n\n%CSIZE%\n10\n\n%ISIZE%\n20\n').encode()
                member = tarfile.TarInfo(name + '-' + self.version + '/desc')
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))

    def synchronize(self, database):
        self.assertTrue(database.is_relative_to(self.cache))
        self.assertNotEqual(database, self.system)
        self.assertEqual((database / 'local' / 'paint-1' / 'desc').read_text(), 'installed sentinel')
        self.sync_paths.append(database)
        if self.download:
            self.write_repository(database / 'sync')
        return self.download and self.repositories == ['extra']

    def run_command(self, command, **_kwargs):
        self.commands.append(command)
        if command == ['pacman-conf']:
            output = self.configuration
        elif command == ['pacman-conf', '--repo-list']:
            output = '\n'.join(self.repositories)
        elif command == ['pacman-conf', 'DBPath']:
            output = str(self.system)
        elif '-Q' in command:
            output = 'paint 1\nbase-devel 1\ngit 1\n'
        else:
            self.assertIn('--dbpath', command)
            database = Path(command[command.index('--dbpath') + 1])
            self.assertTrue(database.is_relative_to(self.cache))
            self.assertTrue((database / 'sync' / 'extra.db').is_file())
            if '-T' in command:
                deps = command[command.index('--') + 1:]
                missing = [dep for dep in deps if dep not in ('base-devel', 'git')]
                return subprocess.CompletedProcess(command, 127 if missing else 0, '\n'.join(missing), '')
            self.assertTrue('-Sup' in command or '-Sp' in command)
            name = command[-1].split('/')[-1]
            with tarfile.open(database / 'sync' / 'extra.db') as archive:
                member = next(item for item in archive if item.name.startswith(name + '-'))
                row = arch.parse_desc(archive.extractfile(member).read().decode(), 'extra')
            output = 'extra|' + name + '|' + row['version'] + '|10\n'
        return subprocess.CompletedProcess(command, 0, output, '')

    def new_engine(self):
        return engine.Engine(SimpleNamespace(pacman=Mock()), self.cache)

    def aur_system(self):
        payload = dict(version=5, type='multiinfo', results=[dict(
            Name='paint-addon', PackageBase='paint-addon', Version='1', Description='Drawing extension',
            NumVotes=1, Popularity=1.0, Maintainer='maintainer', OutOfDate=None,
            FirstSubmitted=1600000000, LastModified=1700000000, URL='', License=['MIT'],
            Depends=['ink'], MakeDepends=[])])
        return aur.System(Mock(return_value=payload), arch.ENV, cache=self.cache)

    def test_fresh_catalog_and_both_plans_share_downloaded_repository(self):
        store = self.new_engine()
        result = store.catalog()
        self.assertGreater(result['total'], 0)
        self.assertEqual(store.installed['paint'], '1')
        self.assertEqual(store.rows['package:extra/paint']['installedVersion'], '1')
        native = arch.install_plan('extra/ink', cache=self.cache)
        community = aur.build_plan('paint-addon', self.aur_system())
        self.assertTrue(native['ok'])
        self.assertEqual(native['packages'][0]['package'], 'ink')
        self.assertEqual([row['package'] for row in community['packages']], ['ink'])
        self.assertEqual(self.sync.call_count, 1)
        self.assertTrue(all(not path.exists() for path in self.sync_paths))

    def test_missing_repository_preserves_catalog_and_refuses_aur_before_dependency_lookup(self):
        self.repositories.append('community')
        store = self.new_engine()
        result = store.catalog()
        self.assertGreater(result['total'], 0)
        self.assertTrue(result['partial'])
        self.assertTrue(any('community' in warning for warning in result['warnings']))
        self.enterContext(patch.object(store, 'aur_enabled', return_value=True))
        self.enterContext(patch.object(store, 'aur_status', return_value={'snapshots': True}))
        fetch = self.enterContext(patch.object(aur, 'fetch_json'))
        result = store.aur_plan('paint-addon')
        self.assertEqual(result['code'], 'metadata-unavailable')
        fetch.assert_not_called()

    def test_repository_update_invalidates_both_plan_tokens(self):
        first = arch.install_plan('extra/ink', cache=self.cache)
        community = aur.build_plan('paint-addon', self.aur_system())
        self.version = '3'
        second = arch.install_plan('extra/ink', cache=self.cache, refresh=True)
        updated = aur.build_plan('paint-addon', self.aur_system())
        self.assertNotEqual(first['token'], second['token'])
        self.assertNotEqual(community['token'], updated['token'])
        self.assertEqual(updated['packages'][0]['version'], '3')

    def test_configuration_change_does_not_reuse_previous_channel(self):
        arch.inventory(cache=self.cache)
        self.configuration = self.configuration.replace('/stable', '/testing')
        self.download = False
        warnings = []
        available, installed = arch.inventory(cache=self.cache, warnings=warnings)
        self.assertEqual(available, {})
        self.assertEqual(installed['paint'], '1')
        self.assertTrue(warnings)
        with self.assertRaises(arch.MetadataUnavailable):
            arch.install_plan('extra/ink', cache=self.cache)
        self.configuration = self.configuration.replace('/testing', '/stable')
        # A return to a previously used channel must not revive its old database.
        self.assertEqual(arch.inventory(cache=self.cache)[0], {})

    def test_crashed_queries_are_removed_without_following_links(self):
        self.cache.mkdir()
        abandoned = self.cache / 'repository-query-abandoned'
        abandoned.mkdir()
        (abandoned / 'partial').write_text('interrupted download')
        link = self.cache / 'repository-query-link'
        link.symlink_to(self.system / 'local', target_is_directory=True)
        arch.inventory(cache=self.cache)
        self.assertFalse(abandoned.exists())
        self.assertFalse(link.is_symlink())
        self.assertTrue((self.system / 'local').is_dir())

    def test_failed_refresh_retains_readable_cached_rows_and_warns(self):
        store = self.new_engine()
        first = store.catalog()
        self.download = False
        refreshed = store.catalog(refresh=True)
        self.assertEqual(refreshed['total'], first['total'])
        self.assertTrue(refreshed['partial'])
        self.assertTrue(any('could not be refreshed' in warning for warning in refreshed['warnings']))
        self.assertTrue(arch.install_plan('extra/ink', cache=self.cache)['ok'])

    def test_sync_command_targets_private_database_as_an_unprivileged_user(self):
        database = self.root / 'download'
        database.mkdir()
        process = Mock(returncode=0)
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        with patch.object(arch.os, 'geteuid', return_value=1000), patch.object(
                arch.subprocess, 'Popen', return_value=process) as launch:
            self.assertTrue(self.real_sync(database))
        command = launch.call_args.args[0]
        self.assertEqual(command[:4], ['fakeroot', '--', 'pacman', '-Sy'])
        self.assertEqual(command[command.index('--dbpath') + 1], str(database))
        self.assertEqual(command[command.index('--logfile') + 1], '/dev/null')
        self.assertNotIn('-Syu', command)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        process.communicate.assert_called_once_with(timeout=180)

    def test_root_cache_ignores_caller_home_and_cache_environment(self):
        with patch.object(arch.os, 'geteuid', return_value=0), patch.dict(
                arch.os.environ, {'HOME': str(self.root / 'caller'), 'XDG_CACHE_HOME': str(self.cache)}):
            self.assertEqual(arch.cache_directory(), Path('/var/cache/emaki-programs'))

    def test_root_download_directories_are_traversable_under_private_umask(self):
        # Exercise both creation and repair of caches left by an older helper.
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.cache.chmod(0o700)
                    for saved in self.cache.glob('repositories-*'):
                        saved.chmod(0o700)
                def download(database):
                    saved = next(self.cache.glob('repositories-*'))
                    for directory in (self.cache, saved, database, database / 'sync'):
                        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
                    return self.synchronize(database)
                old_umask = os.umask(0o077)
                try:
                    with patch.object(arch.os, 'geteuid', return_value=0), patch.object(
                            arch, 'cache_directory', return_value=self.cache), patch.object(
                            arch, 'sync_database', side_effect=download):
                        self.assertTrue(arch.install_plan('extra/ink', refresh=True)['ok'])
                finally:
                    os.umask(old_umask)

    def test_conditional_refresh_preserves_server_timestamps_in_both_directions(self):
        server_mtime = 1600000000
        def download(database):
            target = database / 'sync' / 'extra.db'
            # A conditional downloader retains a file newer than its server copy.
            if not target.exists() or target.stat().st_mtime < server_mtime:
                self.synchronize(database)
                os.utime(target, (server_mtime, server_mtime))
            return True
        with patch.object(arch, 'sync_database', side_effect=download):
            first = arch.install_plan('extra/ink', cache=self.cache)
            saved = next(self.cache.glob('repositories-*')) / 'extra.db'
            self.assertEqual(saved.stat().st_mtime, server_mtime)
            server_mtime += 3600
            self.version = '3'
            second = arch.install_plan('extra/ink', cache=self.cache, refresh=True)
            self.assertEqual(second['packages'][0]['version'], '3')
            self.assertNotEqual(first['token'], second['token'])
            self.assertEqual(saved.stat().st_mtime, server_mtime)
            unchanged = arch.install_plan('extra/ink', cache=self.cache, refresh=True)
            self.assertEqual(unchanged['token'], second['token'])

    def test_engine_refreshes_native_and_aur_plans_before_confirmation(self):
        store = self.new_engine()
        store.catalog()
        self.version = '3'
        native = store.plan('install', 'package:extra/ink')
        self.assertTrue(native['ok'], native)
        self.assertEqual(native['transaction']['packages'][0]['version'], '3')
        self.version = '4'
        self.enterContext(patch.object(store, 'aur_enabled', return_value=True))
        self.enterContext(patch.object(store, 'aur_status', return_value={'snapshots': True}))
        with patch.object(aur, 'fetch_json', self.aur_system().fetch_json):
            community = store.aur_plan('paint-addon')
        self.assertTrue(community['ok'], community)
        self.assertEqual(community['packages'][0]['version'], '4')

    def test_both_engine_plans_name_unavailable_repositories(self):
        store = self.new_engine()
        store.catalog()
        self.repositories.append('community')
        self.enterContext(patch.object(store, 'aur_enabled', return_value=True))
        self.enterContext(patch.object(store, 'aur_status', return_value={'snapshots': True}))
        for result in (store.plan('install', 'package:extra/ink'), store.aur_plan('paint-addon')):
            self.assertEqual(result['code'], 'metadata-unavailable')
            self.assertIn('community', result['message'])
            self.assertNotIn('Connect to the internet', result['message'])

    def test_interrupted_sync_kills_downloaders_and_restores_signal_handler(self):
        process = Mock(pid=123, returncode=-9)
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        previous = arch.signal.getsignal(arch.signal.SIGTERM)
        def interrupt(**_kwargs):
            arch.signal.getsignal(arch.signal.SIGTERM)(arch.signal.SIGTERM, None)
        process.communicate.side_effect = interrupt
        with patch.object(arch.subprocess, 'Popen', return_value=process), patch.object(arch.os, 'killpg') as kill:
            with self.assertRaises(SystemExit):
                self.real_sync(self.root / 'download')
        kill.assert_called_once_with(123, arch.signal.SIGKILL)
        process.wait.assert_called_once()
        self.assertIs(arch.signal.getsignal(arch.signal.SIGTERM), previous)


if __name__ == '__main__':
    unittest.main()
