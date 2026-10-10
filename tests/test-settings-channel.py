#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel state changes and package migration in a private filesystem."""
import importlib.machinery
import importlib.util
import inspect
import io
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('channel', str(ROOT / 'scripts/emaki-update-channel'))
spec = importlib.util.spec_from_loader(loader.name, loader)
channel = importlib.util.module_from_spec(spec)
loader.exec_module(channel)


class Channel(unittest.TestCase):
    def setUp(self):
        self.area = self.enterContext(tempfile.TemporaryDirectory(prefix='settings-channel-'))
        self.base = Path(self.area)
        self.state = self.base / 'var/lib/emaki/channel'
        self.selector = self.base / 'etc/emaki/channel'
        self.state.parent.mkdir(parents=True)
        self.selector.parent.mkdir(parents=True)
        self.state.write_text(f'Include = {channel.emaki_paths.DATADIR}/mirrors/stable.conf\n')
        self.selector.write_text('Include = /var/lib/emaki/channel\n')
        self.enterContext(patch.object(channel, 'STATE', self.state))
        self.enterContext(patch.object(channel, 'SELECTOR', self.selector))
        self.mirrorlist = self.base / 'etc/pacman.d/emaki-mirrorlist'
        self.mirrorlist.parent.mkdir(parents=True)
        self.mirrorlist.write_text('# Choose the update channel in /etc/emaki/channel.\nInclude = /etc/emaki/channel\n')
        self.enterContext(patch.object(channel, 'MIRRORLIST', self.mirrorlist))
        self.databases = self.base / 'var/lib/pacman'
        (self.databases / 'sync').mkdir(parents=True)
        self.synced = [self.databases / 'sync' / name for name in ('emaki.db', 'emaki.db.sig', 'core.db')]
        for path in self.synced:
            path.write_text('database')
        self.enterContext(patch.object(channel, 'database', return_value=self.databases))
        real_regular = channel.regular
        self.enterContext(patch.object(channel, 'regular', lambda path: real_regular(path, os.getuid())))

    def as_root(self):
        self.enterContext(patch.object(channel.os, 'geteuid', return_value=0))
        original = Path.lstat
        def metadata(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path in (self.state.parent, self.state.parent.parent):
                values = list(result)
                values[4] = 0
                return os.stat_result(values)
            return result
        self.enterContext(patch.object(Path, 'lstat', metadata))

    def test_switch_and_reset_change_only_machine_state(self):
        self.as_root()
        before = self.selector.read_bytes()
        with patch.object(channel, 'detect', side_effect=lambda _: channel.managed()):
            self.assertEqual(channel.switch('testing')['status'], 'applied')
            self.assertEqual(channel.managed(), 'testing')
            # The next refresh downloads the selected channel's database; others stay.
            self.assertEqual([path.exists() for path in self.synced], [False, False, True])
            self.synced[0].write_text('database')
            self.assertEqual(channel.switch('testing')['status'], 'unchanged')
            self.assertTrue(self.synced[0].exists())
            result = channel.switch('stable')
            self.assertEqual(result['channel'], 'stable')
            self.assertIn('Packages newer than Stable stay until Stable catches up.', result['message'])
        self.assertEqual(self.selector.read_bytes(), before)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o644)

    def test_confirmation_failure_restores_previous_selection(self):
        self.as_root()
        before = self.state.read_bytes()
        with patch.object(channel, 'detect', return_value='stable'):
            with self.assertRaisesRegex(ValueError, 'could not be confirmed'):
                channel.switch('testing')
        self.assertEqual(self.state.read_bytes(), before)

    def test_running_package_operation_refused_without_changes(self):
        self.as_root()
        before = self.state.read_bytes()
        (self.databases / 'db.lck').touch()
        with patch.object(channel, 'detect', side_effect=lambda _: channel.managed()):
            with self.assertRaisesRegex(ValueError, 'Another package operation'):
                channel.switch('testing')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertTrue(all(path.exists() for path in self.synced))

    def test_unprepared_database_restores_previous_selection(self):
        self.as_root()
        before = self.state.read_bytes()
        with patch.object(channel, 'detect', side_effect=lambda _: channel.managed()), \
                patch.object(channel, 'forget_database', side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError, 'previous choice was restored'):
                channel.switch('testing')
        self.assertEqual(self.state.read_bytes(), before)

    def test_invalid_custom_and_unprivileged_refused_without_changes(self):
        before = self.state.read_bytes()
        for value in ('nightly', '../stable', 'testing;false'):
            with self.assertRaises(ValueError):
                channel.switch(value)
        with patch.object(channel.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'Authentication'):
                channel.switch('testing')
        self.as_root()
        with patch.object(channel, 'detect', return_value='custom'):
            with self.assertRaisesRegex(ValueError, 'custom'):
                channel.switch('testing')
        self.assertEqual(self.state.read_bytes(), before)

    def test_validation_precedes_privileged_file_access(self):
        with patch.object(channel, 'managed', side_effect=AssertionError('Reached channel state')):
            with self.assertRaisesRegex(ValueError, 'Choose Stable'):
                channel.switch('../testing')

    def test_mirror_list_outside_the_chain_is_not_managed(self):
        # A merged .pacnew includes the stable servers directly until the next upgrade.
        self.assertEqual(channel.managed(), 'stable')
        self.mirrorlist.write_text('Include = /usr/share/emaki/mirrors/stable.conf\n')
        with self.assertRaisesRegex(ValueError, 'current repository package'):
            channel.managed()
        with patch.object(channel, 'detect', return_value='stable'):
            self.assertFalse(channel.channel_status('/etc/pacman.conf')['editable'])

    def test_symlink_and_extra_directive_are_not_managed(self):
        self.state.unlink()
        self.state.symlink_to(self.selector)
        with self.assertRaises(ValueError):
            channel.managed()
        self.state.unlink()
        self.state.write_text(f'Include = {channel.emaki_paths.DATADIR}/mirrors/stable.conf\nServer = elsewhere\n')
        with self.assertRaises(ValueError):
            channel.managed()

    def migrate(self):
        return subprocess.run(['bash', '-eu', '-c', 'source "$1"; _emaki_channel_state "$2"',
                               'migration', str(ROOT / 'packaging/emaki-mirrorlist/emaki-mirrorlist.install'),
                               str(self.base)], capture_output=True, text=True)

    def test_packaging_preserves_testing_and_is_idempotent(self):
        self.state.unlink()
        self.selector.write_text('Include = /usr/share/emaki/mirrors/testing.conf\n')
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selector.read_text().splitlines()[-1], 'Include = /var/lib/emaki/channel')
        self.assertIn('/testing.conf', self.state.read_text())
        self.assertEqual(self.migrate().returncode, 0)

    def test_packaging_preserves_custom_and_refuses_symlink(self):
        self.state.unlink()
        self.selector.write_text('Server = https://example.invalid/custom\n')
        before = self.selector.read_bytes()
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(self.selector.read_bytes(), before)
        self.assertFalse(self.state.exists())
        self.selector.write_text('Include = /usr/share/emaki/mirrors/stable.conf\n')
        self.state.symlink_to(self.selector)
        self.assertNotEqual(self.migrate().returncode, 0)

    def test_packaging_resumes_and_keeps_the_channel_in_effect(self):
        # An interrupted migration: the state is already written and kept byte for byte.
        self.selector.write_text('Include = /usr/share/emaki/mirrors/stable.conf\n')
        before = self.state.read_bytes()
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(self.state.read_bytes(), before)
        # The selector pacman reads wins over unused state: a hand edit to testing after the
        # move, or stale custom state, ends on the selector's channel without an error.
        for stale in (before, b'Server = https://example.invalid/custom\n'):
            with self.subTest(stale=stale):
                self.selector.write_text('# Edited by hand.\nInclude = /usr/share/emaki/mirrors/testing.conf\n')
                self.state.write_bytes(stale)
                result = self.migrate()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.selector.read_text().splitlines()[-1], 'Include = /var/lib/emaki/channel')
                self.assertEqual(channel.active_lines(self.state), ['Include = /usr/share/emaki/mirrors/testing.conf'])

    def initialize(self):
        return subprocess.run(['bash', '-eu', '-c', 'source "$1"; _emaki_initialize_channel "$2"',
                               'prepare', str(ROOT / 'packaging/emaki-mirrorlist/emaki-mirrorlist.install'),
                               str(self.base)], capture_output=True, text=True)

    def test_packaging_repairs_missing_or_invalid_state_to_stable(self):
        for state in (None, '', 'junk\n', 'Server = https://example.invalid/custom\n'):
            with self.subTest(state=state):
                self.selector.write_text('Include = /var/lib/emaki/channel\n')
                self.state.unlink(missing_ok=True)
                if state is not None:
                    self.state.write_text(state)
                self.assertEqual(self.initialize().returncode, 0)
                self.assertIn('Include = /usr/share/emaki/mirrors/stable.conf', self.selector.read_text())
                self.assertEqual(self.migrate().returncode, 0)
                self.assertEqual(channel.active_lines(self.selector), ['Include = /var/lib/emaki/channel'])
                self.assertEqual(channel.active_lines(self.state), ['Include = /usr/share/emaki/mirrors/stable.conf'])
        # A valid choice, including a local comment, is kept verbatim.
        self.state.write_text('# Mine.\nInclude = /usr/share/emaki/mirrors/testing.conf\n')
        self.assertEqual(self.initialize().returncode, 0)
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(self.state.read_text(), '# Mine.\nInclude = /usr/share/emaki/mirrors/testing.conf\n')
        self.assertEqual(channel.active_lines(self.selector), ['Include = /var/lib/emaki/channel'])


