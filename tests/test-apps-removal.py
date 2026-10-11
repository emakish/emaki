#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preinstalled programs: removable, removed for good, and kept on the upgrade from 0.5.0.

Real offline pacman transactions in a private root, with stub packages named as in the
0.5.0 and current emaki-apps recipes. A private user namespace supplies root; no host
package state is read or changed.
"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'installer'))
sys.path.insert(0, str(ROOT / 'update-manager'))
from emaki_installer.constants import RICH_APPS
from emaki_installer.worker import software_packages
import programs_catalog as catalog
import programs_remove as helper

metadata = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))['metadata']
BASELINE = 'v0.5.0'
# A library only gwenview needs, and one two programs share.
LIBRARIES = {'gwenview': ['kimageannotator', 'kio'], 'okular': ['kio']}


def run(*args, **kwargs):
    return subprocess.run(*args, text=True, capture_output=True, **kwargs)


def names(values):
    return [re.split(r'[<>=:]', value)[0] for value in values]


class Programs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('pacman', 'repo-add', 'bsdtar', 'unshare', 'git'):
            if not shutil.which(tool):
                raise unittest.SkipTest(f'{tool} is unavailable')
        if run(['git', '-C', str(ROOT), 'rev-parse', '--verify', BASELINE + '^{commit}']).returncode:
            raise unittest.SkipTest('0.5.0 history unavailable in source archive')
        probe = run(['unshare', '--user', '--map-root-user', 'true'])
        if probe.returncode:
            raise unittest.SkipTest('user namespace unavailable: ' + probe.stderr.strip())

    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(dir='/tmp', prefix='programs-')))
        self.root = self.work / 'root'
        self.root.mkdir()
        old = self.work / 'baseline/PKGBUILD'
        old.parent.mkdir(parents=True)
        old.write_text(run(['git', '-C', str(ROOT), 'show', f'{BASELINE}:packaging/emaki-apps/PKGBUILD']).stdout)
        self.old = metadata(old)
        self.new = metadata(ROOT / 'packaging/emaki-apps/PKGBUILD')
        self.old_version = f'{self.old["pkgver"][0]}-{self.old["pkgrel"][0]}'
        self.new_version = f'{self.new["pkgver"][0]}-{self.new["pkgrel"][0]}'
        stubs = set(names(self.old['depends'])) | set(names(self.new['depends'])) | set(names(self.new['optdepends']))
        self.stubs = sorted(stubs | {'kimageannotator', 'kio'})

    def repository(self, label, apps, overrides=None):
        repo = self.work / label
        repo.mkdir()
        packages = [dict(name=name, version='1-1', depends=LIBRARIES.get(name, [])) for name in self.stubs]
        packages.append(apps)
        packages = {info['name']: info for info in packages}
        for name, fields in (overrides or {}).items():
            packages.setdefault(name, dict(name=name, version='1-1')).update(fields)
        packages = packages.values()
        for info in packages:
            stage = repo / 'stage' / info['name']
            (stage / 'usr/share/stub').mkdir(parents=True)
            (stage / 'usr/share/stub' / info['name']).write_text(info['version'])
            if info['name'] in RICH_APPS:
                (stage / 'usr/share/applications').mkdir(parents=True)
                (stage / f'usr/share/applications/{info["name"]}.desktop').write_text(
                    f'[Desktop Entry]\nType=Application\nName={info["name"]}\nExec=true\n')
            fields = [('pkgname', info['name']), ('pkgver', info['version']), ('pkgdesc', 'programs fixture'),
                      ('arch', 'any'), ('size', '0'), ('builddate', '1')]
            fields += [('depend', value) for value in info.get('depends', [])]
            fields += [('optdepend', value) for value in info.get('optdepends', [])]
            fields += [('provides', value) for value in info.get('provides', [])]
            (stage / '.PKGINFO').write_text(''.join(f'{key} = {value}\n' for key, value in fields))
            archive = repo / f'{info["name"]}-{info["version"]}-any.pkg.tar.zst'
            self.assertSuccess(run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf', str(archive),
                                    '-C', str(stage), '.PKGINFO', 'usr']))
        self.assertSuccess(run(['repo-add', '-q', str(repo / 'emaki.db.tar.gz'), *map(str, sorted(repo.glob('*.pkg.tar.zst')))]))
        return repo

    def apps(self, recipe, version):
        return dict(name='emaki-apps', version=version, depends=names(recipe['depends']),
                    optdepends=list(recipe['optdepends']))

    def pacman_command(self, repo, *args):
        for name in ('db', 'cache'):
            (self.work / name).mkdir(exist_ok=True)
        conf = self.work / 'pacman.conf'
        conf.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n[emaki]\nServer = file://{repo}\n')
        return ['unshare', '--user', '--map-root-user', 'pacman', '--config', str(conf),
                    '--root', str(self.root), '--dbpath', str(self.work / 'db'),
                    '--cachedir', str(self.work / 'cache'), '--logfile', str(self.work / 'pacman.log'),
                    '--noconfirm', *args]

    def pacman(self, repo, *args):
        return run(self.pacman_command(repo, *args), env={**os.environ, 'LC_ALL': 'C'})

    def installed(self, repo):
        return dict(line.split() for line in self.pacman(repo, '-Q').stdout.splitlines())

    def preview(self, repo, name):
        result = self.pacman(repo, '-Rs', '--print', '--print-format', '%n', name)
        return catalog.assess(name, result.returncode, result.stdout, result.stderr)

    def remove(self, repo, name, reviewed, intervene=None):
        """Run the real helper and pacman, injecting another frontend at commit time."""
        commands = []
        before_commit = []

        def query(*args, **kwargs):
            answer = self.pacman(repo, *args)
            if args[0] == '-Qlq':
                answer.stdout = answer.stdout.replace(str(self.root), '')
            return answer

        def commit(argv, **kwargs):
            commands.append(argv[1:])
            if intervene:
                intervene()
            before_commit.append(set(self.installed(repo)))
            return subprocess.Popen(self.pacman_command(repo, *argv[1:]), **kwargs)

        output = io.StringIO()
        isolated = SimpleNamespace(Popen=commit, DEVNULL=subprocess.DEVNULL,
                                   PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT)
        with patch.object(helper, 'pacman', query), patch.object(helper, 'subprocess', isolated), \
                patch.object(helper.signal, 'signal'), redirect_stdout(output):
            helper.remove(name, reviewed['token'])
        return json.loads(output.getvalue()), commands, before_commit

    def assertExactRemoval(self, repo, reviewed, intervene=None):
        answer, commands, before = self.remove(repo, 'gwenview', reviewed, intervene)
        self.assertEqual(answer['code'], 'removed')
        self.assertEqual(answer['packages'], reviewed['packages'])
        self.assertEqual(len(commands), 1)
        self.assertEqual(before[0] - set(self.installed(repo)), set(reviewed['packages']))

    def test_helper_removes_exact_confirmed_set(self):
        repo = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'okular'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview', 'kimageannotator'])
        self.assertExactRemoval(repo, reviewed)
        self.assertIn('kio', self.installed(repo))

    def test_helper_keeps_protected_dependency_after_reason_change(self):
        repo = self.repository('new', self.apps(self.new, self.new_version),
                               {'gwenview': {'depends': ['flatpak']}})
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'flatpak'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview'])
        self.assertExactRemoval(repo, reviewed,
                                lambda: self.assertSuccess(self.pacman(repo, '-D', '--asdeps', 'flatpak')))
        self.assertIn('flatpak', self.installed(repo))

    def test_helper_keeps_newly_unused_shared_dependency(self):
        repo = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'okular'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview', 'kimageannotator'])
        self.assertExactRemoval(repo, reviewed,
                                lambda: self.assertSuccess(self.pacman(repo, '-Rn', 'okular')))
        self.assertIn('kio', self.installed(repo))

    def test_helper_refuses_new_dependent_without_removing_anything(self):
        repo = self.repository('new', self.apps(self.new, self.new_version),
                               {'new-consumer': {'depends': ['kimageannotator']}})
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'okular'))
        reviewed = self.preview(repo, 'gwenview')
        answer, commands, before = self.remove(repo, 'gwenview', reviewed,
            lambda: self.assertSuccess(self.pacman(repo, '-S', 'new-consumer')))
        self.assertEqual(answer['code'], 'not-removed')
        self.assertEqual(len(commands), 1)
        self.assertEqual(before[0], set(self.installed(repo)))

    def test_helper_does_not_partially_commit_when_confirmed_target_disappears(self):
        repo = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'okular'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview', 'kimageannotator'])
        answer, commands, before = self.remove(repo, 'gwenview', reviewed,
            lambda: self.assertSuccess(self.pacman(repo, '-Rn', 'gwenview')))
        self.assertEqual(answer['code'], 'unknown')
        self.assertEqual(answer['blocked'], ['kimageannotator'])
        self.assertEqual(len(commands), 1)
        self.assertEqual(before[0], set(self.installed(repo)))
        self.assertIn('kimageannotator', self.installed(repo))

    def test_helper_refuses_changed_preview_before_commit(self):
        repo = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'okular'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertSuccess(self.pacman(repo, '-Rn', 'okular'))
        before = self.installed(repo)
        answer, commands, _ = self.remove(repo, 'gwenview', reviewed)
        self.assertEqual(answer['code'], 'changed')
        self.assertEqual(set(answer['packages']), {'gwenview', 'kimageannotator', 'kio'})
        self.assertEqual(commands, [])
        self.assertEqual(self.installed(repo), before)

    def test_optional_dependency_does_not_prevent_recursive_removal(self):
        repo = self.repository('new', self.apps(self.new, self.new_version), {
            'gwenview': {'depends': ['swayidle']},
            'swayidle': {},
            'emaki-config': {'optdepends': ['swayidle: Idle handling']},
            'emaki-desktop': {'depends': ['swayidle']},
        })
        self.assertSuccess(self.pacman(repo, '-Sy', 'gwenview', 'emaki-config'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview', 'swayidle'])
        self.assertExactRemoval(repo, reviewed)
        self.assertNotIn('swayidle', self.installed(repo))
        self.assertSuccess(self.pacman(repo, '-S', 'gwenview', 'emaki-desktop'))
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview'])
        self.assertExactRemoval(repo, reviewed)
        self.assertIn('swayidle', self.installed(repo))

    def test_versioned_virtual_provider_required_by_emaki_stays(self):
        repo = self.repository('new', self.apps(self.new, self.new_version), {
            'gwenview': {'depends': ['desktop-api>=2']},
            'desktop-provider': {'provides': ['desktop-api=2']},
            'emaki-desktop': {'depends': ['desktop-api>=2']},
        })
        self.assertSuccess(self.pacman(repo, '-Sy', '--asdeps', 'desktop-provider'))
        self.assertSuccess(self.pacman(repo, '-S', 'gwenview', 'emaki-desktop'))
        answer = self.preview(repo, 'desktop-provider')
        self.assertEqual(answer['code'], 'emaki')
        self.assertEqual(answer['requiredBy'], ['emaki-desktop', 'gwenview'])
        reviewed = self.preview(repo, 'gwenview')
        self.assertEqual(reviewed['packages'], ['gwenview'])
        self.assertExactRemoval(repo, reviewed)
        self.assertIn('desktop-provider', self.installed(repo))
        answer = self.preview(repo, 'desktop-provider')
        self.assertEqual((answer['code'], answer['requiredBy']), ('emaki', ['emaki-desktop']))

    def assertSuccess(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_upgrade_from_0_5_0_keeps_every_program(self):
        old = self.repository('old', self.apps(self.old, self.old_version))
        new = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(old, '-Sy', 'emaki-apps'))
        before = self.installed(old)
        self.assertEqual(before['emaki-apps'], self.old_version)
        self.assertTrue(set(RICH_APPS) <= before.keys())
        self.assertSuccess(self.pacman(new, '-Syyu'))
        after = self.installed(new)
        self.assertEqual(after['emaki-apps'], self.new_version)
        self.assertEqual(before.keys() - after.keys(), set())
        # Optional dependencies are not orphans: an orphan cleanup (-Qdt) keeps them.
        self.assertEqual(set(self.pacman(new, '-Qdtq').stdout.split()) & set(RICH_APPS), set())
        # Removing emaki-apps itself (refused here: it is Emaki's) never takes the programs along.
        answer = self.preview(new, 'emaki-apps')
        self.assertEqual((answer['code'], answer['blocked']), ('emaki', ['emaki-apps', 'emaki-desktop']))
        self.assertEqual(set(answer['packages']) & set(RICH_APPS), set())
        self.removed_for_good(new)

    def test_fresh_install_gets_the_full_set_explicitly(self):
        new = self.repository('new', self.apps(self.new, self.new_version))
        targets = [name for name in software_packages('rich') if name in self.stubs or name == 'emaki-apps']
        self.assertEqual(set(targets), {'emaki-apps', 'emaki-desktop', *RICH_APPS})
        self.assertSuccess(self.pacman(new, '-Sy', *targets))
        explicit = set(self.pacman(new, '-Qeq').stdout.split())
        self.assertTrue(set(RICH_APPS) <= explicit)
        self.removed_for_good(new)

    def removed_for_good(self, repo):
        answer = self.preview(repo, 'gwenview')
        self.assertEqual((answer['code'], answer['packages']), ('ready', ['gwenview', 'kimageannotator']))
        self.assertExactRemoval(repo, answer)
        self.assertNotIn('gwenview', self.installed(repo))
        self.assertIn('kio', self.installed(repo))
        # The next Emaki update (a newer emaki-apps) does not bring it back.
        later = self.repository('later', self.apps(self.new, self.new['pkgver'][0] + '-99'))
        self.assertSuccess(self.pacman(later, '-Syyu'))
        after = self.installed(later)
        self.assertEqual(after['emaki-apps'], self.new['pkgver'][0] + '-99')
        self.assertNotIn('gwenview', after)
        self.assertTrue(set(RICH_APPS) - {'gwenview'} <= after.keys())

    def test_hard_dependencies_are_refused(self):
        new = self.repository('new', self.apps(self.new, self.new_version))
        self.assertSuccess(self.pacman(new, '-Sy', 'emaki-apps', *RICH_APPS))
        for name in ('emaki-desktop', 'flatpak', 'cups', 'gst-libav'):
            answer = self.preview(new, name)
            self.assertEqual((answer['ok'], answer['code'], answer['requiredBy']), (False, 'emaki', ['emaki-apps']), name)
        answer = self.preview(new, 'kio')
        self.assertEqual((answer['code'], answer['requiredBy']), ('required', ['gwenview', 'okular']))
        self.assertEqual(self.preview(new, 'not-there')['code'], 'not-installed')


if __name__ == '__main__':
    unittest.main()
