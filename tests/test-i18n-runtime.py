#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check Qt language preferences with partial catalogues in isolated processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeLanguages(unittest.TestCase):
    def test_library_text_resolves_after_late_plugin_load(self):
        with tempfile.TemporaryDirectory(prefix='i18n-js-') as temporary:
            base = Path(temporary)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                        'QT_QPA_PLATFORMTHEME'):
                env.pop(key, None)
            for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                        'XDG_DATA_HOME', 'XDG_STATE_HOME'):
                directory = base / key
                directory.mkdir(mode=0o700)
                env[key] = str(directory)
            env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                       EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), EMAKI_LANGUAGE='en_XA',
                       QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            fixture = base / 'test.qml'
            fixture.write_text('''import QtQuick
import Quickshell
import "SHELL/CaptureText.js" as Capture
import "SHELL/PolkitText.js" as Polkit
import "SHELL/SudoText.js" as Sudo
import "SHELL/SecureBoot.js" as Secure
import "SHELL/WelcomeContent.js" as Welcome
import "SHELL/settings/SettingsIndex.js" as Settings
ShellRoot {
    id: root
    function samples() {
        return [Capture.area(), Polkit.unavailable(), Sudo.unavailable(),
                Sudo.keyMinutesInput(), Secure.title(), Welcome.pages()[0].title,
                Settings.sections()[0].title, Settings.section("wifi").description];
    }
    Timer { interval: 10; running: true; onTriggered: {
        const before = root.samples();
        if (before.some(text => text.startsWith("["))) {
            console.error("EARLY_TRANSLATION"); Qt.exit(1); return;
        }
        Qt.createQmlObject('import QtQml; import Emaki.I18n; QtObject {}', root);
        const after = root.samples();
        if (after.some(text => !text.startsWith("[")) ||
            after.some((text, index) => text === before[index])) {
            console.error("LATE_TRANSLATION_FAILED", JSON.stringify(after));
            Qt.exit(1); return;
        }
        console.log("LATE_TRANSLATION_PASS");
        Qt.quit();
    } }
}
'''.replace('SHELL', (ROOT / 'shell').as_uri()))
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('LATE_TRANSLATION_PASS', output)

    def test_packaged_plugin_uses_compiled_catalogue_path(self):
        with tempfile.TemporaryDirectory(prefix='emaki-i18n-stage-') as temporary:
            base = Path(temporary)
            prefix = base / 'prefix'
            data = prefix / 'custom-data'
            qml = prefix / 'custom-qml'
            build = base / 'build'
            subprocess.run(['cmake', '-S', str(ROOT / 'i18n/native'), '-B', str(build),
                            '-DCMAKE_INSTALL_PREFIX=' + str(prefix),
                            '-DEMAKI_LOCALE_DIR=' + str(data / 'locale')], check=True, capture_output=True)
            subprocess.run(['cmake', '--build', str(build), '--parallel', '2'], check=True, capture_output=True)
            shutil.copytree(build / 'qml', qml)
            shutil.copytree(ROOT / 'shell', base / 'shell')
            subprocess.run(['python3', str(ROOT / 'i18n/install.py'), '--destination', str(data / 'locale')],
                           check=True, capture_output=True)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                        'QT_QPA_PLATFORMTHEME', 'EMAKI_LOCALE_DIR'):
                env.pop(key, None)
            for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                        'XDG_DATA_HOME', 'XDG_STATE_HOME'):
                directory = base / key
                directory.mkdir(mode=0o700)
                env[key] = str(directory)
            env.update(QML_IMPORT_PATH=str(qml), EMAKI_LANGUAGE='en_XA', QT_QPA_PLATFORM='offscreen',
                       QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            fixture = base / 'test.qml'
            fixture.write_text('''import QtQuick
import QtQuick.Window
import Quickshell
import Emaki.I18n
import "shell"
ShellRoot {
    WelcomeWindow { id: welcome; loadWallpaper: false }
    Window { id: firstWindow; visible: false }
    Window { id: secondWindow; visible: false }
    Timer { id: check; interval: 10; running: true; onTriggered: {
        if (WindowIdentity.appId(welcome.view.Window.window) !== "org.emaki.welcome" ||
            !welcome.title.startsWith("[") ||
            !WindowIdentity.identify(firstWindow, "emaki.welcome") ||
            !WindowIdentity.identify(secondWindow, "emaki.settings") ||
            WindowIdentity.appId(firstWindow) !== "emaki.welcome" ||
            WindowIdentity.appId(secondWindow) !== "emaki.settings" ||
            WindowIdentity.identify(check, "emaki.invalid") ||
            WindowIdentity.identify(firstWindow, "invalid identity") ||
            !WindowIdentity.identify(firstWindow, "emaki.welcome") ||
            WindowIdentity.appId(firstWindow) !== "emaki.welcome") {
            console.error("IDENTITY_FAILED"); Qt.exit(1); return;
        }
        // Exercise offscreen platform-surface creation after assigning identity.
        firstWindow.visible = true;
        firstWindow.visible = false;
        console.log("IDENTITY_PASS");
        console.log("STAGED_RESULT", qsTranslate("WelcomeWindow", "Welcome to Emaki"));
        Qt.quit();
    } }
}
''')
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('STAGED_RESULT [', output)
            self.assertIn('IDENTITY_PASS', output)

    def test_qt_preference_fallback_and_c_locale(self):
        with tempfile.TemporaryDirectory(prefix='emaki-i18n-runtime-') as temporary:
            base = Path(temporary)
            locale = base / 'locale'
            locale.mkdir()
            compiler = shutil.which('lrelease6') or shutil.which('lrelease')
            self.assertIsNotNone(compiler)
            for language, messages in {
                'en': [('First', 'First'), ('Second', 'Second')],
                'fr': [('First', 'Premier')],
                'de': [('First', 'Erste'), ('Second', 'Zweite')],
                'en_XA': [('First', '[First]'), ('Second', '[Second]')],
            }.items():
                source = base / (language + '.ts')
                source.write_text('<TS version="2.1" language="' + language + '"><context>'
                                  '<name>RuntimeTest</name>' + ''.join(
                    '<message><source>' + original + '</source><translation>' + translated +
                    '</translation></message>' for original, translated in messages) + '</context></TS>')
                subprocess.run([compiler, str(source), '-qm', str(locale / ('shell_' + language + '.qm'))],
                               check=True, capture_output=True)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                        'QT_QPA_PLATFORMTHEME', 'EMAKI_LANGUAGE', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
                env.pop(key, None)
            for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                        'XDG_DATA_HOME', 'XDG_STATE_HOME'):
                directory = base / key
                directory.mkdir(mode=0o700)
                env[key] = str(directory)
            env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                       EMAKI_LOCALE_DIR=str(locale), QT_QPA_PLATFORM='offscreen',
                       QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1',
                       QML_DISABLE_DISK_CACHE='1', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            fixture = base / 'test.qml'
            fixture.write_text('''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    Timer { interval: 10; running: true; onTriggered: {
        console.log("RUNTIME_RESULT", JSON.stringify([
            qsTranslate("RuntimeTest", "First"), qsTranslate("RuntimeTest", "Second")]));
        Qt.quit();
    } }
}
''')
            for selection, expected in [
                ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'fr:de'}, ['Premier', 'Zweite']),
                ({'LANG': 'de_DE.UTF-8', 'LANGUAGE': 'fr'}, ['Premier', 'Zweite']),
                ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'fr:C:de'}, ['Premier', 'Second']),
                ({'LC_ALL': 'C', 'LANGUAGE': 'fr'}, ['First', 'Second']),
                ({'LC_ALL': 'C.UTF-8', 'LANGUAGE': 'fr'}, ['First', 'Second']),
                ({'LC_MESSAGES': 'POSIX', 'LANG': 'de_DE.UTF-8'}, ['First', 'Second']),
                ({'LC_ALL': 'C', 'EMAKI_LANGUAGE': 'en_XA'}, ['[First]', '[Second]']),
            ]:
                with self.subTest(selection=selection):
                    result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=env | selection,
                                            capture_output=True, text=True, timeout=20)
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn('RUNTIME_RESULT ' + json.dumps(expected, separators=(',', ':')), output)


if __name__ == '__main__':
    unittest.main()