class Refresh(unittest.TestCase):
    """Real pacman keeps a newer database of the other channel unless the copy is gone."""

    def test_switch_downloads_the_selected_database(self):
        for tool in ('pacman', 'repo-add', 'bsdtar', 'unshare'):
            if not shutil.which(tool):
                self.skipTest(f'{tool} is unavailable')
        base = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='channel-refresh-')))
        for name, version, day in (('stable', '1-1', '2026-10-01'), ('testing', '2-1', '2026-10-05')):
            stage = base / f'stage-{name}'
            stage.mkdir()
            (stage / '.PKGINFO').write_text(f'pkgname = demo\npkgver = {version}\npkgdesc = d\narch = any\n'
                                            'size = 0\nbuilddate = 1\n')
            package = base / name / f'demo-{version}-any.pkg.tar.zst'
            package.parent.mkdir()
            subprocess.run(['bsdtar', '--zstd', '-cf', str(package), '-C', str(stage), '.PKGINFO'], check=True)
            subprocess.run(['repo-add', '-q', str(base / name / 'emaki.db.tar.gz'), str(package)],
                           check=True, capture_output=True)
            for database in (base / name).glob('emaki.db*'):
                subprocess.run(['touch', '-h', '-d', day, str(database)], check=True)
            (base / f'{name}.conf').write_text('[options]\nArchitecture = x86_64\nSigLevel = Never\n'
                                               f'[emaki]\nServer = file://{base / name}\n')
        (base / 'root').mkdir()
        (base / 'db').mkdir()

        def pacman(config, *args):
            result = subprocess.run(['unshare', '--user', '--map-root-user', 'pacman', '--config',
                                     str(base / f'{config}.conf'), '--root', str(base / 'root'),
                                     '--dbpath', str(base / 'db'), '--logfile', '/dev/null', *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        pacman('testing', '-Sy')
        pacman('stable', '-Sy')
        self.assertEqual(pacman('stable', '-Sl', 'emaki').split(), ['emaki', 'demo', '2-1'])
        channel.forget_database(base / 'db')
        pacman('stable', '-Sy')
        self.assertEqual(pacman('stable', '-Sl', 'emaki').split(), ['emaki', 'demo', '1-1'])


class Mutants(unittest.TestCase):
    def test_important_paths_reject_mutants(self):
        source = inspect.getsource(channel.switch)
        mutations = [
            ("if channel not in ('stable', 'testing'):", 'if False:',
             'test_validation_precedes_privileged_file_access'),
            ('forget_database(databases)', 'pass',
             'test_switch_and_reset_change_only_machine_state'),
            ('replace_state(old)', 'pass',
             'test_confirmation_failure_restores_previous_selection'),
        ]
        for original, replacement, test in mutations:
            with self.subTest(test=test):
                self.assertIn(original, source)
                namespace = dict(channel.__dict__)
                exec(source.replace(original, replacement, 1), namespace)
                # The function resolves fixture paths in the actual module globals.
                import types
                mutated = types.FunctionType(namespace['switch'].__code__, channel.__dict__)
                with patch.object(channel, 'switch', mutated):
                    result = unittest.TextTestRunner(stream=io.StringIO()).run(Channel(test))
                self.assertFalse(result.wasSuccessful(), 'A critical channel mutant survived.')


if __name__ == '__main__':
    unittest.main()
