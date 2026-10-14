#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""NVIDIA recovery policy against a real package database in a private root."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_receipts as receipts


def probe(directory):
    root = directory / 'root'
    (root / 'var/lib/pacman').mkdir(parents=True)
    for name in ('hooks', 'cache', 'gnupg'):
        (directory / name).mkdir()
    config = directory / 'pacman.conf'
    config.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n')
    packages = []
    for name, dependencies in [('egl-wayland', []), ('egl-gbm', []),
                               ('nvidia-utils', ['egl-wayland', 'egl-gbm']),
                               ('nvidia-open', ['nvidia-utils']), ('nvidia-open-lts', ['nvidia-utils']),
                               ('emaki-nvidia', [])]:
        path = directory / (name + '-1-1-any.pkg.tar.gz')
        content = ('pkgname = ' + name + '\npkgver = 1-1\npkgdesc = Private package\narch = any\nsize = 0\n'
                   + ''.join('depend = ' + dep + '\n' for dep in dependencies)).encode()
        with tarfile.open(path, 'w:gz') as archive:
            info = tarfile.TarInfo('.PKGINFO')
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        packages.append(str(path))
    prefix = ['pacman', '--root', str(root), '--dbpath', str(root / 'var/lib/pacman'),
              '--config', str(config), '--hookdir', str(directory / 'hooks'),
              '--cachedir', str(directory / 'cache'), '--logfile', str(directory / 'pacman.log'),
              '--gpgdir', str(directory / 'gnupg')]
    def pacman(*arguments):
        return subprocess.run(prefix + list(arguments), text=True, capture_output=True,
                              timeout=20, env={**os.environ, 'LC_ALL': 'C'})
    installed = pacman('-U', '--noconfirm', '--asdeps', *packages)
    assert installed.returncode == 0, installed.stderr
    assert pacman('-D', '--asexplicit', 'emaki-nvidia').returncode == 0
    after = dict(line.split() for line in pacman('-Q').stdout.splitlines())
    # This is the old Undo's removal set: pacman rightly protects nvidia-utils.
    rejected = pacman('-Rp', '--print-format', '%n', 'emaki-nvidia', 'egl-wayland', 'egl-gbm')
    assert rejected.returncode != 0, rejected.stdout
    assert 'required by nvidia-utils' in rejected.stdout + rejected.stderr, rejected.stdout + rejected.stderr
    receipt = receipts.record('arch', '', 'NVIDIA support', 'emaki-nvidia', {}, after,
                              directory=directory / 'receipts')
    plan = receipts.undo_plan(receipt, pacman)
    assert plan == dict(ok=False, code='restore-only'), plan


class NvidiaPrivatePacman(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(tool) for tool in ('pacman', 'unshare')),
                         'real pacman and unshare are required')
    def test_nvidia_dependency_graph_cannot_offer_package_undo(self):
        namespace = subprocess.run(['unshare', '-r', 'true'], text=True, capture_output=True)
        if namespace.returncode:
            self.skipTest('user namespace unavailable: ' + namespace.stderr.strip())
        cache = Path(__file__).resolve().parents[2] / '.cache'
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='nvidia-pacman-', dir=cache) as temporary:
            result = subprocess.run(['unshare', '-r', sys.executable, __file__, '--probe', temporary],
                                    text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--probe']:
        probe(Path(sys.argv[2]))
    else:
        unittest.main()
