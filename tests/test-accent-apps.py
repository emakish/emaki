#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""kitty, fuzzel, hyprlock and Qt windows follow the accent chosen in Settings (DECISIONS 2026-10-15).

The real core writes the session's files (`emaki settings session-config`, the path that
builds the niri border); real kitty reads the packaged theme, the real `emaki-config-path`
picks fuzzel's file (real fuzzel checks it) and `emaki-lock` picks hyprlock's; real qt6ct in a
Qt application (qml6) reads the person's qt6ct.conf, real KConfig (kreadconfig6) reads kdeglobals through
the session's XDG_CONFIG_DIRS. Never chosen, a missing or a foreign file: the packaged default
(Rose), exactly as before.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent
BINARY = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
TOKENS = tomllib.loads((ROOT / 'tokens.toml').read_text())
ROSE = TOKENS['accents'][TOKENS['defaults']['accent']]
ORANGE = TOKENS['accents']['orange']
HEADER = '# Emaki accent: written by the session from settings.toml; edits are replaced.\n'
LEGACY_QT6CT = (b'[Appearance]\ncolor_scheme_path=/usr/share/emaki/qt6ct/emaki.conf\n'
                b'custom_palette=true\n')
QT6CT_MIGRATION_LOG = 'Emaki: migrated the unchanged qt6ct configuration to the session accent.\n'
DATADIR = next(line.split('=', 1)[1] for line in (ROOT / 'scripts/paths').read_text().splitlines()
               if line.startswith('EMAKI_DATADIR='))
loader = importlib.machinery.SourceFileLoader('emaki_lock', str(ROOT / 'scripts/emaki-lock'))
spec = importlib.util.spec_from_loader('emaki_lock', loader)
LOCK = importlib.util.module_from_spec(spec)
loader.exec_module(LOCK)
KITTY_KEYS = ('selection_background', 'selection_foreground', 'url_color', 'active_border_color',
              'foreground', 'background')
QT_PROBE = '''
import QtQuick
import QtQuick.Window
// The application palette that qt6ct, the platform theme, gives every Qt window.
Window {
    id: window
    width: 10; height: 10
    SystemPalette { id: system; colorGroup: SystemPalette.Active }
    Timer {
        interval: 50; running: true
        onTriggered: {
            console.log("PALETTE:" + JSON.stringify({
                Highlight: String(system.highlight), HighlightedText: String(system.highlightedText),
                Link: String(window.palette.link), Window: String(system.window),
                WindowText: String(system.windowText)}));
            Qt.quit();
        }
    }
}
'''
KITTY_PROBE = '''
import json, sys
from kitty.config import load_config
bad = []
o = load_config(sys.argv[-1], accumulate_bad_lines=bad)  # as kitty itself loads it
print(json.dumps({k: bytes((getattr(o, k).red, getattr(o, k).green, getattr(o, k).blue)).hex()
                  for k in KEYS} | {'bad_lines': len(bad)}))
'''.replace('KEYS', repr(KITTY_KEYS))


def ini(text, datadir=Path(DATADIR)):
    """fuzzel's effective [colors]: an include is read in place, later keys win."""
    values, section = {}, 'main'
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            section = line.strip('[]')
        elif line.startswith('include='):
            included = Path(line.split('=', 1)[1])
            values.update(ini((ROOT / 'fuzzel' / included.name).read_text())
                          if included.parent == datadir / 'fuzzel' else {})
            section = 'main'
        else:
            key, value = line.split('=', 1)
            values[(section, key)] = value
    return values


