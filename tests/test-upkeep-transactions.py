#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real offline pacman transactions with release metadata and the affected payloads.

External dependencies are omitted, as in the portal resolution suite. These tests prove
package resolution, hook execution and installer selections, not complete release binaries.
A private user namespace supplies chroot capability; no host package state is changed.
"""
import copy
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer.worker import software_packages

packaging = runpy.run_path(str(ROOT / 'tests/test-packaging.py'))
metadata = packaging['metadata']
NOTICE = 'Recording the desktop update [Emaki].'
SESSION_HOOK = 'usr/share/libalpm/hooks/zzz-emaki-session-update.hook'
GRUB_HOOK = 'usr/share/libalpm/hooks/90-emaki-grub-title.hook'
GRUB_HELPER = 'usr/share/libalpm/scripts/emaki-grub-title'
GENERATOR = b'''#!/bin/bash
GRUB_DISTRIBUTOR=Emaki
  OS="${GRUB_DISTRIBUTOR} Linux"
printf "menuentry '%s' {}\\n" "$OS"
'''
SNAPSHOTS = {'snapper', 'snap-pac', 'grub-btrfs'}


def run(*args, **kwargs):
    return subprocess.run(*args, text=True, capture_output=True, **kwargs)


class NoticeContract(unittest.TestCase):
    def test_upgrade_only_and_packaged(self):
        hook = (ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook').read_text()
        self.assertIn('Operation = Upgrade\n', hook)
        self.assertNotIn('Operation = Install', hook)
        self.assertNotIn('Operation = Remove', hook)
        self.assertIn('Description = ' + NOTICE, hook)
        self.assertIn('Exec = /usr/bin/emaki-session-update', hook)
        self.assertIn('/' + SESSION_HOOK, (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines())
        self.assertIn('packaging/emaki-config/zzz-emaki-session-update.hook $(DESTDIR)', (ROOT / 'Makefile').read_text())


class Transactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('pacman', 'repo-add', 'bsdtar', 'unshare', 'ldd', 'git'):
            if not shutil.which(tool):
                raise unittest.SkipTest(f'{tool} is unavailable')
        if run(['git', '-C', str(ROOT), 'rev-parse', '--verify', 'v0.2.0^{commit}']).returncode:
            raise unittest.SkipTest('0.2.0 history unavailable in source archive')
        probe = run(['unshare', '--user', '--map-root-user', 'true'])
        if probe.returncode:
            raise unittest.SkipTest('user namespace unavailable: ' + probe.stderr.strip())

    def setUp(self):
        area = ROOT / '.cache/evidence'
        area.mkdir(parents=True, exist_ok=True)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(dir=area, prefix='transaction-')))
        self.root = self.work / 'root'
        self.root.mkdir()
        self.names = {p.parent.name for p in (ROOT / 'packaging').glob('*/PKGBUILD')}
        self.old = self.recipes('v0.2.0')
        self.new = self.recipes()
        self.old += [{'name': n, 'version': '1-1'} for n in sorted(SNAPSHOTS | {'qt6-base', 'niri', 'unrelated', 'kdeconnect'})]
        self.new += [{'name': n, 'version': '1-1'} for n in sorted(SNAPSHOTS | {'qt6-base', 'niri', 'unrelated', 'kdeconnect'})]
        self.old.append({'name': 'grub', 'version': '2.16-1'})
        self.new.append({'name': 'grub', 'version': '2.16-2'})
        self.runtime()
        self.oldrepo = self.repository('old', self.old, old=True)
        self.newrepo = self.repository('new', self.new)
        result = self.pacman(self.oldrepo, '-Sy', *sorted(r['name'] for r in self.old), 'grub', 'qt6-base', 'niri', 'unrelated', 'kdeconnect', *sorted(SNAPSHOTS))
        self.assertSuccess(result)
        self.assertNotIn(NOTICE, result.stdout)
        installed = self.pacman(self.oldrepo, '-Q').stdout
        self.assertIn('emaki 0.2.0-1\n', installed)
        self.assertIn('emaki-config 0.2.0-1\n', installed)

    def recipes(self, revision=None):
        result = []
        for name in sorted(self.names):
            recipe = ROOT / 'packaging' / name / 'PKGBUILD'
            if revision:
                # A package added after that release (e.g. emaki-nvidia) has no historical recipe.
                if run(['git', '-C', str(ROOT), 'cat-file', '-e', f'{revision}:packaging/{name}/PKGBUILD']).returncode:
                    continue
                historical = run(['git', '-C', str(ROOT), 'show', f'{revision}:packaging/{name}/PKGBUILD'])
                self.assertEqual(historical.returncode, 0, historical.stderr)
                recipe = self.work / 'baseline/packaging' / name / 'PKGBUILD'
                recipe.parent.mkdir(parents=True)
                recipe.write_text(historical.stdout)
                (recipe.parent / 'SOURCE-COMMIT').write_text(run(['git', '-C', str(ROOT), 'rev-parse', revision]).stdout)
            info = metadata(recipe)
            result.append({'name': name, 'version': f'{info["pkgver"][0]}-{info["pkgrel"][0]}',
                           'depends': [d for d in info['depends'] if re.split('[<>=]', d)[0] in self.names | {'kdeconnect'}],
                           'provides': info['provides'], 'conflicts': info['conflicts'], 'replaces': info['replaces']})
        return result

    def runtime(self):
        # Only executables used by the actual hooks and their runtime loader/libraries.
        for name in ('true', 'bash', 'grep', 'mktemp', 'sed', 'chmod', 'mv', 'rm', 'python3'):
            binary = Path(shutil.which(name))
            files = [str(binary)] + re.findall(r'(/[^\s()]+)', run(['ldd', str(binary)]).stdout)
            for source in files:
                dest = self.root / source.lstrip('/')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
        library = Path(sysconfig.get_path('stdlib'))
        shutil.copytree(library, self.root / library.relative_to('/'),
                        ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests',
                                                     'idlelib', 'ensurepip', 'tkinter'))
        (self.root / 'bin').symlink_to('usr/bin')
        (self.root / 'dev').mkdir()
        (self.root / 'dev/null').touch()
        (self.root / 'run').mkdir()

    def repository(self, label, packages, old=False, generator=GENERATOR):
        repo = self.work / label
        repo.mkdir()
        for info in packages:
            stage = repo / 'stage' / info['name']
            stage.mkdir(parents=True)
            fields = [('pkgname', info['name']), ('pkgver', info['version']), ('pkgdesc', 'transaction fixture'),
                      ('arch', 'any'), ('size', '0'), ('builddate', '1')]
            fields += [(key, value) for field, key in (('depends', 'depend'), ('provides', 'provides'),
                                                       ('conflicts', 'conflict'), ('replaces', 'replaces'))
                       for value in info.get(field, ())]
            (stage / '.PKGINFO').write_text(''.join(f'{key} = {value}\n' for key, value in fields))
            payload = {f'usr/share/transaction-fixture/{info["name"]}': (info['version'].encode(), 0o644)}
            if info['name'] == 'emaki-config':
                for source, destination, mode in (('grub/90-emaki-grub-title.hook', GRUB_HOOK, 0o644),
                                                   ('grub/emaki-grub-title', GRUB_HELPER, 0o755)):
                    data = (run(['git', '-C', str(ROOT), 'show', f'v0.2.0:{source}']).stdout.encode()
                            if old else (ROOT / source).read_bytes())
                    payload[destination] = (data, mode)
                if not old and (ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook').exists():
                    payload[SESSION_HOOK] = ((ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook').read_bytes(), 0o644)
                    payload['usr/bin/emaki-session-update'] = ((ROOT / 'scripts/emaki-session-update').read_bytes(), 0o755)
                    payload['usr/libexec/emaki/emaki_session_state.py'] = ((ROOT / 'scripts/emaki_session_state.py').read_bytes(), 0o644)
            if info['name'] == 'emaki-config' and not old:
                payload['usr/share/emaki/xdg/autostart/org.kde.kdeconnect.daemon.desktop'] = (
                    (ROOT / 'packaging/emaki-config/org.kde.kdeconnect.daemon.desktop').read_bytes(), 0o644)
            if info['name'] == 'emaki-config' and not old:
                policy = ROOT / 'packaging/emaki-config/60-emaki-xdg.conf'
                if policy.exists():
                    payload['usr/lib/environment.d/60-emaki-xdg.conf'] = (policy.read_bytes(), 0o644)
            if info['name'] == 'kdeconnect':
                payload['etc/xdg/autostart/org.kde.kdeconnect.daemon.desktop'] = (
                    b'[Desktop Entry]\nType=Application\nName=KDE Connect\nExec=/usr/bin/true\n', 0o644)
            if info['name'] == 'grub':
                payload['etc/grub.d/10_linux'] = (generator, 0o755)
            for path, (data, mode) in payload.items():
                target = stage / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(mode)
            for path in stage.rglob('*'):
                os.utime(path, (1, 1))
            archive = repo / f'{info["name"]}-{info["version"]}-any.pkg.tar.zst'
            self.assertSuccess(run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf', str(archive), '-C', str(stage), *sorted(p.name for p in stage.iterdir())]))
        self.assertSuccess(run(['repo-add', '-q', str(repo / 'emaki.db.tar.gz'), *map(str, sorted(repo.glob('*.pkg.tar.zst')))]))
        return repo

    def pacman(self, repo, *args):
        for name in ('db', 'cache', 'hooks'):
            (self.work / name).mkdir(exist_ok=True)
        conf = self.work / 'pacman.conf'
        conf.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n[emaki]\nServer = file://{repo}\n')
        result = run(['unshare', '--user', '--map-root-user', 'pacman', '--config', str(conf),
                      '--root', str(self.root), '--dbpath', str(self.work / 'db'),
                      '--cachedir', str(self.work / 'cache'), '--hookdir', str(self.work / 'hooks'),
                      '--logfile', str(self.work / 'pacman.log'), '--noconfirm', *([] if args[0].startswith('-Q') else ['--noprogressbar']), *args],
                     env={**os.environ, 'LC_ALL': 'C'})
        with (self.work / 'transactions.log').open('a') as log:
            log.write(f'\n{self.id()}: pacman {" ".join(args)}\n{result.stdout}{result.stderr}')
        return result

    def assertSuccess(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('command failed', result.stderr)
        self.assertNotIn('could not run hook', result.stderr)

    def test_020_upgrade_runs_hook_once_and_preserves_existing_snapshot_packages(self):
        result = self.pacman(self.newrepo, '-Syyu')
        self.assertSuccess(result)
        self.assertEqual(result.stdout.count(NOTICE), 1, result.stdout)
        version = next(info['version'] for info in self.new if info['name'] == 'emaki-config')
        self.assertIn(f'emaki-config {version}\n', self.pacman(self.newrepo, '-Q').stdout)
        for name in SNAPSHOTS:
            self.assertSuccess(self.pacman(self.newrepo, '-Q', name))
        generated = run(['unshare', '--user', '--map-root-user', 'chroot', str(self.root), '/etc/grub.d/10_linux'])
        self.assertSuccess(generated)
        self.assertEqual(generated.stdout, "menuentry 'Emaki' {}\n")
        reinstalled = self.pacman(self.newrepo, '-S', 'emaki-config')
        self.assertSuccess(reinstalled)
        self.assertEqual(reinstalled.stdout.count(NOTICE), 1)
        # Upgrade includes a same-version reinstall, so the hook runs again.
        # This foreign root must never write a live-session marker.
        self.assertFalse((self.root / 'run/emaki-session-update').exists())
        installed = self.pacman(self.newrepo, '-S', 'unrelated')
        self.assertSuccess(installed)
        self.assertNotIn(NOTICE, installed.stdout)

    def test_020_upgrade_keeps_kdeconnect_but_suppresses_autostart(self):
        self.assertIn('kdeconnect', next(x for x in self.old if x['name'] == 'emaki-apps')['depends'])
        self.assertNotIn('kdeconnect', next(x for x in self.new if x['name'] == 'emaki-apps')['depends'])
        upstream = self.root / 'etc/xdg/autostart/org.kde.kdeconnect.daemon.desktop'
        original = upstream.read_bytes()
        self.assertSuccess(self.pacman(self.newrepo, '-Syyu'))
        self.assertSuccess(self.pacman(self.newrepo, '-Q', 'kdeconnect'))
        self.assertEqual(upstream.read_bytes(), original)
        override = self.root / 'usr/share/emaki/xdg/autostart/org.kde.kdeconnect.daemon.desktop'
        self.assertIn('Hidden=true', override.read_text())
        # Read the installed environment.d policy through the real environment
        # generator, then feed its output to the real XDG autostart generator.
        probes = runpy.run_path(str(ROOT / 'tests/test-kdeconnect-autostart.py'))
        probe = probes['AutostartTests']()
        generated = probe.generated('unset', installed=self.root)
        self.assertNotIn(probes['UNIT'], generated['units'])
        control = probe.generated('unset', policy=False, installed=self.root)
        self.assertIn(probes['UNIT'], control['units'])

    def test_later_shell_compositor_and_qt_upgrades_run_hook(self):
        self.assertSuccess(self.pacman(self.newrepo, '-Syyu'))
        marker_version, marker_release = next(info['version'] for info in self.new if info['name'] == 'emaki').rsplit('-', 1)
        marker_release = int(marker_release)
        for index, target in enumerate(('niri', 'niri-emaki', 'quickshell-emaki', 'qt6-base')):
            for info in self.new:
                if info['name'] == target:
                    old = info['version']
                    info['version'] = new = old.rsplit('-', 1)[0] + '-' + str(int(old.rsplit('-', 1)[1]) + 1)
            for info in self.new:
                info['depends'] = [d.replace(f'{target}={old}', f'{target}={new}') for d in info.get('depends', ())]
                if info['name'] == 'emaki':
                    info['version'] = f'{marker_version}-{marker_release + index + 1}'
            repo = self.repository(f'target-{index}', self.new)
            result = self.pacman(repo, '-Syyu')
            self.assertSuccess(result)
            self.assertEqual(result.stdout.count(NOTICE), 1, result.stdout)

    def test_changed_grub_generator_reports_failure_without_overwriting(self):
        self.assertSuccess(self.pacman(self.newrepo, '-Syyu'))
        changed = GENERATOR.replace(b'  OS=', b' OS=')
        broken = copy.deepcopy(self.new)
        next(info for info in broken if info['name'] == 'grub')['version'] = '2.17-1'
        repo = self.repository('changed-grub', broken, generator=changed)
        result = self.pacman(repo, '-Syyu')
        self.assertIn('command failed', result.stderr)
        self.assertIn('expected OS line absent', result.stdout + result.stderr)
        self.assertEqual((self.root / 'etc/grub.d/10_linux').read_bytes(), changed)
        unrelated = self.pacman(repo, '-S', 'unrelated')
        self.assertSuccess(unrelated)
        self.assertNotIn('emaki-grub-title', unrelated.stdout + unrelated.stderr)
        self.assertNotIn('expected OS line absent', unrelated.stdout + unrelated.stderr)
        self.assertEqual((self.root / 'etc/grub.d/10_linux').read_bytes(), changed)

    def test_fresh_filesystem_selections_resolve_without_snapshot_tools_on_ext4(self):
        for btrfs in (False, True):
            selection = software_packages('minimal', btrfs=btrfs)
            # Resolve real pacman archives for all requested installer packages.
            packages = [{'name': name, 'version': '1-1'} for name in sorted(set(selection))]
            repo = self.repository(f'selection-{btrfs}', packages)
            # Separate empty DB: do not let packages on the 0.2.0 machine hide missing deps.
            prior, prior_root = self.work, self.root
            self.work = prior / f'fresh-{btrfs}'
            self.work.mkdir()
            self.root = self.work / 'root'
            self.root.mkdir()
            self.runtime()
            try:
                result = self.pacman(repo, '-Sy', *selection)
                self.assertSuccess(result)
                self.assertNotIn(NOTICE, result.stdout)
                installed = {line.split()[0] for line in self.pacman(repo, '-Q').stdout.splitlines()}
                self.assertEqual(installed & SNAPSHOTS, SNAPSHOTS if btrfs else set())
            finally:
                self.work, self.root = prior, prior_root


if __name__ == '__main__':
    unittest.main()
