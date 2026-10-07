#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real pacman preparation against small local repository fixtures."""
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/mirror'))
import transactions

spec = importlib.util.spec_from_file_location('publisher', ROOT / 'packaging/mirror/publish.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def database(packages):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for package in packages:
            name, version, dependencies, conflicts, *replaces = package
            fields = {'NAME': [name], 'VERSION': [version], 'BASE': [name], 'ARCH': ['any'],
                      'FILENAME': [f'{name}-{version}-any.pkg.tar.zst'], 'CSIZE': ['100'],
                      'ISIZE': ['100'], 'DESC': ['Transaction fixture'],
                      'DEPENDS': dependencies, 'CONFLICTS': conflicts,
                      'REPLACES': replaces[0] if replaces else []}
            raw = ''.join(f'%{key}%\n' + '\n'.join(value) + '\n\n'
                          for key, value in fields.items() if value).encode()
            info = tarfile.TarInfo(f'{name}-{version}/desc')
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


@unittest.skipUnless(shutil.which('pacman') and shutil.which('fakeroot'), 'pacman and fakeroot required')
class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='transaction-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.client = publisher.ScratchPacman()
        self.addCleanup(self.client.close)
        self.previous = database([('emaki', '1-1', ['shell=1-1'], []), ('shell', '1-1', [], [])])

    def check(self, packages, previous=None, arch_dbs='none'):
        (self.repo / 'emaki.db').write_bytes(database(packages))
        return transactions.check_transactions(self.client, self.repo, ['emaki'], arch_dbs, previous)

    def test_unbumped_marker_rejects_rebuild(self):
        with self.assertRaisesRegex(transactions.TransactionError, r'shell=1-1'):
            self.check([('emaki', '1-1', ['shell=1-1'], []), ('shell', '1-2', [], [])], self.previous)

    def test_bumped_marker_allows_rebuild_and_both_upgrade_scenarios(self):
        self.assertEqual(self.check([('emaki', '1-2', ['shell=1-2'], []),
                                     ('shell', '1-2', [], [])], self.previous),
                         ['fresh install', 'fresh install upgrade', 'previous release upgrade'])

    def test_fresh_missing_dependency_rejected(self):
        with self.assertRaisesRegex(transactions.TransactionError, 'missing-dependency'):
            self.check([('emaki', '1-1', ['missing-dependency'], [])])

    def test_conflicts_rejected_even_when_print_resolves(self):
        packages = [('emaki', '1-1', ['shell'], ['shell']), ('shell', '1-1', [], [])]
        (self.repo / 'emaki.db').write_bytes(database(packages))
        self.assertEqual(self.client.resolve(self.repo, ['emaki'], 'none').returncode, 0)
        with self.assertRaisesRegex(transactions.TransactionError, 'conflict'):
            transactions.check_transactions(self.client, self.repo, ['emaki'], 'none')

    def test_removed_marker_retains_installed_pin_on_upgrade(self):
        # Fresh shell alone is valid; the old installed marker makes upgrading it invalid.
        (self.repo / 'emaki.db').write_bytes(database([('shell', '1-2', [], [])]))
        with self.assertRaisesRegex(transactions.TransactionError, r'shell=1-1'):
            transactions.check_transactions(self.client, self.repo, ['shell'], 'none', self.previous)

    def test_iso_only_pin_does_not_block_installed_systems(self):
        (self.repo / 'core.db').write_bytes(database([('archinstall', '5-1', [], [])]))
        (self.repo / 'extra.db').write_bytes(database([]))
        packages = [('emaki', '1-1', [], []),
                    ('emaki-installer', '1-1', ['archinstall=4.5-1'], [])]
        checked = self.check(packages, database(packages), str(self.repo))
        self.assertIn('previous release upgrade', checked)

    def test_unselected_package_is_not_a_fresh_install_target(self):
        self.check([('emaki', '1-1', [], []), ('unused', '1-1', ['missing'], [])])

    def test_default_publication_checks_both_installer_profiles(self):
        sys.path.insert(0, str(ROOT / 'installer'))
        from emaki_installer.worker import software_packages
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(arch_dbs='none')
        with mock.patch.object(transactions, 'check_transactions') as check:
            publish.check_transactions(self.repo, [])
        self.assertEqual([call.args[2] for call in check.call_args_list],
                         [software_packages('minimal'), software_packages('rich')])

    def test_upgrade_accepts_default_replacement(self):
        previous = database([('emaki', '1-1', ['old-shell'], []),
                             ('old-shell', '1-1', [], [])])
        checked = self.check([('emaki', '1-2', ['new-shell'], []),
                              ('new-shell', '1-1', [], ['old-shell'], ['old-shell'])], previous)
        self.assertIn('previous release upgrade', checked)

    def test_invalid_database_is_a_plain_refusal(self):
        with self.assertRaisesRegex(transactions.TransactionError, 'Invalid.*database'):
            self.check([('emaki', '1-1', [], [])], b'not a tar database')

    def test_pacman_timeout_is_a_plain_refusal(self):
        with mock.patch.object(transactions.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('pacman', 120)):
            with self.assertRaisesRegex(transactions.TransactionError, 'timed out'):
                transactions.prepare(self.client, self.repo / 'pacman.conf', 'Fresh install', '-S', 'emaki')

    def test_repaired_arch_bound_does_not_require_old_bound_to_resolve(self):
        (self.repo / 'core.db').write_bytes(database([('qt', '2-1', [], [])]))
        (self.repo / 'extra.db').write_bytes(database([]))
        old = database([('emaki', '1-1', ['qt=1-1'], [])])
        checked = self.check([('emaki', '1-2', ['qt=2-1'], [])], old, str(self.repo))
        self.assertIn('previous release upgrade', checked)


    def test_publisher_tests_stable_baseline_when_publishing_testing(self):
        candidate = database([('shell', '1-2', [], [])])
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(arch_dbs='none')
        publish.pointer = mock.Mock(side_effect=lambda channel: ('snapshot', None))
        publish.snapshot = mock.Mock(side_effect=lambda channel, stamp: {
            'emaki.db': candidate if channel == 'testing' else self.previous})
        with self.assertRaisesRegex(publisher.Refused, r'shell=1-1'):
            publish.check_release(candidate, 'testing', ['shell'])
        self.assertEqual(publish.pointer.call_args_list,
                         [mock.call('testing'), mock.call('stable')])
        publish.snapshot.assert_any_call('stable', 'snapshot')

    def test_publish_refuses_conflicts_before_lock_or_upload(self):
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(closure='emaki', arch_dbs='none')
        publish.state = self.repo
        publish.dry_run = False
        publish.trusted = 'fixture'
        publish._verifier = mock.Mock()
        publish.collect = mock.Mock(return_value={
            'emaki-1-1-any.pkg.tar.zst': {'sha256': 'digest', 'source': {}, 'path': 'fixture'}})
        publish.begin = mock.Mock(return_value={'id': 'trial', 'steps': []})
        publish.check_tree = mock.Mock()
        publish.refuse_burnt = mock.Mock()
        publish.refuse_downgrade = mock.Mock()
        publish.pointer = mock.Mock(return_value=(None, None))
        publish.checked = mock.Mock()
        publish.take_lock = mock.Mock()
        publish.backend = mock.Mock()
        def build(channel, work, packages, sign):
            (work / 'emaki.db.tar.gz').write_bytes(database([
                ('emaki', '1-1', ['shell'], []), ('shell', '1-1', [], ['emaki'])]))
        publish.build_db = build
        with self.assertRaisesRegex(publisher.Refused, 'conflict'):
            publish.publish('testing', self.repo)
        publish.checked.assert_not_called()
        publish.take_lock.assert_not_called()
        self.assertEqual(publish.backend.mock_calls, [])

    def test_completed_promotion_does_not_reopen_preupload_gate(self):
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(closure='emaki')
        publish.pointer = mock.Mock(return_value=('snapshot', None))
        publish.snapshot = mock.Mock(return_value={'MANIFEST': b'', 'emaki.db': b''})
        publish.begin = mock.Mock(return_value={'id': 'done', 'steps': list(range(7))})
        publish.check_stamp = mock.Mock()
        publish.check_release = mock.Mock(side_effect=publisher.Refused('changed Arch database'))
        publish.take_lock = mock.Mock()
        publish.copy_snapshot = mock.Mock()
        publish.promote(False)
        publish.check_release.assert_not_called()
        publish.copy_snapshot.assert_called_once()

    def test_arch_database_help_does_not_offer_skip(self):
        self.assertNotIn("'skip'", publisher.parser().format_help())

    def test_skip_refusal_suggests_usable_arch_databases(self):
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(arch_dbs='skip')
        with self.assertRaises(publisher.Refused) as caught:
            publish.check_transactions(self.repo, ['emaki'])
        self.assertNotIn('none', str(caught.exception))
        self.assertIn('auto', str(caught.exception))

    def test_publisher_forbids_skip(self):
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(arch_dbs='skip')
        with self.assertRaisesRegex(publisher.Refused, 'Cannot skip'):
            publish.check_transactions(self.repo, ['emaki'])

    def bridge(self, candidate, previous):
        publish = publisher.Publisher.__new__(publisher.Publisher)
        publish.args = SimpleNamespace(github='local:unused', closure='emaki', arch_dbs='none')
        publish.dry_run = False
        publish.state = self.repo
        publish.pointer = mock.Mock(return_value=('snapshot', None))
        files = {'MANIFEST': b'', 'emaki.db': database(candidate), 'emaki.files': b'',
                 'SOURCES': b'', 'SOURCES.json': b'{}'}
        publish.snapshot = mock.Mock(return_value=files)
        publish.verify_sources = mock.Mock()
        publish.confirm_channel = mock.Mock()
        target = mock.Mock()
        target.fetch.side_effect = lambda tag, name: database(previous) if name == 'emaki.db' else None
        target.assets.return_value = set()
        return publish, target

    def test_bridge_checks_signed_destination_before_any_upload(self):
        publish, target = self.bridge([('emaki', '1-1', [], [])], [('emaki', '1-1', [], [])])
        publish.confirm_channel.side_effect = publisher.Refused('missing database signature')
        with mock.patch.object(publisher, 'open_github', return_value=target):
            with self.assertRaisesRegex(publisher.Refused, 'missing database signature'):
                publish.github('testing', False)
        target.upload.assert_not_called()
        target.delete.assert_not_called()

    def test_bridge_checks_old_address_installed_set_before_any_upload(self):
        publish, target = self.bridge([('emaki', '1-2', [], []), ('shell', '1-2', [], [])],
                                     [('emaki', '1-1', [], []),
                                      ('old-addon', '1-1', ['shell=1-1'], []),
                                      ('shell', '1-1', [], [])])
        with mock.patch.object(publisher, 'open_github', return_value=target):
            with self.assertRaisesRegex(publisher.Refused, 'shell=1-1'):
                publish.github('testing', False)
        target.upload.assert_not_called()
        target.delete.assert_not_called()

    def test_no_skip(self):
        with self.assertRaisesRegex(transactions.TransactionError, 'cannot be skipped'):
            self.check([('emaki', '1-1', [], [])], arch_dbs='skip')


if __name__ == '__main__':
    unittest.main()
