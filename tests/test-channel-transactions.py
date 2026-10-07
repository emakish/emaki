#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove channel migration and server refresh with offline pacman transactions."""
import errno
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'packaging/emaki-mirrorlist'
OLD_MIRRORLIST = '''# Emaki repository channels. Enable exactly one server.
# After switching channels, run `sudo pacman -Syyu` once.
Server = https://pkgs.emaki.sh/stable/$arch
# Server = https://pkgs.emaki.sh/testing/$arch
'''


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


class Transactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('pacman', 'pacman-conf', 'bsdtar', 'unshare', 'ldd', 'repo-add'):
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
                # Match the sandbox adapter used by the rollback transaction tests.
                if left.sendmsg([b'x'], [], socket.MSG_NOSIGNAL) != 1 or right.recv(1) != b'x':
                    raise RuntimeError('local socket transport unavailable')
                area = ROOT / '.cache/evidence'
                area.mkdir(parents=True, exist_ok=True)
                library = area / 'channel-pacman-transport.so'
                build = run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                             '-o', str(library), str(ROOT / 'tests/pacman-hook-transport.c'), '-ldl'])
                if build.returncode:
                    raise RuntimeError(build.stderr)
                cls.pacman_command = ['env', 'LD_PRELOAD=/usr/lib/channel-transport.so', 'pacman']
                cls.transport_library = library
                print('Using equivalent local socket transport for sandboxed pacman tests.')
        finally:
            left.close()
            right.close()

    def setUp(self):
        area = ROOT / '.cache/evidence'
        area.mkdir(parents=True, exist_ok=True)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(dir=area, prefix='channel-transaction-')))
        self.root = self.work / 'root'
        for directory in ('usr/bin', 'etc/pacman.d/hooks', 'var/lib/pacman', 'var/cache/pacman/pkg',
                          'var/log', 'dev', 'proc', 'tmp'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / 'dev/null').touch()
        (self.root / 'bin').symlink_to('usr/bin')
        sources = []
        for name in ('bash', 'stat', 'mktemp', 'sed', 'chmod', 'chown', 'mv', 'rm', 'cmp', 'mkdir', 'cp', 'cat', 'ln', 'env', 'pacman', 'pacman-conf'):
            binary = shutil.which(name)
            self.assertIsNotNone(binary, name)
            sources += [binary] + re.findall(r'(/[^\s()]+)', run(['ldd', binary]).stdout)
        for source in sources:
            target = self.root / source.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if hasattr(self, 'transport_library'):
            shutil.copy2(self.transport_library, self.root / 'usr/lib/channel-transport.so')
        (self.root / 'usr/bin/sh').symlink_to('bash')
        (self.root / 'etc/pacman.conf').write_text('[options]\nArchitecture = x86_64\nSigLevel = Never\n')

    def success(self, result):
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for failure in ('command failed', 'could not run hook', 'unable to write to pipe',
                        'migration failed', 'command not found'):
            self.assertNotIn(failure, output)

    def pacman(self, *args):
        command = []
        for arg in args:
            if isinstance(arg, Path) and arg.is_relative_to(self.work):
                relative = arg.relative_to(self.root) if arg.is_relative_to(self.root) else Path('tmp') / arg.name
                target = self.root / relative
                if arg != target:
                    shutil.copy2(arg, target)
                arg = '/' + str(relative)
            command.append(str(arg))
        result = run(['unshare', '--user', '--map-root-user', '--mount', 'bash', '-c',
                      'mount --rbind /proc "$1/proc" || exit; '
                      'mount --bind /dev/null "$1/dev/null" || exit; '
                      'exec chroot "$1" /usr/bin/env LC_ALL=C "${@:2}"',
                      '_', str(self.root), *self.pacman_command, '--noconfirm', *command],
                     env={**os.environ, 'LC_ALL': 'C'})
        with (ROOT / '.cache/evidence/channel-transactions.log').open('a') as output:
            output.write(f'{self.id()}: pacman {" ".join(command)}\n{result.stdout}{result.stderr}\n')
        return result

    def archive(self, stage, version):
        metadata = stage / '.PKGINFO'
        if not metadata.exists():
            metadata.write_text(f'pkgname = emaki-mirrorlist\npkgver = {version}\n'
                                'pkgdesc = Emaki package repository channels\narch = any\nsize = 0\n'
                                'builddate = 1\nbackup = etc/pacman.d/emaki-mirrorlist\n')
        result = self.work / f'emaki-mirrorlist-{version}-any.pkg.tar.zst'
        self.success(run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf', str(result),
                          '-C', str(stage), *sorted(item.name for item in stage.iterdir())]))
        return result

    def legacy(self, version='0.2.0-1', contents=OLD_MIRRORLIST):
        stage = self.work / 'legacy'
        target = stage / 'etc/pacman.d/emaki-mirrorlist'
        target.parent.mkdir(parents=True)
        target.write_text(contents)
        if '/usr/share/emaki/mirrors/' in contents:
            mirrors = stage / 'usr/share/emaki/mirrors'
            mirrors.mkdir(parents=True)
            for channel in ('stable', 'testing'):
                shutil.copy2(PACKAGE / f'{channel}.conf', mirrors / f'{channel}.conf')
        return self.archive(stage, version)

    def current(self, refresh=False):
        stage = self.work / ('refreshed' if refresh else 'current')
        stage.mkdir()
        # Exercise the package's actual installation layout and backup metadata.
        script = '''set -eu
source "$1/PKGBUILD"
srcdir=$1
pkgdir=$2
package
[[ -z ${install:-} ]] || cp "$srcdir/$install" "$pkgdir/.INSTALL"
printf 'pkgname = %s\\npkgver = %s-%s\\npkgdesc = %s\\narch = any\\nsize = 0\\nbuilddate = 1\\n' "$pkgname" "$pkgver" "$pkgrel" "$pkgdesc" > "$pkgdir/.PKGINFO"
for item in "${backup[@]}"; do printf 'backup = %s\\n' "$item" >> "$pkgdir/.PKGINFO"; done
printf '%s-%s' "$pkgver" "$pkgrel"
'''
        result = run(['bash', '-c', script, '_', str(PACKAGE), str(stage)])
        self.success(result)
        version = result.stdout
        if refresh:
            version += '.1'
            metadata = stage / '.PKGINFO'
            metadata.write_text(re.sub(r'^pkgver = .*$', 'pkgver = ' + version,
                                       metadata.read_text(), flags=re.MULTILINE))
            for channel in ('stable', 'testing'):
                server = stage / f'usr/share/emaki/mirrors/{channel}.conf'
                server.write_text(server.read_text().replace('https://pkgs.emaki.sh/',
                                                             'https://updated.example.invalid/'))
        return self.archive(stage, version)

    def verify_channel(self, channel, refreshed=False):
        selector = self.root / 'etc/emaki/channel'
        self.assertTrue(selector.is_file(), 'channel choice must live outside the mirrorlist package')
        self.assertEqual([line.strip() for line in selector.read_text().splitlines()
                          if line.strip() and not line.lstrip().startswith('#')],
                         [f'Include = /usr/share/emaki/mirrors/{channel}.conf'])
        mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        self.assertIn('Include = /etc/emaki/channel', mirrorlist.read_text())
        self.assertFalse(Path(str(mirrorlist) + '.pacnew').exists())
        owner = self.pacman('-Qo', selector)
        self.assertNotEqual(owner.returncode, 0, owner.stdout + owner.stderr)
        self.assertIn('No package owns', owner.stderr)
        host = 'updated.example.invalid' if refreshed else 'pkgs.emaki.sh'
        server = self.root / f'usr/share/emaki/mirrors/{channel}.conf'
        self.assertIn(f'Server = https://{host}/{channel}/$arch', server.read_text())
        (self.root / 'etc/effective.conf').write_text(
            '[options]\nArchitecture = x86_64\n[emaki]\nInclude = /etc/pacman.d/emaki-mirrorlist\n')
        effective = run(['unshare', '--user', '--map-root-user', 'chroot', str(self.root),
                         '/usr/bin/pacman-conf', '--config', '/etc/effective.conf',
                         '--repo', 'emaki', 'Server'])
        self.success(effective)
        self.assertEqual(effective.stdout.strip(), f'https://{host}/{channel}/x86_64')

    def migrate(self, channel, edited=None, version='0.2.0-1', contents=OLD_MIRRORLIST):
        self.success(self.pacman('-U', self.legacy(version, contents)))
        mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        if edited is not None:
            mirrorlist.write_text(edited)
        self.enable_fixture_repositories()
        self.healthy()
        upgraded = self.pacman('-U', self.current())
        self.success(upgraded)
        if edited is not None:
            self.assertIn('installed as /etc/pacman.d/emaki-mirrorlist.pacnew', upgraded.stderr)
            self.assertIn('.pacnew was merged automatically and removed', upgraded.stdout)
        self.verify_channel(channel)
        self.healthy()
        selector = self.root / 'etc/emaki/channel'
        selector.write_text(selector.read_text() + '# Local channel choice.\n')
        preserved = selector.read_bytes()
        self.success(self.pacman('-U', self.current(refresh=True)))
        self.verify_channel(channel, refreshed=True)
        self.healthy()
        self.assertEqual(selector.read_bytes(), preserved)

    def enable_fixture_repositories(self):
        # Both repository entries download a real local database through pacman's
        # external transfer interface; no host repository or network is involved.
        fixture = self.root / 'fixture'
        fixture.mkdir(exist_ok=True)
        stage = self.work / 'base-package'
        stage.mkdir()
        (stage / '.PKGINFO').write_text('pkgname = fixture-base\npkgver = 1-1\n'
                                      'pkgdesc = Transaction fixture\narch = any\nsize = 0\n')
        base = self.archive(stage, '1-1')
        self.success(self.pacman('-U', base))
        self.success(run(['repo-add', '--quiet', str(fixture / 'fixture.db.tar.zst'), str(base)]))
        shutil.copy2(fixture / 'fixture.db.tar.zst', fixture / 'repository.db')
        transfer = self.root / 'usr/bin/fixture-transfer'
        transfer.write_text('#!/bin/bash\ncase "$1" in *.db) cp /fixture/repository.db "$2";; *) exit 1;; esac\n')
        transfer.chmod(0o755)
        options = ('[options]\nArchitecture = x86_64\nSigLevel = Never\n'
                   'XferCommand = /usr/bin/fixture-transfer %u %o\n')
        unrelated = ('\n[fixture]\nServer = https://fixture.invalid/$arch\nSigLevel = Never\n')
        self.unrelated_configuration = unrelated
        (self.root / 'etc/recovery.conf').write_text(options + unrelated)
        (self.root / 'etc/pacman.conf').write_text(
            options + unrelated + '\n[emaki]\nInclude = /etc/pacman.d/emaki-mirrorlist\nSigLevel = Never\n')

    def healthy(self):
        self.success(self.pacman('-Q'))
        self.success(self.pacman('-Sy'))

    def legacy_with_repositories(self, edited=None):
        self.success(self.pacman('-U', self.legacy()))
        if edited is not None:
            (self.root / 'etc/pacman.d/emaki-mirrorlist').write_text(edited)
        self.enable_fixture_repositories()
        self.healthy()

    def test_custom_pacnew_can_be_merged(self):
        custom = ('Server = https://custom.invalid/$arch\n'
                  'Server = https://pkgs.emaki.sh/stable/$arch\n')
        self.legacy_with_repositories(custom)
        self.success(self.pacman('-U', self.current()))
        mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        self.assertEqual(mirrorlist.read_text(), custom)
        self.healthy()
        replacement = Path(str(mirrorlist) + '.pacnew')
        self.assertTrue(replacement.is_file())
        mirrorlist.write_bytes(replacement.read_bytes())
        self.healthy()
        self.assertIn('Include = /usr/share/emaki/mirrors/stable.conf',
                      (self.root / 'etc/emaki/channel').read_text())

    def test_blocked_selector_aborts_before_extraction(self):
        self.legacy_with_repositories()
        mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        before = mirrorlist.read_bytes()
        (self.root / 'etc/emaki').write_text('An administrator file.\n')
        result = self.pacman('-U', self.current())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(mirrorlist.read_bytes(), before)
        self.assertIn('emaki-mirrorlist 0.2.0-1', self.pacman('-Q', 'emaki-mirrorlist').stdout)
        self.healthy()

    def blocked_channel_upgrade(self, kind):
        self.legacy_with_repositories()
        selector = self.root / 'etc/emaki/channel'
        selector.parent.mkdir()
        if kind == 'directory':
            selector.mkdir()
        else:
            selector.symlink_to('missing')
        result = self.pacman('-U', self.current())
        # Scriptlet failure does not veto extraction in pacman 7.1.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('cannot prepare', result.stdout + result.stderr)
        self.healthy()
        self.assertTrue(selector.is_dir() if kind == 'directory' else selector.is_symlink())

    def test_channel_directory_keeps_extracted_payload_usable(self):
        self.blocked_channel_upgrade('directory')

    def test_broken_channel_link_keeps_extracted_payload_usable(self):
        self.blocked_channel_upgrade('broken-link')

    def test_invalid_selectors_are_repaired_on_reinstall(self):
        package = self.current()
        self.success(self.pacman('-U', package))
        self.enable_fixture_repositories()
        selector = self.root / 'etc/emaki/channel'
        self.healthy()
        for invalid in (None, '', '# no active channel\n', 'junk\n',
                        'Include = /usr/share/emaki/mirrors/missing.conf\n'):
            with self.subTest(selector=invalid):
                if invalid is None:
                    selector.unlink(missing_ok=True)
                else:
                    selector.write_text(invalid)
                # Missing Includes cannot parse, so use a known working recovery
                # config to reach the package scriptlet before testing normal use.
                self.success(self.pacman('--config', '/etc/recovery.conf', '-U', package))
                self.assertTrue(selector.is_file())
                self.assertIn('Include = /usr/share/emaki/mirrors/stable.conf', selector.read_text())
                self.healthy()

    def test_valid_selectors_preserved_on_reinstall(self):
        package = self.current()
        self.success(self.pacman('-U', package))
        self.enable_fixture_repositories()
        selector = self.root / 'etc/emaki/channel'
        for channel in ('stable', 'testing'):
            with self.subTest(channel=channel):
                contents = f'# Local choice.\nInclude = /usr/share/emaki/mirrors/{channel}.conf\n'
                selector.write_text(contents)
                self.success(self.pacman('-U', package))
                self.assertEqual(selector.read_text(), contents)
                self.healthy()

    def test_removal_keeps_other_repositories_working(self):
        self.success(self.pacman('-U', self.current()))
        self.enable_fixture_repositories()
        self.healthy()
        self.success(self.pacman('-R', 'emaki-mirrorlist'))
        self.assertFalse((self.root / 'etc/emaki/channel').exists())
        self.assertIn(self.unrelated_configuration, (self.root / 'etc/pacman.conf').read_text())
        self.healthy()

    def test_removal_with_custom_mirrorlist_keeps_config_working(self):
        self.success(self.pacman('-U', self.current()))
        self.enable_fixture_repositories()
        mirrorlist = self.root / 'etc/pacman.d/emaki-mirrorlist'
        mirrorlist.write_text('Server = https://custom.invalid/$arch\n')
        self.healthy()
        self.success(self.pacman('-R', 'emaki-mirrorlist'))
        self.assertFalse((self.root / 'etc/emaki/channel').exists())
        self.assertIn(self.unrelated_configuration, (self.root / 'etc/pacman.conf').read_text())
        self.healthy()

    def test_removal_preserves_custom_emaki_server(self):
        self.success(self.pacman('-U', self.current()))
        self.enable_fixture_repositories()
        configuration = self.root / 'etc/pacman.conf'
        custom = 'Server = https://custom.invalid/$arch\nUsage = All\n'
        configuration.write_text(configuration.read_text() + custom)
        self.healthy()
        self.success(self.pacman('-R', 'emaki-mirrorlist'))
        self.assertFalse((self.root / 'etc/emaki/channel').exists())
        self.assertIn('[emaki]', configuration.read_text())
        self.assertIn(custom, configuration.read_text())
        self.assertNotIn('Include = /etc/pacman.d/emaki-mirrorlist', configuration.read_text())
        self.healthy()

    def test_invalid_selector_is_repaired_on_fresh_install(self):
        selector = self.root / 'etc/emaki/channel'
        selector.parent.mkdir()
        selector.write_text('# An incomplete prior choice.\n')
        self.success(self.pacman('-U', self.current()))
        self.enable_fixture_repositories()
        self.assertIn('Include = /usr/share/emaki/mirrors/stable.conf', selector.read_text())
        self.healthy()

    def test_untouched_stable_upgrade_and_server_refresh(self):
        self.migrate('stable')

    def test_untouched_testing_upgrade_preserves_pre_extraction_choice(self):
        testing = OLD_MIRRORLIST.replace('\nServer = ', '\n# Server = ').replace(
            '# Server = https://pkgs.emaki.sh/testing/', 'Server = https://pkgs.emaki.sh/testing/')
        self.migrate('testing', contents=testing)

    def test_edited_testing_upgrade_and_server_refresh(self):
        self.migrate('testing', OLD_MIRRORLIST.replace('\nServer = ', '\n# Server = ')
                     .replace('# Server = https://pkgs.emaki.sh/testing/',
                              'Server = https://pkgs.emaki.sh/testing/'))

    def test_old_github_testing_upgrade(self):
        self.migrate('testing', 'Server = https://github.com/emakish/packages/releases/download/testing\n')

    def test_previous_selector_testing_upgrade(self):
        self.migrate('testing', 'Include = /usr/share/emaki/mirrors/testing.conf\n',
                     version='0.2.0-2', contents='Include = /usr/share/emaki/mirrors/stable.conf\n')

    def test_fresh_install_channel_is_unowned(self):
        self.success(self.pacman('-U', self.current()))
        self.verify_channel('stable')
        self.enable_fixture_repositories()
        self.healthy()


if __name__ == '__main__':
    unittest.main()
