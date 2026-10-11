#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check external-GRUB assets without building or mounting an ISO."""
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('image_check', HERE / 'verify-image.py')
image_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_check)
spec = importlib.util.spec_from_file_location('prepare', HERE / 'prepare-profile.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class GeneratedMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        key = root / 'test.pub'
        key.write_text('ssh-ed25519 fixture\n')
        cls.configs = {}
        for test in (False, True):
            profile = root / ('test' if test else 'release')
            shutil.copytree(HERE / 'profile', profile, symlinks=True)
            prepare.prepare(profile, '/offline', '0.6.0', key if test else None)
            for name in ('grub.cfg', 'loopback.cfg'):
                text = (profile / 'grub' / name).read_text()
                # Apply the configuration substitutions from mkarchiso 91.
                for token, value in {'ARCHISO_LABEL': 'EMAKI_0.6.0',
                                     'ARCHISO_UUID': '2026-10-10-00-00-00-00',
                                     'INSTALL_DIR': 'emaki', 'KERNEL_PARAMS': '',
                                     'ARCH': 'x86_64', 'ARCHISO_SEARCH_FILENAME': ''}.items():
                    text = text.replace('%' + token + '%', value)
                cls.configs[test, name] = text

    def setup_trace(self, text, version=None, platform='efi', font_status=0, gfx_status=0):
        """Execute the setup's shell-compatible control flow with GRUB I/O stubbed.

        The real parser test checks the complete config separately. Neither test
        substitutes for a menu screenshot or a boot of the mastered candidate ISO.
        """
        setup = text.split('# Menu entries')[0]
        setup = re.sub(r'(?m)^(\s*)set (\w+)=(.*)$', r'\1\2=\3', setup)
        stubs = '''
insmod() { echo "insmod $*"; }
terminal_input() { echo "terminal_input $*"; }
terminal_output() {
    echo "terminal_output $*"
    if [ "$1" = gfxterm ]; then return "$gfx_status"; fi
}
loadfont() { echo "loadfont $*"; return "$font_status"; }
background_image() { echo "background_image $*"; }
serial() { return 0; }
search() { return 0; }
probe() { return 0; }
theme=inherited-theme
gfxterm_font=inherited-font
'''
        env = {'PATH': os.environ['PATH'], 'grub_platform': platform, 'grub_cpu': 'x86_64',
               'font_status': str(font_status), 'gfx_status': str(gfx_status)}
        if version is not None:
            env['VENTOY_VERSION'] = version
        result = subprocess.run(['bash', '-c', stubs + setup + '''
printf 'state:%s|%s|%s|%s|%s\\n' "$theme" "$gfxterm_font" "$default" "$timeout" "$archiso_platform"
'''], env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, '')
        return result.stdout.splitlines()

    def test_ventoy_skips_graphics_and_retains_menu_defaults(self):
        for (test, name), text in self.configs.items():
            for platform in ('efi', 'pc'):
                for version in ('1.1.18', 'future-version'):
                    with self.subTest(test=test, name=name, platform=platform, version=version):
                        trace = self.setup_trace(text, version, platform)
                        self.assertIn('terminal_output console', trace)
                        self.assertFalse(any(line.startswith(('loadfont ', 'background_image '))
                                             for line in trace))
                        for command in ('insmod efi_gop', 'insmod all_video',
                                        'insmod gfxterm_background', 'insmod png',
                                        'terminal_output gfxterm'):
                            self.assertNotIn(command, trace)
                        label = 'UEFI' if platform == 'efi' else 'BIOS'
                        self.assertIn(f'state:||archlinux|15|{label}', trace)
                        if name == 'grub.cfg':
                            self.assertIn('terminal_output --append serial', trace)

    def test_ordinary_boot_keeps_graphics_and_failure_fallback(self):
        for (test, name), text in self.configs.items():
            for version in (None, ''):
                for platform in ('efi', 'pc'):
                    with self.subTest(test=test, name=name, version=version, platform=platform):
                        trace = self.setup_trace(text, version, platform)
                        self.assertIn('insmod efi_gop' if platform == 'efi' else 'insmod all_video', trace)
                        self.assertIn('loadfont /boot/grub/menu.pf2', trace)
                        self.assertIn('terminal_output gfxterm', trace)
                        self.assertIn('insmod gfxterm_background', trace)
                        self.assertIn('insmod png', trace)
                        self.assertIn('background_image --mode stretch /boot/grub/background.png', trace)
                        self.assertIn('inherited-theme|DejaVu Sans Mono Regular 30|', trace[-1])
                        no_font = self.setup_trace(text, version, platform, font_status=1)
                        no_gfx = self.setup_trace(text, version, platform, gfx_status=1)
                        self.assertNotIn('terminal_output gfxterm', no_font)
                        for failed in (no_font, no_gfx):
                            self.assertFalse(any(line.startswith('background_image ') for line in failed))
                            self.assertIn('terminal_output console', failed)
                        self.assertGreater(no_gfx.count('terminal_output console'), 1)

    def test_generated_menu_entries_keep_kernel_arguments(self):
        for (test, name), text in self.configs.items():
            with self.subTest(test=test, name=name):
                self.assertNotRegex(text, r'%[A-Z_]+%')
                for entry in ('archlinux', 'archlinux-lts', 'archlinux-safe'):
                    self.assertIn(f"--id '{entry}'", text)
                kernels = re.findall(r'^\s*linux /emaki/boot/x86_64/(.*)$', text, re.M)
                self.assertEqual(len(kernels), 3)
                self.assertTrue(kernels[1].startswith('vmlinuz-linux-lts '))
                self.assertIn('nomodeset', kernels[2])
                for line in kernels:
                    self.assertIn('copytoram=n', line)
                    self.assertEqual('emaki.test=1' in line, test)
                    expected = ('img_dev=UUID=${archiso_img_dev_uuid} img_loop="${iso_path}"'
                                if name == 'loopback.cfg' else
                                'archisosearchuuid=2026-10-10-00-00-00-00')
                    self.assertIn(expected, line)

    def test_generated_grub_syntax(self):
        checker = (os.environ.get('EMAKI_TEST_GRUB_SCRIPT_CHECK') or shutil.which('grub-script-check')
                   or '/tmp/emaki-grub-tools/usr/bin/grub-script-check')
        if not Path(checker).is_file():
            self.skipTest('Real grub-script-check is unavailable')
        for key, text in self.configs.items():
            with self.subTest(config=key):
                result = subprocess.run([checker], input=text, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class LoopbackAssets(unittest.TestCase):
    def test_missing_background_rejected_and_caller_font_not_required(self):
        for mode in ('', '-m stretch ', '-mstretch ', '--mode stretch ', '--mode=stretch '):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                boot = Path(temporary) / 'boot'
                grub = boot / 'grub'
                grub.mkdir(parents=True)
                text = (HERE / 'profile/grub/loopback.cfg').read_text()
                text = re.sub(r'background_image[^\n]*',
                              f'background_image {mode}/boot/grub/background.png', text)
                (grub / 'loopback.cfg').write_text(text)
                with self.assertRaisesRegex(ValueError, 'loopback background'):
                    image_check.check_loaders(boot, check_assets=True)
                (grub / 'background.png').write_bytes(b'background fixture')
                image_check.check_loaders(boot, check_assets=True)
                self.assertFalse((grub / 'fonts/unicode.pf2').exists())
                (grub / 'background.png').write_bytes(b'')
                with self.assertRaisesRegex(ValueError, 'loopback background'):
                    image_check.check_loaders(boot, check_assets=True)

    def test_caller_font_is_guarded_before_load(self):
        text = (HERE / 'profile/grub/loopback.cfg').read_text()
        if '${prefix}/fonts/unicode.pf2' in text:
            self.assertIn('if [ -f "${prefix}/fonts/unicode.pf2" ]; then\n'
                          'if loadfont "${prefix}/fonts/unicode.pf2" ; then', text)
        else:
            self.assertRegex(text, r'if loadfont /boot/grub/menu\.pf2; then')
        font = re.search(r'if loadfont .*?; then', text)
        self.assertIsNotNone(font)
        self.assertLess(font.start(), text.index('terminal_output gfxterm'))
        self.assertLess(font.start(), text.index('background_image'))

    def test_artwork_staged_after_archiso_builds_tree(self):
        script = (HERE / 'build.sh').read_text()
        first = script.index('mkarchiso -v -w "$work/mk"')
        staging = script.index('install -Dm644 "$ROOT/art/grub/background.png" '
                               '"$work/mk/iso/boot/grub/background.png"')
        second = script.index('mkarchiso -v -w "$work/mk"', first + 1)
        self.assertLess(first, staging)
        self.assertLess(staging, second)


if __name__ == '__main__':
    unittest.main()
