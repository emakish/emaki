#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rollback holds with real offline pacman transactions in a private root."""
import contextlib
import _ctypes
import errno
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sysconfig
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('holds', str(ROOT / 'upkeep/emaki-rollback-holds'))
spec = importlib.util.spec_from_loader(loader.name, loader)
holds = importlib.util.module_from_spec(spec)
loader.exec_module(holds)


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def database(root, packages):
    local = root / 'var/lib/pacman/local'
    local.mkdir(parents=True, exist_ok=True)
    for name, version in packages.items():
        folder = local / f'{name}-{version}'
        folder.mkdir()
        (folder / 'desc').write_text(f'%NAME%\n{name}\n\n%VERSION%\n{version}\n')
    (root / 'etc/pacman.d').mkdir(parents=True, exist_ok=True)
    (root / 'etc/pacman.conf').write_text('[options]\nArchitecture = x86_64\nSigLevel = Never\n')


class Records(unittest.TestCase):
    def test_only_backwards_versions_are_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / 'old', Path(tmp) / 'new'
            database(old, {'down': '2-1', 'added': '1-1', 'up': '1-1', 'same': '1-1'})
            database(new, {'down': '1-1', 'removed': '1-1', 'up': '2-1', 'same': '1-1'})
            self.assertEqual(holds.prepare(old, new), {'down': '2-1'})
            self.assertEqual(set(holds.load(new)['differences']), {'down', 'added', 'removed', 'up'})

    def test_no_holds_never_takes_package_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(holds.os, 'open', side_effect=AssertionError('must not take a lock')):
                self.assertEqual(holds.check(root, {'unrelated'}), 0)

    def test_release_all_resets_malformed_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            holds.write(root, holds.STATE, '{bad')
            holds.release(root, '--all')
            self.assertEqual(holds.load(root)['holds'], {})


class Transactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('pacman', 'pacman-conf', 'vercmp', 'repo-add', 'bsdtar', 'unshare', 'ldd'):
            if not shutil.which(tool):
                raise unittest.SkipTest(f'{tool} is unavailable')
        probe = run(['unshare', '--user', '--map-root-user', '--mount', 'true'])
        if probe.returncode:
            raise unittest.SkipTest('user namespace unavailable: ' + probe.stderr.strip())

        cls.pacman_command = ['pacman']
        left, right = socket.socketpair()
        try:
            try:
                left.send(b'x', socket.MSG_NOSIGNAL)
            except OSError as error:
                if error.errno != errno.EPERM:
                    raise
                # This sandbox denies sendto even on anonymous local sockets.
                # Preserve ALPM's real target pipe with an equivalent sendmsg.
                if left.sendmsg([b'x'], [], socket.MSG_NOSIGNAL) != 1 or right.recv(1) != b'x':
                    raise RuntimeError('local socket transport unavailable')
                area = ROOT / '.cache/evidence'
                area.mkdir(parents=True, exist_ok=True)
                library = area / 'pacman-hook-transport.so'
                build = run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                             '-o', str(library), str(ROOT / 'tests/pacman-hook-transport.c'), '-ldl'])
                if build.returncode:
                    raise RuntimeError(build.stderr)
                cls.pacman_command = ['env', 'LD_PRELOAD=' + str(library), 'pacman']
                print('Using equivalent local socket transport for sandboxed pacman tests.')
        finally:
            left.close()
            right.close()

    def setUp(self):
        area = ROOT / '.cache/evidence'
        area.mkdir(parents=True, exist_ok=True)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(dir=area, prefix='rollback-holds-')))
        self.root = self.work / 'root'
        self.root.mkdir()
        for name in ('usr/bin', 'etc/pacman.d/hooks', 'var/lib/pacman', 'var/cache/pacman/pkg', 'var/log', 'dev', 'proc'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'dev/null').touch()
        (self.root / 'bin').symlink_to('usr/bin')
        sources = []
        for binary in ('/usr/bin/sh', '/usr/bin/python3', '/usr/bin/vercmp', _ctypes.__file__):
            sources += [binary] + re.findall(r'(/[^\s()]+)', run(['ldd', binary]).stdout)
        for source in sources:
            dest = self.root / source.lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        stdlib = Path(sysconfig.get_path('stdlib'))
        shutil.copytree(stdlib, self.root / str(stdlib).lstrip('/'), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'idlelib', 'tkinter'))
        result = run(['make', 'install-upkeep', f'DESTDIR={self.root}', 'PREFIX=/usr'], cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Full emaki-config installation supplies this hook executable through
        # the main install target; install-upkeep only supplies its hook.
        shutil.copy2(ROOT / 'scripts/emaki-qt-check', self.root / 'usr/bin/emaki-qt-check')
        # The snapshot policy hook is unrelated and targets only emaki-config.
        self.repository('old', {'changed': '1-1', 'other': '1-1'})
        self.repository('bad', {'changed': '2-1', 'other': '2-1'})
        self.repository('new', {'changed': '3-1', 'other': '3-1'})
        self.configure('old')
        self.success(self.pacman('-Sy', 'changed', 'other'))
        old = self.work / 'rolled-away'
        database(old, {'changed': '2-1', 'other': '1-1'})
        self.assertEqual(holds.prepare(old, self.root), {'changed': '2-1'})
        self.configure('bad')

    def repository(self, label, packages, files=None):
        repo = self.work / label
        repo.mkdir()
        for name, version in packages.items():
            stage = repo / name
            stage.mkdir()
            (stage / '.PKGINFO').write_text(f'pkgname = {name}\npkgver = {version}\npkgdesc = rollback fixture\narch = any\nsize = 0\nbuilddate = 1\n')
            members = ['.PKGINFO']
            for relative, source in (files or {}).get(name, {}).items():
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if relative.split('/')[0] not in members:
                    members.append(relative.split('/')[0])
            package = repo / f'{name}-{version}-any.pkg.tar.zst'
            self.success(run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf', str(package), '-C', str(stage), *members]))
        self.success(run(['repo-add', '-q', str(repo / 'fixture.db.tar.gz'), *map(str, sorted(repo.glob('*.pkg.tar.zst')))]))

    def configure(self, label, extra=''):
        (self.root / 'etc/pacman.conf').write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n{extra}[fixture]\nServer = file://{self.work / label}\n')

    def pacman(self, *args):
        result = run(['unshare', '--user', '--map-root-user', '--mount', 'sh', '-c',
                      'mount --rbind /proc \"$1/proc\" || exit; shift; exec \"$@\"', '_', str(self.root),
                      *self.pacman_command, '--root', str(self.root),
                      '--config', str(self.root / 'etc/pacman.conf'), '--dbpath', str(self.root / 'var/lib/pacman'),
                      '--hookdir', str(self.root / 'etc/pacman.d/hooks'),
                      '--cachedir', str(self.root / 'var/cache/pacman/pkg'), '--logfile', str(self.root / 'var/log/pacman.log'),
                      '--noconfirm', *args], env={**os.environ, 'LC_ALL': 'C'})
        log = ROOT / '.cache/evidence/rollback-holds-transactions.log'
        with log.open('a') as output:
            output.write(f'{self.id()}: pacman {" ".join(args)}\n{result.stdout}{result.stderr}\n')
        return result

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('command failed', result.stderr)
        self.assertNotIn('could not run hook', result.stderr)
        self.assertNotIn('unable to write to pipe', result.stderr)

    def release_cli(self, name, executable='/usr/bin/emaki-rollback'):
        if executable == '/usr/bin/emaki-rollback':
            target = self.root / executable.lstrip('/')
            shutil.copy2(ROOT / 'scripts/emaki-rollback', target)
            target.chmod(0o755)
        result = run(['unshare', '--user', '--map-root-user', '--mount', 'sh', '-c',
                      'mount --rbind /proc "$1/proc" || exit; shift; exec "$@"', '_', str(self.root),
                      'chroot', str(self.root), executable, 'release', name])
        self.success(result)

    def test_broken_python_does_not_block_repair(self):
        (self.root / 'usr/bin/python3').unlink()
        self.success(self.pacman('-Syy', 'other'))
        self.assertIn('other 2-1', self.pacman('-Q', 'other').stdout)

    def test_absent_or_empty_state_does_not_start_python(self):
        python = self.root / 'usr/bin/python3'
        python.write_text('#!/bin/sh\necho python-started >&2\nexit 3\n')
        python.chmod(0o755)
        for empty in (False, True):
            state = self.root / holds.STATE
            state.unlink(missing_ok=True)
            if empty:
                state.touch()
            result = self.pacman('-Syy', 'other')
            self.success(result)
            self.assertNotIn('python-started', result.stdout + result.stderr)

    def test_output_options_cannot_install_held_version(self):
        for options in (('--color', 'never'), ('--color=always',), ('-q',),
                        ('--quiet',), ('--noprogressbar',), ('--verbose',), ('-v',), ('--debug',)):
            with self.subTest(options=options):
                result = self.pacman(*options, '-Syyu')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('sudo emaki-rollback release changed', result.stdout + result.stderr)
                self.assertIn('changed 1-1', self.pacman('-Q', 'changed').stdout)

    def test_unknown_options_refuse_with_release_hint(self):
        # Simulate a newer pacman option not yet recognized by the installed helper.
        helper = self.root / holds.HELPER
        helper.write_text(helper.read_text().replace("'--needed', ", ''))
        result = self.pacman('--needed', '-Syyu')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('sudo emaki-rollback release --all', result.stdout + result.stderr)
        self.assertIn('changed 1-1', self.pacman('-Q', 'changed').stdout)

    def test_full_upgrade_refusal_explains_both_ways_out(self):
        result = self.pacman('-Syyu')
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn('sudo pacman -Syu --ignore changed', output)
        self.assertIn('sudo emaki-rollback release changed', output)
        self.assertIn('sudo emaki-rollback release --all', output)
        self.assertIn('other 1-1', self.pacman('-Q', 'other').stdout)
        self.success(self.pacman('-Syu', '--ignore', 'changed'))
        self.assertIn('changed 1-1', self.pacman('-Q', 'changed').stdout)
        self.assertIn('other 2-1', self.pacman('-Q', 'other').stdout)

    def test_forward_versions_and_new_packages_do_not_create_holds(self):
        holds.release(self.root, '--all')
        away = self.work / 'forward-away'
        database(away, {'changed': '0-1', 'other': '1-1', 'added': '2-1'})
        self.assertEqual(holds.prepare(away, self.root), {})
        self.repository('forward', {'changed': '2-1', 'other': '2-1', 'added': '2-1'})
        self.configure('forward')
        self.success(self.pacman('-Syyu'))
        self.success(self.pacman('-S', 'added'))
        self.assertIn('added 2-1', self.pacman('-Q', 'added').stdout)

    def test_exact_hold_refuses_but_unrelated_update_proceeds(self):
        update = self.pacman('-Syyu')
        self.assertNotEqual(update.returncode, 0, update.stdout + update.stderr)
        self.assertIn('emaki-rollback release changed', update.stdout + update.stderr)
        self.assertIn('changed 1-1', self.pacman('-Q', 'changed').stdout)
        self.success(self.pacman('-S', 'other'))
        self.assertIn('other 2-1', self.pacman('-Q', 'other').stdout)
        self.assertEqual(holds.load(self.root)['holds'], {'changed': '2-1'})
        self.release_cli('changed')
        self.success(self.pacman('-Su'))
        self.assertIn('changed 2-1', self.pacman('-Q', 'changed').stdout)

    def test_newer_version_updates_without_waiting_or_lock(self):
        self.configure('new')
        self.success(self.pacman('-Syyu'))
        self.assertEqual(holds.load(self.root)['holds'], {})
        self.assertIn('changed 3-1', self.pacman('-Q', 'changed').stdout)
        self.assertFalse((self.root / 'var/lib/pacman/db.lck').exists())
        self.assertFalse(list((self.root / 'usr/lib/systemd/system').glob('*rollback*')))

    def test_local_archive_exact_version_only(self):
        result = self.pacman('--ask=1', '-U', str(self.work / 'bad/changed-2-1-any.pkg.tar.zst'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('emaki-rollback release changed', result.stdout + result.stderr)
        self.success(self.pacman('-U', str(self.work / 'new/changed-3-1-any.pkg.tar.zst')))
        self.assertIn('changed 3-1', self.pacman('-Q', 'changed').stdout)

    def test_older_local_archive_is_allowed(self):
        self.success(self.pacman('-U', str(self.work / 'old/changed-1-1-any.pkg.tar.zst')))
        self.assertEqual(holds.load(self.root)['holds'], {'changed': '2-1'})

    def test_malformed_record_allows_install_and_upgrade(self):
        (self.root / holds.STATE).write_text('{bad')
        self.repository('extra', {'extra': '1-1', 'other': '3-1'})
        self.configure('extra')
        for args in (('-Syy', 'extra'), ('-S', 'other')):
            result = self.pacman(*args)
            self.success(result)
            self.assertEqual((result.stdout + result.stderr).count('The holds file is unreadable'), 1)
            self.assertIn('emaki-rollback release --all', result.stdout + result.stderr)
        self.release_cli('--all')
        self.assertEqual(holds.load(self.root)['holds'], {})

    def test_qualified_repository_does_not_use_priority_version(self):
        self.configure('new', extra=f'[priority]\nServer = file://{self.work / "bad"}\n')
        shutil.copyfile(self.work / 'bad/fixture.db.tar.gz', self.work / 'bad/priority.db')
        self.success(self.pacman('-Syy', 'fixture/changed'))
        self.assertIn('changed 3-1', self.pacman('-Q', 'changed').stdout)

    def test_pending_release_keeps_hold_and_refuses_real_upgrade(self):
        source = self.work / 'source'
        self.success(run(['make', 'install-upkeep', f'DESTDIR={source}', 'PREFIX=/usr'], cwd=ROOT))
        holds.install(self.root, source)
        target = self.root / 'usr/bin/emaki-rollback'
        shutil.copy2(ROOT / 'scripts/emaki-rollback', target)
        target.chmod(0o755)
        proc = self.root / 'proc'
        (proc / 'self').mkdir()
        (proc / 'cmdline').write_text('rootflags=subvol=@ rw')
        (proc / 'self/mountinfo').write_text(
            '36 25 0:32 /@emaki-kept-20261006T120000Z-aabbccdd / rw - btrfs /dev/test rw\n')
        before = (self.root / holds.STATE).read_bytes()
        for executable in ('/usr/bin/emaki-rollback', '/usr/local/bin/emaki-rollback'):
            for name in ('changed', '--all'):
                with self.subTest(executable=executable, name=name):
                    result = run(['unshare', '--user', '--map-root-user', 'chroot', str(self.root),
                                  executable, 'release', name])
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('Restart first, then release.', result.stdout + result.stderr)
                    self.assertEqual((self.root / holds.STATE).read_bytes(), before)
                    update = self.pacman('-Syyu')
                    self.assertNotEqual(update.returncode, 0, update.stdout + update.stderr)
                    self.assertIn('changed 1-1', self.pacman('-Q', 'changed').stdout)

    def test_carry_only_replaces_an_older_packaged_helper(self):
        holds.release(self.root, '--all')
        helper = 'usr/share/libalpm/scripts/emaki-rollback-holds'
        (self.root / helper).unlink()
        (self.root / 'usr/share/libalpm/hooks/55-emaki-snapshot-policy.hook').unlink(missing_ok=True)
        for version in ('1-1', '2-1', '3-1'):
            self.repository('config-' + version, {'emaki-config': version},
                            {'emaki-config': {helper: ROOT / 'upkeep/emaki-rollback-holds'}})
        self.configure('config-2-1')
        self.success(self.pacman('-Syy', 'emaki-config'))
        source = self.work / 'source'
        self.success(run(['make', 'install-upkeep', f'DESTDIR={source}', 'PREFIX=/usr'], cwd=ROOT))
        shutil.copytree(self.root / 'var/lib/pacman/local', source / 'var/lib/pacman/local')
        for version in ('1-1', '2-1', '3-1'):
            with self.subTest(version=version):
                package = self.work / ('config-' + version) / ('emaki-config-' + version + '-any.pkg.tar.zst')
                self.success(self.pacman('-U', str(package)))
                self.assertIn('emaki-config', self.pacman('-Qo', str(self.root / helper)).stdout)
                holds.install(self.root, source)
                self.assertEqual((self.root / holds.FALLBACK).exists(), version == '1-1')
                self.assertEqual((self.root / holds.HOOK).exists(), version == '1-1')
                self.assertEqual((self.root / holds.RELEASE_LINK).is_symlink(), version == '1-1')
                # The selected hook still rejects the rolled-away package.
                holds.save(self.root, {'holds': {'changed': '2-1'}, 'differences': {}})
                self.configure('bad')
                result = self.pacman('-Syyu')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('sudo emaki-rollback release changed', result.stdout + result.stderr)
                holds.release(self.root, '--all')

    def test_fallback_is_unowned_and_upgrade_takes_over_once(self):
        source = self.work / 'source'
        self.success(run(['make', 'install-upkeep', f'DESTDIR={source}', 'PREFIX=/usr'], cwd=ROOT))
        payload = {
            'usr/bin/emaki-rollback': ROOT / 'scripts/emaki-rollback',
            'usr/share/libalpm/scripts/emaki-rollback-holds': ROOT / 'upkeep/emaki-rollback-holds',
            'usr/share/libalpm/hooks/00-emaki-rollback-holds.hook': ROOT / 'upkeep/00-emaki-rollback-holds.hook',
            'usr/share/libalpm/hooks/99-emaki-rollback-holds.hook': ROOT / 'upkeep/99-emaki-rollback-holds.hook',
        }
        for relative in payload:
            (self.root / relative).unlink(missing_ok=True)
        old_cli = self.work / 'old-rollback'
        old_cli.write_text('#!/usr/bin/python3\nimport sys\nprint("old-status")\nsys.exit(0 if sys.argv[1:] == ["status"] else 2)\n')
        old_cli.chmod(0o755)
        old_files = {'usr/bin/emaki-rollback': old_cli} if getattr(self, 'old_command', False) else {}
        self.repository('config-old', {'emaki-config': '1-1'}, {'emaki-config': old_files})
        (self.root / 'usr/share/libalpm/hooks/55-emaki-snapshot-policy.hook').unlink(missing_ok=True)
        self.configure('config-old')
        self.success(self.pacman('-Syy', 'emaki-config'))
        holds.install(self.root, source)
        holds.install(self.root, source)
        if getattr(self, 'old_command', False):
            status = run(['unshare', '--user', '--map-root-user', 'chroot', str(self.root),
                          '/usr/local/bin/emaki-rollback', 'status'])
            self.success(status)
            self.assertIn('old-status', status.stdout)
        self.assertFalse((self.root / holds.HELPER).exists())
        self.release_cli('--all', '/usr/local/bin/emaki-rollback')
        self.repository('config', {'emaki-config': '3-1'}, {'emaki-config': payload})
        # This fixture isolates rollback protection from the separate snapshot policy.
        self.configure('config')
        result = self.pacman('-Syyu')
        self.success(result)
        self.assertEqual(result.stdout.count('Checking packages held after rollback'), 1)
        self.assertFalse((self.root / holds.FALLBACK).exists())
        self.assertFalse((self.root / holds.RELEASE_LINK).is_symlink())
        self.assertFalse((self.root / holds.HOOK).exists())
        self.assertIn('emaki-config', self.pacman('-Qo', str(self.root / holds.HELPER)).stdout)
        self.success(self.pacman('-S', 'emaki-config'))
        self.assertIn('emaki-config', self.pacman('-Qo', str(self.root / 'usr/bin/emaki-rollback')).stdout)
        self.success(run(['unshare', '--user', '--map-root-user', '--mount', 'sh', '-c',
                          'mount --rbind /proc "$1/proc" || exit; shift; exec "$@"', '_', str(self.root),
                          'chroot', str(self.root), '/usr/bin/emaki-rollback', 'release', '--all']))

    def test_older_packaged_command_hands_release_to_updated_package(self):
        self.old_command = True
        self.test_fallback_is_unowned_and_upgrade_takes_over_once()

    def test_incomplete_database_and_bad_record_skip_preparation(self):
        for broken in ('database', 'record'):
            away = self.work / ('broken-' + broken)
            database(away, {'changed': '2-1'})
            if broken == 'database':
                (away / 'var/lib/pacman/local/incomplete-1-1').mkdir()
            else:
                holds.write(away, holds.STATE, '{bad')
            # Real installed records in the restored root; execute preparation's
            # cleanup path with installation redirected to this staged payload.
            with patch.object(holds.sys, 'argv', ['holds', 'prepare', str(away), str(self.root)]):
                self.assertNotEqual(holds.main(), 0)
            self.assertEqual(holds.load(self.root)['holds'], {})
            self.success(self.pacman('-Syy', 'other'))


if __name__ == '__main__':
    unittest.main()
