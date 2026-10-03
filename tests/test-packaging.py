#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check release metadata, non-root staging and sleep inhibition without a live bus."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import py_compile
import runpy
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ('pkgname', 'pkgver', 'pkgrel', 'arch', 'license', 'depends', 'makedepends',
          'checkdepends', 'optdepends', 'backup', 'conflicts', 'source', 'sha256sums',
          'sha512sums', 'b2sums', 'install')


def run(argv, **kwargs):
    return subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, **kwargs)


def metadata(recipe):
    # Let bash parse real PKGBUILD syntax (arrays, comments and variable expansion).
    # Only metadata is evaluated; no prepare/build/check/package hooks are invoked.
    result = run(['bash', '-eu', '-c', '''
        startdir=$1
        cd "$startdir"
        source ./PKGBUILD
        shift
        for field in "$@"; do
            declare -n values="$field"
            printf '%s\\0' "$field"
            for value in "${values[@]}"; do printf '%s\\0' "$value"; done
            printf '\\0'
            unset -n values
        done
    ''', 'metadata', str(recipe.parent), *FIELDS])
    if result.returncode:
        raise AssertionError(f'{recipe}: {result.stderr}')
    result_fields = {}
    parts = iter(result.stdout.split('\0'))
    for name in FIELDS:
        assert next(parts) == name
        values = []
        while (value := next(parts)) != '':
            values.append(value)
        result_fields[name] = values
    return result_fields


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {p.parent.name: metadata(p) for p in sorted(ROOT.glob('packaging/*/PKGBUILD'))}

    def test_release_versions(self):
        for name in ('emaki', 'emaki-config', 'emaki-desktop', 'emaki-keyring', 'emaki-mirrorlist'):
            with self.subTest(package=name):
                info = self.recipes[name]
                self.assertEqual(info['pkgname'], [name])
                self.assertEqual(info['pkgver'], ['0.1.1'])
                self.assertEqual(info['pkgrel'], ['1'])
                self.assertEqual(info['license'], ['GPL-3.0-or-later'])
        cargo = tomllib.loads((ROOT / 'Cargo.toml').read_text())
        self.assertEqual(cargo['workspace']['package']['version'], '0.1.1')
        packages = tomllib.loads((ROOT / 'Cargo.lock').read_text())['package']
        self.assertEqual({p['version'] for p in packages if p['name'].startswith('emaki-')}, {'0.1.1'})
        self.assertEqual(self.recipes['emaki']['depends'], [
            'emaki-config=0.1.1-1', 'emaki-desktop=0.1.1-1', 'niri-emaki=26.04-7',
            'quickshell-emaki=0.3.1-1', 'emaki-keyring=0.1.1-1', 'emaki-mirrorlist=0.1.1-1'])

    def test_preset_units_have_shipped_providers(self):
        # Cached upstream file-list evidence keeps this gate usable off Arch.
        # Prefer installed/cached package file lists whenever available.
        evidence = json.loads((ROOT / 'packaging/emaki-config/preset-unit-files.json').read_text())
        providers = {}
        shipped = set((ROOT / 'iso/target-packages.txt').read_text().split())
        for recipe in self.recipes.values():
            shipped.update(recipe['pkgname'])
            shipped.update(re.split(r'[<>=]', dep)[0] for dep in recipe['depends'])
        for package, record in evidence.items():
            paths = record['files']
            if shutil.which('pacman'):
                for action in ('-Qlq', '-Flq'):
                    result = run(['pacman', action, package])
                    if result.returncode == 0:
                        paths = result.stdout.splitlines()
                        break
            if package in shipped:
                for path in paths:
                    if path.lstrip('/').startswith('usr/lib/systemd/system/'):
                        providers[Path(path).name] = package
        preset = (ROOT / 'systemd/50-emaki.preset').read_text().splitlines()
        for line in preset:
            if line.startswith('enable '):
                self.assertIn(line.split()[1], providers, f'no shipped package provides {line}')
        required = {'systemd-networkd.service', 'systemd-networkd.socket',
                    'systemd-networkd-wait-online.service', 'systemd-network-generator.service',
                    'systemd-resolved.service', 'systemd-homed.service',
                    'systemd-homed-activate.service', 'systemd-boot-update.service', 'iwd.service'}
        disabled = {line.split()[1] for line in preset if line.startswith('disable ')}
        self.assertTrue(required <= disabled)
        self.assertNotIn('systemd-timesyncd.service', disabled)

    def test_commit_recomputed_and_explicit_override(self):
        head = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
        previous = run(['git', 'rev-parse', 'HEAD^']).stdout.strip()
        for name in ('emaki-config', 'emaki-installer'):
            for override, expected in (('', head), (previous, previous)):
                env = dict(os.environ, _emaki_commit='stale-private-value', EMAKI_SOURCE_COMMIT=override)
                result = run(['bash', '-eu', '-c', 'startdir=$1; source "$startdir/PKGBUILD"; printf "%s" "$_emaki_commit"',
                              'recipe', str(ROOT / 'packaging' / name)], env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_config_contract(self):
        info = self.recipes['emaki-config']
        self.assertEqual(set(info['backup']), {
            'etc/niri/config.kdl', 'etc/xdg/hypr/hyprlock.conf', 'etc/xdg/fastfetch/config.jsonc'})
        self.assertEqual(info['conflicts'], ['emaki-core'])
        self.assertEqual(set(info['makedepends']), {'git', 'make', 'python', 'rust', 'qt6-shadertools'})
        self.assertIn('niri', info['checkdepends'])
        required = {
            'niri-emaki>=26.04-6', 'quickshell-emaki', 'python', 'python-gobject', 'python-pillow',
            'gtk3', 'wpaperd', 'wl-clipboard', 'cliphist', 'polkit-gnome', 'udiskie', 'kitty',
            'fastfetch>=2.68.1', 'imagemagick', 'hyprlock', 'playerctl', 'fuzzel', 'qt6ct',
            'adwaita-cursors', 'adwaita-fonts', 'adwaita-icon-theme', 'brightnessctl',
            'power-profiles-daemon', 'networkmanager', 'bluez', 'upower', 'pipewire', 'wireplumber',
            'pipewire-pulse', 'wlsunset', 'greetd', 'greetd-regreet', 'gnome-keyring',
            'coreutils', 'dbus', 'procps-ng', 'systemd', 'util-linux', 'bash',
        }
        self.assertTrue(required <= set(info['depends']), required - set(info['depends']))
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        for text in ('export _emaki_commit', 'archive --format=tar', '"$_emaki_commit"',
                     'cargo fetch --locked', '_emaki_make build', '_emaki_make check',
                     '_emaki_make install install-niri-emaki DESTDIR="$pkgdir"',
                     'diff -u packaging/emaki-config/expected-files.list'):
            self.assertIn(text, recipe)
        self.assertIn('"--locked", "--offline"', (ROOT / 'scripts/core-package.py').read_text())

    def test_desktop_and_fork_constraints(self):
        desktop = self.recipes['emaki-desktop']
        self.assertTrue({'emaki-config', 'niri-emaki>=26.04-6', 'quickshell-emaki', 'hyprlock',
                         'firefox', 'noto-fonts'} <= set(desktop['depends']))
        self.assertFalse({'make', 'rust', 'qt6-shadertools'} & set(desktop['depends']))
        self.assertTrue(any(d.startswith('swayidle:') for d in desktop['optdepends']))
        self.assertFalse(any(d.startswith('niri-emaki:') for d in desktop['optdepends']))
        niri = self.recipes['niri-emaki']
        self.assertEqual((niri['pkgver'], niri['pkgrel']), (['26.04'], ['7']))
        self.assertTrue({'niri=26.04', 'libdisplay-info.so=3-64', 'libinput.so=10-64',
                         'libseat.so=1-64', 'mesa'} <= set(niri['depends']))
        self.assertNotIn('libgbm.so=1-64', niri['depends'])  # Arch mesa does not provide it
        qs = self.recipes['quickshell-emaki']
        self.assertEqual((qs['pkgver'], qs['pkgrel']), (['0.3.1'], ['1']))
        for dep in ('qt6-base', 'qt6-declarative', 'qt6-wayland'):
            self.assertIn(dep + '>=6.11', qs['depends'])
            self.assertIn(dep + '<6.12', qs['depends'])

    def test_local_source_checksums(self):
        for name, info in self.recipes.items():
            for algorithm, field in [('sha256', 'sha256sums'), ('sha512', 'sha512sums'), ('blake2b', 'b2sums')]:
                if not info[field]:
                    continue
                self.assertEqual(len(info['source']), len(info[field]), name)
                for source, expected in zip(info['source'], info[field]):
                    if '://' in source or expected == 'SKIP':
                        continue
                    with self.subTest(package=name, source=source, algorithm=algorithm):
                        actual = hashlib.new(algorithm, (ROOT / 'packaging' / name / source).read_bytes()).hexdigest()
                        self.assertEqual(actual, expected)

    def test_keyring_and_mirrorlist(self):
        keyring = self.recipes['emaki-keyring']
        self.assertEqual(keyring['source'], ['emaki.gpg', 'emaki-trusted', 'emaki-revoked'])
        self.assertEqual(keyring['install'], ['emaki-keyring.install'])
        self.assertIn('pacman', keyring['depends'])
        self.assertEqual((ROOT / 'packaging/emaki-keyring/emaki-trusted').read_text().strip(),
                         '668569956D2D747847D87D01F62680BE583363AC:4:')
        self.assertEqual(self.recipes['emaki-mirrorlist']['backup'], ['etc/pacman.d/emaki-mirrorlist'])
        mirror = (ROOT / 'packaging/emaki-mirrorlist/emaki-mirrorlist').read_text().splitlines()
        server = 'Server = https://github.com/emakish/packages/releases/download/'
        self.assertEqual([line for line in mirror if line.startswith('Server')], [server + 'stable'])
        self.assertIn('# ' + server + 'testing', mirror)

    def test_small_package_payloads_and_keyring_hooks(self):
        expected = {
            'emaki-desktop': {'/etc/pam.d/emaki-lock'},
            'emaki-keyring': {'/usr/share/pacman/keyrings/' + name
                              for name in ('emaki.gpg', 'emaki-trusted', 'emaki-revoked')},
            'emaki-mirrorlist': {'/etc/pacman.d/emaki-mirrorlist'},
            'emaki': set(),
        }
        with tempfile.TemporaryDirectory(prefix='emaki-small-') as temporary:
            base = Path(temporary)
            for name, files in expected.items():
                dest = base / name
                dest.mkdir()
                result = run(['bash', '-eu', '-c', 'source "$1/PKGBUILD"; srcdir=$1; pkgdir=$2; package',
                              'package', str(ROOT / 'packaging' / name), str(dest)])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual({'/' + p.relative_to(dest).as_posix() for p in dest.rglob('*')
                                  if p.is_file() or p.is_symlink()}, files)
            result = run(['bash', '-eu', '-c', '''
                pacman-key() { printf '%s\n' "$*"; }
                source "$1"
                post_install 0.1.0-1
                post_upgrade 0.1.0-1 0.0.0-1
            ''', 'hooks', str(ROOT / 'packaging/emaki-keyring/emaki-keyring.install')])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['--populate emaki', '--populate emaki'])

    def test_keyring_uninitialized_skips_population(self):
        result = run(['bash', '-eu', '-c', '''
            pacman-key() {
                if [ "$*" = '-l' ]; then
                    echo 'You do not have sufficient permissions to read the pacman keyring' >&2
                    return 1
                fi
                echo "Unexpected call: $*"
                return 99
            }
            source "$1"
            post_install 0.1.0-1
            post_upgrade 0.1.0-1 0.0.0-1
        ''', 'hooks', str(ROOT / 'packaging/emaki-keyring/emaki-keyring.install')])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))


