#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline selective-release checks against published per-package source commits."""
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
import release_inputs as release

spec = importlib.util.spec_from_file_location('release_test_publish', ROOT / 'tests/test-publish.py')
publish_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish_tests)
exceptions = sys.modules['release_exceptions']
setUpModule, tearDownModule = publish_tests.setUpModule, publish_tests.tearDownModule


class ReleaseInputs(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='release-inputs-', dir=evidence)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.write('scripts/make-public.sh', (ROOT / 'scripts/make-public.sh').read_text())
        for name in ('emaki-config', 'emaki-installer'):
            self.write(f'packaging/{name}/PKGBUILD', 'pkgver=0.2.0\npkgrel=1\n')
        self.write('installer/emaki_installer/constants.py', 'ALONGSIDE = False\n')
        self.write('installer/main.py', 'initial\n')
        self.write('niri/default.kdl', 'initial\n')
        self.write('README.md', 'private\n')
        self.old = self.commit()
        self.metadata = {'format': 1, 'packages': {
            name: {'commit': self.old, 'version': '0.2.0-1'}
            for name in ('emaki-config', 'emaki-installer')}}
        tools = self.base / 'bin'
        tools.mkdir()
        programs = {
            'makepkg': "from pathlib import Path\nfor line in Path('PKGBUILD').read_text().splitlines():\n print(line.replace('=', ' = ', 1))\n",
            'vercmp': "import sys\na,b=[tuple(map(int,v.replace('-', '.').split('.'))) for v in sys.argv[1:]]\nprint((a>b)-(a<b))\n",
        }
        for name, body in programs.items():
            path = tools / name
            path.write_text('#!/usr/bin/env python3\n' + body)
            path.chmod(0o755)
        env = patch.dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'])
        env.start()
        self.addCleanup(env.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture')
        return self.git('rev-parse', 'HEAD')

    def bump(self, name):
        self.write(f'packaging/{name}/PKGBUILD', 'pkgver=0.2.0\npkgrel=2\n')

    def acceptance(self, **overrides):
        cells = dict(result='PASS', machine='Windows test laptop', day='2026-10-04',
                     iso='0123456789abcdef' * 4, evidence='evidence/windows-acceptance.md')
        cells.update(overrides)
        return '| H-W-ALONGSIDE | ' + ' | '.join(cells.values()) + ' |\n'

    def test_disabled_default_needs_no_hardware_record(self):
        release.check_alongside(self.repo)

    def test_enabled_switch_requires_committed_hardware_acceptance(self):
        self.write('installer/emaki_installer/constants.py', 'ALONGSIDE = True\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'real-Windows acceptance'):
            release.check_alongside(self.repo)
        self.write('docs/hardware-acceptance.md', self.acceptance())
        with self.assertRaisesRegex(ValueError, 'real-Windows acceptance'):
            release.check_alongside(self.repo)
        self.commit()
        release.check_alongside(self.repo)

    def test_incomplete_failed_or_synthetic_acceptance_is_refused(self):
        self.write('installer/emaki_installer/constants.py', 'ALONGSIDE = True\n')
        for overrides in ({'result': 'PENDING'}, {'result': 'FAIL'}, {'machine': '—'},
                          {'machine': 'synthetic VM'}, {'day': 'not a date'},
                          {'day': '2999-01-01'}, {'iso': 'abcdef123456'},
                          {'iso': '0' * 64}, {'evidence': 'pending'}):
            with self.subTest(overrides=overrides):
                self.write('docs/hardware-acceptance.md', self.acceptance(**overrides))
                self.commit()
                with self.assertRaisesRegex(ValueError, 'real-Windows acceptance'):
                    release.check_alongside(self.repo)
        self.write('docs/hardware-acceptance.md', self.acceptance() * 2)
        self.commit()
        with self.assertRaisesRegex(ValueError, 'real-Windows acceptance'):
            release.check_alongside(self.repo)

    def test_unreadable_switch_fails_closed_without_execution(self):
        for source in ('', 'ALONGSIDE = 0', 'ALONGSIDE = bool(0)',
                       'ALONGSIDE = False\nALONGSIDE = True',
                       'ALONGSIDE = False\nif True:\n ALONGSIDE = True', 'ALONGSIDE ='):
            with self.subTest(source=source):
                self.write('installer/emaki_installer/constants.py', source)
                self.commit()
                with self.assertRaisesRegex(ValueError, 'alongside release switch'):
                    release.check_alongside(self.repo)

    def test_all_builds_check_alongside_before_dry_run(self):
        for name in ('build.sh', 'release_inputs.py', 'source_archive.py'):
            shutil.copy2(ROOT / 'packaging' / name, self.repo / 'packaging' / name)
        shutil.copytree(ROOT / 'packaging/mirror', self.repo / 'packaging/mirror',
                        ignore=shutil.ignore_patterns('__pycache__', 'exceptions'))
        self.write('installer/emaki_installer/constants.py', 'ALONGSIDE = True\n')
        self.commit()
        metadata = self.base / 'SOURCES.json'
        metadata.write_text(json.dumps(self.metadata))
        for args in ([], ['--only', 'emaki-installer'],
                     ['--published-sources', str(metadata), '--only', 'emaki-config']):
            with self.subTest(args=args):
                result = subprocess.run(['bash', str(self.repo / 'packaging/build.sh'),
                                         '--dry-run', *args], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('real-Windows acceptance', result.stderr)
                self.assertNotIn('Would build from', result.stdout)

    def test_optional_turn_setup_changes_select_its_package(self):
        previous = self.old
        for path in ('scripts/emaki-sudo-turn', 'sudo/turn-reporting.md',
                     'sudo/integrations/example.json'):
            with self.subTest(path=path):
                self.write(path, 'updated setup\n')
                current = self.commit()
                self.assertTrue(release.changed_inputs(self.repo, 'emaki-sudo-key', previous, current))
                previous = current
        self.write('sudo/broker.cpp', 'changed service\n')
        current = self.commit()
        self.assertFalse(release.changed_inputs(self.repo, 'emaki-sudo-key', previous, current))

    def test_code_change_without_bump_refused(self):
        self.write('niri/default.kdl', 'changed\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'emaki-config: recipe version.*increase pkgrel'):
            release.check(self.repo, self.metadata, {'emaki-config'})

    def test_omitted_changed_package_refused_even_after_bump(self):
        self.write('installer/main.py', 'changed\n')
        self.bump('emaki-config')
        self.bump('emaki-installer')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'add --only emaki-installer'):
            release.check(self.repo, self.metadata, {'emaki-config'})

    def test_bumped_selected_packages_pass(self):
        self.write('installer/main.py', 'changed\n')
        for name in self.metadata['packages']:
            self.bump(name)
        self.commit()
        release.check(self.repo, self.metadata, set(self.metadata['packages']))

    def test_excluded_notes_do_not_change_config_or_installer(self):
        self.write('README.md', 'changed private text\n')
        self.write('nested/STATUS.md', 'private status\n')
        head = self.commit()
        for name in self.metadata['packages']:
            self.assertFalse(release.changed_inputs(self.repo, name, self.old, head))

    def test_added_and_deleted_archived_paths_count(self):
        self.write('installer/new.py', 'new\n')
        (self.repo / 'installer/main.py').unlink()
        head = self.commit()
        for name in self.metadata['packages']:
            self.assertTrue(release.changed_inputs(self.repo, name, self.old, head))

    def test_each_package_uses_its_own_published_commit(self):
        self.write('installer/main.py', 'already published\n')
        installer_commit = self.commit()
        self.metadata['packages']['emaki-installer']['commit'] = installer_commit
        self.metadata['commit'] = self.old
        self.bump('emaki-config')
        self.commit()
        release.check(self.repo, self.metadata, {'emaki-config'})

    def test_shared_nvidia_payload_changes_select_every_consumer(self):
        consumers = {
            'installer/emaki_installer/graphics.py': ('emaki-nvidia', 'emaki-installer'),
            'LICENSE': ('emaki-nvidia', 'emaki-installer'),
        }
        previous = self.old
        for path, packages in consumers.items():
            for contents in ('initial\n', 'changed\n', None):
                if contents is None:
                    (self.repo / path).unlink()
                else:
                    self.write(path, contents)
                current = self.commit()
                for name in packages:
                    with self.subTest(path=path, contents=contents, package=name):
                        self.assertTrue(release.changed_inputs(self.repo, name, previous, current))
                previous = current

    def test_nvidia_does_not_rebuild_for_unshipped_installer_or_other_recipes(self):
        self.write('installer/main.py', 'changed\n')
        self.bump('emaki-installer')
        current = self.commit()
        self.assertFalse(release.changed_inputs(self.repo, 'emaki-nvidia', self.old, current))

    def test_current_exclusions_cannot_hide_old_archive_inputs(self):
        self.write('niri/default.kdl', 'changed\n')
        head = self.commit()
        old_policy = release.public_policy(self.repo, self.old)
        current_policy = (old_policy[0] + ['niri'], old_policy[1], old_policy[2])
        with patch.object(release, 'public_policy', side_effect=[old_policy, current_policy]):
            self.assertTrue(release.changed_inputs(self.repo, 'emaki-config', self.old, head))

    def test_shared_build_commit_supported(self):
        self.metadata['commit'] = self.old
        for record in self.metadata['packages'].values():
            del record['commit']
        self.bump('emaki-config')
        self.commit()
        release.check(self.repo, self.metadata, {'emaki-config'})

    def test_invalid_or_absent_commit_refused(self):
        for commit in (None, 'not-a-commit', '0' * 40):
            self.metadata['packages']['emaki-config']['commit'] = commit
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                release.check(self.repo, self.metadata, {'emaki-config'})

    def test_build_release_mode_checks_before_dry_run(self):
        for name in ('build.sh', 'release_inputs.py', 'source_archive.py'):
            shutil.copy2(ROOT / 'packaging' / name, self.repo / 'packaging' / name)
        self.bump('emaki-config')
        self.commit()
        metadata = self.base / 'SOURCES.json'
        metadata.write_text(json.dumps(self.metadata))
        command = ['bash', str(self.repo / 'packaging/build.sh'), '--dry-run',
                   '--published-sources', str(metadata)]
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('requires explicit --only', refused.stderr)
        accepted = subprocess.run(command + ['--only', 'emaki-config'], capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn('Release input check passed.', accepted.stdout)
        self.assertIn('Would build from', accepted.stdout)
        self.write('installer/main.py', 'changed\n')
        self.commit()
        refused = subprocess.run(command + ['--only', 'emaki-config'], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('add --only emaki-installer', refused.stderr)
        self.assertNotIn('Would build from', refused.stdout)


class AlongsideExceptions(unittest.TestCase):
    """The build uses committed approval and trust data for the exact committed switch."""

    gate = 'build.alongside-acceptance'
    artifact = 'installer/emaki_installer/constants.py'
    git, write, commit = ReleaseInputs.git, ReleaseInputs.write, ReleaseInputs.commit

    @classmethod
    def setUpClass(cls):
        if not shutil.which('gpg') or not shutil.which('gpgconf'):
            raise unittest.SkipTest('gpg and gpgconf are required')
        cls.storage = tempfile.TemporaryDirectory(prefix='alongside-key-')
        cls.addClassCleanup(cls.storage.cleanup)
        cls.keys = publish_tests.Keys(cls.storage.name)
        cls.addClassCleanup(cls.keys.close)

    def setUp(self):
        ReleaseInputs.setUp(self)
        self.write(self.artifact, 'ALONGSIDE = True\n')
        keyring = self.repo / 'packaging/emaki-keyring'
        keyring.mkdir()
        shutil.copy2(self.keys.keyring, keyring / 'emaki.gpg')
        shutil.copy2(self.keys.trusted, keyring / 'emaki-trusted')
        (keyring / 'emaki-revoked').write_text('')
        self.directory = self.repo / 'packaging/mirror/exceptions'
        self.directory.mkdir(parents=True)
        self.commit()

    def record(self, *, gate=None, digest=None, expired=False, signed=True):
        now = datetime.datetime.now(datetime.timezone.utc)
        if expired:
            now -= datetime.timedelta(days=2)
        digest = digest or hashlib.sha256((self.repo / self.artifact).read_bytes()).hexdigest()
        record = exceptions.new_record(gate or self.gate, {self.artifact: digest},
                                       'Owner accepts the pending hardware check', self.keys.fpr, 1, now)
        path, signature, _ = exceptions.paths(self.directory, record['id'])
        path.write_bytes(exceptions.encode(record))
        if signed:
            self.keys.sign(path)
        return record, path

    def refused(self):
        with self.assertRaisesRegex(ValueError, self.gate) as caught:
            release.check_alongside(self.repo)
        return str(caught.exception)

    def test_valid_signed_exception_reports_id_and_reason(self):
        record, _ = self.record()
        self.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            release.check_alongside(self.repo)
        self.assertIn(f'PASSED BY EXCEPTION {record["id"]}: {record["reason"]}', output.getvalue())

    def test_invalid_exceptions_fail_closed(self):
        cases = {
            'expired': {'expired': True},
            'wrong hash': {'digest': '0' * 64},
            'unsigned': {'signed': False},
            'tampered': {},
            'wrong gate': {'gate': 'publish.tree-clean'},
            'revoked record': {},
            'revoked key': {},
        }
        for case, kwargs in cases.items():
            with self.subTest(case=case):
                for path in self.directory.iterdir():
                    path.unlink()
                self.write('packaging/emaki-keyring/emaki-revoked', '')
                record, path = self.record(**kwargs)
                if case == 'tampered':
                    record['reason'] = 'Changed after signing'
                    path.write_bytes(exceptions.encode(record))
                elif case == 'revoked record':
                    exceptions.paths(self.directory, record['id'])[2].write_text('Revoked\n')
                elif case == 'revoked key':
                    self.write('packaging/emaki-keyring/emaki-revoked', self.keys.fpr + '\n')
                self.commit()
                self.refused()

    def test_uncommitted_exception_does_not_authorize(self):
        self.record()
        self.refused()

    def test_scope_hashes_raw_committed_bytes(self):
        (self.repo / self.artifact).write_bytes(b'ALONGSIDE = True\r\n')
        digest = hashlib.sha256((self.repo / self.artifact).read_bytes()).hexdigest()
        self.commit()
        refusal = self.refused()
        self.assertIn(self.artifact, refusal)
        self.assertIn(digest, refusal)
        self.record()
        self.commit()
        self.write(self.artifact, 'ALONGSIDE = False\n')
        release.check_alongside(self.repo)
        self.write(self.artifact, 'ALONGSIDE = True\n# Changed constants\n')
        self.commit()
        self.refused()

    def test_uncommitted_trust_change_cannot_authorize(self):
        self.record()
        trusted = self.repo / 'packaging/emaki-keyring/emaki-trusted'
        trusted.write_text('')
        self.commit()
        shutil.copy2(self.keys.trusted, trusted)
        self.refused()

    def test_valid_exception_survives_clean_checkout(self):
        self.record()
        for name in ('build.sh', 'release_inputs.py', 'source_archive.py'):
            shutil.copy2(ROOT / 'packaging' / name, self.repo / 'packaging' / name)
        for path in (ROOT / 'packaging/mirror').glob('*.py'):
            shutil.copy2(path, self.repo / 'packaging/mirror' / path.name)
        self.commit()
        clone = self.base / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(clone)], check=True)
        result = subprocess.run([sys.executable, str(clone / 'packaging/release_inputs.py'),
                                 '--repo', str(clone)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASSED BY EXCEPTION', result.stdout)
        build = subprocess.run(['bash', str(clone / 'packaging/build.sh'), '--dry-run',
                                '--only', 'emaki-installer'], capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertIn('PASSED BY EXCEPTION', build.stdout)
        self.assertIn('Would build from', build.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
