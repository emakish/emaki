#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shipped space hook with real offline pacman in a private user-namespace root."""
import _ctypes
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sysconfig
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('holds_fixture', str(ROOT / 'tests/test-rollback-holds.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
fixture = importlib.util.module_from_spec(spec)
loader.exec_module(fixture)
run = fixture.run

# Inject only measurement inputs, immediately before the original main dispatch.
# The launcher, parent identity, package selection and estimator remain real.
MEASUREMENTS = '''
import types
def fixture_statvfs(path):
    with open('/space-measurements', 'a') as output:
        output.write(str(path) + '\\n')
    return types.SimpleNamespace(f_bavail=int(Path('/space-free').read_text()), f_frsize=1)
mount_details = lambda path, mountinfo=None: ('/dev/fixture', 'btrfs')
os.statvfs = fixture_statvfs
'''


class SpaceTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.Transactions.setUpClass.__func__(cls)

    success = fixture.Transactions.success

    def setUp(self):
        area = ROOT / '.cache/fx24b'
        area.mkdir(parents=True, exist_ok=True)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(dir=area, prefix='space-alpm-')))
        self.root = self.work / 'root'
        for name in ('usr/bin', 'etc/pacman.d/hooks', 'var/lib/pacman',
                     'var/cache/pacman/pkg', 'var/log', 'dev', 'proc', 'tmp', '.snapshots'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'dev/null').touch()
        (self.root / 'bin').symlink_to('usr/bin')
        for binary in ('sh', 'cat', 'python3', 'vercmp', 'pacman', 'pacman-conf', 'env', 'mktemp', 'rm'):
            self.copy_binary(shutil.which(binary))
        self.copy_binary(_ctypes.__file__)
        self.copy_binary('/usr/lib/libalpm.so')
        stdlib = Path(sysconfig.get_path('stdlib'))
        shutil.copytree(stdlib, self.root / str(stdlib).lstrip('/'), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'idlelib', 'tkinter'))
        self.success(run(['make', 'install-upkeep', f'DESTDIR={self.root}', 'PREFIX=/usr'], cwd=ROOT))
        shutil.copy2(ROOT / 'scripts/emaki-qt-check', self.root / 'usr/bin/emaki-qt-check')
        self.inner_command = ['pacman']
        if self.pacman_command[0] == 'env':
            library = Path(self.pacman_command[1].split('=', 1)[1])
            shutil.copy2(library, self.root / 'transport.so')
            self.inner_command = ['env', 'LD_PRELOAD=/transport.so', 'pacman']
        self.estimator = self.root / 'usr/libexec/emaki/emaki_update_space.py'
        # The full package installs these shared modules outside install-upkeep.
        shutil.copy2(ROOT / 'scripts/emaki_i18n.py', self.estimator.parent / 'emaki_i18n.py')
        renderer = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        (self.estimator.parent / 'emaki_paths.py').write_text(
            renderer['modules'](renderer['paths']())[0])
        source = self.estimator.read_text()
        marker = "if __name__ == '__main__':"
        self.assertEqual(source.count(marker), 1)
        self.estimator.write_text(source.replace(marker, MEASUREMENTS + '\n' + marker))
        self.repository({'changed': '1-1', 'other': '1-1'}, size=0)
        self.success(self.pacman('-Sy', 'changed', 'other'))
        # Ignore bytecode created by unrelated setup hooks; later assertions
        # specifically check the space module's imports and all measured runs.
        shutil.rmtree(self.estimator.parent / '__pycache__', ignore_errors=True)
        self.repository({'changed': '9-1', 'other': '9-1'}, size=500 * 1024 ** 2, group='fixture-group')

    def copy_binary(self, binary):
        self.assertIsNotNone(binary)
        for source in [binary, *re.findall(r'(/[^\s()]+)', run(['ldd', binary]).stdout)]:
            dest = self.root / source.lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(source, dest)

    def repository(self, packages, size=0, group=None):
        repo = self.root / 'srv/repository'
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir(parents=True)
        for name, version in packages.items():
            stage = self.work / 'stage'
            stage.mkdir(exist_ok=True)
            (stage / '.PKGINFO').write_text(
                f'pkgname = {name}\npkgver = {version}\npkgdesc = space fixture\n'
                f'arch = any\nsize = {size}\nbuilddate = 1\n' + (f'group = {group}\n' if group else ''))
            self.success(run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf',
                              str(repo / f'{name}-{version}-any.pkg.tar.zst'), '-C', str(stage), '.PKGINFO']))
        self.success(run(['repo-add', '-q', str(repo / 'fixture.db.tar.gz'),
                          *map(str, sorted(repo.glob('*.pkg.tar.zst')))]))
        (self.root / 'etc/pacman.conf').write_text(
            '[options]\nArchitecture = x86_64\nSigLevel = Never\n'
            '[fixture]\nServer = file:///srv/repository\n')

    def pacman(self, *args, free=10 * 1024 ** 3, timeout=20):
        (self.root / 'space-free').write_text(str(free))
        (self.root / 'space-measurements').unlink(missing_ok=True)
        command = ['unshare', '--user', '--map-root-user', '--mount', '--pid', '--fork', 'sh', '-c',
                   'mount -t proc proc "$1/proc" || exit; shift; exec "$@"', '_', str(self.root),
                   'chroot', str(self.root), *self.inner_command, '--noconfirm', *args]
        env = {key: value for key, value in os.environ.items() if key not in ('DISPLAY', 'WAYLAND_DISPLAY')}
        with subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(env, LC_ALL='C'), start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Killing this private PID namespace's init also disposes of
                # estimator probes that started their own process sessions.
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
                raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        with (ROOT / '.cache/fx24b/space-alpm.log').open('a') as output:
            output.write(f'{self.id()}: {args!r}\nrc={result.returncode}\n{stdout}{stderr}\n')
        return result

    def measured(self, result):
        self.assertNotIn('Warning: the update space check is unavailable', result.stdout + result.stderr)
        marker = self.root / 'space-measurements'
        self.assertTrue(marker.exists(), result.stdout + result.stderr)
        self.assertIn('/', marker.read_text().splitlines())
        self.assertFalse((self.estimator.parent / '__pycache__').exists())

    def test_shortage_refuses_and_preserves_installed_version(self):
        result = self.pacman('-Syyu', free=1024 ** 3 + 100 * 1024 ** 2)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Not enough free space for this update', result.stdout + result.stderr)
        self.measured(result)
        self.assertEqual(self.pacman('-Q', 'changed', 'other').stdout, 'changed 1-1\nother 1-1\n')

    def test_fitting_install_is_measured(self):
        result = self.pacman('-Syy', 'other')
        self.success(result)
        self.measured(result)
        self.assertEqual(self.pacman('-Q', 'other').stdout, 'other 9-1\n')

    def test_translated_shortage_still_refuses(self):
        source = self.estimator.read_text()
        self.estimator.write_text(source.replace(
            "if __name__ == '__main__':",
            "_ = lambda text: text.replace('Not enough free space', 'Translated shortage')\n"
            "if __name__ == '__main__':"))
        result = self.pacman('-Syyu', free=1024 ** 3 + 100 * 1024 ** 2)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Translated shortage', result.stdout + result.stderr)
        self.measured(result)
        self.assertEqual(self.pacman('-Q', 'changed', 'other').stdout, 'changed 1-1\nother 1-1\n')

    def test_group_selection_is_measured_without_waiting_for_deadline(self):
        started = time.monotonic()
        result = self.pacman('-Syy', 'fixture-group')
        self.assertLess(time.monotonic() - started, 20)
        self.success(result)
        self.measured(result)
        self.assertEqual(self.pacman('-Q', 'changed', 'other').stdout, 'changed 9-1\nother 9-1\n')

    def test_early_interpreter_failure_drains_large_target_list(self):
        names = [f'p{index:04d}-' + 'x' * 110 for index in range(700)]
        self.assertGreater(sum(len(name) + 1 for name in names), 64 * 1024)
        self.repository(dict.fromkeys(names, '1-1'))
        # An invalid interpreter option is an actual startup failure: the
        # interpreter exits before opening the estimator or reading targets.
        launcher = self.root / 'usr/share/libalpm/scripts/emaki-update-space'
        source = launcher.read_text()
        self.assertIn('/usr/bin/python3', source)
        launcher.write_text(source.replace('/usr/bin/python3', '/usr/bin/python3 --invalid-startup-option'))
        result = self.pacman('-Syy', *names, timeout=45)
        self.success(result)
        text = result.stdout + result.stderr
        self.assertEqual(text.count('Warning: the update space check is unavailable;'), 1)
        self.assertNotIn('Broken pipe', text)
        self.assertFalse((self.root / 'space-measurements').exists())
        installed = set(self.pacman('-Qq').stdout.splitlines())
        self.assertTrue(set(names).issubset(installed))


if __name__ == '__main__':
    unittest.main(verbosity=2)
