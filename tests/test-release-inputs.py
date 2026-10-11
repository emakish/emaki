#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline selective-release checks against published per-package source commits."""
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
