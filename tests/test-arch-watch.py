#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Arch database and real PKGBUILD metadata tests for arch-watch."""

import importlib.machinery
import importlib.util
import io
import json
import gzip
import http.client
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr, redirect_stdout
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'packaging/arch-watch'
loader = importlib.machinery.SourceFileLoader('arch_watch', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
watch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = watch
loader.exec_module(watch)


def archive(packages):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as tar:
        for record in packages:
            name, version, provides, *extra = record
            text = f'%NAME%\n{name}\n\n%VERSION%\n{version}\n\n'
            if provides:
                text += '%PROVIDES%\n' + '\n'.join(provides) + '\n\n'
            for field, values in zip(('DEPENDS', 'REPLACES'), extra):
                text += f'%{field}%\n' + '\n'.join(values) + '\n\n'
            content = text.encode()
            entry = tarfile.TarInfo(f'{name}-{version}/desc')
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    return result.getvalue()


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipes = self.root / 'packaging'
        self.recipes.mkdir()
        self.dbs = self.root / 'db'
        self.dbs.mkdir()
        self.recipe('shell', "depends=('qt6-base>=6.11' 'qt6-base<6.12')")
        self.write_db('core', [('pacman', '7.1.0.r9.g54d9411-2', [])])
        self.write_db('extra', [('qt6-base', '6.11.2-3', [])])
        self.write_db('core-testing', [])
        self.write_db('extra-testing', [])

    def recipe(self, name, metadata):
        directory = self.recipes / name
        directory.mkdir(exist_ok=True)
        (directory / 'PKGBUILD').write_text(
            f'pkgname={name}\npkgver=1\npkgrel=1\narch=(x86_64)\n'
            f'pkgdesc="Fixture"\nlicense=(MIT)\n{metadata}\n'
            'build() { touch BUILD_MUST_NOT_RUN; exit 91; }\n'
            + ('package() { touch PACKAGE_MUST_NOT_RUN; exit 92; }\n'
             if 'package_shell' not in metadata else ''))

    def write_db(self, repo, packages):
        (self.dbs / f'{repo}.db').write_bytes(archive(packages))

    def run_watch(self):
        return subprocess.run([sys.executable, str(SCRIPT), '--packaging-dir', str(self.recipes),
                               '--db-dir', str(self.dbs)], text=True, capture_output=True,
                              timeout=30)

    def test_healthy_stable_and_testing_fallback(self):
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('0 broken fence groups', result.stdout)
        self.assertFalse(list(self.recipes.rglob('*MUST_NOT_RUN')))

    def test_testing_minor_break_reports_exact_fence(self):
        self.write_db('extra-testing', [('qt6-base', '6.12.0-2', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('BREAK testing: shell depends [qt6-base<6.12, qt6-base>=6.11]', result.stdout)
        self.assertIn('extra-testing/qt6-base 6.12.0-2', result.stdout)
        self.assertNotIn('BREAK stable', result.stdout)

    def test_pacman_major_change_requires_review_in_stable_and_testing(self):
        for repo in ('core', 'core-testing'):
            with self.subTest(repo=repo):
                self.write_db('core', [('pacman', '7.1.0-1', [])])
                self.write_db('core-testing', [])
                self.write_db(repo, [('pacman', '8.0.0-1', [])])
                result = self.run_watch()
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                self.assertIn(f'REVIEW {repo}/pacman 8.0.0-1', result.stdout)
                for component in ('snap-pac', 'installer transactions', 'iso/build.sh'):
                    self.assertIn(component, result.stdout)

    def test_pacman_current_major_handles_epoch_patch_and_package_releases(self):
        self.write_db('core-testing', [('pacman', '1:7.2.3.r1.gabcd-2.1', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn('REVIEW ', result.stdout)

    def test_pacman_missing_or_unknown_version_is_incomplete(self):
        for records in ([], [('pacman', 'next-1', [])]):
            with self.subTest(records=records):
                self.write_db('core', records)
                result = self.run_watch()
                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                self.assertIn('(pacman)', result.stderr)

    def test_new_stock_niri_runs_validation_and_remains_a_release_alarm(self):
        self.recipe('shell', "depends=('niri>=26.04')")
        self.write_db('extra', [('niri', '26.04-3', [])])
        self.write_db('extra-testing', [('niri', '26.05-1', [])])
        with patch.object(watch, 'validate_stock_niri', return_value=0) as validate, \
                redirect_stdout(io.StringIO()) as output:
            status = watch.main(['--packaging-dir', str(self.recipes), '--db-dir', str(self.dbs)])
        self.assertEqual(status, 1)
        self.assertNotIn('RELEASE stable', output.getvalue())
        self.assertIn('RELEASE testing: extra-testing/niri 26.05-1', output.getvalue())
        self.assertNotIn('BREAK ', output.getvalue())
        validate.assert_called_once_with(watch.Package('niri', '26.05-1', 'extra-testing'), self.root)

    def test_new_stock_niri_missing_candidate_fails_incomplete(self):
        fences = [watch.Fence('recipe', 'shell', 'depends', 'niri>=26.04')]
        databases = {repo: {} for repo in watch.REPOS}
        databases['extra']['niri'] = watch.Package('niri', '26.05-1', 'extra')
        with patch.object(watch, 'validate_stock_niri', side_effect=watch.WatchError('wrong version')) as validate, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(watch.watch_stock_niri(fences, databases, self.root), 2)
        self.assertIn('RELEASE stable', output.getvalue())
        self.assertIn('RELEASE testing', output.getvalue())
        self.assertIn('NIRI INCOMPLETE', output.getvalue())
        validate.assert_called_once()

    def test_distinct_niri_candidates_do_not_require_two_installed_versions(self):
        fences = [watch.Fence('recipe', 'shell', 'depends', 'niri>=26.04')]
        databases = {repo: {} for repo in watch.REPOS}
        databases['extra']['niri'] = watch.Package('niri', '26.05-1', 'extra')
        databases['extra-testing']['niri'] = watch.Package('niri', '26.06-1', 'extra-testing')
        for installed in ('26.05-1', '26.06-1', None):
            def validate(package, root):
                if package.version != installed:
                    raise watch.CandidateUnavailable('candidate not installed')
                return 0
            with self.subTest(installed=installed), \
                    patch.object(watch, 'validate_stock_niri', side_effect=validate) as run, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(watch.watch_stock_niri(fences, databases, self.root),
                                 1 if installed else 2)
            self.assertEqual(run.call_count, 2)
            self.assertIn('NIRI NOT CHECKED:', output.getvalue())
            self.assertEqual('NIRI INCOMPLETE:' in output.getvalue(), installed is None)

    def test_scheduled_new_niri_is_attention_without_local_validation(self):
        self.recipe('shell', "depends=('niri>=26.04')")
        self.write_db('extra', [('niri', '26.05-1', [])])
        self.write_db('extra-testing', [('niri', '26.06-1', [])])
        with patch.object(watch, 'validate_stock_niri',
                          side_effect=watch.CandidateUnavailable('not installed')) as validate, \
                redirect_stdout(io.StringIO()) as output:
            status = watch.main(['--scheduled', '--packaging-dir', str(self.recipes),
                                 '--db-dir', str(self.dbs)])
        self.assertEqual(status, 1)
        validate.assert_not_called()
        self.assertEqual(output.getvalue().count('NIRI NOT CHECKED:'), 2)
        self.assertIn('scheduled watch does not validate release candidates', output.getvalue())
        self.assertIn('stock niri package installed', output.getvalue())
        self.assertNotIn('NIRI INCOMPLETE:', output.getvalue())

    def test_niri_validation_error_is_not_hidden_by_another_candidate(self):
        fences = [watch.Fence('recipe', 'shell', 'depends', 'niri>=26.04')]
        databases = {repo: {} for repo in watch.REPOS}
        databases['extra']['niri'] = watch.Package('niri', '26.05-1', 'extra')
        databases['extra-testing']['niri'] = watch.Package('niri', '26.06-1', 'extra-testing')
        with patch.object(watch, 'validate_stock_niri',
                          side_effect=[0, watch.WatchError('configuration missing')]), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(watch.watch_stock_niri(fences, databases, self.root), 2)
        self.assertIn('NIRI INCOMPLETE:', output.getvalue())

    def test_stock_niri_validation_checks_both_checkout_configs(self):
        for name in ('greetd/niri.kdl', 'niri/default.kdl'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// fixture\n')
        candidate = watch.Package('niri', '26.05-1', 'extra')
        for failure in (0, 1):
            responses = [SimpleNamespace(stdout='niri\n'), SimpleNamespace(stdout='niri 26.05-1\n'),
                         SimpleNamespace(returncode=failure, stdout='', stderr='bad config' if failure else ''),
                         SimpleNamespace(returncode=0, stdout='', stderr='')]
            with patch.object(watch.subprocess, 'run', side_effect=responses) as run, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(watch.validate_stock_niri(candidate, self.root), failure)
            self.assertEqual([call.args[0] for call in run.call_args_list[2:]], [
                ['/usr/bin/niri', 'validate', '-c', str(self.root / name)]
                for name in ('greetd/niri.kdl', 'niri/default.kdl')])

    def test_stock_niri_refuses_older_or_fork_owned_binary(self):
        for owner, version in (('niri', '26.04-3'), ('niri-emaki', '26.05-1')):
            responses = [SimpleNamespace(stdout=owner), SimpleNamespace(stdout=f'niri {version}')]
            with patch.object(watch.subprocess, 'run', side_effect=responses) as run:
                with self.assertRaisesRegex(watch.WatchError, 'must own /usr/bin/niri'):
                    watch.validate_stock_niri(watch.Package('niri', '26.05-1', 'extra'), self.root)
            self.assertEqual(run.call_count, 2)

    def test_stable_break_is_reported(self):
        self.write_db('extra', [('qt6-base', '6.12.0-2', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('BREAK stable', result.stdout)
        self.assertIn('BREAK testing', result.stdout)

    def test_missing_and_malformed_databases_are_errors(self):
        path = self.dbs / 'core-testing.db'
        path.unlink()
        self.assertEqual(self.run_watch().returncode, 2)
        path.write_bytes(b'not a tar database')
        result = self.run_watch()
        self.assertEqual(result.returncode, 2)
        self.assertIn('unreadable database', result.stderr)

    def test_removed_soname_does_not_use_old_stable_provider(self):
        self.recipe('shell', "depends=('libseat.so=1-64')")
        self.write_db('extra', [('seatd', '1.0-1', ['libseat.so=1-64'])])
        self.write_db('extra-testing', [('seatd', '2.0-1', ['libseat.so=2-64'])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('BREAK stable', result.stdout)
        self.assertIn('BREAK testing: shell depends [libseat.so=1-64]', result.stdout)
        self.assertIn('provides libseat.so=2-64', result.stdout)

    def test_package_moving_between_repositories_shadows_stable(self):
        self.write_db('core-testing', [('qt6-base', '6.12.0-1', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('core-testing/qt6-base', result.stdout)

    def test_missing_provider_is_broken_not_silently_skipped(self):
        self.recipe('shell', "depends=('unknown>=1')")
        result = self.run_watch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('no provider in this repository set', result.stdout)

    def test_unversioned_soname_caveat_and_missing_provider(self):
        self.recipe('shell', "depends=('libseat.so')")
        self.write_db('extra', [('seatd', '1.0-1', ['libseat.so=1-64'])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0)
        self.assertIn('installed ABI cannot be proven', result.stdout)
        self.write_db('extra-testing', [('seatd', '2.0-1', [])])
        self.assertEqual(self.run_watch().returncode, 1)

    def test_versioned_provides_and_unversioned_provide_rejection(self):
        self.recipe('shell', "depends=('virtual>=3')")
        self.write_db('extra', [('provider', '100-1', ['virtual=3'])])
        self.assertEqual(self.run_watch().returncode, 0)
        self.write_db('extra-testing', [('provider', '101-1', ['virtual'])])
        self.assertEqual(self.run_watch().returncode, 1)

    def test_single_provider_must_satisfy_both_range_bounds(self):
        self.recipe('shell', "depends=('virtual>=3' 'virtual<5')")
        self.write_db('extra', [('low', '1', ['virtual=2']), ('high', '1', ['virtual=6'])])
        self.assertEqual(self.run_watch().returncode, 1)
        self.write_db('extra-testing', [('middle', '1', ['virtual=4'])])
        result = self.run_watch()
        self.assertIn('BREAK stable', result.stdout)
        self.assertNotIn('BREAK testing', result.stdout)

    def test_local_packages_are_explicitly_outside_arch_scope(self):
        self.recipe('shell', "depends=('local-package>=3' 'qt6-base<6.12')")
        self.recipe('local-package', 'depends=()')
        result = self.run_watch()
        self.assertEqual(result.returncode, 0)
        self.assertIn('LOCAL shell depends local-package>=3', result.stdout)

    def test_arch_and_split_overrides_and_build_requirements(self):
        self.recipe('shell', """
pkgname=(shell shell-tools)
arch=(x86_64 aarch64)
depends=('qt6-base<6.12')
depends_x86_64=('libseat.so=1-64')
depends_aarch64=('wrong-architecture=1')
makedepends=('compiler>=2')
checkdepends=('tester=1')
package_shell() { :; }
package_shell-tools() {
    depends=('tool=2')
    depends_x86_64=()
    touch SPLIT_MUST_NOT_RUN
}
""")
        fences, names = watch.discover(self.recipes, 'x86_64')
        values = {(f.package, f.kind, f.value) for f in fences}
        self.assertEqual(names, {'shell', 'shell-tools'})
        self.assertIn(('shell', 'depends', 'libseat.so=1-64'), values)
        self.assertIn(('shell-tools', 'depends', 'tool=2'), values)
        self.assertNotIn(('shell-tools', 'depends', 'qt6-base<6.12'), values)
        self.assertNotIn(('shell-tools', 'depends', 'libseat.so=1-64'), values)
        self.assertIn(('shell-tools', 'makedepends', 'compiler>=2'), values)
        self.assertFalse(any('wrong-architecture' in f.value for f in fences))
        self.assertFalse(list(self.recipes.rglob('*MUST_NOT_RUN')))

    def test_staged_nested_recipe_is_inactive(self):
        path = self.recipes / 'staged' / 'shell'
        path.mkdir(parents=True)
        (path / 'PKGBUILD').write_text('exit 93\n')
        self.assertEqual(self.run_watch().returncode, 0)

    def test_metadata_failure_and_empty_discovery_fail_closed(self):
        (self.recipes / 'shell/PKGBUILD').write_text('exit 94\n')
        self.assertEqual(self.run_watch().returncode, 2)
        (self.recipes / 'shell/PKGBUILD').unlink()
        self.assertEqual(self.run_watch().returncode, 2)

    def test_arch_version_order_uses_epoch_release_and_missing_release(self):
        for actual, operator, required, expected in (
            ('1:1.0-1', '>', '9.0-9', True),
            ('26.04-3', '=', '26.04', True),
            ('4.5-2', '=', '4.5-1', False),
            ('1.0-10', '>', '1.0-2', True),
            ('6.12rc1-1', '<', '6.12-1', True),
        ):
            with self.subTest(actual=actual, required=required):
                self.assertEqual(watch.accepts(actual, operator, required), expected)

    def test_database_rejects_missing_fields_duplicate_and_traversal(self):
        with self.assertRaises(watch.WatchError):
            watch.database(archive([('same', '1', []), ('same', '2', [])]), 'extra')
        for name, content in [('pkg/desc', b'%NAME%\npkg\n'),
                              ('../desc', b'%NAME%\npkg\n\n%VERSION%\n1\n')]:
            result = io.BytesIO()
            with tarfile.open(fileobj=result, mode='w:gz') as tar:
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                tar.addfile(entry, io.BytesIO(content))
            with self.assertRaises(watch.WatchError):
                watch.database(result.getvalue(), 'extra')

    def test_missing_dependency_tool_fails_with_exit_two(self):
        with patch.object(watch.shutil, 'which', return_value=None), redirect_stderr(io.StringIO()):
            self.assertEqual(watch.main(['--db-dir', str(self.dbs)]), 2)

    def test_downloads_all_four_databases_with_user_agent_and_timeout(self):
        class Response(io.BytesIO):
            def geturl(self):
                return 'https://mirror.example/extra.db'

        with patch.object(watch.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: Response(archive([]))) as opener:
            databases = watch.load_databases(SimpleNamespace(
                db_dir=None, mirror='https://mirror.example', arch='x86_64'))
        self.assertEqual(set(databases), set(watch.REPOS))
        self.assertEqual([call.args[0].full_url for call in opener.call_args_list], [
            f'https://mirror.example/{repo}/os/x86_64/{repo}.db' for repo in watch.REPOS])
        for call in opener.call_args_list:
            self.assertEqual(call.args[0].get_header('User-agent'), 'emaki-arch-watch')
        self.assertTrue(all(call.kwargs['timeout'] == 60 for call in opener.call_args_list))

    def test_download_failure_is_incomplete(self):
        with patch.object(watch.urllib.request, 'urlopen', side_effect=OSError('offline')), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(watch.main(['--packaging-dir', str(self.recipes)]), 2)

    def test_insecure_redirect_is_rejected(self):
        class Response(io.BytesIO):
            def geturl(self):
                return 'http://mirror.example/extra.db'

        with patch.object(watch.urllib.request, 'urlopen',
                          return_value=Response(archive([]))):
            with self.assertRaisesRegex(watch.WatchError, 'outside HTTPS'):
                watch.load_databases(SimpleNamespace(
                    db_dir=None, mirror='https://mirror.example', arch='x86_64'))

    def test_no_eligible_architecture_is_incomplete(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(watch.main(['--packaging-dir', str(self.recipes),
                                         '--db-dir', str(self.dbs), '--arch', 'aarch64']), 2)

    def test_qt_patch_alarm_uses_published_build_for_both_modules_and_repositories(self):
        self.release_fixture()
        self.recipe('quickshell-emaki',
                    "pkgver=0.3.1\ndepends=('qt6-base>=6.11.2' 'qt6-base<6.12' "
                    "'qt6-declarative>=6.11.2' 'qt6-declarative<6.12')")
        self.write_db('emaki', [('quickshell-emaki', '0.3.1-4',
                                ['emaki-quickshell-qt-build=6.11.2'])])
        healthy = [('qt6-base', '6.11.2-9', []), ('qt6-declarative', '6.11.2-1', [])]
        self.write_db('extra', healthy)
        self.assertEqual(self.run_watch().returncode, 0)
        for repo in ('extra', 'extra-testing'):
            for module in ('qt6-base', 'qt6-declarative'):
                with self.subTest(repo=repo, module=module):
                    self.write_db('extra', healthy)
                    self.write_db('extra-testing', healthy)
                    changed = [(name, '6.11.3-1' if name == module else version, provides)
                               for name, version, provides in healthy]
                    self.write_db(repo, changed)
                    result = self.run_watch()
                    self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                    self.assertIn(f'REBUILD: {repo}/{module} 6.11.3-1 differs from Qt 6.11.2 '
                                  'used to build quickshell-emaki 0.3.1-4; rebuild required', result.stdout)
                    self.assertNotIn('BREAK ', result.stdout)

    def test_qt_alarm_detects_older_stable_after_early_testing_rebuild(self):
        self.write_db('emaki', [('quickshell-emaki', '0.3.1-5',
                                ['emaki-quickshell-qt-build=6.11.3'])])
        self.write_db('extra-testing', [('qt6-base', '6.11.3-1', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('REBUILD: extra/qt6-base 6.11.2-3 differs from Qt 6.11.3', result.stdout)
        self.assertNotIn('REBUILD: extra-testing', result.stdout)

    def test_published_quickshell_requires_unique_exact_build_metadata(self):
        for provides in ([], ['emaki-quickshell-qt-build'],
                         ['emaki-quickshell-qt-build=6.11'],
                         ['emaki-quickshell-qt-build=6.11.2-1'],
                         ['emaki-quickshell-qt-build=6.11.2', 'emaki-quickshell-qt-build=6.11.3']):
            with self.subTest(provides=provides):
                self.write_db('emaki', [('quickshell-emaki', '0.3.1-4', provides)])
                result = self.run_watch()
                self.assertEqual(result.returncode, 2)
                self.assertIn('missing or invalid Qt build metadata', result.stderr)
                self.assertNotIn('REBUILD:', result.stdout)

    def test_missing_qt_build_metadata_preserves_other_checks(self):
        self.release_fixture(version='v0.3.2')
        self.recipe('quickshell-emaki', "pkgver=0.3.1")
        self.write_db('emaki', [('quickshell-emaki', '0.3.1-4', [], ['libgone.so=1-64'])])
        self.write_db('extra-testing', [('qt6-base', '6.12.0-1', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertEqual(result.stderr.count('missing or invalid Qt build metadata'), 1)
        self.assertIn('BREAK testing:', result.stdout)
        self.assertIn('libgone.so=1-64', result.stdout)
        self.assertIn('qt6-base<6.12', result.stdout)
        self.assertIn('UPDATE upstream:', result.stdout)
        self.assertIn('0.3.2', result.stdout)
        self.assertNotIn('REBUILD:', result.stdout)

    def test_qt_alarm_does_not_accept_another_packages_build_marker(self):
        self.write_db('emaki', [('shell', '1', ['emaki-quickshell-qt-build=6.10.0'])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('REBUILD:', result.stdout)

    def test_shipped_runtime_sonames_supplement_recipes(self):
        self.recipe('shell', "depends=('libcpptrace.so')")
        self.write_db('emaki', [('shell', '1', [], ['libcpptrace.so=1-64']),
                                ('portal', '1', [], ['libportal.so=1-64'])])
        self.write_db('extra', [('cpptrace', '1', ['libcpptrace.so=1-64']),
                                ('portal-lib', '1', ['libportal.so=1-64'])])
        self.assertEqual(self.run_watch().returncode, 0)
        self.write_db('extra-testing', [('cpptrace', '2', ['libcpptrace.so=2-64']),
                                        ('portal-lib', '2', ['libportal.so=2-64'])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('shell depends [libcpptrace.so=1-64]', result.stdout)
        self.assertIn('portal depends [libportal.so=1-64]', result.stdout)
        self.assertNotIn('BREAK stable', result.stdout)

    def test_absent_published_database_is_explicit_fallback(self):
        self.assertIn('FALLBACK:', self.run_watch().stdout)
        for version, expected in (('6.11.2-3', 0), ('6.12.0-2', 1)):
            self.write_db('extra-testing', [('qt6-base', version, [])])
            databases = watch.load_databases(SimpleNamespace(db_dir=self.dbs))
            body = io.BytesIO(b'pointer missing\n')
            with self.subTest(version=version), patch.object(watch.urllib.request, 'urlopen',
                    side_effect=watch.urllib.error.HTTPError('https://pkgs.emaki.sh', 503,
                                                           'Service Unavailable', {}, body)), \
                    patch.object(watch, 'load_databases', return_value=databases), \
                    redirect_stderr(io.StringIO()) as stderr, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(watch.main(['--packaging-dir', str(self.recipes)]),
                                 expected, stderr.getvalue())
                self.assertIn('recipe fences only', output.getvalue())
                self.assertEqual('BREAK testing:' in output.getvalue(), expected == 1)
                self.assertEqual(stderr.getvalue(), '')
                self.assertTrue(body.closed)

    def test_published_database_http_errors_are_incomplete(self):
        databases = watch.load_databases(SimpleNamespace(db_dir=self.dbs))
        cases = [(503, b'pointer unreadable\n'), (503, b'Service Unavailable'),
                 (503, b''), (503, b'pointer missing'),
                 (503, b'pointer missing\nextra'), (503, b' pointer missing\n'),
                 (404, b'pointer missing\n'), (403, b'Forbidden'),
                 (500, b'pointer missing\n')]
        for status, content in cases:
            body = io.BytesIO(content)
            with self.subTest(status=status, body=content), \
                    patch.object(watch.urllib.request, 'urlopen', side_effect=
                        watch.urllib.error.HTTPError('https://pkgs.emaki.sh', status,
                                                     'unavailable', {}, body)), \
                    patch.object(watch, 'load_databases', return_value=databases), \
                    redirect_stderr(io.StringIO()) as stderr, \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(watch.main(['--packaging-dir', str(self.recipes)]), 2)
                self.assertIn('incomplete', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertNotIn('FALLBACK', stdout.getvalue())
                self.assertNotIn('BREAK', stdout.getvalue())
                self.assertTrue(body.closed)

    def test_published_database_redirect_and_source(self):
        class Response(io.BytesIO):
            def geturl(self):
                return 'https://pkgs.emaki.sh/snapshots/current/emaki.db'
        with patch.object(watch.urllib.request, 'urlopen', return_value=Response(
                archive([('shell', '1', [], ['libcpptrace.so=1-64'])]))) as opener, \
                redirect_stdout(io.StringIO()):
            fences, names = watch.shipped_fences(SimpleNamespace(
                emaki_db=None, db_dir=None, channel='testing', arch='x86_64'))
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'https://pkgs.emaki.sh/testing/x86_64/emaki.db')
        self.assertEqual(request.get_header('User-agent'), 'emaki-arch-watch')
        self.assertEqual(opener.call_args.kwargs['timeout'], 60)
        self.assertEqual([f.value for f in fences], ['libcpptrace.so=1-64'])
        self.assertEqual(names, {'shell'})

    def test_corrupt_published_database_is_not_fallback(self):
        (self.dbs / 'emaki.db').write_bytes(b'broken')
        result = self.run_watch()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('FALLBACK', result.stdout)
        self.assertNotIn('BREAK', result.stdout)

    def test_versioned_replacement_removes_old_soname(self):
        self.recipe('shell', "depends=('libseat.so=1-64')")
        self.write_db('extra', [('seatd', '1.0-1', ['libseat.so=1-64'])])
        for replaces, expected in (('seatd', 1), ('seatd<2', 1), ('seatd>=2', 0)):
            with self.subTest(replaces=replaces):
                self.write_db('extra-testing', [('seatd-new', '2', ['libseat.so=2-64'], [], [replaces])])
                result = self.run_watch()
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                self.assertNotIn('BREAK stable', result.stdout)

    def test_archive_middle_header_and_compression_tail_fail_closed(self):
        good = archive([('qt6-base', '6.11.2-3', []), ('another', '1', []), ('last', '1', [])])
        raw = bytearray(gzip.decompress(good))
        raw[1024] ^= 1  # Header after the first complete package.
        bad_crc = bytearray(good)
        bad_crc[-8] ^= 1
        bad_deflate = good[:10] + b'\xff' + good[11:]
        for blob in (gzip.compress(raw), bytes(bad_crc), bad_deflate, good[:-4],
                     gzip.compress(gzip.decompress(good)[:2048])):
            with self.subTest(blob=blob[:16]):
                (self.dbs / 'extra.db').write_bytes(blob)
                result = self.run_watch()
                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                self.assertNotIn('BREAK', result.stdout)

    def test_zstd_stream_is_checked_to_end(self):
        raw = gzip.decompress(archive([('qt6-base', '6.11.2-3', [])]))
        compressed = subprocess.run(['zstd', '-q', '-c'], input=raw,
                                    capture_output=True, check=True).stdout
        self.assertIn('qt6-base', watch.database(compressed, 'extra'))
        for blob in (compressed[:-2], compressed + b'garbage'):
            (self.dbs / 'extra.db').write_bytes(blob)
            self.assertEqual(self.run_watch().returncode, 2)

    def test_expanded_archive_limit(self):
        raw = gzip.decompress(archive([('qt6-base', '6.11.2-3', [])]))
        compressed = subprocess.run(['zstd', '-q', '-c'], input=raw,
                                    capture_output=True, check=True).stdout
        with patch.object(watch, 'LIMIT', 1024):
            for blob in (gzip.compress(raw), compressed):
                with self.subTest(compression=blob[:4]):
                    (self.dbs / 'extra.db').write_bytes(blob)
                    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                        self.assertEqual(watch.main(['--packaging-dir', str(self.recipes),
                                                     '--db-dir', str(self.dbs)]), 2)

    def test_http_protocol_failures_are_incomplete_without_traceback(self):
        for error in (http.client.IncompleteRead(b'partial'), http.client.LineTooLong('header')):
            with self.subTest(error=error), patch.object(watch.urllib.request, 'urlopen',
                    side_effect=error), redirect_stderr(io.StringIO()) as stderr, \
                    redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(watch.main(['--packaging-dir', str(self.recipes)]), 2)
                self.assertIn('incomplete', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertNotIn('BREAK', stdout.getvalue())

    def test_comparison_failure_does_not_leave_partial_breaks(self):
        fences = [watch.Fence('test', 'shell', 'depends', 'absent=1'),
                  watch.Fence('test', 'shell', 'depends', 'qt6-base=1')]
        with patch.object(watch, 'discover', return_value=(fences, set())), \
                patch.object(watch, 'compare', side_effect=OSError('failed')), \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(watch.main(['--db-dir', str(self.dbs)]), 2)
            self.assertNotIn('BREAK', stdout.getvalue())

    def release_fixture(self, version='v0.3.1', **fields):
        (self.dbs / 'quickshell-release.json').write_text(json.dumps(
            dict(tag_name=version, draft=False, prerelease=False, **fields)))

    def test_new_upstream_quickshell_release_without_arch_update(self):
        self.recipe('quickshell-emaki', 'pkgver=0.3.1')
        self.release_fixture('v0.4.0')
        result = self.run_watch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('UPDATE upstream: New release 0.4.0 [quickshell]', result.stdout)
        self.assertIn('fork quickshell-emaki uses 0.3.1.', result.stdout)
        self.assertNotIn('BREAK', result.stdout)
        for version in ('v0.3.1', 'v0.3.0'):
            self.release_fixture(version)
            self.assertEqual(self.run_watch().returncode, 0)

    def test_new_portal_release_in_both_arch_repositories(self):
        self.recipe('xdg-desktop-portal-gnome-emaki', 'pkgver=50.0')
        for repo in ('extra', 'extra-testing'):
            self.write_db(repo, [('qt6-base', '6.11.2-3', []),
                                 ('xdg-desktop-portal-gnome', '51.0-1', [])])
        result = self.run_watch()
        self.assertEqual(result.returncode, 1, result.stderr)
        for repo in ('extra', 'extra-testing'):
            self.assertIn(f'UPDATE {repo}: New release 51.0-1 [xdg-desktop-portal-gnome]',
                          result.stdout)
        self.assertNotIn('BREAK', result.stdout)

    def test_portal_packaging_epoch_and_release_are_not_upstream_releases(self):
        self.recipe('xdg-desktop-portal-gnome-emaki', 'pkgver=50.0')
        for version in ('50.0-99', '1:50.0-2', '49.0-1'):
            self.write_db('extra-testing', [('xdg-desktop-portal-gnome', version, [])])
            result = self.run_watch()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('UPDATE', result.stdout)

    def test_missing_release_sources_are_incomplete(self):
        for name in ('quickshell-emaki', 'xdg-desktop-portal-gnome-emaki'):
            self.recipe(name, 'pkgver=1.0')
            result = self.run_watch()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('UPDATE', result.stdout)
            (self.recipes / name / 'PKGBUILD').unlink()

    def test_invalid_release_metadata_never_reports_a_release(self):
        self.recipe('quickshell-emaki', 'pkgver=0.3.1')
        for release in (b'broken', b'[]', b'{}',
                        json.dumps(dict(tag_name='v0.4.0', draft=True, prerelease=False)).encode(),
                        json.dumps(dict(tag_name='v0.4.0', draft=False, prerelease=True)).encode(),
                        json.dumps(dict(tag_name='v0.4.0\nBAD', draft=False,
                                        prerelease=False)).encode(),
                        b' ' * (watch.RELEASE_LIMIT + 1)):
            (self.dbs / 'quickshell-release.json').write_bytes(release)
            result = self.run_watch()
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('UPDATE', result.stdout)
            self.assertNotIn('BREAK', result.stdout)

    def test_release_fetch_failure_preserves_completed_breaks(self):
        self.recipe('quickshell-emaki', 'pkgver=0.3.1')
        self.write_db('extra-testing', [('qt6-base', '6.12.0-2', [])])
        output = io.StringIO()
        with patch.object(watch, 'quickshell_release',
                          side_effect=watch.urllib.error.URLError('GitHub unavailable')), \
                redirect_stdout(output), redirect_stderr(output):
            status = watch.main(['--packaging-dir', str(self.recipes),
                                 '--db-dir', str(self.dbs)])
        self.assertEqual(status, 2)
        text = output.getvalue()
        self.assertIn('BREAK testing: shell depends', text)
        self.assertNotIn('BREAK stable', text)
        self.assertIn('ERROR: arch-watch incomplete:', text)
        self.assertIn('GitHub unavailable', text)
        self.assertLess(text.index('BREAK testing:'), text.index('ERROR:'))
        self.assertNotIn('UPDATE upstream:', text)

    def test_release_download_uses_upstream_https_and_client_header(self):
        class Response(io.BytesIO):
            def geturl(self):
                return watch.QUICKSHELL_RELEASE
        with patch.object(watch.urllib.request, 'urlopen', return_value=Response(
                b'{"tag_name":"v0.4.0","draft":false,"prerelease":false}')) as opener:
            self.assertEqual(watch.quickshell_release(SimpleNamespace(db_dir=None)), '0.4.0')
        self.assertEqual(opener.call_args.args[0].full_url, watch.QUICKSHELL_RELEASE)
        self.assertEqual(opener.call_args.args[0].get_header('User-agent'), 'emaki-arch-watch')
        self.assertEqual(opener.call_args.kwargs['timeout'], 60)
        with patch.object(Response, 'geturl', return_value='http://example.com/release'), \
                patch.object(watch.urllib.request, 'urlopen', return_value=Response(b'{}')):
            with self.assertRaisesRegex(watch.WatchError, 'outside HTTPS'):
                watch.quickshell_release(SimpleNamespace(db_dir=None))

    def test_active_fork_versions_come_from_recipes(self):
        releases = []
        watch.discover(ROOT / 'packaging', 'x86_64', releases)
        self.assertEqual(sorted(releases), [
            ('quickshell-emaki', 'quickshell', '0.3.1'),
            ('xdg-desktop-portal-gnome-emaki', 'xdg-desktop-portal-gnome', '50.0')])

    def test_actual_recipes_discover_complete_fence_set(self):
        fences, names = watch.discover(ROOT / 'packaging', 'x86_64')
        active = (ROOT / 'packaging/quickshell-emaki/PKGBUILD').read_text()
        activated = '0003-' in active
        lower, upper = ('6.12.0', '6.13') if activated else ('6.11.2', '6.12')
        # The marker pins whatever release the active recipe carries.
        release = re.search(r'^pkgrel=(\d+)$', active, re.M).group(1)
        expected = {
            'emaki-config': {'fastfetch>=2.68.1', 'niri-emaki>=26.04-12', 'quickshell-emaki>=0.3.1-4', 'kwallet>=6.30'},
            'emaki-desktop': {'fastfetch>=2.68.1', 'niri-emaki>=26.04-12'},
            'emaki-installer': {'archinstall=4.5-1'},
            'emaki': {'emaki-config=0.4.2-1', 'emaki-desktop=0.4.2-1', 'emaki-keyring>=0.4.2-1',
                      'emaki-mirrorlist>=0.4.2-1', 'niri-emaki=26.04-12', f'quickshell-emaki=0.3.1-{release}'},
            'niri-emaki': {'libdisplay-info.so=3-64', 'libinput.so=10-64', 'libpipewire-0.3.so=0-64',
                           'libseat.so=1-64', 'libxkbcommon.so=0-64', 'niri>=26.04'},
            'quickshell-emaki': {'libEGL.so', 'libOpenGL.so', 'libgcc_s.so',
                                'libjemalloc.so', 'libpam.so', 'libpipewire-0.3.so',
                                'libstdc++.so', 'libwayland-client.so'} | {
                f'{package}{bound}' for package in ('qt6-base', 'qt6-declarative')
                for bound in (f'>={lower}', f'<{upper}')},
            'xdg-desktop-portal-gnome-emaki': {'xdg-desktop-portal-gtk>=1.10.0-2'},
        }
        expected_fences = {(package, 'depends', value) for package, values in expected.items()
                           for value in values}
        if activated:
            # The Qt 6.12 build also bounds SVG at run time and ShaderTools at build time.
            expected_fences |= {('quickshell-emaki', kind, package + bound)
                                for kind, package in (('depends', 'qt6-svg'),
                                                      ('makedepends', 'qt6-shadertools'))
                                for bound in (f'>={lower}', f'<{upper}')}
        self.assertEqual({(f.package, f.kind, f.value) for f in fences}, expected_fences)
        self.assertEqual(len(fences), 36 if activated else 32)
        self.assertTrue(set(expected) <= names)


class ScheduledWatch(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader(
            'arch_watch_notify', str(ROOT / 'packaging/arch-watch-notify'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.notify = importlib.util.module_from_spec(spec)
        loader.exec_module(self.notify)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        env = patch.dict(self.notify.os.environ, {'STATE_DIRECTORY': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.now = 100000

    def state(self):
        return json.loads((self.directory / 'alarms.json').read_text())

    def status(self):
        text = (self.directory / 'status').read_text()
        self.assertEqual(len(text.splitlines()), 1)
        return json.loads(text)

    def check(self, status=0, output='', delivery_error=None, argv=None):
        alarms = self.notify.attention_alarms(output)
        with patch.object(self.notify, 'run_watch', return_value=(status, output, alarms)) as watch_run, \
                patch.object(self.notify.time, 'time', return_value=self.now), \
                patch.object(self.notify.subprocess, 'run', side_effect=delivery_error) as send, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            returned = self.notify.main(argv or [])
        if argv:
            watch_run.assert_not_called()
        else:
            self.assertIn(output, (self.directory / 'latest.log').read_text())
        return returned, [call.args[0] for call in send.call_args_list]

    def test_clean_watch_is_quiet_and_records_last_complete(self):
        returned, calls = self.check(output='All fences hold.')
        self.assertEqual((returned, calls), (0, []))
        self.assertEqual(self.status(), dict(checked_at=self.now, last_complete=self.now,
                         result='current', incomplete_runs=0, notification_pending=False))
        self.assertEqual((self.directory / 'last-complete').read_text(), f'{self.now}\n')

    def test_attention_changes_notify_once_and_prioritize_stable(self):
        output = ('BREAK testing: future Qt fence\nBREAK stable: Qt moved\n'
                  'REBUILD: extra/qt6-base 6.11.3\nREBUILD: extra-testing/qt6-base 6.12.0\n'
                  'UPDATE upstream: new release\n')
        returned, calls = self.check(1, output)
        self.assertEqual(returned, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len({args[-2] for args in calls}), 3)
        self.assertIn('stable updates blocked', calls[2][-2])
        self.assertIn('stable Qt rebuild required', calls[1][-2])
        self.assertIn('shell crashes', calls[1][-1])
        for args in calls[1:]:
            self.assertIn('--urgency=critical', args)
        self.assertIn('--urgency=normal', calls[0])
        for text in ('BREAK testing:', 'REBUILD: extra-testing/', 'UPDATE upstream:'):
            self.assertIn(text, calls[0][-1])
        for args in calls:
            self.assertGreater(int(next(a.split('=')[1] for a in args
                                       if a.startswith('--expire-time='))), 0)
        self.assertEqual(self.check(1, output)[1], [])
        self.assertEqual(self.check(1, '\n'.join(reversed(output.splitlines())))[1], [])
        changed = output.replace('Qt moved', 'Qt moved again')
        self.assertEqual(len(self.check(1, changed)[1]), 1)
        self.check(0)
        self.assertEqual(len(self.check(1, changed)[1]), 3)

    def test_incomplete_threshold_and_recovery_reset(self):
        self.check(0)
        last_complete = self.now
        for count in range(1, 5):
            self.now += 60
            returned, calls = self.check(2, 'ERROR: network unavailable')
            self.assertEqual(returned, 1)
            self.assertEqual(len(calls), int(count == 3))
            self.assertEqual(self.status()['incomplete_runs'], count)
            self.assertEqual(self.status()['last_complete'], last_complete)
        self.check(0)
        self.assertEqual(self.status()['incomplete_runs'], 0)
        self.assertEqual(self.check(2)[1], [])
        self.assertEqual(self.check(2)[1], [])
        self.assertEqual(len(self.check(2)[1]), 1)

    def test_incomplete_alert_when_last_complete_is_six_hours_old(self):
        self.check(0)
        self.now += 6 * 60 * 60
        self.assertEqual(len(self.check(2)[1]), 1)
        self.assertEqual(self.status()['incomplete_runs'], 1)

    def test_first_check_age_counts_when_no_complete_check_exists(self):
        self.assertEqual(self.check(2)[1], [])
        self.now += 6 * 60 * 60
        self.assertEqual(len(self.check(2)[1]), 1)
        self.assertIsNone(self.status()['last_complete'])

    def test_stopped_notification_server_keeps_pending_until_delivery(self):
        for error in (FileNotFoundError('notify-send missing'),
                      subprocess.CalledProcessError(1, ['notify-send'])):
            with self.subTest(error=error):
                self.check(0)
                returned, calls = self.check(1, 'BREAK stable: Qt moved', error)
                self.assertEqual((returned, len(calls)), (1, 1))
                self.assertTrue(self.status()['notification_pending'])
                self.assertFalse(self.state()['delivered'])
                self.assertIn('stable', self.state()['pending'])
                self.assertEqual(self.check(delivery_error=error, argv=['--deliver'])[0], 1)
                self.assertFalse(self.state()['delivered'])
                self.assertEqual(self.check(argv=['--deliver'])[0], 0)
                self.assertFalse(self.status()['notification_pending'])
                self.assertIn('stable', self.state()['delivered'])
                self.assertEqual(self.check(1, 'BREAK stable: Qt moved')[1], [])

    def test_partial_alarm_alerts_immediately_and_incomplete_does_not_clear_it(self):
        self.assertEqual(len(self.check(2, 'BREAK stable: Qt moved')[1]), 1)
        self.assertEqual(self.check(2)[1], [])
        self.assertNotIn('stable', self.state()['delivered'])
        self.assertIn('BREAK stable: Qt moved', self.state()['delivered_lines']['stable'])
        self.assertEqual(len(self.check(2, 'BREAK stable: Qt moved')[1]), 1)
        self.assertIn('incomplete', self.state()['delivered'])

    def test_unexpected_errors_exit_outside_unit_success_statuses(self):
        for mode in ([], ['--failure'], ['--deliver']):
            with self.subTest(mode=mode):
                (self.directory / 'alarms.json').write_text('{"first_check": 1}')
                result = subprocess.run([sys.executable, str(ROOT / 'packaging/arch-watch-notify'),
                                         *mode], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertIn('incomplete alarm state', result.stderr)
        (self.directory / 'alarms.json').unlink()
        for error in (PermissionError('state denied'), OSError(28, 'No space left on device')):
            with self.subTest(error=error), patch.object(self.notify, 'atomic_write', side_effect=error), \
                    patch.object(self.notify, 'run_watch', return_value=(0, '', {})), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(self.notify.main([]), 2)
        blocked = self.directory / 'not-a-directory'
        blocked.touch()
        with patch.dict(self.notify.os.environ, {'STATE_DIRECTORY': str(blocked)}):
            result = subprocess.run([sys.executable, str(ROOT / 'packaging/arch-watch-notify')],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        for unit in ('emaki-arch-watch.service', 'emaki-arch-watch-failure.service'):
            text = (ROOT / 'packaging/systemd' / unit).read_text()
            self.assertIn('SuccessExitStatus=1\n', text)
            self.assertNotIn('SuccessExitStatus=2', text)

    def test_stable_fence_suppresses_duplicate_testing_fence(self):
        output = ('BREAK testing: shell depends [qt<6.12] (recipe): extra-testing/qt 6.13\n'
                  'BREAK stable: shell depends [qt<6.12] (recipe): extra/qt 6.12\n')
        calls = self.check(1, output)[1]
        self.assertEqual(len(calls), 1)
        self.assertIn('stable updates blocked', calls[0][-2])
        self.assertNotIn('BREAK testing:', calls[0][-1])

    def test_partial_changes_do_not_replace_complete_signature_or_pending_body(self):
        full = 'BREAK testing: future fence\nUPDATE upstream: new release\n'
        partial = 'BREAK testing: future fence\n'
        for error in (None, FileNotFoundError('notification server unavailable')):
            with self.subTest(error=error):
                self.check(0)
                self.check(1, full, error)
                before = self.state()
                calls = self.check(2, partial, error)[1]
                self.assertEqual(self.state()['delivered'], before['delivered'])
                self.assertEqual(self.state()['pending'], before['pending'])
                self.assertEqual(len(calls), int(error is not None))
                if calls:
                    self.assertIn('UPDATE upstream:', calls[0][-1])
                self.assertEqual(len(self.check(1, full)[1]), int(error is not None))

    def test_partial_critical_evidence_preserves_complete_signature(self):
        full = 'BREAK stable: first fence\nBREAK stable: second fence\n'
        self.check(1, full)
        signature = self.state()['delivered']['stable']
        self.assertEqual(self.check(2, 'BREAK stable: first fence\n')[1], [])
        self.assertEqual(len(self.check(2, 'BREAK stable: new fence\n')[1]), 1)
        self.assertEqual(self.state()['delivered']['stable'], signature)
        self.assertEqual(self.check(1, full)[1], [])

    def test_partial_critical_evidence_retains_pending_complete_signature(self):
        full = 'BREAK stable: first fence\nBREAK stable: second fence\n'
        error = FileNotFoundError('notification server unavailable')
        self.check(1, full, error)
        signature = self.state()['pending']['stable']['signature']
        self.check(2, 'BREAK stable: new fence\n', error)
        pending = self.state()['pending']['stable']
        self.assertEqual(pending['signature'], signature)
        self.assertIn('second fence', pending['body'])
        self.assertIn('new fence', pending['body'])
        self.check(argv=['--deliver'])
        self.assertEqual(self.state()['delivered']['stable'], signature)
        self.assertEqual(self.check(1, full)[1], [])

    def test_health_pending_survives_recovery_and_next_incident_notifies(self):
        error = FileNotFoundError('notification server unavailable')
        for key in ('service', 'incomplete'):
            with self.subTest(key=key):
                self.check(0)
                if key == 'service':
                    self.check(argv=['--failure'], delivery_error=error)
                else:
                    for _ in range(3):
                        self.check(2, delivery_error=error)
                self.assertIn(key, self.state()['pending'])
                self.check(0, delivery_error=error)
                self.assertIn(key, self.state()['pending'])
                calls = self.check(argv=['--deliver'])[1]
                self.assertEqual(len(calls), 1)
                self.assertLess(calls[0][-1].index('failed.' if key == 'service' else 'Repeated'),
                                calls[0][-1].index('Read'))
                self.assertNotIn(key, self.state()['delivered'])
                self.assertFalse(self.state()['pending'])
                if key == 'service':
                    self.assertEqual(len(self.check(argv=['--failure'])[1]), 1)
                else:
                    self.check(2)
                    self.check(2)
                    self.assertEqual(len(self.check(2)[1]), 1)

    def test_delivery_retry_sends_critical_last_after_state_reload(self):
        output = ('BREAK stable: moved\nREBUILD: extra/qt6-base 6.12.0\n'
                  'UPDATE upstream: new release\n')
        self.check(1, output, FileNotFoundError('notification server unavailable'))
        self.check(argv=['--failure'], delivery_error=FileNotFoundError('unavailable'))
        calls = self.check(argv=['--deliver'])[1]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all('--urgency=normal' in call for call in calls[:2]))
        self.assertTrue(all('--urgency=critical' in call for call in calls[2:]))
        self.assertIn('stable updates blocked', calls[-1][-2])

    def test_service_failure_is_persisted_and_deduplicated(self):
        self.check(0)
        completed = self.now
        self.now += 60
        returned, calls = self.check(argv=['--failure'])
        self.assertEqual(returned, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn('service failed', calls[0][-2])
        self.assertEqual(self.status()['result'], 'failed')
        self.assertEqual(self.status()['last_complete'], completed)
        self.assertEqual(self.check(argv=['--failure'])[1], [])
        self.check(0)
        self.assertEqual(len(self.check(argv=['--failure'])[1]), 1)

    def attempts(self, responses):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        def run(args, **kwargs):
            response = responses.pop(0)
            if isinstance(response, Exception):
                elapsed[0] += kwargs['timeout']
                raise response
            return response
        with patch.object(self.notify.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                patch.object(self.notify.time, 'sleep', side_effect=sleep) as backoff, \
                patch.object(self.notify.subprocess, 'run', side_effect=run) as process:
            result = self.notify.run_watch(ROOT)
        self.assertLessEqual(elapsed[0], 600)
        for call in process.call_args_list:
            self.assertIn('--scheduled', call.args[0])
            self.assertLessEqual(call.kwargs['timeout'], 180)
            self.assertEqual(call.kwargs['stderr'], subprocess.STDOUT)
        return result, process, backoff

    def test_transient_failure_recovers_on_third_attempt(self):
        result, process, backoff = self.attempts([
            subprocess.CompletedProcess([], 2, 'ERROR: unavailable\n'),
            subprocess.CompletedProcess([], 2, 'ERROR: unavailable\n'),
            subprocess.CompletedProcess([], 0, 'All fences hold.\n')])
        self.assertEqual(result[0], 0)
        self.assertEqual(result[2], {})
        self.assertEqual(process.call_count, 3)
        self.assertEqual([c.args[0] for c in backoff.call_args_list], [10, 30])
        self.assertIn('Attempt 3 exit=0', result[1])

    def test_three_timeouts_preserve_partial_alarm_within_budget(self):
        result, process, backoff = self.attempts([
            subprocess.TimeoutExpired(['arch-watch'], 180,
                                      output=b'BREAK stable: Qt moved\n') for _ in range(3)])
        self.assertEqual(result[0], 2)
        self.assertEqual(process.call_count, 3)
        self.assertEqual(backoff.call_count, 2)
        self.assertIn('stable', result[2])
        self.assertIn('timed out', result[1])


if __name__ == '__main__':
    unittest.main()