class GrubTitleTests(unittest.TestCase):
    def test_hook_fields(self):
        sections = {}
        for line in (ROOT / 'grub/90-emaki-grub-title.hook').read_text().splitlines():
            if line.startswith('['):
                fields = sections.setdefault(line, {})
            elif line.strip() and not line.startswith('#'):
                key, value = map(str.strip, line.split('=', 1))
                fields.setdefault(key, []).append(value)
        self.assertEqual(sections['[Trigger]'], {
            'Operation': ['Install', 'Upgrade'], 'Type': ['Package'],
            'Target': ['grub', 'emaki-config']})
        self.assertEqual(sections['[Action]']['When'], ['PostTransaction'])
        self.assertEqual(sections['[Action]']['Exec'], ['/usr/share/libalpm/scripts/emaki-grub-title'])

    def test_patch_and_noops(self):
        original = (ROOT / 'grub/tests/10_linux-2.16.fixture').read_bytes()
        old = b'  OS="${GRUB_DISTRIBUTOR} Linux"\n'
        new = b'  OS="${GRUB_DISTRIBUTOR}"\n'
        self.assertEqual(original.count(old), 1)
        with tempfile.TemporaryDirectory(prefix='emaki-grub-') as temporary:
            target = Path(temporary) / '10_linux'
            target.write_bytes(original)
            target.chmod(0o755)
            command = ['bash', str(ROOT / 'grub/emaki-grub-title'), str(target)]
            before = target.stat()
            result = run(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((result.stdout, result.stderr), ('', ''))
            self.assertEqual(target.read_bytes(), original.replace(old, new))
            self.assertEqual(target.stat().st_mode & 0o7777, 0o755)
            self.assertNotEqual(target.stat().st_ino, before.st_ino)
            before = target.stat()
            result = run(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((result.stdout, result.stderr), ('', ''))
            self.assertEqual(target.read_bytes(), original.replace(old, new))
            self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns),
                             (before.st_ino, before.st_mtime_ns))
            # Similar but non-exact lines must never be rewritten.
            changed = original.replace(old, b' OS="${GRUB_DISTRIBUTOR} Linux"\n')
            target.write_bytes(changed)
            before = target.stat()
            result = run(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(len(result.stderr.splitlines()), 1)
            self.assertIn('WARNING', result.stderr)
            self.assertEqual(target.read_bytes(), changed)
            self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns),
                             (before.st_ino, before.st_mtime_ns))
            self.assertEqual(list(Path(temporary).iterdir()), [target])
            target.unlink()
            result = run(command)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
            self.assertFalse(target.exists())

    def test_installer_patches_in_chroot_before_mkconfig(self):
        result = run(['python3', '-c', '''
from unittest.mock import Mock, call
from emaki_installer.worker import Worker
worker = Worker.__new__(Worker)
worker.target = '/target'
worker.runner = Mock()
worker.files = Mock()
worker.files.read.return_value = 'linux /boot/vmlinuz-linux\\nlinux /boot/vmlinuz-linux-lts\\n'
worker.grub_config()
assert worker.runner.chroot.call_args_list == [
    call(['/usr/share/libalpm/scripts/emaki-grub-title'], '/target'),
    call(['grub-mkconfig', '-o', '/boot/grub/grub.cfg'], '/target')]
'''], env=dict(os.environ, PYTHONPATH=str(ROOT / 'installer')))
        self.assertEqual(result.returncode, 0, result.stderr)


