#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise install reasons with real pacman in a private user-namespace root."""
import io
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_aur_build as helper


def probe(directory, operation, reason):
    root = directory / 'root'
    (root / 'var/lib/pacman').mkdir(parents=True)
    for name in ('hooks', 'cache', 'gnupg', 'repo'):
        (directory / name).mkdir()
    config = directory / 'pacman.conf'
    config.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n'
                      '[private]\nServer = file://' + str(directory / 'repo') + '\n')
    def package(name, version, dependencies=()):
        path = directory / 'repo' / f'{name}-{version}-any.pkg.tar.gz'
        content = ('pkgname = ' + name + '\npkgver = ' + version +
                   '\npkgdesc = Private regression package\narch = any\nsize = 0\n' +
                   ''.join('depend = ' + dep + '\n' for dep in dependencies)).encode()
        with tarfile.open(path, 'w:gz') as archive:
            info = tarfile.TarInfo('.PKGINFO')
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return str(path)
    old = package('libx', '1-1')
    new = package('libx', '2-1')
    app = package('app', '2-1', ['libx>=2', 'newdep'])
    dep = package('newdep', '1-1')
    subprocess.run(['repo-add', str(directory / 'repo/private.db.tar.gz'), new, app, dep],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    prefix = ['pacman', '--root', str(root), '--dbpath', str(root / 'var/lib/pacman'),
              '--config', str(config), '--hookdir', str(directory / 'hooks'),
              '--cachedir', str(directory / 'cache'), '--logfile', str(directory / 'pacman.log'),
              '--gpgdir', str(directory / 'gnupg')]
    def run(arguments, timeout=None):
        return subprocess.run(prefix + arguments[1:], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    def stream(arguments):
        result = run(arguments)
        return result.returncode, result.stdout.splitlines()
    def versions():
        result = run(['pacman', '-Q'])
        return dict(line.split() for line in result.stdout.splitlines())
    result = run(['pacman', '-U', '--noconfirm', old])
    assert result.returncode == 0, result.stderr
    if reason == 'dependency':
        assert run(['pacman', '-D', '--asdeps', 'libx']).returncode == 0
    assert run(['pacman', '-Sy']).returncode == 0
    arguments = ['pacman', operation, '--asdeps', '--']
    arguments += [new, dep, app] if operation == '-U' else ['app']
    with patch.object(helper, 'run', side_effect=run), patch.object(helper, 'stream', side_effect=stream), \
            patch.object(helper, 'versions', side_effect=versions):
        assert helper.add_only(arguments, versions(), set()) == 0
    assert run(['pacman', '-D', '--asexplicit', 'app']).returncode == 0
    explicit = set(run(['pacman', '-Qeq']).stdout.split())
    assert ('libx' in explicit) == (reason == 'explicit'), (reason, explicit)
    assert 'newdep' not in explicit, explicit
    removal = set(run(['pacman', '-Rs', '--print', '--print-format', '%n', 'app']).stdout.split())
    assert removal == ({'app', 'newdep'} if reason == 'explicit' else {'app', 'newdep', 'libx'}), removal


class PrivatePacman(unittest.TestCase):
    def test_denied_user_namespace_skips_before_creating_packages(self):
        denied = subprocess.CompletedProcess(['unshare', '-r', 'true'], 1, '', 'Operation not permitted')
        method = inspect.unwrap(self.test_existing_install_reasons_survive_built_and_repository_upgrades)
        with patch.object(subprocess, 'run', return_value=denied) as run, \
                self.assertRaisesRegex(unittest.SkipTest, 'user namespace unavailable'):
            if getattr(method, '__self__', None) is not None:
                method()
            else:
                method(self)
        run.assert_called_once_with(['unshare', '-r', 'true'], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('pacman', 'repo-add', 'unshare')),
                         'real pacman, repo-add and unshare are required')
    def test_existing_install_reasons_survive_built_and_repository_upgrades(self):
        namespace = subprocess.run(['unshare', '-r', 'true'], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if namespace.returncode:
            self.skipTest('user namespace unavailable: ' + namespace.stderr.strip())
        cache = Path(__file__).resolve().parents[2] / '.cache'
        cache.mkdir(exist_ok=True)
        for operation in ('-U', '-S'):
            for reason in ('explicit', 'dependency'):
                with self.subTest(operation=operation, reason=reason), \
                        tempfile.TemporaryDirectory(prefix='aur-pacman-', dir=cache) as temporary:
                    result = subprocess.run(['unshare', '-r', sys.executable, __file__,
                                             '--probe', temporary, operation, reason],
                                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--probe']:
        probe(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        unittest.main()
