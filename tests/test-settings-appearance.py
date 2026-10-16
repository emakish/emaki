#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check appearance controls, live values and schedule validation offscreen."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper
reaper.guard()
ROOT = Path(os.environ.get('EMAKI_SETTINGS_SOURCE', Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='settings-appearance-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/test.qml').write_text('''import QtQuick
import Quickshell
import QtTest
import "Keyboard.js" as Keyboard
ShellRoot {
 Window {
  id: window
  visible: true
  width: 850; height: 650
  QtObject {
   id: catalog
   property bool writing: false
   property var calls: []
   property var values: ({"appearance.theme":"light", "appearance.accent":"orange", "appearance.light_time":"07:00", "appearance.dark_time":"19:00"})
   property var defaults: ({"appearance.theme":"light", "appearance.accent":"rose", "appearance.light_time":"07:00", "appearance.dark_time":"19:00"})
   function value(key) { return values[key]; }
   function row(key) { return {value:values[key] ?? defaults[key],default:defaults[key],editable:true}; }
   function set(key,value) { calls = calls.concat([[key,value]]); const next = Object.assign({},values); next[key]=value; values=next; }
   function reset(key) { set(key,defaults[key]); }
  }
  FocusScope {
   id: navigation
   anchors.fill: parent
   function keyboardNavigate(event) { Keyboard.handle(navigation, event); }
   Keys.onPressed: event => keyboardNavigate(event)
   SettingsAppearancePage { id: page; width: 800; catalog: catalog }
  }
  // A catalog still loading (or without the appearance rows): the page falls back to the default theme and accent.
  QtObject {
   id: loading
   property bool writing: false
   function value(key) { return undefined; }
   function row(key) { return null; }
   function set(key,value) {}
   function reset(key) {}
  }
  SettingsAppearancePage { id: loadingPage; width: 800; y: 700; catalog: loading }
  TestCase {
   name: "AppearanceSettings"
   when: window.visible
   function test_accent_keys_data() {
    return [{tag:"Return", key:Qt.Key_Return}, {tag:"Enter", key:Qt.Key_Enter}, {tag:"Space", key:Qt.Key_Space}];
   }
   function test_accent_keys(data) {
    catalog.writing = false;
    catalog.values = Object.assign({},catalog.values,{"appearance.accent":"orange"});
    verify(waitForPolish(window));
    const button = findChild(page,"accent-rose");
    verify(button); button.forceActiveFocus(); tryCompare(button,"activeFocus",true);
    const before = catalog.calls.length;
    keyClick(data.key);
    compare(catalog.value("appearance.accent"),"rose");
    compare(catalog.calls.length,before+1);
    verify(button.checked);
   }
   function test_accent_focus_keeps_selection() {
    catalog.writing = false;
    catalog.values = Object.assign({},catalog.values,{"appearance.accent":"orange"});
    const orange = findChild(page,"accent-orange");
    const rose = findChild(page,"accent-rose");
    orange.forceActiveFocus(); tryCompare(orange,"activeFocus",true);
    const before = catalog.calls.length;
    keyClick(Qt.Key_Right); keyClick(Qt.Key_Right);
    tryCompare(rose,"activeFocus",true);
    compare(catalog.value("appearance.accent"),"orange");
    compare(catalog.calls.length,before);
    verify(orange.checked); verify(!rose.checked);
    verify(findChild(orange,"accent-selected-mark").visible);
    verify(!findChild(rose,"accent-selected-mark").visible);
    verify(findChild(rose,"accent-focus-ring").visible);
    verify(!findChild(orange,"accent-focus-ring").visible);
    keyClick(Qt.Key_Left); keyClick(Qt.Key_Left);
    tryCompare(orange,"activeFocus",true);
    verify(orange.checked);
    keyClick(Qt.Key_Right); keyClick(Qt.Key_Right); keyClick(Qt.Key_Return);
    compare(catalog.value("appearance.accent"),"rose");
    compare(catalog.calls.length,before+1);
    verify(findChild(rose,"accent-selected-mark").visible);
    verify(!findChild(orange,"accent-selected-mark").visible);
    keyClick(Qt.Key_Left); keyClick(Qt.Key_Left); keyClick(Qt.Key_Space);
    compare(catalog.value("appearance.accent"),"orange");
    compare(catalog.calls.length,before+2);
    verify(findChild(orange,"accent-selected-mark").visible);
    verify(!findChild(rose,"accent-selected-mark").visible);
   }
   function test_accent_leaves_row_data() {
    return [{tag:"Up", name:"orange", key:Qt.Key_Up}, {tag:"Right edge", name:"violet", key:Qt.Key_Right}];
   }
   function test_accent_leaves_row(data) {
    catalog.writing = false;
    catalog.values = Object.assign({},catalog.values,{"appearance.theme":"light", "appearance.accent":"orange"});
    verify(waitForPolish(window));
    const orange = findChild(page,"accent-orange");
    const start = findChild(page,"accent-" + data.name);
    start.forceActiveFocus(); tryCompare(start,"activeFocus",true);
    keyClick(data.key);
    verify(!window.activeFocusItem.objectName.startsWith("accent-"));
    if (data.key === Qt.Key_Right)
     tryCompare(findChild(page,"setting-appearance.accent").resetItem,"activeFocus",true);
    compare(catalog.value("appearance.accent"),"orange");
    verify(orange.checked);
    verify(findChild(orange,"accent-selected-mark").visible);
   }
   function test_controls() {
    const light = findChild(page,"setting-appearance.light_time");
    const dark = findChild(page,"setting-appearance.dark_time");
    verify(light); verify(dark); verify(!light.visible);
    // No saved theme: the page shows the light default.
    const unset = Object.assign({},catalog.values); delete unset["appearance.theme"]; catalog.values = unset;
    compare(page.theme,"light"); compare(findChild(page,"setting-appearance.theme").value,"light");
    catalog.values = Object.assign({},unset,{"appearance.theme":"dark"}); compare(page.theme,"dark");
    findChild(page,"setting-appearance.theme").requestValue("automatic");
    compare(catalog.value("appearance.theme"),"automatic"); verify(light.visible); verify(dark.visible);
    const input = findChild(page,"appearance.light_time-input");
    verify(input); input.text="19:00"; input.editingFinished();
    compare(catalog.value("appearance.light_time"),"07:00"); compare(light.errorText,"Choose two different times.");
    input.text="08:30"; input.editingFinished(); compare(catalog.value("appearance.light_time"),"08:30");
    compare(light.errorText,""); light.requestReset(); compare(catalog.value("appearance.light_time"),"07:00");
    compare(ShellPalette.accents.length,8); compare(ShellPalette.accents[0].id,"orange");
    for (const accent of ShellPalette.accents) {
     const button=findChild(page,"accent-"+accent.id); verify(button); button.clicked();
     compare(catalog.value("appearance.accent"),accent.id); verify(button.checked);
    }
    catalog.writing=true; findChild(page,"accent-orange").clicked(); compare(catalog.value("appearance.accent"),"violet");
   }
   function test_missing_catalog_row() {
    compare(loadingPage.theme,"light");
    compare(findChild(loadingPage,"setting-appearance.theme").value,"light");
    const row = findChild(loadingPage,"setting-appearance.accent");
    verify(row); compare(ShellPalette.defaultAccent,"rose"); compare(row.value,ShellPalette.defaultAccent);
    verify(findChild(loadingPage,"accent-rose").checked); verify(!findChild(loadingPage,"accent-orange").checked);
   }
   onCompletedChanged: if (completed) console.log("APPEARANCE_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
  }
 }
}
''')
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base/'home'), XDG_CONFIG_HOME=str(base/'config'),
               XDG_DATA_HOME=str(base/'data'), XDG_STATE_HOME=str(base/'state'), XDG_CACHE_HOME=str(base/'cache'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path='+str(base/'no-bus'))
    for key in ('DISPLAY','WAYLAND_DISPLAY','NIRI_SOCKET'):
        env.pop(key,None)
    result = subprocess.run(['qs','-p',str(base/'shell/test.qml'),'--no-color'],env=env,
                            capture_output=True,text=True,timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'APPEARANCE_SETTINGS_RESULT 9 0' in output, output
    print('PASS: appearance choices, keyboard activation, selection retained during arrow focus, spatial exit from the accent row, reset, time validation, busy state and the default accent '
          'without a catalog row')