def hook_sections(path):
    sections = {}
    for line in path.read_text().splitlines():
        if line.startswith('['):
            fields = sections.setdefault(line, {})
        elif line.strip() and not line.startswith('#'):
            key, value = map(str.strip, line.split('=', 1))
            fields.setdefault(key, []).append(value)
    return sections


def os_release_fields(text):
    """os-release(5): KEY=value lines; '#' lines and blank lines are ignored."""
    fields = {}
    for line in text.splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            fields[key] = value[1:-1] if value[:1] == '"' else value
    return fields


class OsReleaseTests(unittest.TestCase):
    ARCH = '../usr/lib/os-release'
    EMAKI = '../usr/lib/emaki/os-release'
    SCRIPT = ROOT / 'os-release/emaki-os-release'

    def test_identity(self):
        raw = (ROOT / 'os-release/os-release').read_bytes()
        self.assertTrue(raw.isascii())
        fields = os_release_fields(raw.decode())
        # ID stays arch (installers that match ID alone); the name is Emaki everywhere.
        self.assertEqual({k: fields[k] for k in ('NAME', 'PRETTY_NAME', 'ID', 'VARIANT_ID', 'BUILD_ID')},
                         {'NAME': 'Emaki', 'PRETTY_NAME': 'Emaki', 'ID': 'arch',
                          'VARIANT_ID': 'emaki', 'BUILD_ID': 'rolling'})
        for absent in ('ID_LIKE', 'VERSION', 'VERSION_ID', 'LOGO', 'IMAGE_ID'):
            self.assertNotIn(absent, fields)
        red = tomllib.loads((ROOT / 'tokens.toml').read_text())['ansi']['red']
        self.assertEqual(fields['ANSI_COLOR'], '38;2;' + ';'.join(str(b) for b in bytes.fromhex(red)),
                         'stale: run scripts/render-theme')
        self.assertTrue(all(fields[k].startswith('https://github.com/emakish/emaki')
                            for k in ('HOME_URL', 'BUG_REPORT_URL')))

    def test_hook_fields_and_order(self):
        apply = hook_sections(ROOT / 'os-release/50-emaki-os-release.hook')
        self.assertEqual(apply['[Trigger]'], {
            'Operation': ['Install', 'Upgrade'], 'Type': ['Package'],
            'Target': ['emaki-config', 'systemd']})
        self.assertEqual(apply['[Action]']['When'], ['PostTransaction'])
        self.assertEqual(apply['[Action]']['Exec'], ['/usr/share/libalpm/scripts/emaki-os-release'])
        restore = hook_sections(ROOT / 'os-release/50-emaki-os-release-remove.hook')
        self.assertEqual(restore['[Trigger]'], {
            'Operation': ['Remove'], 'Type': ['Package'], 'Target': ['emaki-config']})
        self.assertEqual(restore['[Action]']['When'], ['PreTransaction'])
        self.assertEqual(restore['[Action]']['Exec'],
                         ['/usr/share/libalpm/scripts/emaki-os-release --restore'])
        # alpm runs hooks by file name: after systemd's tmpfiles hook has created
        # Arch's link in the same transaction, before mkinitcpio copies os-release.
        self.assertTrue('21-systemd-tmpfiles.hook' < '50-emaki-os-release.hook' < '90-mkinitcpio-install.hook')

    def make_root(self, temporary):
        root = Path(temporary)
        (root / 'etc').mkdir()
        (root / 'usr/lib/emaki').mkdir(parents=True)
        (root / 'usr/lib/os-release').write_text('NAME="Arch Linux"\nID=arch\n')
        shutil.copyfile(ROOT / 'os-release/os-release', root / 'usr/lib/emaki/os-release')
        return root, root / 'etc/os-release'

    def script(self, root, *args):
        result = run(['bash', str(self.SCRIPT), *args, str(root)])
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)
        return result.stderr

    def test_apply_restore_and_noops(self):
        with tempfile.TemporaryDirectory(prefix='emaki-os-release-') as temporary:
            root, link = self.make_root(temporary)
            for arch in (self.ARCH, '/usr/lib/os-release', None):
                link.unlink(missing_ok=True)
                if arch:
                    link.symlink_to(arch)
                self.assertEqual(self.script(root), '')
                self.assertEqual(os.readlink(link), self.EMAKI)
                self.assertEqual(link.read_bytes(), (ROOT / 'os-release/os-release').read_bytes())
                self.assertEqual(os.listdir(root / 'etc'), ['os-release'])
            before = link.lstat()
            self.assertEqual(self.script(root), '')
            self.assertEqual((link.lstat().st_ino, link.lstat().st_mtime_ns),
                             (before.st_ino, before.st_mtime_ns))
            self.assertEqual(self.script(root, '--restore'), '')
            self.assertEqual(os.readlink(link), self.ARCH)
            self.assertEqual(self.script(root, '--restore'), '')
            self.assertEqual(os.readlink(link), self.ARCH)
            link.unlink()
            self.assertEqual(self.script(root, '--restore'), '')
            self.assertFalse(link.exists() or link.is_symlink())
            self.assertEqual(os.listdir(root / 'etc'), [])

    def test_administrator_choice_is_kept(self):
        with tempfile.TemporaryDirectory(prefix='emaki-os-release-') as temporary:
            root, link = self.make_root(temporary)
            for setup in ('file', 'link'):
                link.unlink(missing_ok=True)
                if setup == 'file':
                    link.write_text('NAME="Mine"\n')
                else:
                    link.symlink_to('../usr/lib/os-release.mine')
                before = (link.lstat().st_ino, link.lstat().st_mtime_ns)
                for args in ((), ('--restore',)):
                    stderr = self.script(root, *args)
                    self.assertEqual(len(stderr.splitlines()), 1, stderr)
                    self.assertIn('WARNING', stderr)
                    self.assertEqual((link.lstat().st_ino, link.lstat().st_mtime_ns), before)
            # Without Emaki's file there is nothing to link to.
            link.unlink()
            link.symlink_to(self.ARCH)
            (root / 'usr/lib/emaki/os-release').unlink()
            stderr = self.script(root)
            self.assertIn('WARNING', stderr)
            self.assertEqual(os.readlink(link), self.ARCH)

    def test_systemd_tmpfiles_keeps_the_link(self):
        """The real systemd rule recreates only a missing link; it never replaces ours."""
        rule = Path('/usr/lib/tmpfiles.d/etc.conf')
        tmpfiles = shutil.which('systemd-tmpfiles')
        lines = [line for line in (rule.read_text().splitlines() if rule.is_file() else [])
                 if line.split()[1:2] == ['/etc/os-release']]
        if not tmpfiles or not lines:
            self.skipTest('systemd-tmpfiles or its /etc/os-release rule is unavailable')
        self.assertEqual(lines, ['L /etc/os-release - - - - ../usr/lib/os-release'])
        with tempfile.TemporaryDirectory(prefix='emaki-os-release-') as temporary:
            root, link = self.make_root(temporary)
            conf = root / 'etc-os-release.conf'
            conf.write_text(lines[0] + '\n')
            tmpfiles_create = [tmpfiles, '--create', f'--root={root}', str(conf)]
            result = run(tmpfiles_create)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.readlink(link), self.ARCH)
            self.assertEqual(self.script(root), '')
            result = run(tmpfiles_create)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.readlink(link), self.EMAKI)


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='emaki-payload-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.dest = cls.base / 'root'
        forbidden = cls.base / 'forbidden'
        forbidden.mkdir()
        for name in ('pacman', 'niri', 'niri-emaki', 'systemctl', 'systemd-tmpfiles'):
            stub = forbidden / name
            stub.write_text('#!/bin/sh\necho "Unexpected host command: $0" >&2\nexit 99\n')
            stub.chmod(0o755)
        result = run(['make', 'install', 'install-niri-emaki', f'DESTDIR={cls.dest}'],
                     env=dict(os.environ, PATH=str(forbidden) + ':' + os.environ['PATH'],
                              SUDO_USER='must-not-be-used'))
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        license_path = cls.dest / 'usr/share/licenses/emaki-config/LICENSE'
        license_path.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'LICENSE', license_path)

    def test_inventory(self):
        expected = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        actual = sorted('/' + p.relative_to(self.dest).as_posix() for p in self.dest.rglob('*')
                        if p.is_file() or p.is_symlink())
        self.assertEqual(expected, sorted(set(expected)), 'manifest must be sorted and unique')
        self.assertEqual(actual, expected)
        self.assertNotIn('/usr/bin/niri-emaki', actual)
        self.assertNotIn('/usr/share/emaki/boot/emaki-splash.bmp', actual)
        self.assertTrue((ROOT / 'boot/emaki-splash.bmp').is_file())
        for path in self.dest.rglob('*'):
            if path.is_symlink():
                self.assertTrue(path.resolve().is_relative_to(self.dest))
                self.assertTrue(path.resolve().exists(), str(path))
            elif path.is_file():
                mode = path.stat().st_mode & 0o7777
                executable = path.parent in (self.dest / 'usr/bin', self.dest / 'usr/share/libalpm/scripts')
                self.assertEqual(mode, 0o755 if executable else 0o644, str(path))
        binary = self.dest / 'usr/bin/emaki'
        self.assertEqual(run([str(binary), 'version']).stdout, 'emaki 0.1.1\n')
        result = run(['python3', 'scripts/core-package.py', 'verify-build-paths', '--binary', str(binary)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_defaults_and_presets(self):
        commit = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
        self.assertEqual((self.dest / 'usr/lib/emaki-release').read_text(),
                         f'VERSION=0.1.1\nCHANNEL=stable\nEMAKI_COMMIT={commit}\n')
        expected = {'greetd.service', 'NetworkManager.service', 'bluetooth.service', 'grub-btrfsd.service',
                    'snapper-timeline.timer', 'snapper-cleanup.timer', 'fstrim.timer'}
        preset = (self.dest / 'usr/lib/systemd/system-preset/50-emaki.preset').read_text().splitlines()
        self.assertEqual({line.split()[1] for line in preset if line.startswith('enable ')}, expected)
        skel = self.dest / 'etc/skel/.config'
        wallpaper = tomllib.loads((skel / 'wpaperd/config.toml').read_text())['any']
        self.assertEqual(wallpaper['path'], '/usr/share/emaki/wallpaper/ring.png')
        self.assertEqual(wallpaper['mode'], 'center')
        self.assertTrue((self.dest / wallpaper['path'].lstrip('/')).is_file())
        self.assertEqual((skel / 'kitty/kitty.conf').read_text().strip(), 'include /usr/share/emaki/kitty/theme.conf')
        qt = configparser.ConfigParser()
        qt.read(skel / 'qt6ct/qt6ct.conf')
        self.assertTrue(qt.getboolean('Appearance', 'custom_palette'))
        self.assertEqual(qt['Appearance']['color_scheme_path'], '/usr/share/emaki/qt6ct/emaki.conf')
        self.assertEqual((self.dest / 'usr/share/emaki/grub/background.png').read_bytes(),
                         (ROOT / 'art/grub/background.png').read_bytes())
        for staged, source in (('usr/lib/emaki/os-release', 'os-release/os-release'),
                               ('usr/share/libalpm/hooks/50-emaki-os-release.hook',
                                'os-release/50-emaki-os-release.hook'),
                               ('usr/share/libalpm/hooks/50-emaki-os-release-remove.hook',
                                'os-release/50-emaki-os-release-remove.hook'),
                               ('usr/share/libalpm/scripts/emaki-os-release', 'os-release/emaki-os-release')):
            self.assertEqual((self.dest / staged).read_bytes(), (ROOT / source).read_bytes(), staged)
        # Staging never links /etc/os-release itself: no package may own that path.
        self.assertFalse((self.dest / 'etc/os-release').exists() or (self.dest / 'etc/os-release').is_symlink())

    def test_sleep_guard_wiring(self):
        unit = self.dest / 'usr/lib/systemd/user/emaki-sleep-guard.service'
        config = configparser.ConfigParser(interpolation=None)
        config.read(unit)
        self.assertEqual(config['Unit']['PartOf'], 'graphical-session.target')
        self.assertEqual(config['Install']['WantedBy'], 'graphical-session.target')
        self.assertEqual(config['Service']['ExecStart'], '/usr/bin/python3 -I /usr/bin/emaki-sleep-guard')
        self.assertEqual(config['Service']['Restart'], 'on-failure')
        self.assertIn('"--no-block" "emaki-shell.service" "emaki-sleep-guard.service"',
                      (self.dest / 'usr/share/emaki/niri/default.kdl').read_text())
        self.assertFalse((self.dest / 'usr/lib/systemd/user/graphical-session.target.wants/emaki-sleep-guard.service').exists())
        logind = configparser.ConfigParser()
        logind.read(self.dest / 'usr/lib/systemd/logind.conf.d/50-emaki.conf')
        self.assertEqual(logind['Login']['InhibitDelayMaxSec'], '20')
        py_compile.compile(str(self.dest / 'usr/bin/emaki-sleep-guard'),
                           cfile=str(self.base / 'guard.pyc'), doraise=True)
        if shutil.which('systemd-analyze'):
            result = run(['systemd-analyze', '--user', 'verify', str(unit)])
            diagnostics = result.stderr.splitlines()
            sandbox_errors = (
                'Failed to turn off SO_PASSRIGHTS on user lookup socket, ignoring: Operation not permitted',
                'Failed to enable SO_PASSCRED on handoff timestamp socket: Operation not permitted',
            )
            if result.returncode and diagnostics and all(line in sandbox_errors for line in diagnostics):
                self.skipTest('systemd-analyze cannot create manager sockets in this sandbox; verify in the VM')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_greeter_requires_explicit_user(self):
        result = run(['make', 'enable-greeter', 'GREETER_USER=', f'DESTDIR={self.dest}'],
                     env=dict(os.environ, SUDO_USER='must-not-be-used'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('set GREETER_USER explicitly', result.stderr)


class SleepGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import without running main(): tests never open the system/session bus.
        cls.module = runpy.run_path(str(ROOT / 'scripts/emaki-sleep-guard'))

    def make_guard(self):
        from gi.repository import Gio, GLib

        class Logind:
            calls = 0
            readers = []

            def call_with_unix_fd_list_sync(proxy, method, params, flags, timeout, in_fds, cancellable):
                self.assertEqual(method, 'Inhibit')
                self.assertEqual(params.unpack(), ('sleep', 'Emaki session', 'Lock the session before sleep', 'delay'))
                self.assertEqual(flags, Gio.DBusCallFlags.NONE)
                self.assertLessEqual(timeout, 5000)
                self.assertIsNone(in_fds)
                self.assertIsNone(cancellable)
                reader, writer = os.pipe()
                self.addCleanup(os.close, reader)
                os.set_blocking(reader, False)
                proxy.readers.append(reader)
                fds = Gio.UnixFDList.new()
                handle = fds.append(writer)
                os.close(writer)
                proxy.calls += 1
                return GLib.Variant('(h)', (handle,)), fds

        guard = self.module['SleepGuard'](Logind())
        self.addCleanup(guard.release)
        guard.acquire()
        self.assertFalse(os.get_inheritable(guard.inhibitor))
        return guard

    def assert_held(self, reader):
        with self.assertRaises(BlockingIOError):
            os.read(reader, 1)

    def test_lock_then_release_and_rearm_after_each_resume(self):
        guard = self.make_guard()
        guard.acquire()
        self.assertEqual(guard.proxy.calls, 1)
        for _ in range(2):
            reader = guard.proxy.readers[-1]
            self.assert_held(reader)

            def lock(argv, **kwargs):
                self.assertEqual(argv, ['/usr/bin/emaki-lock', '--wait'])
                self.assertEqual(kwargs['timeout'], 18)
                self.assertTrue(kwargs['close_fds'])
                self.assert_held(reader)  # Inhibitor must span the entire readiness wait.
                return subprocess.CompletedProcess(argv, 0)

            with patch('subprocess.run', side_effect=lock) as process:
                guard.prepare_for_sleep(True)
                guard.prepare_for_sleep(True)
                self.assertEqual(process.call_count, 1)
            self.assertEqual(os.read(reader, 1), b'', 'Gio must not retain a duplicate inhibitor fd')
            self.assertIsNone(guard.inhibitor)
            guard.prepare_for_sleep(False)
            self.assert_held(guard.proxy.readers[-1])
        self.assertEqual(guard.proxy.calls, 3)
        guard.release()
        self.assertEqual(os.read(guard.proxy.readers[-1], 1), b'')

    def test_failed_or_timed_out_lock_releases_and_recovers(self):
        for failure in (subprocess.CompletedProcess([], 1),
                        subprocess.TimeoutExpired('emaki-lock', 18), FileNotFoundError('lock missing')):
            with self.subTest(failure=failure):
                guard = self.make_guard()
                reader = guard.proxy.readers[-1]
                options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch('subprocess.run', **options), patch('sys.stderr') as errors:
                    guard.prepare_for_sleep(True)
                    self.assertTrue(errors.write.called)
                self.assertIsNone(guard.inhibitor)
                self.assertEqual(os.read(reader, 1), b'')
                guard.prepare_for_sleep(False)
                self.assert_held(guard.proxy.readers[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
