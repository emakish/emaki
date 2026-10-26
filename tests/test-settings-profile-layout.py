#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the production Settings account row through compiled translations."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import reaper
from runtime_fixture import runtime_path

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('en', 'es', 'pt_BR', 'de', 'fr', 'ru')
QML = '''import QtQuick
import Quickshell
import QtTest
import Emaki.I18n
ShellRoot {
 Window {
  id: window
  visible: true
  width: 1240; height: 810
  QtObject {
   id: catalog
   property bool writing: false
   property var history: []
   property string lastStatus: ""
   function value(key) { return ""; }
   function row(key) { return null; }
  }
  SystemService {
   id: service
   live: false; helpersEnabled: false
   account: ({state: "ready", name: "Emaki", administrator: true})
  }
  NiriService { id: niri; binary: "" }
  SettingsView {
   id: view
   anchors.fill: parent
   catalog: catalog; service: service; niri: niri
   availablePages: []; page: ""
  }
  TestCase {
   name: "SettingsProfileLayout"
   when: window.visible
   function findText(item, text) {
    if (item.text === text) return item;
    for (const child of item.children || []) {
     const found = findText(child, text);
     if (found) return found;
    }
    return null;
   }
   function test_profile_data() {
    return [{tag: "default-administrator", w: 1240, h: 810, admin: true},
            {tag: "narrow-administrator", w: 800, h: 600, admin: true},
            {tag: "default-standard", w: 1240, h: 810, admin: false},
            {tag: "narrow-standard", w: 800, h: 600, admin: false}];
   }
   function test_profile(data) {
    window.width = data.w; window.height = data.h;
    service.account = {state: "ready", name: "Emaki", administrator: data.admin};
    tryCompare(view, "width", data.w);
    verify(waitForPolish(window));
    const expected = data.admin ? qsTranslate("SettingsView", "Administrator · this computer")
                                : qsTranslate("SettingsView", "This computer");
    const label = findText(view, expected);
    verify(label !== null, "The full catalogue text must be present");
    if (Quickshell.env("EMAKI_LANGUAGE") !== "en")
     verify(expected !== (data.admin ? "Administrator · this computer" : "This computer"), "Compiled catalogue must be loaded");
    const row = findChild(view, "settings-account-row").parent;
    const origin = label.mapToItem(row, 0, 0);
    const search = view.search.mapToItem(row.parent, 0, 0);
    console.log("PROFILE_GEOMETRY " + JSON.stringify({language: Quickshell.env("EMAKI_LANGUAGE"),
      tag: data.tag, text: label.text, truncated: label.truncated, width: label.width,
      height: label.height, contentWidth: label.contentWidth, contentHeight: label.contentHeight,
      rowHeight: row.height, searchGap: search.y - row.y - row.height}));
    verify(!label.truncated, "Profile description must not be elided");
    verify(label.contentWidth <= label.width + 0.5, "All text must fit horizontally");
    verify(label.contentHeight <= label.height + 0.5, "All text must fit vertically");
    verify(origin.x >= 42 && origin.x + label.width <= row.width + 0.5, "Description must clear avatar and fit row");
    verify(origin.y >= 0 && origin.y + label.height <= row.height + 0.5, "Description must fit clickable account row");
    const name = findText(row, "Emaki");
    verify(name.mapToItem(row, 0, 0).y + name.height <= origin.y, "Name and description must not overlap");
    verify(row.y + row.height < search.y, "Account row must not overlap search field");
   }
   onCompletedChanged: if (completed)
    console.log("PROFILE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
  }
 }
}
'''


def main():
    failures = []
    with tempfile.TemporaryDirectory(prefix='settings-profile-') as temporary:
        base = Path(temporary)
        shell = base / 'shell'
        shutil.copytree(ROOT / 'shell', shell)
        # Keep production UI and geometry; prevent all helper processes in the copy.
        for path in shell.rglob('*.qml'):
            text = path.read_text()
            text = re.sub(r'(?m)^(\s*)command: (?!\[).+$', r'\1command: ["/nonexistent-emaki-fixture-command"]', text)
            text = re.sub(r'command: \[', 'command: ["/nonexistent-emaki-fixture-command", ', text)
            path.write_text(text)
        fixture = shell / 'ProfileTest.qml'
        fixture.write_text(QML)
        for name in ('home', 'config', 'data', 'cache', 'state'):
            (base / name).mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_DATA_HOME=str(base / 'data'), XDG_STATE_HOME=str(base / 'state'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-session-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET'):
            env.pop(key, None)
        for language in LANGUAGES:
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                    env=dict(env, EMAKI_LANGUAGE=language),
                                    capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            print('\n'.join(line for line in output.splitlines()
                            if 'PROFILE_' in line or 'FAIL!' in line), flush=True)
            if result.returncode != 0 or 'PROFILE_RESULT 5 0' not in output:
                failures.append((language, f'qs return code: {result.returncode}\n' + output))
    assert not failures, '\n'.join(language + ':\n' + output for language, output in failures)
    print('PASS: 24 translated account rows fit without clipping or overlap')


if __name__ == '__main__':
    main()
