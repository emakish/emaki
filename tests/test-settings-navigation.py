#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that Settings navigation and search describe available controls offscreen."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import reaper
from runtime_fixture import runtime_path

reaper.guard()
ROOT = Path(os.environ.get('EMAKI_SETTINGS_SOURCE', Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='settings-navigation-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/test.qml').write_text('''import QtQuick
import Quickshell
import QtTest
import "settings/SettingsIndex.js" as Index
ShellRoot {
 Window {
  id: window
  visible: true
  width: 1000; height: 750
  QtObject {
   id: catalog
   property bool writing: false
   property var history: []
   property string lastStatus: ""
   function value(key) { return key === "appearance.accent" ? "rose" : "dark"; }
   function row(key) { return {value:value(key),default:value(key),editable:true}; }
  }
  SystemService { id: service; live: false; helpersEnabled: false }
  NiriService { id: niri; binary: "" }
  SettingsPageRegistry { id: registry }
  SettingsView {
   id: view
   anchors.fill: parent
   catalog: catalog; service: service; niri: niri
   availablePages: registry.availablePages
   page: "appearance"
  }
  TestCase {
   name: "SettingsNavigation"
   when: window.visible
   function init() { view.search.text = ""; verify(waitForPolish(window)); }
   function test_no_dead_accessibility() {
    const button = findChild(view,"section-accessibility");
    verify(!button || !button.visible,"Unavailable Accessibility must not appear in the sidebar");
    verify(!Index.sections().some(section => section.id === "accessibility"));
    view.search.text = "Accessibility";
    compare(view.results.length,0);
   }
   function test_lock_description() {
    view.search.text = "Lock & Login";
    const result = view.results.find(row => row.id === "lock");
    verify(result);
    compare(result.explanation,"Change your password and choose when to lock.");
   }
   function test_updates_description() {
    view.search.text = "Updates & Recovery";
    compare(view.results.length,1);
    compare(view.results[0].explanation,"Choose an update channel and review snapshot recovery.");
   }
   onCompletedChanged: if (completed) console.log("SETTINGS_NAVIGATION_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
  }
 }
}
''')
    for name in ('home', 'config', 'data', 'cache', 'state'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_STATE_HOME=str(base / 'state'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'SETTINGS_NAVIGATION_RESULT 4 0' in output, output
    print('PASS: unavailable Accessibility is hidden and search describes existing controls')
