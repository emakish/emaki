#!/usr/bin/env python3
"""Probe KDE's real menu/MIME/palette APIs without using the session or home."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
tokens = tomllib.loads((ROOT / 'tokens.toml').read_text())['color']
with tempfile.TemporaryDirectory(prefix='emaki-kde-') as temporary:
    base = Path(temporary)
    generated = base / 'kdeglobals'
    subprocess.run(['python3', str(ROOT / 'scripts/render-kde-theme'), str(generated)], check=True)
    assert generated.read_bytes() == (ROOT / 'packaging/emaki-config/kdeglobals').read_bytes()
    # Dolphin's Open Terminal (KIO's KTerminalLauncherJob) reads [General] TerminalService, then
    # TerminalApplication; with neither it looks only for Konsole and xterm, which Emaki lacks.
    # Only TerminalApplication ships: a TerminalService in the system file would win over a
    # person's own TerminalApplication (see terminal() below).
    kde = configparser.ConfigParser(interpolation=None)
    kde.optionxform = str
    kde.read_string(generated.read_text())
    terminal = (kde['General'].get('TerminalService'), kde['General'].get('TerminalApplication'))
    assert terminal == (None, 'kitty'), terminal
    # Dolphin's own default status bar ("Small") elides its text; the system dolphinrc holds one key.
    dolphin = configparser.ConfigParser(interpolation=None)
    dolphin.optionxform = str
    dolphin.read_string((ROOT / 'packaging/emaki-config/dolphinrc').read_text())
    settings = {section: dict(dolphin[section]) for section in dolphin.sections()}
    assert settings == {'General': {'ShowStatusBar': 'FullWidth'}}, settings
    if (not shutil.which('kbuildsycoca6') or not shutil.which('kreadconfig6')
            or not Path('/usr/include/KF6/KColorScheme').exists()):
        raise SystemExit('UNVERIFIED: KDE runtime/development packages required for integration probe')
    flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Widgets'], text=True).split()
    subprocess.run(['c++', '-std=c++17', '-fPIC', str(ROOT / 'tests/fixtures/kde-defaults.cpp'),
                    '-o', str(base / 'probe'), *flags,
                    '-I/usr/include/KF6/KService', '-I/usr/include/KF6/KConfigCore',
                    '-I/usr/include/KF6/KConfig',
                    '-I/usr/include/KF6/KIOWidgets', '-I/usr/include/KF6/KIO',
                    '-I/usr/include/KF6/KColorScheme', '-I/usr/include/KF6/KCoreAddons',
                    '-lKF6Service', '-lKF6ConfigCore', '-lKF6ColorScheme', '-lKF6KIOWidgets'], check=True)
    for rich in (False, True):
        stage = base / ('rich' if rich else 'minimal')
        for directory in ('home', 'config', 'cache', 'data/applications', 'runtime', 'system/menus'):
            (stage / directory).mkdir(parents=True, mode=0o700)
        for name in ('mimeapps.list', 'kdeglobals'):
            shutil.copy(ROOT / 'packaging/emaki-config' / name, stage / 'system' / name)
        shutil.copy(ROOT / 'packaging/emaki-config/emaki-applications.menu', stage / 'system/menus')
        entries = {'org.kde.dolphin': 'inode/directory',
                   'firefox': 'application/pdf;image/png;x-scheme-handler/mailto'}
        if rich:
            entries['okularApplication_pdf'] = 'application/pdf'
            entries['org.kde.gwenview'] = 'image/png'
            entries['org.mozilla.Thunderbird'] = 'x-scheme-handler/mailto'
        for name, mime in entries.items():
            (stage / 'data/applications' / (name + '.desktop')).write_text(
                f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\nMimeType={mime};\n')
        env = dict(os.environ, HOME=str(stage / 'home'), XDG_CONFIG_HOME=str(stage / 'config'),
                   XDG_CONFIG_DIRS=str(stage / 'system'), XDG_DATA_HOME=str(stage / 'data'),
                   XDG_DATA_DIRS='/usr/share', XDG_CACHE_HOME=str(stage / 'cache'),
                   XDG_RUNTIME_DIR=str(stage / 'runtime'), XDG_MENU_PREFIX='emaki-',
                   XDG_CURRENT_DESKTOP='niri', QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(stage / 'no-bus'))
        subprocess.run(['kbuildsycoca6', '--noincremental'], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run([str(base / 'probe'), 'okularApplication_pdf.desktop' if rich else 'firefox.desktop',
                        '#' + tokens['background'], '#' + tokens['text'],
                        'org.kde.gwenview.desktop' if rich else 'firefox.desktop'], env=env, check=True)
        for mime, expected in (
            ('application/pdf', 'okularApplication_pdf.desktop' if rich else 'firefox.desktop'),
            ('image/png', 'org.kde.gwenview.desktop' if rich else 'firefox.desktop'),
        ):
            output = subprocess.check_output(['gio', 'mime', mime], env=dict(env, LC_ALL='C'), text=True)
            assert output.splitlines()[0].endswith(': ' + expected), output
        print('PASS: GIO PDF/PNG defaults for ' + ('Rich' if rich else 'Minimal'))
        # Only the staged entries: a mail client installed on the host must not answer.
        (stage / 'empty').mkdir()
        output = subprocess.check_output(['gio', 'mime', 'x-scheme-handler/mailto'],
                                         env=dict(env, LC_ALL='C', XDG_DATA_DIRS=str(stage / 'empty')), text=True)
        expected = 'org.mozilla.Thunderbird.desktop' if rich else 'firefox.desktop'
        assert output.splitlines()[0].endswith(': ' + expected), output
        print('PASS: GIO mailto default for ' + ('Rich' if rich else 'Minimal'))
    # Dolphin reads like KSharedConfig::openConfig(): dolphinrc plus the kdeglobals cascade
    # (kreadconfig6 --include-globals); system files in XDG_CONFIG_DIRS, the person's in XDG_CONFIG_HOME.
    stage = base / 'cascade'
    for directory in ('home', 'config', 'system'):
        (stage / directory).mkdir(parents=True, mode=0o700)
    for name in ('kdeglobals', 'dolphinrc'):
        shutil.copy(ROOT / 'packaging/emaki-config' / name, stage / 'system')
    env = dict(os.environ, HOME=str(stage / 'home'), XDG_CONFIG_HOME=str(stage / 'config'),
               XDG_CONFIG_DIRS=str(stage / 'system'), QT_QPA_PLATFORM='offscreen')

    def dolphin_setting(key):
        return subprocess.check_output(['kreadconfig6', '--file', 'dolphinrc', '--include-globals',
                                        '--group', 'General', '--key', key], env=env, text=True).strip()

    def terminal():
        # KIO 6.30 serviceFromConfig() (src/gui/kterminallauncherjob.cpp), behind Dolphin's
        # Open Terminal (Shift+F4): a non-empty TerminalService names a desktop entry and
        # wins; only without it is TerminalApplication the command.
        service = dolphin_setting('TerminalService')
        return ('service', service) if service else ('command', dolphin_setting('TerminalApplication'))

    assert terminal() == ('command', 'kitty'), terminal()
    print('PASS: Dolphin reads kitty as its terminal from the system kdeglobals')
    # A person's own choice in ~/.config/kdeglobals wins over the system file.
    (stage / 'config/kdeglobals').write_text('[General]\nTerminalApplication=foot\n')
    assert terminal() == ('command', 'foot'), terminal()
    (stage / 'config/kdeglobals').unlink()
    print('PASS: a person\'s TerminalApplication wins over Emaki\'s terminal')
    assert dolphin_setting('ShowStatusBar') == 'FullWidth'
    # The person's own ~/.config/dolphinrc is parsed after the system file and wins.
    (stage / 'config/dolphinrc').write_text('[General]\nShowStatusBar=Small\n')
    assert dolphin_setting('ShowStatusBar') == 'Small'
    print('PASS: Dolphin status bar FullWidth by default, the person\'s dolphinrc wins')
