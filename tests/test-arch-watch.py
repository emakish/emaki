#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Arch database and real PKGBUILD metadata tests for arch-watch."""

import importlib.machinery
import importlib.util
import io
import gzip
import http.client
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr, redirect_stdout
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
        self.write_db('core', [])
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

    def test_actual_recipes_discover_complete_fence_set(self):
        fences, names = watch.discover(ROOT / 'packaging', 'x86_64')
        active = (ROOT / 'packaging/quickshell-emaki/PKGBUILD').read_text()
        activated = '0003-' in active
        lower, upper, release = ('6.12', '6.13', '3') if activated else ('6.11', '6.12', '2')
        expected = {
            'emaki-config': {'fastfetch>=2.68.1', 'niri-emaki>=26.04-6', 'quickshell-emaki>=0.3.1-2'},
            'emaki-desktop': {'fastfetch>=2.68.1', 'niri-emaki>=26.04-6'},
            'emaki-installer': {'archinstall=4.5-1'},
            'emaki': {'emaki-config=0.2.0-1', 'emaki-desktop=0.2.0-1', 'emaki-keyring=0.2.0-1',
                      'emaki-mirrorlist=0.2.0-1', 'niri-emaki=26.04-8', f'quickshell-emaki=0.3.1-{release}'},
            'niri-emaki': {'libdisplay-info.so=3-64', 'libinput.so=10-64', 'libpipewire-0.3.so=0-64',
                           'libseat.so=1-64', 'libxkbcommon.so=0-64', 'niri=26.04'},
            'quickshell-emaki': {'libEGL.so', 'libOpenGL.so', 'libcpptrace.so', 'libgcc_s.so',
                                'libjemalloc.so', 'libpam.so', 'libpipewire-0.3.so',
                                'libstdc++.so', 'libwayland-client.so'} | {
                f'{package}{bound}' for package in ('qt6-base', 'qt6-declarative', 'qt6-wayland')
                for bound in (f'>={lower}', f'<{upper}')},
            'xdg-desktop-portal-gnome-emaki': {'xdg-desktop-portal-gtk>=1.10.0-2'},
        }
        self.assertEqual({(f.package, f.kind, f.value) for f in fences},
                         {(package, 'depends', value) for package, values in expected.items()
                          for value in values})
        self.assertEqual(len(fences), 34)
        self.assertTrue(set(expected) <= names)


if __name__ == '__main__':
    unittest.main()
