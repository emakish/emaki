#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Theme catalog interaction: one atomic request, live selection, unavailable media."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else next(
    (path for path in (ROOT / '.cache/target/debug/emaki', ROOT / 'target/debug/emaki')
     if path.is_file()), None)
assert BINARY is not None, 'Build the emaki binary before this test'

with tempfile.TemporaryDirectory(prefix='settings-themes-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/test.qml').write_text('''import QtQuick
import Quickshell
import QtTest
ShellRoot {
 Window {
  id: window
  visible: true; width: 850; height: 700
  QtObject {
   id: catalog
   property bool writing: false
   property var calls: []
   property var values: ({})
   property var styles: [
    {id:"emaki",name:"Emaki",accent:"rose",wallpaper:"/a.png",overview_video:"/a.mp4",overview_still:"/a.jpg",available:true},
    {id:"07-blue-light",name:"Blue Light",accent:"blue",wallpaper:"/b.png",overview_video:"",overview_still:"/b.jpg",available:true,video_available:false},
    {id:"18-graphite",name:"Graphite",accent:"graphite",wallpaper:"/c.png",overview_video:"",overview_still:"/c.jpg",available:false}
   ]
   function value(key) { return values[key] ?? null; }
   function row(key) { return {value:value(key),editable:true}; }
   function set(key,value) { calls = calls.concat([[key,value]]); }
   function applied(style) {
    values = {"appearance.style":style.id,"appearance.accent":style.accent,
      "appearance.wallpaper":style.wallpaper,"appearance.overview_video":style.overview_video,
      "appearance.overview_still":style.overview_still};
   }
  }
  SettingsThemesPage { id: page; width: 800; catalog: catalog }
  SettingsCatalog { id: realCatalog; active: false }
  SettingsThemesPage { id: realPage; width: 800; catalog: realCatalog; visible: false }
  SettingsCatalog {
   id: pickerCatalog; active: false
   property var calls: []
   property var values: [
    {key:"appearance.wallpaper",value:"/cube.png",default:"/cube.png"},
    {key:"appearance.style",value:"emaki"},
    {key:"appearance.accent",value:"rose"},
    {key:"appearance.overview_video",value:"/cube.mp4"},
    {key:"appearance.overview_still",value:"/cube.jpg"}
   ]
   function row(key) { return values.find(entry => entry.key === key) || null; }
   function value(key) { return row(key)?.value; }
   function send(action,args) { if (action === "set") calls = calls.concat([[action,args]]); }
  }
  SettingsWallpaperPage { id: picker; width: 800; catalog: pickerCatalog; visible: false }
  TestCase {
   name: "ThemeCatalog"; when: window.visible
   function init() { catalog.writing=false; catalog.calls=[]; catalog.values={}; }
   function test_real_helper_and_rejected_reply() {
    tryVerify(() => !realCatalog.busy, 15000);
    verify(realCatalog.styles.length > 0);
    const count=realCatalog.styles.length;
    const first=realCatalog.styles[0];
    compare(realCatalog.styles.length,19);
    for (let i=0;i<19;i++) {
     compare(realCatalog.wallpapers[i].path,realCatalog.styles[i].wallpaper);
     compare(realCatalog.wallpapers[i].thumbnail,realCatalog.styles[i].thumbnail);
     compare(realCatalog.wallpapers[i].available,false);
    }
    verify(findChild(realPage,"theme-choice-"+first.id));
    const missing=realCatalog.styles.find(style => !style.available);
    verify(missing);
    realCatalog.set("appearance.style",missing.id);
    tryVerify(() => !realCatalog.busy && realCatalog.lastStatus === "rejected",15000);
    compare(realCatalog.lastAction,"style_assets_missing");
    compare(realCatalog.styles.length,count);
    verify(findChild(realPage,"theme-choice-"+first.id));
   }
   function equal(actual, expected) {
    if (actual !== expected) console.error("Wallpaper compare", actual, expected, new Error().stack);
    compare(actual, expected);
   }
   function check(condition) {
    if (!condition) console.error("Wallpaper verification", new Error().stack);
    verify(condition);
   }
   function test_wallpaper_ribbon_is_one_independent_request() {
    page.visible=false; picker.visible=true;
    pickerCatalog.calls=[];
    pickerCatalog.styles=[
     {id:"emaki",name:"Emaki",wallpaper:"/cube.png",thumbnail:Quickshell.env("SETTINGS_TEST_THUMBNAIL"),wallpaper_available:true},
     {id:"07-blue-light",name:"Blue Light",wallpaper:"/ribbon.png",thumbnail:Quickshell.env("SETTINGS_TEST_THUMBNAIL"),wallpaper_available:true},
     {id:"18-graphite",name:"Graphite",wallpaper:"/missing.png",thumbnail:Quickshell.env("SETTINGS_TEST_THUMBNAIL"),wallpaper_available:false}
    ];
    check(waitForPolish(window));
    const ribbon=findChild(picker,"wallpaper-choice-1"); check(ribbon.enabled);
    const preview=findChild(picker,"wallpaper-thumbnail-1"); tryCompare(preview,"status",Image.Ready);
    equal(preview.source.toString(),picker.pictureUrl(pickerCatalog.styles[1].thumbnail));
    check(!findChild(picker,"wallpaper-choice-2").enabled);
    mouseClick(ribbon);
    equal(pickerCatalog.calls.length,1);
    equal(pickerCatalog.calls[0][0],"set");
    equal(pickerCatalog.calls[0][1][0],"appearance.wallpaper");
    equal(pickerCatalog.calls[0][1][1],"/ribbon.png");
    equal(pickerCatalog.value("appearance.style"),"emaki");
    equal(pickerCatalog.value("appearance.accent"),"rose");
    equal(pickerCatalog.value("appearance.overview_video"),"/cube.mp4");
    equal(pickerCatalog.value("appearance.overview_still"),"/cube.jpg");
    pickerCatalog.styles=pickerCatalog.styles.map(style => Object.assign({},style));
    equal(findChild(picker,"wallpaper-choice-1"),ribbon);
    picker.visible=false; page.visible=true;
   }
   function test_select_is_one_request() {
    verify(waitForPolish(window));
    const blue=findChild(page,"theme-choice-07-blue-light"); verify(blue);
    mouseClick(blue); compare(catalog.calls.length,1);
    compare(catalog.calls[0][0],"appearance.style"); compare(catalog.calls[0][1],"07-blue-light");
    verify(!blue.selected);
    catalog.applied(catalog.styles[1]); verify(blue.selected);
    compare(findChild(page,"theme-status-07-blue-light").text,"Current theme");
    catalog.values=Object.assign({},catalog.values,{"appearance.overview_video":"/personal/video.mp4","appearance.overview_still":"/personal/still.png"});
    verify(blue.selected);
    catalog.values=Object.assign({},catalog.values,{"appearance.accent":"teal"});
    verify(!blue.selected);
   }
   function test_keyboard_data() { return [{tag:"Return",key:Qt.Key_Return},{tag:"Space",key:Qt.Key_Space}]; }
   function test_keyboard(data) {
    const emaki=findChild(page,"theme-choice-emaki"); emaki.forceActiveFocus();
    tryCompare(emaki,"activeFocus",true); keyClick(data.key);
    compare(catalog.calls.length,1); compare(catalog.calls[0][1],"emaki");
   }
   function test_busy_and_missing() {
    const missing=findChild(page,"theme-choice-18-graphite"); verify(!missing.enabled);
    page.selectStyle(catalog.styles[2]); compare(catalog.calls.length,0);
    catalog.writing=true;
    verify(!findChild(page,"theme-choice-emaki").enabled);
    page.selectStyle(catalog.styles[0]); compare(catalog.calls.length,0);
   }
   function test_reflow_and_still() {
    page.width=340; verify(waitForPolish(window));
    const first=findChild(page,"theme-choice-emaki");
    const second=findChild(page,"theme-choice-07-blue-light");
    verify(second.y > first.y); verify(second.width < page.width);
    compare(findChild(page,"theme-status-07-blue-light").text,"Still overview background");
    page.width=800;
    compare(page.pictureUrl("/a #/b%.jpg"),"file:///a%20%23/b%25.jpg");
   }
   onCompletedChanged: if (completed) console.log("THEME_CATALOG_RESULT "+qtest_results.passCount+" "+qtest_results.failCount)
  }
 }
}
''')
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               EMAKI_BIN=str(BINARY), EMAKI_SETTINGS_PROFILE=str(base),
               EMAKI_THEME_ASSET_ROOT=str(base / 'missing-assets'),
               EMAKI_THEME_CATALOG=str(ROOT / 'themes/catalog.json'),
               SETTINGS_TEST_THUMBNAIL=str(ROOT / 'themes/previews/07-blue-light.jpg'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'], env=env,
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'THEME_CATALOG_RESULT 8 0' in output, output
    assert not any(error in output for error in ('ReferenceError', 'TypeError', 'Binding loop')), output
    print('PASS: one theme request, live selection, keyboard, busy/unavailable entries, responsive grid, real helper catalog and rejected reply preservation')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsThemesScrollTest.qml',
                    base / 'shell/SettingsThemesScrollTest.qml')
    styles = json.loads((ROOT / 'themes/catalog.json').read_text())['styles']
    env['SETTINGS_TEST_STYLES'] = json.dumps([
        dict(style, available=True, selected=False, thumbnail='') for style in styles])

    def scroll_test(label, expected=True):
        env['XDG_RUNTIME_DIR'] = str(runtime_path(base / label))
        result = subprocess.run(['qs', '-p', str(base / 'shell/SettingsThemesScrollTest.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and 'THEME_SCROLL_RESULT 8 0' in output
        assert passed == expected and 'THEME_SCROLL_RESULT' in output, output
        assert not any(error in output for error in ('ReferenceError', 'TypeError', 'Binding loop')), output
        return output

    scroll_test('scroll')
    print('PASS: pointer, Return and Space preserve theme scroll, visibility, current mark and focus through apply/list replies')
    mutations = [
        ('scope-reveal', 'SettingsView.qml',
         'if (!item || item === pageLoader)', 'if (!item)',
         'Saving must preserve scroll position'),
        ('card-rebuild', 'SettingsThemesPage.qml',
         'model: page.styles.length', 'model: page.styles',
         'Catalog refresh must preserve the card'),
    ]
    for label, filename, before, after, failure in mutations:
        path = base / 'shell' / filename
        original = path.read_text()
        assert original.count(before) == 1
        try:
            path.write_text(original.replace(before, after))
            output = scroll_test(label, expected=False)
            assert failure in output, output
            print('PASS: rejected mutant ' + label)
        finally:
            path.write_text(original)
