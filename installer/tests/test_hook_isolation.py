# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise native hook precedence in disposable roots, without host privileges."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from emaki_installer.media import package_source


HOOK = '''[Trigger]
Type = Path
Operation = Install
Target = usr/lib/modules/*/vmlinuz

[Action]
Description = Updating linux initcpios...
When = PostTransaction
Exec = /usr/share/libalpm/scripts/mkinitcpio install
NeedsTargets
'''


class DownloadFixture:
    """Keep source preparation offline; the transaction itself uses real pacman."""
    def run(self, args):
        if args[:2] == ['pacman', '-Sp']:
            return 'https://mirror.example/linux-1-1-any.pkg.tar.gz\n'
        if args[0] == 'curl':
            Path(args[args.index('--output') + 1]).write_bytes(b'fixture')
        if args[0] == 'repo-add':
            Path(args[1].replace('.db.tar.gz', '.db')).write_bytes(b'fixture')
        return ''


class NativeHookIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for executable in ('bwrap', 'pacman', 'cc'):
            if not shutil.which(executable):
                raise unittest.SkipTest(f'{executable} is required for native hook transactions')
        probe = subprocess.run(['bwrap', '--unshare-user', '--uid', '0', '--gid', '0',
                                '--ro-bind', '/', '/', '--', 'true'], capture_output=True)
        if probe.returncode:
            raise unittest.SkipTest('User namespaces unavailable: ' + probe.stderr.decode())
        evidence = Path(__file__).resolve().parents[2] / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='hook-fixture-', dir=evidence)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        # A static action works in an otherwise empty root. The kernel path
        # trigger selects it, but it does not consume NeedsTargets or build a real
        # image: those parts of mkinitcpio still require installation acceptance.
        # The sandbox denies libalpm's NeedsTargets pipe transfer (EPERM); keeping
        # the directive still checks that this hook is selected and executed.
        source = cls.work / 'preset.c'
        source.write_text('''#include <stdio.h>
int main(void) {
    FILE *out = fopen("/etc/mkinitcpio.d/linux.preset", "w");
    if (!out) return 3;
    fputs("PRESETS=('default')\\n", out);
    return fclose(out) != 0;
}
''')
        cls.action = cls.work / 'mkinitcpio'
        result = subprocess.run(['cc', '-static', '-o', str(cls.action), str(source)],
                                capture_output=True, text=True)
        if result.returncode:
            raise unittest.SkipTest('Static C runtime unavailable: ' + result.stderr)
        cls.archive = cls.work / 'linux-1-1-any.pkg.tar.gz'
        with tarfile.open(cls.archive, 'w:gz') as package:
            for name, data in {
                '.PKGINFO': b'pkgname = linux\npkgver = 1-1\npkgdesc = Hook fixture\n'
                            b'arch = any\nsize = 7\n',
                'usr/lib/modules/fixture/vmlinuz': b'fixture',
                'usr/share/libalpm/hooks/90-mkinitcpio-install.hook': HOOK.encode(),
                'usr/share/libalpm/scripts/mkinitcpio': cls.action.read_bytes(),
            }.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o755 if name.endswith('/scripts/mkinitcpio') else 0o644
                package.addfile(entry, io.BytesIO(data))

    def transaction(self, directory, config, live_hooks):
        target = directory / 'target'
        for name in ('var/lib/pacman', 'etc/mkinitcpio.d', 'usr/share/libalpm/hooks',
                     'usr/share/libalpm/scripts', 'tmp'):
            (target / name).mkdir(parents=True, exist_ok=True)
        # Only signature verification is relaxed for this unsigned local fixture.
        fixture_config = directory / 'transaction.conf'
        fixture_config.write_text(config.replace('Required DatabaseOptional TrustedOnly', 'Never')
                                  .replace('Required TrustedOnly', 'Never'))
        result = subprocess.run([
            'bwrap', '--unshare-user', '--uid', '0', '--gid', '0',
            '--cap-add', 'CAP_SYS_CHROOT', '--ro-bind', '/', '/',
            '--bind', str(self.work), str(self.work),
            # Hosts without local hooks have no mount point; the fixture needs only the keyring.
            '--tmpfs', '/etc/pacman.d', '--ro-bind-try', '/etc/pacman.d/gnupg', '/etc/pacman.d/gnupg',
            '--ro-bind', str(live_hooks), '/etc/pacman.d/hooks', '--',
            'pacman', '--config', str(fixture_config), '--root', str(target),
            '--dbpath', str(target / 'var/lib/pacman'),
            '--logfile', str(directory / 'pacman.log'),
            '--cachedir', str(directory), '--noconfirm', '-U', str(self.archive),
        ], capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / 'usr/lib/modules/fixture/vmlinuz').is_file())
        return target / 'etc/mkinitcpio.d/linux.preset', result.stdout + result.stderr

    def test_live_wrapper_is_overridden_only_by_source_isolation(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            directory = Path(temporary)
            live_hooks = directory / 'live/etc/pacman.d/hooks'
            live_hooks.mkdir(parents=True)
            wrapper = HOOK.replace('Exec = /usr/share/libalpm/scripts/mkinitcpio install',
                                   'Exec = /usr/share/libalpm/scripts/emaki-initramfs-refresh --stock')
            (live_hooks / '90-mkinitcpio-install.hook').write_text(wrapper)
            unsafe = directory / 'unsafe'
            unsafe.mkdir()
            # Omitting HookDir recreates the original default-directory leak.
            preset, output = self.transaction(unsafe, '[options]\nArchitecture = auto\n'
                                              'SigLevel = Never\n', live_hooks)
            self.assertFalse(preset.exists())
            self.assertIn('call to execv failed', output)
            for route in ('standard', 'relocated', 'online'):
                with self.subTest(route=route):
                    root = directory / route
                    root.mkdir()
                    if route != 'online':
                        mount = 'bootmnt' if route == 'standard' else 'copytoram'
                        repo = root / f'run/archiso/{mount}/emaki/repo'
                        repo.mkdir(parents=True)
                        if route == 'standard':
                            shipped = root / 'etc/emaki-installer/pacman-offline.conf'
                            shipped.parent.mkdir(parents=True)
                            shipped.write_text('[options]\nArchitecture = auto\n'
                                               'SigLevel = Required DatabaseOptional TrustedOnly\n'
                                               'LocalFileSigLevel = Required TrustedOnly\n'
                                               '[emaki-offline]\n'
                                               'SigLevel = Required DatabaseOptional TrustedOnly\n'
                                               f'Server = {repo.as_uri()}\n')
                    with package_source(DownloadFixture(), ['linux'], online=True,
                                        root=root, work_parent=root / 'work') as source:
                        config = source.config.read_text()
                        hookdirs = [line.split('=', 1)[1].strip() for line in config.splitlines()
                                    if line.startswith('HookDir =')]
                        self.assertEqual(len(hookdirs), 1)
                        self.assertEqual(list(Path(hookdirs[0]).iterdir()), [])
                        self.assertNotEqual(Path(hookdirs[0]), live_hooks)
                        preset, output = self.transaction(root, config, live_hooks)
                        self.assertTrue(preset.is_file(), output)
                        self.assertEqual(preset.read_text(), "PRESETS=('default')\n")
                        self.assertNotIn('call to execv failed', output)
                        self.assertNotIn('command failed to execute correctly', output)
            self.assertEqual((live_hooks / '90-mkinitcpio-install.hook').read_text(), wrapper)


if __name__ == '__main__':
    unittest.main()