class Session:
    def __init__(self, base, accent=None, theme=None):
        self.base = Path(base)
        for name in ('home', 'config', 'state', 'runtime', 'cache'):
            (self.base / name).mkdir(mode=0o700)
        self.env = {k: v for k, v in os.environ.items() if k not in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY')}
        self.env.update(HOME=str(self.base / 'home'), XDG_CONFIG_HOME=str(self.base / 'config'),
                        XDG_STATE_HOME=str(self.base / 'state'), XDG_RUNTIME_DIR=str(self.base / 'runtime'),
                        XDG_CACHE_HOME=str(self.base / 'cache'), PYTHONDONTWRITEBYTECODE='1')
        lines = [f'{key} = "{value}"' for key, value in (('accent', accent), ('theme', theme)) if value]
        if lines:
            (self.base / 'config/emaki').mkdir(mode=0o700)
            (self.base / 'config/emaki/settings.toml').write_text(
                'schema_version = 1\n\n[appearance]\n' + '\n'.join(lines) + '\n')
        self.accent = self.base / 'runtime/emaki-settings/accent'
        # /etc/skel's qt6ct.conf, as every new home has it; the packaged kdeglobals as /etc/xdg.
        self.qt6ct = self.base / 'config/qt6ct/qt6ct.conf'
        self.qt6ct.parent.mkdir()
        shutil.copy(ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf', self.qt6ct)
        (self.base / 'etc-xdg/hypr').mkdir(parents=True)
        shutil.copy(ROOT / 'packaging/emaki-config/kdeglobals', self.base / 'etc-xdg/kdeglobals')
        shutil.copy(ROOT / 'hypr/hyprlock.conf', self.base / 'etc-xdg/hypr/hyprlock.conf')
        (self.base / 'etc-xdg/qt6ct').mkdir()
        shutil.copy(ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf', self.base / 'etc-xdg/qt6ct/qt6ct.conf')
        self.env['XDG_CONFIG_DIRS'] = str(self.base / 'etc-xdg')  # as /etc/xdg on an installed system
        # The default state directory, ~/.local/state, which the packaged qt6ct.conf names.
        del self.env['XDG_STATE_HOME']
        self.scheme = self.base / 'home/.local/state/emaki/accent/qt6ct.conf'
        self.kdeglobals = self.base / 'home/.local/state/emaki/defaults/kdeglobals'

    def config_dirs(self):
        """XDG_CONFIG_DIRS exactly as scripts/niri-emaki-session builds it for the session."""
        text = (ROOT / 'scripts/niri-emaki-session').read_text()
        start = text.index('case "${XDG_STATE_HOME:-}" in')
        block = text[start:text.index('export XDG_CONFIG_DIRS', start)]
        env = dict(self.env, XDG_CONFIG_DIRS=str(self.base / 'etc-xdg'), EMAKI_DATADIR=DATADIR)
        result = subprocess.run(['bash', '-c', block + 'printf %s "$XDG_CONFIG_DIRS"'], env=env,
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0 and not result.stderr, result
        return result.stdout

    def qt(self):
        """Effective colours: real qt6ct in a real QApplication, real KConfig for kdeglobals."""
        probe = self.base / f'qt-probe-{len(list(self.base.glob("qt-probe-*")))}'
        (probe / 'qt6ct').mkdir(parents=True)
        # The person's file as it is; only the packaged scheme comes from this repository. Without
        # one, qt6ct itself copies the first qt6ct/qt6ct.conf on XDG_CONFIG_DIRS.
        if self.qt6ct.is_file():
            (probe / 'qt6ct/qt6ct.conf').write_text(self.qt6ct.read_text().replace(
                f'{DATADIR}/qt6ct/emaki.conf', str(ROOT / 'qt6ct/emaki.conf')))
        (probe / 'palette.qml').write_text(QT_PROBE)
        env = dict(self.env, XDG_CONFIG_HOME=str(probe), QT_QPA_PLATFORM='offscreen',
                   QT_QPA_PLATFORMTHEME='qt6ct', QT_LOGGING_RULES='qml.debug=true',
                   QT_FORCE_STDERR_LOGGING='1', DBUS_SESSION_BUS_ADDRESS='disabled:')
        result = subprocess.run(['qml6', '--apptype', 'gui', str(probe / 'palette.qml')], env=env,
                                text=True, capture_output=True, timeout=30)
        lines = [line.split('PALETTE:', 1)[1] for line in result.stderr.splitlines() if 'PALETTE:' in line]
        assert result.returncode == 0 and len(lines) == 1, result
        colours = {key: value.lstrip('#') for key, value in json.loads(lines[0]).items()}
        env = dict(self.env, XDG_CONFIG_DIRS=self.config_dirs())
        for group, key in (('Selection', 'BackgroundNormal'), ('Selection', 'ForegroundNormal'),
                           ('View', 'DecorationFocus'), ('Button', 'DecorationHover'),
                           ('View', 'ForegroundLink'), ('View', 'BackgroundNormal')):
            result = subprocess.run(['kreadconfig6', '--file', 'kdeglobals', '--group', f'Colors:{group}',
                                     '--key', key], env=env, text=True, capture_output=True, timeout=10)
            assert result.returncode == 0, result
            colours[f'kde {group} {key}'] = bytes(int(n) for n in result.stdout.split(',')).hex()
        return colours

    def start(self, expected_stderr=''):
        result = subprocess.run([BINARY, 'settings', 'session-config'], env=self.env, text=True,
                                capture_output=True, timeout=30)
        assert result.returncode == 0 and result.stderr == expected_stderr, result
        return self

    def kitty(self):
        conf = self.base / 'home/kitty.conf'
        # /etc/skel's kitty.conf, pointed at the repository's theme.
        conf.write_text((ROOT / 'etc-skel/.config/kitty/kitty.conf').read_text().replace(
            f'{DATADIR}/kitty/theme.conf', str(ROOT / 'kitty/theme.conf')))
        result = subprocess.run(['kitty', '+runpy', KITTY_PROBE, str(conf)], env=self.env, text=True,
                                capture_output=True, timeout=60)
        assert result.returncode == 0, result
        return json.loads(result.stdout)

    def fuzzel(self):
        result = subprocess.run(['sh', ROOT / 'scripts/emaki-config-path', 'fuzzel', 'fuzzel.ini'],
                                env=self.env, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0 and not result.stderr, result
        return Path(result.stdout.strip())

    def hyprlock(self):
        return LOCK.hyprlock_config(self.env)


class AccentAppsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='accent-apps-', dir=ROOT / '.cache')
        self.serial = 0

    def tearDown(self):
        self.temporary.cleanup()

    def session(self, accent=None, theme=None):
        self.serial += 1
        base = Path(self.temporary.name, str(self.serial))
        base.mkdir()
        return Session(base, accent, theme)

    def assert_kitty(self, colours, accent):
        self.assertEqual(colours['selection_background'], accent['base'])
        self.assertEqual(colours['url_color'], accent['base'])
        self.assertEqual(colours['bad_lines'], 0)
        self.assertEqual(colours['active_border_color'], accent['base'])
        self.assertEqual(colours['selection_foreground'], accent['text'])
        self.assertEqual(colours['foreground'], TOKENS['color']['text'])
        self.assertEqual(colours['background'], TOKENS['color']['background'])

    def assert_packaged(self, session):
        self.assert_kitty(session.kitty(), ROSE)
        self.assertEqual(session.fuzzel(), Path(DATADIR, 'fuzzel/fuzzel.ini'))
        self.assertEqual(session.hyprlock(), [])

    def test_chosen_orange_reaches_kitty_fuzzel_and_hyprlock_in_light_and_dark(self):
        for theme in ('light', 'dark', None):
            with self.subTest(theme=theme):
                session = self.session('orange', theme).start()
                self.assert_kitty(session.kitty(), ORANGE)
                path = session.fuzzel()
                self.assertEqual(path, session.accent / 'fuzzel/fuzzel.ini')
                text = path.read_text()
                self.assertTrue(text.startswith(HEADER))
                effective, packaged = ini(text), ini((ROOT / 'fuzzel/fuzzel.ini').read_text())
                for key in ('match', 'selection', 'border'):
                    self.assertEqual(effective['colors', key], ORANGE['base'] + 'ff')
                for key in ('selection-text', 'selection-match'):
                    self.assertEqual(effective['colors', key], ORANGE['text'] + 'ff')
                # Everything else is the packaged file's.
                self.assertEqual({k: v for k, v in effective.items() if v != packaged.get(k)},
                                 {k: v for k, v in effective.items() if ORANGE['base'] in v or ORANGE['text'] in v})
                self.assertEqual(set(effective), set(packaged))
                # Real fuzzel accepts the file (its include pointed at the repository's).
                check = session.base / 'fuzzel-check.ini'
                check.write_text(text.replace(f'{DATADIR}/fuzzel/fuzzel.ini', str(ROOT / 'fuzzel/fuzzel.ini')))
                result = subprocess.run(['fuzzel', '--config', check, '--check-config'], env=session.env,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result)
                argv = session.hyprlock()
                self.assertEqual(argv, ['--config', str(session.accent / 'hypr/hyprlock.conf')])
                lock = Path(argv[1]).read_text()
                self.assertIn(f'outer_color = rgb({ORANGE["base"]})', lock)
                self.assertIn(f'check_color = rgb({ORANGE["base"]})', lock)
                self.assertEqual(lock, HEADER + (ROOT / 'hypr/hyprlock.conf').read_text().replace(
                    ROSE['base'], ORANGE['base']))

    def test_never_chosen_and_chosen_rose_are_the_packaged_look(self):
        for accent, theme in ((None, None), (None, 'light'), ('rose', 'dark'), ('rose', 'light')):
            with self.subTest(accent=accent, theme=theme):
                session = self.session(accent, theme).start()
                self.assert_packaged(session)
                self.assertEqual(sorted(p.name for p in session.accent.iterdir()), ['kitty.conf'])

    def test_missing_or_foreign_files_fall_back_to_the_packaged_default(self):
        # Before the session wrote anything, and with no runtime directory at all.
        session = self.session('orange')
        self.assert_packaged(session)
        del session.env['XDG_RUNTIME_DIR']
        self.assert_packaged(session)
        session = self.session('orange').start()
        session.env['XDG_RUNTIME_DIR'] = str(session.base / 'runtime')
        fuzzel = session.accent / 'fuzzel/fuzzel.ini'
        lock = session.accent / 'hypr/hyprlock.conf'
        kitty = session.accent / 'kitty.conf'
        # Unreadable values and garbage: kitty keeps the packaged value of each bad line.
        kitty.write_text(HEADER + 'selection_background not-a-colour\nurl_color\n\0\xff garbage\n')
        colours = session.kitty()
        self.assertEqual(colours['selection_background'], ROSE['base'])
        self.assertEqual(colours['url_color'], ROSE['base'])
        self.assertGreater(colours['bad_lines'], 0)  # kitty reports them and starts
        kitty.unlink()
        self.assert_kitty(session.kitty(), ROSE)
        # A copy without the session's first line (edited, truncated, empty) is not used.
        for broken in ('', '[colors]\nselection=00000000\n', HEADER[:-2]):
            fuzzel.write_text(broken)
            lock.write_text(broken)
            self.assertEqual(session.fuzzel(), Path(DATADIR, 'fuzzel/fuzzel.ini'))
            self.assertEqual(session.hyprlock(), [])
        # Nor one whose header is intact but whose body is not exactly what the session writes:
        # the header alone, an invalid or truncated or extended body, a broken colour.
        session.start()
        fuzzel_text, lock_text = fuzzel.read_text(), lock.read_text()
        kitty.unlink()  # rewritten by that start; the rest of this test expects it gone
        for broken in (HEADER, HEADER + '[colors]\nselection=invalid\n', fuzzel_text[:len(fuzzel_text) // 2],
                       fuzzel_text + 'background=000000ff\n',
                       fuzzel_text.replace(f'border={ORANGE["base"]}ff', 'border=invalid')):
            with self.subTest(fuzzel=broken[len(HEADER):][:40]):
                fuzzel.write_text(broken)
                self.assertEqual(session.fuzzel(), Path(DATADIR, 'fuzzel/fuzzel.ini'))
                check = session.base / 'fuzzel-broken.ini'
                check.write_text(broken.replace(f'{DATADIR}/fuzzel/fuzzel.ini', str(ROOT / 'fuzzel/fuzzel.ini')))
        for broken in (HEADER, lock_text[:len(lock_text) // 2], lock_text + 'general {\n}\n',
                       lock_text.replace('rgb(', 'rgba(', 1), lock_text.replace(ORANGE['base'], 'zz' * 3, 1),
                       # R9A2: a non-accent colour swapped (password text = its background).
                       lock_text.replace(TOKENS['color']['text'], TOKENS['color']['background']),
                       # Or the accent swapped for a colour that is no accent.
                       lock_text.replace(ORANGE['base'], '123456')):
            with self.subTest(hyprlock=broken[len(HEADER):][:40]):
                lock.write_text(broken)
                self.assertEqual(session.hyprlock(), [])
        # A copy from another release than the installed packaged file: the packaged one.
        lock.write_text(lock_text)
        self.assertEqual(session.hyprlock(), ['--config', str(lock)])
        packaged_lock = session.base / 'etc-xdg/hypr/hyprlock.conf'
        packaged_lock.write_text(packaged_lock.read_text() + '# a later release\n')
        self.assertEqual(session.hyprlock(), [])
        packaged_lock.write_text((ROOT / 'hypr/hyprlock.conf').read_text())
        # Neither a link to another file nor a directory in its place.
        for path in (fuzzel, lock):
            path.unlink()
            path.symlink_to(session.base / 'elsewhere')
        (session.base / 'elsewhere').write_text(HEADER)
        self.assertEqual(session.fuzzel(), Path(DATADIR, 'fuzzel/fuzzel.ini'))
        self.assertEqual(session.hyprlock(), [])
        for path in (fuzzel, lock):
            path.unlink()
            path.mkdir()
        self.assertEqual(session.fuzzel(), Path(DATADIR, 'fuzzel/fuzzel.ini'))
        self.assertEqual(session.hyprlock(), [])
        for path in (fuzzel, lock):
            path.rmdir()
        self.assert_packaged(session)

    def test_the_persons_own_configs_still_win(self):
        session = self.session('orange').start()
        own = session.base / 'config'
        (own / 'fuzzel').mkdir()
        (own / 'fuzzel/fuzzel.ini').write_text('[colors]\n')
        (own / 'hypr').mkdir()
        (own / 'hypr/hyprlock.conf').write_text('general {\n}\n')
        self.assertEqual(session.fuzzel(), own / 'fuzzel/fuzzel.ini')
        self.assertEqual(session.hyprlock(), [])
        # A kitty setting after the theme's include line is the person's and stays.
        conf = session.base / 'home/kitty.conf'
        session.kitty()
        conf.write_text(conf.read_text() + 'selection_background #010203\n')
        result = subprocess.run(['kitty', '+runpy', KITTY_PROBE, str(conf)], env=session.env, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(json.loads(result.stdout)['selection_background'], '010203')

    def assert_qt(self, colours, accent):
        self.assertEqual(colours['Highlight'], accent['base'], colours)
        self.assertEqual(colours['HighlightedText'], accent['text'], colours)
        self.assertEqual(colours['Link'], accent['base'], colours)
        for key in ('kde Selection BackgroundNormal', 'kde View DecorationFocus',
                    'kde Button DecorationHover', 'kde View ForegroundLink'):
            self.assertEqual(colours[key], accent['base'], (key, colours))
        self.assertEqual(colours['kde Selection ForegroundNormal'], accent['text'], colours)
        # The rest of the palette is the packaged one.
        self.assertEqual(colours['Window'], TOKENS['color']['background'])
        self.assertEqual(colours['WindowText'], TOKENS['color']['text'])
        self.assertEqual(colours['kde View BackgroundNormal'], TOKENS['color']['background'])

    def test_qt_windows_follow_chosen_orange_in_light_and_dark(self):
        skel = (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_text()
        for theme in ('light', 'dark', None):
            with self.subTest(theme=theme):
                session = self.session('orange', theme)
                # Before any Emaki session wrote the scheme the packaged default names, qt6ct
                # finds no file and Qt shows its own plain palette (DECISIONS 2026-10-15).
                before = session.qt()
                self.assertNotIn(before['Highlight'], (ROSE['base'], ORANGE['base']))
                self.assertNotEqual(before['Window'], TOKENS['color']['background'])
                session.start()
                self.assertEqual(session.qt6ct.read_text(), skel)  # the person's file: never written
                self.assertTrue(session.scheme.read_text().startswith(HEADER))
                self.assert_qt(session.qt(), ORANGE)
                # The session's layer is the one niri-emaki-session puts above the packaged file.
                self.assertEqual(session.config_dirs().split(':')[0], str(session.kdeglobals.parent))

    def test_qt_never_chosen_and_chosen_rose_are_the_packaged_look(self):
        skel = (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_text()
        for accent, theme in ((None, None), (None, 'light'), ('rose', 'dark')):
            with self.subTest(accent=accent, theme=theme):
                session = self.session(accent, theme).start()
                self.assertEqual(session.qt6ct.read_text(), skel)  # never touched
                self.assertFalse(session.kdeglobals.exists())
                self.assertEqual(session.scheme.read_text(),
                                 HEADER + (ROOT / 'qt6ct/emaki.conf').read_text())
                self.assert_qt(session.qt(), ROSE)

    def test_an_unchanged_old_home_follows_the_accent_after_one_migration(self):
        skel = (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_bytes()
        for accent in ('orange', 'teal', None):
            with self.subTest(accent=accent):
                session = self.session(accent)
                session.qt6ct.write_bytes(LEGACY_QT6CT)
                session.start(expected_stderr=QT6CT_MIGRATION_LOG)
                self.assertEqual(session.qt6ct.read_bytes(), skel)
                self.assertEqual(sorted(p.name for p in session.qt6ct.parent.iterdir()), ['qt6ct.conf'])
                expected = TOKENS['accents'][accent] if accent else ROSE
                self.assert_qt(session.qt(), expected)
                migrated = session.qt6ct.stat()
                session.start()  # A second start neither rewrites the selector nor logs again.
                self.assertEqual(session.qt6ct.read_bytes(), skel)
                unchanged = session.qt6ct.stat()
                self.assertEqual((unchanged.st_ino, unchanged.st_mtime_ns),
                                 (migrated.st_ino, migrated.st_mtime_ns))
                self.assert_qt(session.qt(), expected)

    def test_an_old_home_with_a_different_state_directory_keeps_rose(self):
        session = self.session('orange')
        state = session.base / 'state'
        session.env['XDG_STATE_HOME'] = str(state)
        session.qt6ct.write_bytes(LEGACY_QT6CT)
        before = session.qt6ct.stat()
        session.start()
        self.assertEqual(session.qt6ct.read_bytes(), LEGACY_QT6CT)
        after = session.qt6ct.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns, after.st_mode),
                         (before.st_ino, before.st_mtime_ns, before.st_mode))
        self.assertFalse((session.base / 'home/.local').exists())
        self.assertTrue((state / 'emaki/accent/qt6ct.conf').is_file())
        self.assertFalse((state / 'emaki/defaults/kdeglobals').exists())
        self.assert_qt(session.qt(), ROSE)

    def test_an_old_home_with_explicit_default_state_follows_the_accent(self):
        session = self.session('orange')
        session.env['XDG_STATE_HOME'] = str(session.base / 'home/.local/state')
        session.qt6ct.write_bytes(LEGACY_QT6CT)
        session.start(expected_stderr=QT6CT_MIGRATION_LOG)
        self.assertEqual(session.qt6ct.read_bytes(),
                         (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_bytes())
        self.assertTrue(session.scheme.is_file())
        self.assertTrue(session.kdeglobals.is_file())
        self.assert_qt(session.qt(), ORANGE)

    def test_a_hardlinked_old_selector_is_not_migrated(self):
        session = self.session('orange')
        session.qt6ct.write_bytes(LEGACY_QT6CT)
        linked = session.base / 'dotfiles-qt6ct.conf'
        os.link(session.qt6ct, linked)
        before = session.qt6ct.stat()
        session.start()
        for path in (session.qt6ct, linked):
            self.assertEqual(path.read_bytes(), LEGACY_QT6CT)
            after = path.stat()
            self.assertEqual((after.st_ino, after.st_mtime_ns, after.st_mode, after.st_nlink),
                             (before.st_ino, before.st_mtime_ns, before.st_mode, 2))
        self.assertFalse(session.kdeglobals.exists())
        self.assert_qt(session.qt(), ROSE)

    def test_migration_preserves_the_old_selector_permissions(self):
        for mode in (0o644, 0o600):
            with self.subTest(mode=oct(mode)):
                session = self.session('orange')
                session.qt6ct.write_bytes(LEGACY_QT6CT)
                session.qt6ct.chmod(mode)
                session.start(expected_stderr=QT6CT_MIGRATION_LOG)
                self.assertEqual(session.qt6ct.read_bytes(),
                                 (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_bytes())
                self.assertEqual(session.qt6ct.stat().st_mode & 0o7777, mode)
                self.assert_qt(session.qt(), ORANGE)

    def test_a_home_with_its_own_qt6ct_conf_keeps_it_and_one_look(self):
        # An edited pre-0.9 selector stays the person's file: Qt and KDE both stay Rose
        # (no KDE accent layer), whatever accent is chosen. Even a comment counts as an edit.
        for accent in ('orange', 'teal'):
            with self.subTest(accent=accent):
                session = self.session(accent)
                old = LEGACY_QT6CT + b'# Keep my palette.\n'
                session.qt6ct.write_bytes(old)
                mode = session.qt6ct.stat().st_mode
                session.start()
                self.assertEqual(session.qt6ct.read_bytes(), old)
                self.assertEqual(session.qt6ct.stat().st_mode, mode)
                self.assertEqual(sorted(p.name for p in session.qt6ct.parent.iterdir()), ['qt6ct.conf'])
                self.assertFalse(session.kdeglobals.exists())
                self.assert_qt(session.qt(), ROSE)
        # A home without a qt6ct.conf: nothing is created; qt6ct's own copy of the packaged
        # default will name the session scheme, so the KDE layer is written.
        session = self.session('orange')
        session.qt6ct.unlink()
        session.start()
        self.assertFalse(session.qt6ct.exists())
        self.assertTrue(session.kdeglobals.exists())

    def test_emaki_lock_knows_exactly_the_accents_of_tokens(self):
        self.assertEqual({name: (base.decode(), text.decode()) for name, (base, text) in LOCK.ACCENTS.items()},
                         {name: (a['base'], a['text']) for name, a in TOKENS['accents'].items()})
        self.assertEqual(LOCK.DEFAULT_ACCENT, TOKENS['defaults']['accent'])

    def test_a_fifo_as_qt6ct_conf_never_blocks_the_session_start(self):
        # R9A2: a FIFO where qt6ct.conf belongs hung `settings session-config`.
        session = self.session('orange')
        session.qt6ct.unlink()
        os.mkfifo(session.qt6ct)
        result = subprocess.run([BINARY, 'settings', 'session-config'], env=session.env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result)
        self.assertTrue(session.scheme.is_file())
        self.assertFalse(session.kdeglobals.exists())

    def test_qt_and_kde_agree_for_every_spelling_qt6ct_accepts(self):
        # R9A2: each spelling gave Orange Qt with Rose KDE or the reverse. qt6ct expands ~ and
        # $NAME/ (an unset one to nothing) and QSettings reads quotes and booleans its own way.
        relative = '.local/state/emaki/accent/qt6ct.conf'
        cases = (('$HOME/' + relative, 'true', None, True),
                 ('$XDG_STATE_HOME/emaki/accent/qt6ct.conf', 'true', 'home/.local/state', True),
                 ('$XDG_STATE_HOME/emaki/accent/qt6ct.conf', 'true', None, False),
                 ('$EMAKI_UNSET_FOR_TESTS/{base}/home/' + relative, 'true', None, True),
                 ('"~/' + relative + '"', 'true', None, True),
                 ('~/' + relative, 'True', None, True),
                 ('~/' + relative, '"1"', None, True),
                 ('~/' + relative, 'false', None, False),
                 ('~/' + relative, '0', None, False),
                 ('~/' + relative + ', /elsewhere', 'true', None, False))
        for path, custom, state, orange in cases:
            with self.subTest(path=path, custom=custom, state=state):
                session = self.session('orange')
                if state:
                    session.env['XDG_STATE_HOME'] = str(session.base / state)
                path = path.replace('{base}', str(session.base).lstrip('/'))
                session.qt6ct.write_text(f'[Appearance]\ncolor_scheme_path={path}\ncustom_palette={custom}\n')
                colours = session.start().qt()
                qt = colours['Highlight'] == ORANGE['base']
                kde = colours['kde Selection BackgroundNormal'] == ORANGE['base']
                self.assertEqual((qt, kde), (orange, orange), colours)
                if not orange:
                    self.assertEqual(colours['kde Selection BackgroundNormal'], ROSE['base'])

    def test_without_a_personal_qt6ct_conf_the_copied_system_one_decides(self):
        # R9A2: no personal file was taken for the packaged default, but qt6ct copies the first
        # qt6ct/qt6ct.conf on XDG_CONFIG_DIRS, which may name another scheme.
        for first, orange in (('other', False), ('packaged', True)):
            with self.subTest(first=first):
                session = self.session('orange')
                session.qt6ct.unlink()
                dirs = [session.base / 'etc-xdg']  # holds the packaged qt6ct.conf, as /etc/xdg does
                if first == 'other':
                    (session.base / 'site/qt6ct').mkdir(parents=True)
                    (session.base / 'site/qt6ct/qt6ct.conf').write_text(
                        f'[Appearance]\ncolor_scheme_path={ROOT}/qt6ct/emaki.conf\ncustom_palette=true\n')
                    dirs.insert(0, session.base / 'site')
                session.env['XDG_CONFIG_DIRS'] = ':'.join(map(str, dirs))
                colours = session.start().qt()
                expected = ORANGE if orange else ROSE
                self.assertEqual(colours['Highlight'], expected['base'], colours)
                self.assertEqual(colours['kde Selection BackgroundNormal'], expected['base'], colours)
                self.assertFalse(session.qt6ct.exists())

    def test_a_custom_state_directory_is_all_the_session_writes(self):
        # XDG_STATE_HOME elsewhere (as every test of the core sets it): the scheme goes there and
        # nothing is written below HOME; the packaged ~/… then names no file, so no KDE layer.
        session = self.session('orange')
        state = session.base / 'state'
        session.env['XDG_STATE_HOME'] = str(state)
        session.start()
        self.assertTrue((state / 'emaki/accent/qt6ct.conf').read_text().startswith(HEADER))
        self.assertFalse((session.base / 'home/.local').exists())
        self.assertFalse((state / 'emaki/defaults/kdeglobals').exists())
        # A qt6ct.conf naming that file follows it, Qt and KDE alike.
        session.qt6ct.write_text(f'[Appearance]\ncolor_scheme_path={state}/emaki/accent/qt6ct.conf\n'
                                 'custom_palette=true\n')
        session.start()
        self.assertTrue((state / 'emaki/defaults/kdeglobals').exists())
        session.kdeglobals = state / 'emaki/defaults/kdeglobals'
        self.assert_qt(session.qt(), ORANGE)

    def test_qt_back_to_default_missing_or_foreign_files(self):
        session = self.session('teal').start()
        teal = TOKENS['accents']['teal']
        self.assert_qt(session.qt(), teal)
        # Back to the default: Rose everywhere, the KDE layer removed.
        (session.base / 'config/emaki/settings.toml').write_text('schema_version = 1\n')
        session.start()
        self.assertEqual(session.qt6ct.read_text(), (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_text())
        self.assertFalse(session.kdeglobals.exists())
        self.assert_qt(session.qt(), ROSE)
        # A deleted or edited state file is rewritten at the next session start.
        (session.base / 'config/emaki/settings.toml').write_text(
            'schema_version = 1\n\n[appearance]\naccent = "orange"\n')
        session.scheme.unlink()
        session.kdeglobals.parent.joinpath('kdeglobals').write_text('[Colors:Selection]\ngarbage\n')
        session.start()
        self.assert_qt(session.qt(), ORANGE)
        # A garbage KDE layer: KConfig skips the bad lines and keeps the packaged values.
        session.kdeglobals.write_text('\0\xff[Colors:Selection\nBackgroundNormal=\n=x\n')
        colours = session.qt()
        self.assertEqual(colours['kde Selection BackgroundNormal'], ROSE['base'])
        self.assertEqual(colours['kde View DecorationFocus'], ROSE['base'])
        session.start()
        # The person's own kdeglobals wins over the layer.
        (session.base / 'config/kdeglobals').write_text('[Colors:Selection]\nBackgroundNormal=1,2,3\n')
        self.assertEqual(session.qt()['kde Selection BackgroundNormal'], '010203')
        # The person's own scheme wins and is never written.
        own = session.base / 'mine.conf'
        own.write_text((ROOT / 'qt6ct/emaki.conf').read_text().replace('#ffe780aa', '#ff010203'))
        text = f'[Appearance]\ncolor_scheme_path={own}\ncustom_palette=true\n'
        session.qt6ct.write_text(text)
        session.start()
        self.assertEqual(session.qt6ct.read_text(), text)
        self.assertEqual(session.qt()['Highlight'], '010203')
        # A linked qt6ct.conf (dotfiles) naming the session scheme follows it and stays a link.
        linked = session.base / 'dotfiles-qt6ct.conf'
        shutil.copy(ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf', linked)
        session.qt6ct.unlink()
        session.qt6ct.symlink_to(linked)
        (session.base / 'config/kdeglobals').unlink()
        session.start()
        self.assertEqual(linked.read_text(), (ROOT / 'etc-skel/.config/qt6ct/qt6ct.conf').read_text())
        self.assertTrue(session.qt6ct.is_symlink())
        self.assert_qt(session.qt(), ORANGE)

    def test_switching_back_to_the_default_removes_the_copies(self):
        session = self.session('orange').start()
        self.assertEqual(session.fuzzel(), session.accent / 'fuzzel/fuzzel.ini')
        (session.base / 'config/emaki/settings.toml').write_text('schema_version = 1\n')
        session.start()
        self.assert_packaged(session)


if __name__ == '__main__':
    assert BINARY is not None and BINARY.is_file(), 'Build the emaki binary before this test'
    for program in ('kitty', 'fuzzel', 'kreadconfig6', 'qml6'):
        assert shutil.which(program), f'{program} is required for this test'
    unittest.main()
