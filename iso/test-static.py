#!/usr/bin/env python3
"""Offline regression checks: no package manager, service, or QEMU invocation."""
import base64
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare-profile')
repo = load('repo-files')
importer = load('import-releng')
image_check = load('verify-image')
# releng's mirror chooser, install guide and networkd units: the live session uses NetworkManager.
RELENG_LEFTOVERS = ('usr/local/bin/choose-mirror', 'usr/local/bin/Installation_guide',
                    'etc/systemd/network/20-ethernet.network', 'etc/systemd/network/20-wlan.network',
                    'etc/systemd/network/20-wwan.network')


class StaticTests(unittest.TestCase):
    def test_target_closure_excludes_nautilus(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'closure.txt'
            manifest.write_text('xdg-desktop-portal-gnome-emaki-50.0-1-x86_64.pkg.tar.zst\n')
            repo.check_closure(manifest)
            for forbidden in ('nautilus-50.0-1-x86_64.pkg.tar.zst',
                              'xdg-desktop-portal-gnome-50.0-1-x86_64.pkg.tar.zst'):
                manifest.write_text(forbidden + '\n')
                with self.assertRaises(ValueError):
                    repo.check_closure(manifest)
        self.assertIn('repo-files.py" check-closure "$work/closure.txt"',
                      (HERE / 'build.sh').read_text())

    def test_rich_apps_are_offline_only(self):
        seeds = set((HERE / 'target-packages.txt').read_text().split())
        local = set((HERE / 'emaki-packages.txt').read_text().split())
        self.assertIn('emaki-apps', seeds)
        self.assertIn('emaki-apps', local)
        for path in ('packages-extra.txt', 'profile/packages.x86_64'):
            live = set((HERE / path).read_text().split())
            self.assertIn('dolphin', live)
            self.assertFalse({'emaki-apps', 'nautilus', 'loupe', 'file-roller', 'papers',
                              'gwenview', 'libreoffice-fresh', 'haruna'} & live)
        builder = (HERE / 'build.sh').read_text()
        self.assertIn('"$work/profile/packages.x86_64" "$HERE/target-packages.txt"', builder)
        self.assertIn('"$work/resolved-db"', builder)

    def test_readable_boot_menus(self):
        for name in ('grub.cfg', 'loopback.cfg'):
            grub = (HERE / 'profile/grub' / name).read_text()
            self.assertIn('set gfxmode="1024x768,800x600,640x480,auto"', grub)
            self.assertIn('terminal_output gfxterm', grub)
            self.assertIn('background_image /boot/grub/background.png', grub)
        self.assertIn('"$ROOT/art/grub/background.png" "$work/profile/grub/background.png"',
                      (HERE / 'build.sh').read_text())
        self.assertIn('MENU RESOLUTION 640 480', (HERE / 'profile/syslinux/archiso_head.cfg').read_text())

    def test_test_packages_in_offline_closure_seeds_not_release_transaction(self):
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        seeds = (HERE / 'target-packages.txt').read_text().splitlines()
        live = (HERE / 'profile/packages.x86_64').read_text().splitlines()
        for name in constants['TEST_PACKAGES']:
            self.assertIn(name, seeds)
            self.assertIn(name, live)
            self.assertNotIn(name, constants['PACKAGES'])

    def test_installer_packages_are_offline_closure_seeds(self):
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        seeds = set((HERE / 'target-packages.txt').read_text().split())
        self.assertEqual(set(constants['PACKAGES'] + ['mkinitcpio']) - seeds, set())

    def test_no_releng_mirror_guide_or_networkd_leftovers(self):
        root = HERE / 'profile/airootfs'
        for relative in RELENG_LEFTOVERS:
            self.assertFalse((root / relative).exists(), relative)
        profiledef = (HERE / 'profile/profiledef.sh').read_text()
        self.assertIsNone(re.search(r'choose-mirror|Installation_guide|20-.*\.network', profiledef))

    def test_previous_image_removed_only_after_input_checks(self):
        builder = (HERE / 'build.sh').read_text()
        removal = builder.index('rm -f -- "$out"/emaki-*.iso*')
        self.assertGreater(removal, builder.index('check-input "$repo"'))
        self.assertGreater(removal, builder.index("fail 'invalid VERSION'"))
        self.assertLess(removal, builder.index('mkdir -p -- "$work/db"'))

    def test_snapshot_and_zram_packages_are_target_only(self):
        seeds = set((HERE / 'target-packages.txt').read_text().split())
        for path in ('packages-extra.txt', 'profile/packages.x86_64'):
            live = set((HERE / path).read_text().split())
            for name in ('snapper', 'snap-pac', 'grub-btrfs', 'zram-generator'):
                self.assertIn(name, seeds)
                self.assertNotIn(name, live, path)

    def test_live_hardware_is_installed(self):
        # Firmware, microcode and graphics/video drivers on the live image are also installed
        # on every target, so a laptop keeps on disk what worked from the stick.
        # LIVE_ONLY_HARDWARE takes a live seed only when it adds no hardware support by itself.
        live_only_hardware = {
            'b43-fwcutter',  # Extracts Broadcom firmware from a vendor driver; ships none.
        }
        pattern = re.compile(r'(linux-firmware.*|sof-firmware|alsa-firmware|.*-ucode|mesa'
                             r'|vulkan-.*|intel-media-driver|libva-.*|b43-fwcutter)')
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        recipes = {p.parent.name: p for p in (HERE.parent / 'packaging').glob('*/PKGBUILD')}
        installed, pending = set(), list(constants['PACKAGES']) + ['mkinitcpio']
        while pending:
            name = pending.pop()
            if name in installed:
                continue
            installed.add(name)
            if name in recipes:
                result = subprocess.run(
                    ['bash', '-c', 'startdir=$1; cd "$1"; source ./PKGBUILD; printf "%s\\n" "${depends[@]}"',
                     '_', str(recipes[name].parent)], capture_output=True, text=True, check=True)
                pending.extend(re.split(r'[<>=]', line, maxsplit=1)[0] for line in result.stdout.split())
        live = (HERE / 'profile/packages.x86_64').read_text().split()
        hardware = {name for name in live if pattern.fullmatch(name)}
        self.assertTrue({'linux-firmware', 'mesa'} <= hardware)
        self.assertEqual(hardware - live_only_hardware - installed, set())
        self.assertEqual(live_only_hardware - hardware, set())

    def test_desktop_dependencies_are_explicit_live_seeds(self):
        # The live image and the offline repo name every emaki-desktop dependency
        # explicitly (packages-extra.txt, merged into packages.x86_64 by import-releng.py).
        recipe = HERE.parent / 'packaging/emaki-desktop/PKGBUILD'
        result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s\\n" "${depends[@]}"', '_', str(recipe)],
                                capture_output=True, text=True, check=True)
        names = {re.split(r'[<>=]', line, maxsplit=1)[0] for line in result.stdout.splitlines()}
        self.assertIn('firefox', names)
        # The portal is required by its upstream name; the image names the fork that provides it.
        names = {'xdg-desktop-portal-gnome-emaki' if n == 'xdg-desktop-portal-gnome' else n for n in names}
        extra = set((HERE / 'packages-extra.txt').read_text().split())
        live = set((HERE / 'profile/packages.x86_64').read_text().split())
        self.assertEqual(names - extra, set())
        self.assertEqual(extra - live, set())

    def make_profile(self, directory):
        profile = Path(directory) / 'profile'
        root = profile / 'airootfs'
        for name in ('home/live/.config/emaki', 'etc', 'root'):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / 'etc/shadow').write_text('root:!:::::::\n')
        (profile / 'profiledef.sh').write_text("declare -A file_permissions=()\nbootmodes=('bios.syslinux' 'uefi.grub' 'uefi.systemd-boot')\n")
        entries = {
            'efiboot/loader/entries/live.conf': 'options archisobasedir=%INSTALL_DIR% archisosearchuuid=%ARCHISO_UUID%\n',
            'grub/grub.cfg': '    linux /%INSTALL_DIR%/boot/vmlinuz-linux archisobasedir=%INSTALL_DIR% archisosearchuuid=%ARCHISO_UUID%\n',
            'syslinux/archiso_sys-linux.cfg': 'APPEND archisobasedir=%INSTALL_DIR% archisosearchuuid=%ARCHISO_UUID%\n',
        }
        for name, content in entries.items():
            path = profile / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return profile, entries

    def test_test_key_permissions_and_all_loaders(self):
        with tempfile.TemporaryDirectory(prefix='emaki-iso-static-') as directory:
            profile, entries = self.make_profile(directory)
            key = Path(directory) / 'test.pub'
            key.write_text('ssh-ed25519 TEST fixture\n')
            prepare.prepare(profile, '/offline', '0.1.0', key)
            for name in entries:
                self.assertEqual((profile / name).read_text().count('emaki.test=1'), 1)
            definition = (profile / 'profiledef.sh').read_text()
            self.assertIn('["/home/live/.ssh"]="1000:1000:0700"', definition)
            self.assertIn('["/etc/emaki-test/authorized_keys"]="0:0:0600"', definition)
            self.assertNotIn('[core]', (profile / 'pacman.conf').read_text())
            subprocess.run(['bash', '-n', str(profile / 'profiledef.sh')], check=True)
            with self.assertRaisesRegex(ValueError, 'test key material'):
                prepare.prepare(profile, '/offline', '0.1.0')

    def test_release_rejects_test_command_line(self):
        with tempfile.TemporaryDirectory(prefix='emaki-iso-static-') as directory:
            profile, _ = self.make_profile(directory)
            path = profile / 'grub/grub.cfg'
            path.write_text(path.read_text().rstrip() + ' emaki.test=1\n')
            with self.assertRaisesRegex(ValueError, 'test kernel argument'):
                prepare.prepare(profile, '/offline', '0.1.0')

    def test_release_has_no_test_paths(self):
        with tempfile.TemporaryDirectory(prefix='emaki-iso-static-') as directory:
            profile, entries = self.make_profile(directory)
            prepare.prepare(profile, '/offline', '0.1.0')
            for name in entries:
                self.assertNotIn('emaki.test=1', (profile / name).read_text())
            self.assertFalse((profile / 'airootfs/etc/emaki-test').exists())
            self.assertFalse((profile / 'airootfs/home/live/.ssh').exists())

    def test_import_retains_loaders_and_removes_releng_login(self):
        with tempfile.TemporaryDirectory(prefix='emaki-iso-static-') as directory:
            source = Path(directory) / 'releng'
            for name in ('grub', 'syslinux', 'airootfs/root',
                         'airootfs/etc/systemd/system/getty@tty1.service.d',
                         'airootfs/etc/systemd/system/multi-user.target.wants'):
                (source / name).mkdir(parents=True, exist_ok=True)
            (source / 'profiledef.sh').write_text(
                "bootmodes=('bios.syslinux' 'uefi.grub')\nfile_permissions=(\n"
                '  ["/usr/local/bin/choose-mirror"]="0:0:755"\n'
                '  ["/usr/local/bin/Installation_guide"]="0:0:755"\n)\n')
            for relative in RELENG_LEFTOVERS:
                (source / 'airootfs' / relative).parent.mkdir(parents=True, exist_ok=True)
                (source / 'airootfs' / relative).write_text('releng\n')
            (source / 'packages.x86_64').write_text('linux\niwd\nopenssh\n')
            (source / 'pacman.conf').write_text('[options]\n')
            (source / 'grub/grub.cfg').write_text('menuentry "Arch Linux" {\n linux /%INSTALL_DIR%/boot/vmlinuz-linux archisosearchuuid=%ARCHISO_UUID%\n}\n')
            (source / 'syslinux/archiso.cfg').write_text('APPEND archisobasedir=%INSTALL_DIR%\n')
            (source / 'airootfs/root/.zlogin').write_text('automatic root shell\n')
            (source / 'airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf').write_text('root\n')
            (source / 'airootfs/etc/systemd/system/multi-user.target.wants/sshd.service').symlink_to('/usr/lib/systemd/system/sshd.service')
            wait = source / 'airootfs/etc/systemd/system/sysinit.target.wants/systemd-time-wait-sync.service'
            wait.parent.mkdir()
            wait.symlink_to('/usr/lib/systemd/system/systemd-time-wait-sync.service')
            destination = Path(directory) / 'complete'
            importer.import_profile(source, destination)
            self.assertFalse((destination / 'airootfs/root/.zlogin').exists())
            self.assertFalse((destination / 'airootfs/etc/systemd/system/getty@tty1.service.d/autologin.conf').exists())
            for relative in RELENG_LEFTOVERS:
                self.assertFalse((destination / 'airootfs' / relative).exists(), relative)
            self.assertIsNone(re.search(r'choose-mirror|Installation_guide|20-.*\.network',
                                        (destination / 'profiledef.sh').read_text()))
            self.assertIn('%ARCHISO_UUID%', (destination / 'grub/grub.cfg').read_text())
            version = (HERE / 'VERSION').read_text().strip()
            self.assertIn('Emaki ' + version, (destination / 'grub/grub.cfg').read_text())
            packages = (destination / 'packages.x86_64').read_text().splitlines()
            self.assertEqual(packages, sorted(set(packages)))
            self.assertNotIn('iwd', packages)
            self.assertFalse((destination / wait.relative_to(source)).is_symlink())
            image_check.check_loaders(destination)
            self.assertIn('linux', packages)
            self.assertEqual((destination / 'airootfs/etc/systemd/system/greetd.service.d/emaki.conf').readlink(), Path('/dev/null'))
            subprocess.run(['bash', '-c', 'declare -A file_permissions=(); source "$1"; [[ $iso_label == "EMAKI_$2" && ${bootmodes[*]} == "bios.syslinux uefi.grub" ]]',
                            '_', str(destination / 'profiledef.sh'), version], check=True)

    def test_embedded_package_signature_materialization(self):
        with tempfile.TemporaryDirectory(prefix='emaki-iso-static-') as directory:
            cache = Path(directory) / 'offline'
            sync = Path(directory) / 'db/sync'
            cache.mkdir()
            sync.mkdir(parents=True)
            package = cache / 'example-1-1-any.pkg.tar.zst'
            package.write_bytes(b'package')
            signature = b'detached-signature-fixture'
            desc = f'%NAME%\nexample\n\n%FILENAME%\n{package.name}\n\n%PGPSIG%\n{base64.b64encode(signature).decode()}\n\n'.encode()
            with tarfile.open(sync / 'extra.db', 'w:gz') as archive:
                member = tarfile.TarInfo('example-1-1/desc')
                member.size = len(desc)
                archive.addfile(member, io.BytesIO(desc))
            with patch.object(repo.subprocess, 'run') as verify:
                repo.signatures(cache, sync.parent)
                verify.assert_called_once()
            self.assertEqual(Path(str(package) + '.sig').read_bytes(), signature)

    def test_every_vendored_loader_retains_usb_repository(self):
        image_check.check_loaders(HERE / 'profile')
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile'
            import shutil
            shutil.copytree(HERE / 'profile', profile, symlinks=True)
            prepare.prepare(profile, '/offline', '0.1.0')
            # Model mkarchiso v91 substitution and check the generated entries.
            for pattern in ('efiboot/loader/entries/*.conf', 'grub/*.cfg', 'syslinux/*.cfg'):
                for path in profile.glob(pattern):
                    text = path.read_text().replace('%KERNEL_PARAMS%', '').replace('%INSTALL_DIR%', 'emaki')
                    path.write_text(text)
            image_check.check_loaders(profile)

    def test_offline_installer_start_chain_has_no_wait_service(self):
        root = HERE / 'profile/airootfs'
        for path in (root / 'etc/systemd/system').rglob('*'):
            if path.is_symlink() and path.readlink() != Path('/dev/null'):
                self.assertNotRegex(path.name, r'(?:wait-online|time-wait-sync)')
            elif path.is_file():
                for line in path.read_text().splitlines():
                    if re.match(r'^(?:Requires|Wants|After)=', line):
                        self.assertNotRegex(line, r'(?:wait-online|time-wait-sync)')
        for path in (root / 'etc/systemd/system-preset').glob('*'):
            self.assertNotRegex(path.read_text(), r'(?m)^enable .*?(?:wait-online|time-wait-sync)')

    def test_mastered_loader_guard_rejects_test_and_missing_copytoram(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / 'live.conf'
            for params in ('', 'copytoram=y', 'copytoram=n emaki.test=1', 'copytoram=n copytoram=y'):
                path.write_text('options archisobasedir=emaki ' + params + '\n')
                with self.assertRaises(ValueError):
                    image_check.check_loaders(directory)
            path.write_text('options archisobasedir=emaki copytoram=n\n')
            image_check.check_loaders(directory)
            with self.assertRaises(ValueError):
                image_check.check_loaders(directory, True)

    def test_mastered_squashfs_guard_rejects_both_test_paths(self):
        clean = 'squashfs-root\nsquashfs-root/etc\nsquashfs-root/home/live\n'
        image_check.check_root_listing(clean)
        for name in ('home/live/.ssh', 'etc/emaki-test'):
            with self.assertRaises(ValueError):
                image_check.check_root_listing(clean + 'squashfs-root/' + name + '\n')
        with self.assertRaises(ValueError):
            image_check.check_root_listing('')
        image_check.check_root_listing(clean + 'squashfs-root/home/live/.ssh\nsquashfs-root/etc/emaki-test\n', True)

    def test_mastered_image_tools_extract_actual_efi_partition(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'xorriso':
                for index, word in enumerate(argv):
                    if word == '-extract':
                        source, destination = argv[index + 1:index + 3]
                        destination = Path(destination)
                        if source in ('/boot', '/loader'):
                            destination.mkdir()
                            (destination / 'live.conf').write_text('options archisobasedir=emaki copytoram=n\n')
                        else:
                            destination.write_bytes(b'image')
                images = Path(argv[-1])
                images.mkdir()
                (images / 'eltorito_img2_uefi.img').touch()
            elif argv[0] == 'mcopy':
                (Path(argv[-1]) / 'live.conf').write_text('options archisobasedir=emaki copytoram=n\n')
            return subprocess.CompletedProcess(argv, 0, stdout='squashfs-root\nsquashfs-root/etc\n')
        with tempfile.TemporaryDirectory() as temporary, patch.object(image_check.subprocess, 'run', side_effect=run):
            image_check.verify(Path(temporary) / 'test.iso')
        self.assertEqual([c[0] for c in calls], ['xorriso', 'mcopy', 'unsquashfs'])
        self.assertIn('-extract_boot_images', calls[0])
        self.assertEqual(calls[-1][1], '-l')

    def test_builder_window_requirement_is_release_only(self):
        text = (HERE / 'build.sh').read_text()
        block = text[text.index('if [[ ! -f $work/mk/x86_64/airootfs/usr/bin/emaki-install'):]
        block = block[:block.index('\nfi') + 3]
        with tempfile.TemporaryDirectory() as temporary:
            for test_mode, status in ((0, 1), (1, 0)):
                result = subprocess.run(['bash', '-eu', '-c',
                                         'work=$1; test_mode=$2; fail() { echo "$*" >&2; exit 1; };\n' + block,
                                         '_', temporary, str(test_mode)], capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn('WARNING' if test_mode else 'required live file missing', result.stderr)

    def test_journal_error_allowlist_is_narrow(self):
        spec = importlib.util.spec_from_file_location('boot_check', HERE.parent / 'tests/vm/iso-boot-check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(module.journal_errors_ok('-- No entries --\n'))
            self.assertTrue(module.journal_errors_ok('i8042: PNP: No PS/2 controller found.\n'))
            for line in ('virt/tdx: TDX not supported by the host platform',
                         "gkr-pam: couldn't unlock the login keyring."):
                self.assertTrue(module.journal_errors_ok(line + '\n'))
                self.assertFalse(module.journal_errors_ok(line + ' unexpected\n'))
                self.assertFalse(module.journal_errors_ok('prefix ' + line + '\n'))
                self.assertFalse(module.journal_errors_ok(line + '\nI/O error\n'))
            self.assertFalse(module.journal_errors_ok('Failed to start greetd.service\n'))
            self.assertFalse(module.journal_errors_ok('i8042: PNP: No PS/2 controller found.\nI/O error\n'))

    def test_builder_clears_stale_images_and_rejects_shared_output(self):
        text = (HERE / 'build.sh').read_text()
        block = text[text.index('exec 8>'):text.index('if [[ -f $work/.emaki-mode')]
        # The removal itself runs after the input checks; append it to the output block.
        block += text[text.index('# A failed rebuild must never'):text.index('# Keep downloads across')]
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            for name in ('emaki-old.iso', 'emaki-old.iso.sha256'):
                (out / name).touch()
            script = 'out=$1; mode=$2; fail() { echo "$*" >&2; exit 1; };\n' + block
            result = subprocess.run(['bash', '-eu', '-c', script, '_', temporary, 'test'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(out.glob('emaki-*.iso*')), [])
            self.assertEqual((out / '.emaki-mode').read_text(), 'test\n')
            result = subprocess.run(['bash', '-eu', '-c', script, '_', temporary, 'release'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
