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
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='settings-appearance-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/test.qml').write_text('''import QtQuick
import Quickshell
import QtTest
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
   property var defaults: ({"appearance.theme":"dark", "appearance.accent":"orange", "appearance.light_time":"07:00", "appearance.dark_time":"19:00"})
   function value(key) { return values[key]; }
   function row(key) { return {value:values[key] ?? defaults[key],default:defaults[key],editable:true}; }
   function set(key,value) { calls = calls.concat([[key,value]]); const next = Object.assign({},values); next[key]=value; values=next; }
   function reset(key) { set(key,defaults[key]); }
  }
  SettingsAppearancePage { id: page; width: 800; catalog: catalog }
  TestCase {
   name: "AppearanceSettings"
   when: window.visible
   function test_controls() {
    const light = findChild(page,"setting-appearance.light_time");
    const dark = findChild(page,"setting-appearance.dark_time");
    verify(light); verify(dark); verify(!light.visible);
    // No saved theme: the page shows the dark default.
    const unset = Object.assign({},catalog.values); delete unset["appearance.theme"]; catalog.values = unset;
    compare(page.theme,"dark"); compare(findChild(page,"setting-appearance.theme").value,"dark");
    catalog.values = Object.assign({},unset,{"appearance.theme":"light"}); compare(page.theme,"light");
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
   onCompletedChanged: if (completed) console.log("APPEARANCE_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
  }
 }
}
''')
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base/'home'), XDG_CONFIG_HOME=str(base/'config'),
               XDG_STATE_HOME=str(base/'state'), XDG_CACHE_HOME=str(base/'cache'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path='+str(base/'no-bus'))
    for key in ('DISPLAY','WAYLAND_DISPLAY','NIRI_SOCKET'):
        env.pop(key,None)
    result = subprocess.run(['qs','-p',str(base/'shell/test.qml'),'--no-color'],env=env,
                            capture_output=True,text=True,timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'APPEARANCE_SETTINGS_RESULT 2 0' in output, output
    print('PASS: appearance choices, reset, time validation and busy state')
