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
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ('pkgname', 'pkgver', 'pkgrel', 'arch', 'license', 'depends', 'makedepends',
          'checkdepends', 'optdepends', 'backup', 'conflicts', 'source', 'sha256sums',
          'sha512sums', 'b2sums', 'install', 'provides', 'replaces',
          'groups', 'validpgpkeys')


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
        for name in ('emaki', 'emaki-config', 'emaki-desktop', 'emaki-apps', 'emaki-keyring', 'emaki-mirrorlist', 'emaki-installer'):
            with self.subTest(package=name):
                info = self.recipes[name]
                self.assertEqual(info['pkgname'], [name])
                self.assertEqual(info['pkgver'], ['0.2.0'])
                self.assertEqual(info['pkgrel'], ['1'])
                # emaki-installer also ships the zone map (ODbL) and the GRUB unlock-screen fonts (DejaVu, Bitstream Vera).
                self.assertEqual(info['license'], ['GPL-3.0-or-later', 'ODbL-1.0', 'Bitstream-Vera']
                                 if name == 'emaki-installer' else ['GPL-3.0-or-later'])
        cargo = tomllib.loads((ROOT / 'Cargo.toml').read_text())
        self.assertEqual(cargo['workspace']['package']['version'], '0.2.0')
        packages = tomllib.loads((ROOT / 'Cargo.lock').read_text())['package']
        self.assertEqual({p['version'] for p in packages if p['name'].startswith('emaki-')}, {'0.2.0'})
        self.assertEqual(self.recipes['emaki']['depends'], [
            'emaki-config=0.2.0-1', 'emaki-desktop=0.2.0-1', 'niri-emaki=26.04-8',
            'quickshell-emaki=0.3.1-' + self.recipes['quickshell-emaki']['pkgrel'][0], 'emaki-keyring=0.2.0-1', 'emaki-mirrorlist=0.2.0-1'])

    def test_preset_units_have_shipped_providers(self):
        # Cached upstream file-list evidence keeps this gate usable off Arch.
        # Prefer installed/cached package file lists whenever available.
        evidence = json.loads((ROOT / 'packaging/emaki-config/preset-unit-files.json').read_text())
        evidence.update(json.loads((ROOT / 'packaging/emaki-apps/preset-unit-files.json').read_text()))
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
        rich = (ROOT / 'packaging/emaki-apps/45-emaki-apps.preset').read_text().splitlines()
        for line in preset + rich:
            if line.startswith('enable '):
                self.assertIn(line.split()[1], providers, f'no shipped package provides {line}')
        for line in rich:
            if line.startswith('enable '):
                self.assertIn(providers[line.split()[1]], self.recipes['emaki-apps']['depends'])
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
            'etc/niri/config.kdl', 'etc/xdg/hypr/hyprlock.conf', 'etc/xdg/fastfetch/config.jsonc',
            'etc/xdg/mimeapps.list', 'etc/xdg/kdeglobals', 'etc/xdg/dolphinrc', 'etc/xdg/qt6ct/qt6ct.conf',
            'etc/xdg/menus/emaki-applications.menu', 'etc/xdg/xdg-desktop-portal/niri-portals.conf'})
        self.assertEqual(info['conflicts'], ['emaki-core'])
        self.assertEqual(set(info['makedepends']), {'git', 'make', 'python', 'rust', 'qt6-shadertools'})
        self.assertIn('niri', info['checkdepends'])
        required = {
            'niri-emaki>=26.04-6', 'quickshell-emaki>=0.3.1-2', 'python', 'python-gobject', 'python-pillow',
            'gtk3', 'wpaperd', 'wl-clipboard', 'cliphist', 'polkit-gnome', 'udiskie', 'kitty',
            'fastfetch>=2.68.1', 'imagemagick', 'hyprlock', 'playerctl', 'fuzzel', 'qt6ct',
            'adwaita-cursors', 'adwaita-fonts', 'adwaita-icon-theme', 'brightnessctl',
            'power-profiles-daemon', 'networkmanager', 'bluez', 'upower', 'pipewire', 'wireplumber',
            'pipewire-pulse', 'wlsunset', 'greetd', 'greetd-regreet', 'gnome-keyring',
            'coreutils', 'dbus', 'procps-ng', 'systemd', 'util-linux', 'bash',
        }
        self.assertTrue(required <= set(info['depends']), required - set(info['depends']))
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        for text in ('export _emaki_commit', 'source_archive.py" checkout', '"$_emaki_commit"',
                     'cargo fetch --locked', '_emaki_make build', '_emaki_make check',
                     '_emaki_make install install-niri-emaki DESTDIR="$pkgdir"',
                     'diff -u packaging/emaki-config/expected-files.list'):
            self.assertIn(text, recipe)
        self.assertIn('"--locked", "--offline"', (ROOT / 'scripts/core-package.py').read_text())

    def test_desktop_and_fork_constraints(self):
        desktop = self.recipes['emaki-desktop']
        self.assertTrue({'emaki-config', 'niri-emaki>=26.04-6', 'quickshell-emaki', 'hyprlock',
                         'firefox', 'noto-fonts', 'dolphin', 'kitty'} <= set(desktop['depends']))
        # niri runs X11 clients only through xwayland-satellite found on PATH.
        self.assertIn('xwayland-satellite', desktop['depends'])
        # Colour emoji outside Firefox (file names, chat, the terminal).
        self.assertIn('noto-fonts-emoji', desktop['depends'])
        self.assertFalse({'nautilus', 'loupe', 'file-roller', 'papers', '7zip', 'unrar',
                          'emaki-apps'} & set(desktop['depends']))
        self.assertFalse({'make', 'rust', 'qt6-shadertools'} & set(desktop['depends']))
        self.assertTrue(any(d.startswith('swayidle:') for d in desktop['optdepends']))
        self.assertFalse(any(d.startswith('niri-emaki:') for d in desktop['optdepends']))
        niri = self.recipes['niri-emaki']
        self.assertEqual((niri['pkgver'], niri['pkgrel']), (['26.04'], ['8']))
        self.assertTrue({'niri=26.04', 'libdisplay-info.so=3-64', 'libinput.so=10-64',
                         'libseat.so=1-64', 'mesa'} <= set(niri['depends']))
        self.assertNotIn('libgbm.so=1-64', niri['depends'])  # Arch mesa does not provide it
        qs = self.recipes['quickshell-emaki']
        self.assertEqual(qs['pkgver'], ['0.3.1'])
        self.assertIn(qs['pkgrel'], (['2'], ['3']))
        lower, upper = ('6.11', '6.12') if qs['pkgrel'] == ['2'] else ('6.12', '6.13')
        for dep in ('qt6-base', 'qt6-declarative', 'qt6-wayland'):
            self.assertIn(dep + '>=' + lower, qs['depends'])
            self.assertIn(dep + '<' + upper, qs['depends'])

    def test_staged_qt612_recipe(self):
        active = ROOT / 'packaging/quickshell-emaki'
        staged = active / 'qt-6.12'
        candidate = metadata(staged / 'PKGBUILD')
        self.assertEqual(candidate['pkgver'], ['0.3.1'])
        self.assertEqual(candidate['pkgrel'], ['3'])
        for dep in ('qt6-base', 'qt6-declarative', 'qt6-wayland'):
            self.assertIn(dep + '>=6.12', candidate['depends'])
            self.assertIn(dep + '<6.13', candidate['depends'])
        self.assertEqual(len(candidate['source']), len(candidate['sha256sums']))
        for source, digest in zip(candidate['source'], candidate['sha256sums']):
            if '://' in source:
                continue
            path = staged / source if (staged / source).is_file() else active / source
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, source)
        # The build driver selects the active recipe; activation also selects patch 0003.
        self.assertNotIn('qt-6.12', (ROOT / 'packaging/build.sh').read_text())
        qs = self.recipes['quickshell-emaki']
        self.assertEqual('0003-qt-6.12-moc-includes.patch' in qs['source'], qs['pkgrel'] == ['3'])

    def test_qt612_activation(self):
        activate = runpy.run_path(str(ROOT / 'packaging/activate-qt612.py'))['activate']
        with tempfile.TemporaryDirectory(prefix='qt612-', dir='/tmp') as directory:
            checkout = Path(directory)
            shutil.copytree(ROOT / 'packaging', checkout / 'packaging')
            for name in ('Cargo.toml', 'Cargo.lock'):
                shutil.copy2(ROOT / name, checkout / name)
            activate(checkout)
            # Run the same packaging gates against the activated tree.
            with patch.dict(globals(), ROOT=checkout):
                instance = MetadataTests()
                instance.recipes = dict(self.recipes)
                for name in ('emaki', 'quickshell-emaki'):
                    instance.recipes[name] = metadata(checkout / 'packaging' / name / 'PKGBUILD')
                instance.test_release_versions()
                instance.test_desktop_and_fork_constraints()
                instance.test_staged_qt612_recipe()
                instance.test_local_source_checksums()
            self.assertEqual(metadata(checkout / 'packaging/quickshell-emaki/PKGBUILD')['pkgrel'], ['3'])

    def test_portal_without_nautilus(self):
        portal = self.recipes['xdg-desktop-portal-gnome-emaki']
        self.assertEqual(portal['pkgver'], ['50.0'])
        self.assertEqual(portal['pkgrel'], ['2'])
        self.assertNotIn('nautilus', portal['depends'])
        self.assertIn('xdg-desktop-portal-gnome', portal['provides'])
        self.assertIn('xdg-desktop-portal-gnome', portal['conflicts'])
        self.assertIn('xdg-desktop-portal-gnome', portal['replaces'])
        self.assertEqual(portal['groups'], [])
        self.assertEqual(len(portal['validpgpkeys']), 2)
        for fingerprint in portal['validpgpkeys']:
            key = ROOT / 'packaging/xdg-desktop-portal-gnome-emaki/keys/pgp' / f'{fingerprint}.asc'
            self.assertTrue(key.is_file(), key)
        self.assertEqual(portal['b2sums'], [
            '85ef8077172eb7fe181f2b3ab1585e7d8e467cec5acc163c9438364d4f8f127d4c582141a91899c859689b08a7b8c470668f9fd883f1457254b6cf913b75ce2e',
            'afa272ad0b3f5b417326269ff55cb943a8c811860f13a48867e0673ebcb58df8a4b91756a0aceace9860f8754880b95328d6670fd9c0420f81d859db4631b19c'])
        # By the upstream name, never by the fork's own (PortalResolutionTests).
        for name, recipe in self.recipes.items():
            self.assertNotIn(FORK, [re.split(r'[<>=]', d)[0] for d in recipe['depends']], name)
        for name in ('emaki-config', 'emaki-desktop'):
            self.assertIn(PORTAL, self.recipes[name]['depends'], name)
        config = configparser.ConfigParser()
        config.read(ROOT / 'packaging/emaki-config/niri-portals.conf')
        self.assertEqual(config['preferred']['org.freedesktop.impl.portal.FileChooser'], 'gtk;')
        self.assertEqual(config['preferred']['org.freedesktop.impl.portal.ScreenCast'], 'gnome;')
        installed = 'etc/xdg/xdg-desktop-portal/niri-portals.conf'
        self.assertIn(installed, self.recipes['emaki-config']['backup'])
        self.assertIn('/' + installed, (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines())
        evidence = json.loads((ROOT / 'packaging/emaki-apps/arch-packages.json').read_text())
        self.assertNotIn('nautilus', evidence['live_and_target_closure_names'])
        self.assertNotIn('xdg-desktop-portal-gnome', evidence['live_and_target_closure_names'])
        self.assertIn('xdg-desktop-portal-gnome-emaki', evidence['live_and_target_closure_names'])
        self.assertEqual(evidence['retained_gnome_apps'], [])

    def test_rich_apps(self):
        info = self.recipes['emaki-apps']
        self.assertEqual(info['arch'], ['any'])
        self.assertEqual(info['pkgver'], [(ROOT / 'iso/VERSION').read_text().strip()])
        required = {'emaki-desktop', 'gwenview', 'ark', 'okular', 'thunderbird', 'qbittorrent',
                    'haruna', 'elisa', 'kate', 'libreoffice-fresh', 'spectacle', 'obs-studio',
                    'plasma-systemmonitor', 'partitionmanager', 'filelight', 'discover', 'flatpak',
                    'fwupd', 'isoimagewriter', 'cups', 'print-manager', 'skanlite', 'kdeconnect',
                    'kcharselect', 'keepassxc', '7zip', 'unrar', 'ffmpeg', 'qt6-multimedia-ffmpeg',
                    'gst-plugins-base', 'gst-plugins-good', 'gst-plugins-bad', 'gst-plugins-ugly', 'gst-libav',
                    'ttf-liberation', 'ttf-carlito', 'hunspell-en_us'}
        self.assertEqual(set(info['depends']), required)
        self.assertEqual(len(info['depends']), len(required))
        evidence = json.loads((ROOT / 'packaging/emaki-apps/arch-packages.json').read_text())
        self.assertEqual({p['pkgname'] for p in evidence['direct']}, required - {'emaki-desktop'})
        self.assertTrue(all(p['repo'] in ('core', 'extra') for p in evidence['direct']))

    def test_rich_printing_preset(self):
        # Rich enables CUPS through a preset file of its own: the shared 50-emaki.preset
        # also reaches Minimal, and a scriptlet must not enable or start units on a
        # running system (it cannot tell "never enabled" from "switched off by the person").
        self.assertIn('cups', self.recipes['emaki-apps']['depends'])
        preset = (ROOT / 'packaging/emaki-apps/45-emaki-apps.preset').read_text().splitlines()
        rules = [line for line in preset if line.strip() and not line.startswith('#')]
        self.assertEqual(rules, ['enable cups.service', 'enable cups.socket', 'enable cups.path'])
        for path in (ROOT / 'systemd/50-emaki.preset', ROOT / 'packaging/emaki-apps/45-emaki-apps.preset'):
            self.assertNotIn('avahi', path.read_text())
        self.assertEqual(self.recipes['emaki-apps']['install'], ['emaki-apps.install'])
        hooks = ROOT / 'packaging/emaki-apps/emaki-apps.install'
        self.assertNotIn('systemctl ', ''.join(line for line in hooks.read_text().splitlines(True)
                                               if not line.lstrip().startswith(('#', 'echo'))))
        result = run(['bash', '-eu', '-c', 'source "$1"; post_install 0.1.2-1; post_upgrade 0.1.2-1 0.1.1-1',
                      'hooks', str(hooks)])
        self.assertEqual(result.returncode, 0, result.stderr)
        line = 'Printing is off. Switch it on: sudo systemctl enable --now cups.service'
        enabled = (Path('/etc/systemd/system/multi-user.target.wants/cups.service').exists()
                   or Path('/etc/systemd/system/cups.service').is_symlink())
        self.assertEqual(result.stdout.splitlines(), [] if enabled else [line, line])

    def test_installer_dependencies(self):
        depends = self.recipes['emaki-installer']['depends']
        # The worker runs the snapshot and zram steps inside the target, never on the live host.
        for name in ('snapper', 'snap-pac', 'grub-btrfs', 'zram-generator'):
            self.assertNotIn(name, depends)
        # The installer UI imports the shell modules and shaders under /usr/share/emaki/shell.
        self.assertIn('emaki-config', depends)

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
        server = 'Server = https://pkgs.emaki.sh/'
        self.assertEqual([line for line in mirror if line.startswith('Server')], [server + 'stable/$arch'])
        self.assertIn('# ' + server + 'testing/$arch', mirror)
        # The address the package ships is the one the mirror documentation describes.
        self.assertIn(server + 'stable/$arch', (ROOT / 'docs/mirror.md').read_text())

    def test_small_package_payloads_and_keyring_hooks(self):
        expected = {
            'emaki-desktop': {'/etc/pam.d/emaki-lock'},
            'emaki-keyring': {'/usr/share/pacman/keyrings/' + name
                              for name in ('emaki.gpg', 'emaki-trusted', 'emaki-revoked')},
            'emaki-mirrorlist': {'/etc/pacman.d/emaki-mirrorlist'},
            'emaki': set(),
            'emaki-apps': {'/usr/lib/systemd/system-preset/45-emaki-apps.preset'},
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


PORTAL, FORK = 'xdg-desktop-portal-gnome', 'xdg-desktop-portal-gnome-emaki'
# The names whose relations decide which portal a machine gets; the stubs carry only these.
PORTAL_NAMES = {'emaki', 'emaki-config', 'emaki-desktop', 'emaki-apps', FORK, PORTAL, 'nautilus',
                'xdg-desktop-portal', 'xdg-desktop-portal-gtk'}
# Arch's side as `pacman -Si` showed it on 2026-10-05 (extra/xdg-desktop-portal-gnome 50.0-1).
ARCH_STUBS = [
    {'name': PORTAL, 'version': '50.0-1',
     'depends': ['nautilus', 'xdg-desktop-portal', 'xdg-desktop-portal-gtk>=1.10.0-2'],
     'provides': ['xdg-desktop-portal-impl'], 'conflicts': ['xdg-desktop-portal-gtk<1.10.0-2'],
     'replaces': ['xdg-desktop-portal-gtk<1.10.0-2']},
    {'name': 'nautilus', 'version': '50.3.1-1'},
    {'name': 'xdg-desktop-portal', 'version': '1.20.3-1'},
    {'name': 'xdg-desktop-portal-gtk', 'version': '1.15.3-1', 'depends': ['xdg-desktop-portal'],
     'provides': ['xdg-desktop-portal-impl']},
]


def released(version):
    """The portal relations of an install from the <version> ISO (recipes at v0.1.0 and v0.1.1;
    0.1.2 is a726db4, whose fork 50.0-1 had no replaces=)."""
    release, portal = f'{version}-1', FORK if version == '0.1.2' else PORTAL
    desktop = ['emaki-config', portal, 'xdg-desktop-portal-gtk'] + ([] if version == '0.1.2' else ['nautilus'])
    stubs = [{'name': 'emaki-config', 'version': release, 'depends': [portal, 'xdg-desktop-portal-gtk']},
             {'name': 'emaki-desktop', 'version': release, 'depends': desktop},
             {'name': 'emaki', 'version': release,
              'depends': [f'emaki-config={release}', f'emaki-desktop={release}']}]
    if version == '0.1.2':
        stubs.append({'name': FORK, 'version': '50.0-1',
                      'depends': ['xdg-desktop-portal', 'xdg-desktop-portal-gtk>=1.10.0-2'],
                      'provides': [PORTAL, 'xdg-desktop-portal-impl'],
                      'conflicts': [PORTAL, 'xdg-desktop-portal-gtk<1.10.0-2'],
                      'replaces': ['xdg-desktop-portal-gtk<1.10.0-2']})
    return stubs


def stub_repository(directory, name, stubs):
    """A pacman repository of empty packages that carry only the given relations."""
    directory.mkdir(parents=True)
    for stub in stubs:
        stage = directory / 'stage' / stub['name']
        # Arch's portal and the fork own the same files, as the real packages do.
        owned = stage / ('usr/lib/xdg-desktop-portal-gnome' if stub['name'] in (PORTAL, FORK)
                         else f'usr/share/stub/{stub["name"]}')
        owned.parent.mkdir(parents=True)
        owned.write_text(stub['version'] + '\n')
        fields = [('pkgname', stub['name']), ('pkgbase', stub['name']), ('pkgver', stub['version']),
                  ('pkgdesc', 'stub'), ('builddate', '1'), ('size', '0'), ('arch', 'any')]
        fields += [(key, value) for field, key in (('depends', 'depend'), ('provides', 'provides'),
                                                    ('conflicts', 'conflict'), ('replaces', 'replaces'))
                   for value in stub.get(field, ())]
        (stage / '.PKGINFO').write_text(''.join(f'{key} = {value}\n' for key, value in fields))
        archive = directory / f'{stub["name"]}-{stub["version"]}-any.pkg.tar.zst'
        subprocess.run(['bsdtar', '--zstd', '--uid', '0', '--gid', '0', '-cf', str(archive),
                        '-C', str(stage), '.PKGINFO', 'usr'], check=True)
    subprocess.run(['repo-add', '-q', str(directory / f'{name}.db.tar.gz'),
                    *sorted(map(str, directory.glob('*.pkg.tar.zst')))], check=True, capture_output=True)
    return directory


@unittest.skipUnless(all(map(shutil.which, ('pacman', 'repo-add', 'bsdtar')))
                     and (os.geteuid() == 0 or shutil.which('fakeroot')),
                     'needs pacman, repo-add, bsdtar and fakeroot')
class PortalResolutionTests(unittest.TestCase):
    """Which portal each kind of machine ends up with, resolved by pacman in a scratch root.

    Installed systems list [emaki] after [core] and [extra] (the installer's worker.py since
    0.1.0). For each installed package libalpm looks for a replacement or an upgrade repository
    by repository and stops at the first one that carries the package's own name
    (alpm_sync_sysupgrade in lib/libalpm/sync.c), so [extra]'s xdg-desktop-portal-gnome hides
    the fork's replaces= from 0.1.0 and 0.1.1. A recipe that pulls the fork in by its name then
    meets the conflict question, whose default is No: `pacman -Syu` with Enter (or
    --noconfirm) aborts the whole update.
    """

    @classmethod
    def setUpClass(cls):
        cls.candidate = []
        for name in ('emaki', 'emaki-config', 'emaki-desktop', 'emaki-apps', FORK):
            info = metadata(ROOT / 'packaging' / name / 'PKGBUILD')
            cls.candidate.append({
                'name': name, 'version': f'{info["pkgver"][0]}-{info["pkgrel"][0]}',
                'depends': [d for d in info['depends'] if re.split(r'[<>=]', d)[0] in PORTAL_NAMES],
                'provides': info['provides'], 'conflicts': info['conflicts'], 'replaces': info['replaces']})
        cls.versions = {stub['name']: stub['version'] for stub in cls.candidate}

    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='emaki-portal-')))
        self.extra = stub_repository(self.work / 'extra', 'extra', ARCH_STUBS)
        self.emaki = stub_repository(self.work / 'emaki', 'emaki', self.candidate)

    def pacman(self, system, repos, *args, answers=None):
        """pacman on a scratch system; answers: what a person types, else --noconfirm (= Enter)."""
        for name in ('root', 'db', 'cache', 'hooks'):
            (system / name).mkdir(parents=True, exist_ok=True)
        (system / 'pacman.conf').write_text('[options]\nArchitecture = x86_64\nSigLevel = Never\n' + ''.join(
            f'[{name}]\nServer = file://{path}\n' for name, path in repos))
        argv = ['pacman', '--config', str(system / 'pacman.conf'), '--root', str(system / 'root'),
                '--dbpath', str(system / 'db'), '--cachedir', str(system / 'cache'),
                '--hookdir', str(system / 'hooks'), '--logfile', str(system / 'pacman.log'),
                '--noprogressbar', '--color', 'never', *(['--noconfirm'] if answers is None else []), *args]
        result = run(argv if os.geteuid() == 0 else ['fakeroot', '--', *argv], input=answers or '')
        return result.returncode, result.stdout + result.stderr

    def installed(self, system):
        result = run(['pacman', '--dbpath', str(system / 'db'), '-Q'])
        return dict(line.split() for line in result.stdout.splitlines())

    def upgrade(self, version, emaki_first=False):
        """Install <version> as its ISO left it, then `pacman -Syu --noconfirm` to the candidate."""
        self.system = self.work / f'{version}-{emaki_first}'
        old = self.work / f'emaki-{version}'
        if not old.exists():
            stub_repository(old, 'emaki', released(version))

        def repos(emaki):
            pair = [('extra', self.extra), ('emaki', emaki)]
            return pair[::-1] if emaki_first else pair

        code, output = self.pacman(self.system, repos(old), '-Sy', 'emaki')
        self.assertEqual(code, 0, output)
        # -yy: a scratch database can carry the same mtime second as the one it replaces.
        code, output = self.pacman(self.system, repos(self.emaki), '-Syyu')
        return code, output, self.installed(self.system)

    def test_old_installs_update_with_default_answers(self):
        for version in ('0.1.0', '0.1.1'):
            with self.subTest(start=version):
                code, output, installed = self.upgrade(version)
                self.assertNotIn('are in conflict', output)
                self.assertEqual(code, 0, output)
                for name in ('emaki', 'emaki-config', 'emaki-desktop'):
                    self.assertEqual(installed[name], self.versions[name])
                # Arch's portal stays until the person switches (docs/updates-runbook.md).
                self.assertEqual(installed.get(PORTAL), '50.0-1')
                self.assertNotIn(FORK, installed)

    def test_old_installs_switch_by_hand(self):
        # The release-note commands (docs/updates-runbook.md, step 11), answered as a person does.
        code, output, _ = self.upgrade('0.1.1')
        self.assertEqual(code, 0, output)
        repos = [('extra', self.extra), ('emaki', self.emaki)]
        code, output = self.pacman(self.system, repos, '-S', FORK, answers='y\n\n')
        self.assertIn(f'Remove {PORTAL}? [y/N]', output)
        self.assertEqual(code, 0, output)
        installed = self.installed(self.system)
        self.assertEqual(installed.get(FORK), self.versions[FORK])
        self.assertNotIn(PORTAL, installed)
        self.assertIn('nautilus', installed)  # nothing requires it any more, but pacman keeps it
        code, output = self.pacman(self.system, repos, '-Rs', 'nautilus', answers='\n')
        self.assertEqual(code, 0, output)
        installed = self.installed(self.system)
        self.assertNotIn('nautilus', installed)
        self.assertTrue({'emaki', 'emaki-desktop', FORK} <= installed.keys())

    def test_fork_replaces_where_emaki_is_checked_first(self):
        code, output, installed = self.upgrade('0.1.1', emaki_first=True)
        self.assertIn(f':: Replace {PORTAL} with emaki/{FORK}? [Y/n]', output)
        self.assertEqual(code, 0, output)
        self.assertEqual(installed.get(FORK), self.versions[FORK])
        self.assertNotIn(PORTAL, installed)

    def test_0_1_2_installs_keep_the_fork(self):
        code, output, installed = self.upgrade('0.1.2')
        self.assertEqual(code, 0, output)
        self.assertEqual(installed.get(FORK), self.versions[FORK])
        self.assertFalse({PORTAL, 'nautilus'} & installed.keys())

    def test_fresh_install_gets_the_fork(self):
        stubs = {stub['name']: stub for stub in ARCH_STUBS + self.candidate}
        # iso/build.sh: the offline closure of the live and target seeds, [emaki-offline] first.
        seeds = set((ROOT / 'iso/profile/packages.x86_64').read_text().split())
        seeds |= set((ROOT / 'iso/target-packages.txt').read_text().split())
        source = stub_repository(self.work / 'input-repo', 'emaki-offline', self.candidate)
        code, output = self.pacman(self.work / 'iso', [('emaki-offline', source), ('extra', self.extra)],
                                   '-Syp', '--print-format', '%n', *sorted(seeds & stubs.keys()))
        self.assertEqual(code, 0, output)
        closure = {line.strip() for line in output.splitlines()} & stubs.keys()
        self.assertIn(FORK, closure)
        self.assertFalse({PORTAL, 'nautilus'} & closure)
        # The installer resolves its list from [emaki-offline] alone (worker.preflight_repo).
        offline = stub_repository(self.work / 'offline', 'emaki-offline', [stubs[n] for n in closure])
        targets = set(runpy.run_path(str(ROOT / 'installer/emaki_installer/constants.py'))['PACKAGES'])
        target = self.work / 'target'
        code, output = self.pacman(target, [('emaki-offline', offline)], '-Sy',
                                   *sorted((targets | {'emaki-apps'}) & stubs.keys()))
        self.assertEqual(code, 0, output)
        self.assertIn(FORK, self.installed(target))
        # Its first update, with [emaki] after [extra] as the installer writes it.
        code, output = self.pacman(target, [('extra', self.extra), ('emaki', self.emaki)], '-Syyu')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.installed(target).get(FORK), self.versions[FORK])
        self.assertFalse({PORTAL, 'nautilus'} & self.installed(target).keys())


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
worker.plan = Mock(config={'mode': 'erase'})
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
        self.assertEqual(run([str(binary), 'version']).stdout, 'emaki 0.2.0\n')
        result = run(['python3', 'scripts/core-package.py', 'verify-build-paths', '--binary', str(binary)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_defaults_and_presets(self):
        commit = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
        self.assertEqual((self.dest / 'usr/lib/emaki-release').read_text(),
                         f'VERSION=0.2.0\nLABEL=alpha\nCHANNEL=stable\nEMAKI_COMMIT={commit}\n')
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

    def test_snapshot_boot_hook(self):
        # Under /usr/lib/initcpio, where pacman owns it and an update replaces it. Never in
        # /etc/initcpio: mkinitcpio reads that first, and 0.1.2's installer wrote its own copies
        # there; a package owning those paths would stop the upgrade ("exists in filesystem").
        for directory in ('hooks', 'install'):
            staged = self.dest / 'usr/lib/initcpio' / directory / 'emaki-snapshot-fstab'
            self.assertEqual(staged.read_bytes(), (ROOT / 'initcpio' / directory / 'emaki-snapshot-fstab').read_bytes())
        self.assertFalse((self.dest / 'etc/initcpio').exists())

    def test_skel_templates_name_only_shipped_files(self):
        # useradd copies these into a new home once; the copies are the person's (zone 3) and
        # no update rewrites them. Every package path they name must therefore keep existing.
        listed = set((ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines())
        templates = sorted(p for p in listed if p.startswith('/etc/skel/'))
        self.assertTrue(templates)
        for template in templates:
            named = re.findall(r'/usr/[^\s"\']+', (self.dest / template.lstrip('/')).read_text())
            self.assertTrue(named, template)
            for path in named:
                self.assertTrue(path in listed, f'{template} names {path}, which the package does not ship')
        # The person's niri file is never created from a template.
        self.assertFalse([p for p in listed if p.startswith('/etc/skel/.config/niri')])

    def test_zone_page(self):
        # The one table of who owns which file is on every installed system, not only in the repo.
        page = '/usr/share/doc/emaki/ZONES.md'
        listed = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        self.assertTrue(page in listed, f'{page} is not in expected-files.list')
        self.assertEqual((self.dest / page.lstrip('/')).read_bytes(), (ROOT / 'docs/ZONES.md').read_bytes())
        # The niri defaults are the file people open first; their header points at the page.
        header = (self.dest / 'usr/share/emaki/niri/default.kdl').read_text().split('\n\n', 1)[0]
        self.assertIn(page, header)

    def test_sleep_guard_wiring(self):
        unit = self.dest / 'usr/lib/systemd/user/emaki-sleep-guard.service'
        config = configparser.ConfigParser(interpolation=None)
        config.read(unit)
        self.assertEqual(config['Unit']['PartOf'], 'graphical-session.target')
        self.assertEqual(config['Install']['WantedBy'], 'graphical-session.target')
        self.assertEqual(config['Service']['ExecStart'], '/usr/bin/python3 -I /usr/bin/emaki-sleep-guard')
        self.assertEqual(config['Service']['Restart'], 'always')
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
