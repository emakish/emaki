#!/usr/bin/env python3
"""Offline regression checks: no package manager, service, or QEMU invocation."""
import base64
import contextlib
import importlib.util
import io
import os
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
grub_modules = load('check-grub-modules')
# releng's mirror chooser, install guide and networkd units: the live session uses NetworkManager.
RELENG_LEFTOVERS = ('usr/local/bin/choose-mirror', 'usr/local/bin/Installation_guide',
                    'etc/systemd/network/20-ethernet.network', 'etc/systemd/network/20-wlan.network',
                    'etc/systemd/network/20-wwan.network')


class StaticTests(unittest.TestCase):
    def test_idle_available_in_live_and_target_images(self):
        for name in ('packages-extra.txt', 'profile/packages.x86_64', 'target-packages.txt'):
            self.assertIn('swayidle', (HERE / name).read_text().split())

    def test_cache_pruning_keeps_full_current_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            keep = ['target-2-1-any.pkg.tar.zst', 'live-1-1-any.pkg.tar.zst']
            stale = ['target-1-1-any.pkg.tar.zst', 'removed-1-1-any.pkg.tar.zst']
            for name in keep + stale:
                (cache / name).write_bytes(b'package')
                (cache / (name + '.sig')).write_bytes(b'signature')
            (cache / 'download.part').write_bytes(b'partial')
            manifest = cache / 'closure.txt'
            manifest.write_text(''.join(name + '\n' for name in keep))
            repo.prune(cache, manifest)
            self.assertEqual({p.name for p in cache.glob('*.pkg.tar.zst*')},
                             set(keep + [name + '.sig' for name in keep]))
            self.assertTrue((cache / 'download.part').exists())
            manifest.write_text('missing-1-1-any.pkg.tar.zst\n')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                repo.prune(cache, manifest)
            self.assertTrue((cache / keep[0]).exists())
        builder = (HERE / 'build.sh').read_text()
        self.assertIn('prune "$work/offline" "$work/closure.txt"', builder)

    def test_grub_helper_license_headers(self):
        for name in ('check-grub-modules.py', 'test-efi-image.py'):
            with self.subTest(name=name):
                lines = (HERE / name).read_text().splitlines()
                self.assertEqual(lines[1], '# Copyright (C) 2026 Artur Yakymenko')
                self.assertEqual(lines[2], '# SPDX-License-Identifier: GPL-3.0-or-later')

    def test_loopback_background_options_require_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            boot = Path(temporary)
            grub = boot / 'grub'
            grub.mkdir()
            background = grub / 'background.png'
            for option in ('', '-m stretch ', '-m normal ', '-mstretch ',
                           '--mode stretch ', '--mode normal ', '--mode=stretch '):
                with self.subTest(option=option):
                    (grub / 'loopback.cfg').write_text(
                        f'background_image {option}/boot/grub/background.png\n'
                        'linux /vmlinuz-linux archisobasedir=emaki copytoram=n\n')
                    background.unlink(missing_ok=True)
                    with self.assertRaisesRegex(ValueError, 'missing or empty loopback background'):
                        image_check.check_loaders(boot, check_assets=True)
                    background.touch()
                    with self.assertRaisesRegex(ValueError, 'missing or empty loopback background'):
                        image_check.check_loaders(boot, check_assets=True)
                    background.write_bytes(b'asset')
                    image_check.check_loaders(boot, check_assets=True)

    def test_module_gate_skips_only_missing_default_directory(self):
        text = (HERE / 'check.sh').read_text()
        block = text[text.index('modules='):text.index('python3 -B "$HERE/test-static.py"')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / 'missing'
            modules = root / 'modules'
            modules.mkdir()
            for config in (HERE / 'profile/grub').glob('*.cfg'):
                for name in re.findall(r'^\s*insmod\s+(\w+)', config.read_text(), re.M):
                    (modules / f'{name}.mod').write_bytes(b'module')
            script = 'HERE=$1; incomplete=0;\n' + block.replace(
                '/usr/lib/grub/x86_64-efi', str(missing)) + '\nexit "$incomplete"'
            env = dict(os.environ)
            env.pop('GRUB_MODULE_DIR', None)
            for value, status in ((None, 0), (str(missing), 1), ('', 1),
                                  (str(modules), 0)):
                with self.subTest(value=value):
                    case_env = dict(env)
                    if value is not None:
                        case_env['GRUB_MODULE_DIR'] = value
                    result = subprocess.run(['bash', '-eu', '-c', script, '_', str(HERE)],
                                            env=case_env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    if value is None:
                        self.assertIn('SKIP:', result.stdout)
                        self.assertIn(str(missing), result.stdout)
                    elif status:
                        self.assertIn('GRUB_MODULE_DIR', result.stdout + result.stderr)
                    else:
                        self.assertIn('profile insmod commands exist', result.stdout)

    def test_grub_modules_match_available_build_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'grub'
            modules = root / 'x86_64-efi'
            profile.mkdir()
            modules.mkdir()
            (modules / 'efi_gop.mod').write_bytes(b'module')
            config = profile / 'grub.cfg'
            config.write_text('insmod efi_gop\n# insmod nonexistent\n')
            self.assertEqual(grub_modules.check(profile, modules), 1)
            # The former UGA request must fail against a module set without it.
            loopback = profile / 'loopback.cfg'
            loopback.write_text('insmod efi_uga\n')
            with self.assertRaisesRegex(ValueError, 'loopback.cfg: missing GRUB module: efi_uga.mod'):
                grub_modules.check(profile, modules)
            loopback.write_text('insmod "efi_gop"; insmod missing\n')
            with self.assertRaisesRegex(ValueError, 'missing.mod'):
                grub_modules.check(profile, modules)
            loopback.write_text('insmod ${video_module}\n')
            with self.assertRaisesRegex(ValueError, 'literal module name'):
                grub_modules.check(profile, modules)
            loopback.unlink()
            (modules / 'efi_gop.mod').unlink()
            with self.assertRaisesRegex(ValueError, 'no installed GRUB modules'):
                grub_modules.check(profile, modules)

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
            self.assertIn('set gfxmode=auto', grub)
            self.assertIn('if terminal_output gfxterm; then', grub)
            self.assertIn('terminal_output console', grub)
            self.assertIn('insmod efi_gop', grub)
            self.assertNotIn('insmod efi_uga', grub)
            self.assertIn('loadfont /boot/grub/menu.pf2', grub)
            self.assertIn('set gfxterm_font="DejaVu Sans Mono Regular 30"', grub)
            self.assertIn('background_image --mode stretch /boot/grub/background.png', grub)
            self.assertNotIn('accessibility=on', grub)
        self.assertIn('"$ROOT/art/grub/background.png" "$work/profile/grub/background.png"',
                      (HERE / 'build.sh').read_text())
        self.assertIn('MENU RESOLUTION 640 480', (HERE / 'profile/syslinux/archiso_head.cfg').read_text())

    def test_public_allowlist_has_no_deleted_speech_service(self):
        self.assertFalse(any('livecd-talk.service' in line for line in
                             (HERE.parent / 'scripts/public/allow.txt').read_text().splitlines()))

    def test_live_boot_paths_and_no_speech(self):
        result = subprocess.run(['bash', '-c', 'declare -A file_permissions=(); source "$1"; printf "%s\\n" "${bootmodes[@]}"',
                                 '_', str(HERE / 'profile/profiledef.sh')], check=True, text=True, capture_output=True)
        self.assertEqual(result.stdout.splitlines(), ['bios.syslinux', 'uefi.grub'])
        self.assertFalse((HERE / 'profile/efiboot').exists())
        self.assertFalse((HERE / 'profile/airootfs/etc/systemd/system/livecd-talk.service').exists())
        self.assertFalse((HERE / 'profile/airootfs/etc/systemd/system/livecd-alsa-unmuter.service').exists())
        self.assertFalse((HERE / 'profile/airootfs/usr/local/bin/livecd-sound').exists())
        for architecture in ('x86_64', 'aarch64'):
            self.assertFalse({'espeakup', 'livecd-sounds'} & set((HERE / f'profile/packages.{architecture}').read_text().split()))
        for path in (HERE / 'profile/syslinux').glob('*.cfg'):
            self.assertNotIn('accessibility=on', path.read_text())

    def test_menu_font_cap_height_and_title_fit(self):
        import math
        import struct
        font_tools = runpy.run_path(str(HERE.parent / 'installer/assets/grub/subset-pf2.py'))
        data = (HERE / 'profile/grub/menu.pf2').read_bytes()
        fields = dict(font_tools['read_sections'](data))
        glyphs = font_tools['glyphs'](data, fields['CHIX'])
        for char in 'EMH':
            _, height, _, _, width = struct.unpack('>HHhhh', glyphs[ord(char)][:10])
            self.assertGreaterEqual(height * 13.3 * 25.4 / math.hypot(2560, 1600), 2.4)
            self.assertEqual(width, 18)
        # Stock gfxterm reserves a ten-pixel border and menu decorations.
        available = (1024 - 20) // 18 - 8
        for path in (HERE / 'profile/grub').glob('*.cfg'):
            for title in re.findall(r'^menuentry "([^"]+)"', path.read_text(), re.M):
                title = title.replace('%ARCH%', 'x86_64').replace('${archiso_platform}', 'UEFI')
                self.assertLessEqual(len(title), available, title)

    def test_both_live_kernels_have_payload_packages_and_complete_boot_entries(self):
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        target = set((HERE / 'target-packages.txt').read_text().split())
        live = set((HERE / 'profile/packages.x86_64').read_text().split())
        self.assertIn('linux-lts', (HERE / 'packages-extra.txt').read_text().split())
        for kernel in ('linux', 'linux-lts'):
            self.assertIn(kernel, constants['PACKAGES'])
            self.assertIn(kernel, target)
            self.assertIn(kernel, live)
        common = ['archisobasedir=%INSTALL_DIR%', '%KERNEL_PARAMS%', 'copytoram=n']
        for name in ('grub.cfg', 'loopback.cfg'):
            text = (HERE / 'profile/grub' / name).read_text()
            lines = [line.split() for line in text.splitlines() if line.lstrip().startswith('linux /%INSTALL_DIR%/')]
            self.assertEqual(len(lines), 3)
            for kernel, line in zip(('linux', 'linux-lts'), lines[:2]):
                self.assertEqual(line[1], f'/%INSTALL_DIR%/boot/%ARCH%/vmlinuz-{kernel}')
                location = (['archisosearchuuid=%ARCHISO_UUID%'] if name == 'grub.cfg' else
                            ['img_dev=UUID=${archiso_img_dev_uuid}', 'img_loop="${iso_path}"'])
                self.assertEqual(line[2:], [common[0], *location, *common[1:]])
            self.assertEqual(lines[2], [*lines[0], 'nomodeset'])
            self.assertEqual(re.findall(r'^\s*initrd (.+)$', text, re.M),
                             [f'/%INSTALL_DIR%/boot/%ARCH%/initramfs-{kernel}.img'
                              for kernel in ('linux', 'linux-lts', 'linux')])
        syslinux = (HERE / 'profile/syslinux/archiso_sys-linux.cfg').read_text()
        self.assertEqual(re.findall(r'^LINUX (.+)$', syslinux, re.M),
                         [f'/%INSTALL_DIR%/boot/%ARCH%/vmlinuz-{kernel}'
                          for kernel in ('linux', 'linux-lts', 'linux')])
        self.assertEqual(re.findall(r'^INITRD (.+)$', syslinux, re.M),
                         [f'/%INSTALL_DIR%/boot/%ARCH%/initramfs-{kernel}.img'
                          for kernel in ('linux', 'linux-lts', 'linux')])
        args = re.findall(r'^APPEND (.+)$', syslinux, re.M)
        self.assertEqual(args, [args[0], args[0], args[0] + ' nomodeset'])

    def test_safe_graphics_preserves_every_network_transport(self):
        text = (HERE / 'profile/syslinux/archiso_pxe-linux.cfg').read_text()
        blocks = {re.search(r'^LABEL (\S+)', block)[1]: block
                  for block in re.split(r'(?=^LABEL )', text, flags=re.M) if block.strip()}
        self.assertEqual(len(blocks), 6)
        for protocol in ('nbd', 'nfs', 'http'):
            normal = blocks[f'arch_{protocol}']
            safe = blocks[f'arch_{protocol}_safe']
            for directive, operand in (
                    ('LINUX', '::/%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux'),
                    ('INITRD', '::/%INSTALL_DIR%/boot/%ARCH%/initramfs-linux.img'),
                    ('SYSAPPEND', '3')):
                for block in (normal, safe):
                    self.assertEqual(re.findall(rf'^{directive} (.+)$', block, re.M), [operand])
            original = re.search(r'^APPEND (.+)$', normal, re.M)[1]
            self.assertIn(f'archiso_{protocol}_srv=', original)
            self.assertEqual(re.search(r'^APPEND (.+)$', safe, re.M)[1], original + ' nomodeset')

    def test_safe_graphics_guard_rejects_missing_or_misplaced_argument(self):
        for relative in ('grub/grub.cfg', 'grub/loopback.cfg',
                         'syslinux/archiso_sys-linux.cfg', 'syslinux/archiso_pxe-linux.cfg'):
            path = HERE / 'profile' / relative
            text = path.read_text()
            self.assertIn("basic display, text console", text)
            image_check.check_graphics_entries(text, path, require_safe=True)
            for broken in (text.replace(' nomodeset', ''),
                           text.replace('copytoram=n', 'copytoram=n nomodeset', 1),
                           text.replace('copytoram=n nomodeset', 'copytoram=n nomodeset nomodeset')):
                with self.subTest(path=relative), self.assertRaisesRegex(ValueError, 'nomodeset'):
                    image_check.check_graphics_entries(broken, path, require_safe=True)
            with self.assertRaisesRegex(ValueError, 'safe graphics'):
                image_check.check_graphics_entries('', path, require_safe=True)

    def test_test_packages_in_offline_closure_seeds_not_release_transaction(self):
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        seeds = (HERE / 'target-packages.txt').read_text().splitlines()
        live = (HERE / 'profile/packages.x86_64').read_text().splitlines()
        for name in constants['TEST_PACKAGES']:
            self.assertIn(name, seeds)
            if name == 'grim':
                self.assertNotIn(name, live)
            else:
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
        self.assertTrue({'linux-firmware', 'mesa', 'vulkan-nouveau'} <= hardware)
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
        (profile / 'profiledef.sh').write_text("declare -A file_permissions=()\nbootmodes=('bios.syslinux' 'uefi.grub')\n")
        entries = {
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
                "bootmodes=('bios.syslinux' 'uefi.systemd-boot')\nfile_permissions=(\n"
                '  ["/usr/local/bin/choose-mirror"]="0:0:755"\n'
                '  ["/usr/local/bin/Installation_guide"]="0:0:755"\n)\n')
            for relative in RELENG_LEFTOVERS:
                (source / 'airootfs' / relative).parent.mkdir(parents=True, exist_ok=True)
                (source / 'airootfs' / relative).write_text('releng\n')
            (source / 'packages.x86_64').write_text('linux\niwd\nopenssh\nespeakup\nlivecd-sounds\n')
            old_loader = source / 'efiboot/loader/entries/live.conf'
            old_loader.parent.mkdir(parents=True)
            old_loader.write_text('title Old loader\n')
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
            self.assertFalse({'espeakup', 'livecd-sounds'} & set(packages))
            self.assertFalse((destination / 'efiboot').exists())
            self.assertFalse((destination / wait.relative_to(source)).is_symlink())
            image_check.check_loaders(destination)
            self.assertIn('linux', packages)
            self.assertIn('linux-lts', packages)
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
        image_check.check_loaders(HERE / 'profile', require_safe=True)
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile'
            import shutil
            shutil.copytree(HERE / 'profile', profile, symlinks=True)
            prepare.prepare(profile, '/offline', '0.1.0')
            # Model mkarchiso v91 substitution and check the generated entries.
            for pattern in ('grub/*.cfg', 'syslinux/*.cfg'):
                for path in profile.glob(pattern):
                    text = path.read_text().replace('%KERNEL_PARAMS%', '').replace('%INSTALL_DIR%', 'emaki')
                    path.write_text(text)
            image_check.check_loaders(profile, require_safe=True)

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
            path = directory / 'live.cfg'
            for params in ('', 'copytoram=y', 'copytoram=n emaki.test=1', 'copytoram=n copytoram=y'):
                path.write_text('linux /emaki/boot/x86_64/vmlinuz-linux archisobasedir=emaki ' + params + '\n')
                with self.assertRaises(ValueError):
                    image_check.check_loaders(directory)
            path.write_text('linux /emaki/boot/x86_64/vmlinuz-linux archisobasedir=emaki copytoram=n\n')
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

    def test_frozen_image_hygiene_uses_strict_internal_version(self):
        for version in ('0.1.0', '0.2.0'):
            self.assertFalse(image_check.requires_hygiene(version))
        for version in ('0.2.1', '0.3.0', '1.0.0'):
            self.assertTrue(image_check.requires_hygiene(version))
        for version in ('', '0.2', '0.2.0-test', '0.2.0\n0.3.0'):
            with self.assertRaisesRegex(ValueError, 'invalid mastered image version'):
                image_check.requires_hygiene(version)
        clean = 'squashfs-root\nsquashfs-root/etc\n'
        for leftover in ('squashfs-root/usr/include/legacy.h\n',
                         'squashfs-root/etc/systemd/system/legacy.service -> missing.service\n'):
            image_check.check_root_listing(clean + leftover, check_hygiene=False)
            with self.assertRaises(ValueError):
                image_check.check_root_listing(clean + leftover)
        for material in ('home/live/.ssh', 'etc/emaki-test'):
            with self.assertRaisesRegex(ValueError, 'test material'):
                image_check.check_root_listing(clean + 'squashfs-root/' + material + '\n',
                                               check_hygiene=False)

    def test_mastered_image_tools_extract_actual_efi_partition(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'xorriso':
                if '-find' in argv:
                    paths = ['/EFI/BOOT/BOOTX64.EFI', '/boot/grub/grub.cfg',
                             '/boot/memtest86+/memtest.efi', '/boot/memtest86+/memtest']
                    paths += [f'/emaki/boot/x86_64/{name}' for name in
                              ('vmlinuz-linux', 'vmlinuz-linux-lts',
                               'initramfs-linux.img', 'initramfs-linux-lts.img')]
                    return subprocess.CompletedProcess(argv, 0, stdout='\n'.join(paths))
                for index, word in enumerate(argv):
                    if word == '-extract':
                        source, destination = argv[index + 1:index + 3]
                        destination = Path(destination)
                        if source == '/boot':
                            destination.mkdir()
                            (destination / 'live.cfg').write_text('linux /emaki/boot/x86_64/vmlinuz-linux archisobasedir=emaki copytoram=n\n')
                            grub = destination / 'grub'
                            grub.mkdir()
                            for name in ('grub.cfg', 'loopback.cfg'):
                                text = (HERE / 'profile/grub' / name).read_text()
                                text = text.replace('%INSTALL_DIR%', 'emaki').replace('%ARCH%', 'x86_64')
                                (grub / name).write_text(text)
                            for name in ('menu.pf2', 'background.png'):
                                (grub / name).write_bytes(b'asset')
                        elif source == '/emaki/version':
                            destination.write_text('0.3.0\n')
                        else:
                            destination.write_bytes(b'image')
                images = Path(argv[-1])
                images.mkdir()
                (images / 'eltorito_img2_uefi.img').touch()
            elif argv[0] == 'mdir':
                return subprocess.CompletedProcess(argv, 0, stdout='::/EFI/BOOT/BOOTX64.EFI\n')
            return subprocess.CompletedProcess(argv, 0, stdout='squashfs-root\nsquashfs-root/etc\n')
        with tempfile.TemporaryDirectory() as temporary, patch.object(image_check.subprocess, 'run', side_effect=run):
            image_check.verify(Path(temporary) / 'test.iso')
        self.assertEqual([c[0] for c in calls], ['xorriso', 'xorriso', 'mdir', 'unsquashfs'])
        self.assertIn('-extract_boot_images', calls[1])
        self.assertEqual(calls[-1][1], '-ll')

    def test_builder_asserts_generated_hookdir_after_pass_one(self):
        builder = (HERE / 'build.sh').read_text()
        start = builder.index('hookdirs=$(grep')
        end = builder.index("\n", builder.index("|| fail 'archiso v91 HookDir changed'", start))
        block = builder[start:end]
        first_pass = builder.index('mkarchiso -v -w')
        self.assertLess(first_pass, start)
        self.assertLess(end, builder.index('mkarchiso -v -w', first_pass + 1))
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'mk').mkdir()
            config = work / 'mk/iso.pacman.conf'
            expected = f'HookDir = {work}/mk/x86_64/airootfs/etc/pacman.d/hooks/'
            cases = ((expected, 0), ('', 1), ('HookDir = /etc/pacman.d/hooks/', 1),
                     (expected + '\n' + expected, 1),
                     (expected + '\n  HookDir = /tmp/extra', 1),
                     (expected + ' /tmp/extra', 1), (expected.rstrip('/'), 1))
            for content, status in cases:
                with self.subTest(content=content):
                    config.write_text('[options]\n' + content + '\n')
                    result = subprocess.run(
                        ['bash', '-eu', '-c',
                         'work=$1; fail() { echo "$*" >&2; exit 1; };\n' + block,
                         '_', temporary], capture_output=True, text=True)
                    self.assertEqual(result.returncode, status, result.stderr)
            config.unlink()
            result = subprocess.run(
                ['bash', '-eu', '-c',
                 'work=$1; fail() { exit 1; };\n' + block, '_', temporary],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

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
                         "Ignoring duplicate name 'org.freedesktop.secrets' in service file '/usr/share/dbus-1/services/org.freedesktop.secrets.service'",
                         "Ignoring duplicate name 'org.freedesktop.impl.portal.desktop.kwallet' in service file '/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.kwallet.service'",
                         "Ignoring duplicate name 'org.kde.secretservicecompat' in service file '/usr/share/dbus-1/services/org.kde.secretservicecompat.service'",
                         '   emaki : a password is required ; PWD=/home/emaki ; USER=root ; COMMAND=/usr/bin/true'):
                self.assertTrue(module.journal_errors_ok(line + '\n'))
                self.assertFalse(module.journal_errors_ok(line + ' unexpected\n'))
                self.assertFalse(module.journal_errors_ok('prefix ' + line + '\n'))
                self.assertFalse(module.journal_errors_ok(line + '\nI/O error\n'))
            # Only the exact names emaki-wallet-start shadows, and only the denial of
            # the probe command for the account's own home directory.
            self.assertFalse(module.journal_errors_ok(
                "Ignoring duplicate name 'org.gnome.keyring' in service file '/usr/share/dbus-1/services/org.gnome.keyring.service'\n"))
            for line in ('   emaki : a password is required ; PWD=/home/emaki ; USER=root ; COMMAND=/usr/bin/pacman',
                         '   emaki : a password is required ; PWD=/home/other ; USER=root ; COMMAND=/usr/bin/true',
                         '   emaki : 3 incorrect password attempts ; PWD=/home/emaki ; USER=root ; COMMAND=/usr/bin/true'):
                self.assertFalse(module.journal_errors_ok(line + '\n'))
            self.assertFalse(module.journal_errors_ok("gkr-pam: couldn't unlock the login keyring.\n"))
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
