#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check installed-system UI formatting against the 0.10 English strings."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
QML = '''import QtQuick
import Quickshell
import Emaki.I18n
import "installer" as Installer
import "programs" as Programs
import "programs/ManagerLogic.js" as Logic
ShellRoot {
    Installer.InstallerController { id: installer }
    Programs.UpdateController { id: updates }
    FloatingWindow {
        implicitWidth: 1024; implicitHeight: 700
        Installer.InstallerView { id: view; anchors.fill: parent; controller: installer }
        Programs.ManagerUpdates { id: updatePage; visible: false; controller: updates }
        Programs.ProgramReviews { id: reviews; visible: false }
        Programs.Stars { id: stars; visible: false; value: 4.7; count: 1804; showCount: true }
    }
    function equal(actual, expected) {
        if (actual !== expected) throw new Error(JSON.stringify(actual) + " != " + JSON.stringify(expected));
    }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) { const result = find(child, name); if (result) return result; }
        return null;
    }
    Timer {
        id: checks
        property int stage: 0
        interval: 500; running: true
        onTriggered: {
            try {
                if (stage === 1) { installer.step = "welcome"; stage = 2; restart(); return; }
                if (stage === 2) {
                    equal(installer.language, "en_US.UTF-8");
                    equal(find(view, "languageChoice").currentIndex, 0);
                    installer.session.plan = {token: "fixture", summary: ["[Translated keyboard summary]"], summary_keys: ["keyboard"], warnings: [], errors: []};
                    installer.step = "review"; stage = 3; restart(); return;
                }
                if (stage === 3) {
                    equal(find(view, "reviewKeyboard").text, "•  [Translated keyboard summary]");
                    console.log("ENGLISH_UI_OK"); Qt.quit(); return;
                }
                const stamp = new Date(2026, 9, 21, 14, 30);
                equal(Qt.locale().name, "en_US");
                equal(Logic.day(stamp), "21 Oct 2026, 14:30");
                equal(updatePage.date(stamp), "Wed 21 Oct 2026, 14:30");
                equal(reviews.date(stamp.getTime() / 1000), "21 Oct 2026");
                equal(view.size(2000398934016), "1863.0 GiB");
                equal(Logic.downloadText(150000000), "150.0 MB");
                equal(stars.fullText, "4.7 · 1,804 ratings");
                equal(stars.compact(9999), "10k");
                installer.timezone = "Pacific/Honolulu";
                installer.timezoneInfo = {timezone: installer.timezone, unix_ms: Date.UTC(2026, 9, 21, 14, 30, 17), offset_seconds: -36000, receivedMs: installer.clockMs};
                equal(installer.zoneClock(), "04:30:17");
                equal(installer.languageChoices.length, 1);
                equal(installer.languageChoices[0].locale, "en_US.UTF-8");
                equal(find(view, "languageChoice").currentIndex, 0);
                installer.step = "keyboard";
                stage = 1; restart();
            } catch (error) { console.error("ASSERTION_FAILED " + error); Qt.exit(1); }
        }
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='english-ui-') as directory:
        root = Path(directory)
        for name, source in [('installer', ROOT / 'installer/ui'), ('programs', ROOT / 'update-manager/ui')]:
            shutil.copytree(source, root / name, ignore=shutil.ignore_patterns('tests', '__pycache__', 'artifacts'))
            for path in list((root / name).glob('*.qml')) + list((root / name).glob('*.js')):
                path.write_text(path.read_text().replace('file:///usr/share/emaki/shell', 'file://' + str(ROOT / 'shell')).replace('file:@EMAKI_DATADIR@/shell', 'file://' + str(ROOT / 'shell')))
        entry = root / 'English.qml'
        entry.write_text(QML)
        env = dict(os.environ, LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8', EMAKI_LANGUAGE='en',
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   EMAKI_INSTALLER_SOCKET=str(root / 'missing.sock'))
        for name, variable in [('home', 'HOME'), ('runtime', 'XDG_RUNTIME_DIR'), ('cache', 'XDG_CACHE_HOME'),
                               ('config', 'XDG_CONFIG_HOME'), ('data', 'XDG_DATA_HOME'), ('state', 'XDG_STATE_HOME')]:
            (root / name).mkdir(mode=0o700)
            env[variable] = str(root / name)
        for variable in ['DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'QT_QPA_PLATFORMTHEME', 'LANGUAGE']:
            env.pop(variable, None)
        result = subprocess.run(['qs', '-p', str(entry), '--no-color'], env=env, capture_output=True, text=True, timeout=20)
        log = result.stdout + result.stderr
        assert result.returncode == 0 and 'ENGLISH_UI_OK' in log, log
        assert not any(text in log for text in ['ReferenceError', 'TypeError', 'ASSERTION_FAILED']), log
        print('ENGLISH_UI_OK: en_US dates, counts, sizes, selected-zone clock and available languages')


if __name__ == '__main__':
    main()
