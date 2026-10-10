#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run both pages against private fixed-command fixtures, including refusal paths."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'


def run(mutation=None, missing=False):
    with tempfile.TemporaryDirectory(prefix='settings-region-about-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        with (base / 'shell/qmldir').open('a') as stream:
            for name in ('SettingsRegionPage', 'SettingsAboutPage'):
                stream.write(f'\n{name} 1.0 {name}.qml\n')
        if mutation:
            path = base / 'shell' / mutation[0]
            text = path.read_text()
            assert mutation[1] in text
            path.write_text(text.replace(mutation[1], mutation[2]))
        (base / 'bin').mkdir()
        (base / 'runtime').mkdir(mode=0o700)
        report = base / 'problem.txt'
        report.write_text('Fixture report\n')
        scripts = {
            'emaki-machine-settings': '''import json,sys
args=sys.argv[1:]
rows=[{'key':'locale.language','value':'en_US.UTF-8','editable':False,'source':'declared'}, {'key':'locale.formats','value':'','editable':True,'source':'machine'}, {'key':'time.zone','value':'UTC','editable':True,'source':'machine'}, {'key':'time.automatic','value':True,'editable':True,'source':'machine'}]
reply={'schema_version':1,'status':'read','settings':rows}
if args[0]=='set': reply.update(status='rejected',reason='authorization_refused')
if args[0]=='bad': reply['schema_version']=99
if args[0]=='choices': reply={'schema_version':1,'status':'read','locales':['en_US.UTF-8','de_DE.UTF-8'],'time_zones':['Europe/Berlin','UTC']}
if args[0]=='busy': reply={'schema_version':1,'status':'rejected','reason':'machine_settings_busy'}
print(json.dumps(reply))
''',
            'emaki-settings-about': f'''import json,sys
reply={{'schema_version':1,'status':'ready','details':{{'version':'0.5.0','channel':'testing'}}}}
if sys.argv[1]=='report': reply.update(status='saved',path={str(report)!r})
if sys.argv[1]=='bad': reply['schema_version']=99
print(json.dumps(reply))
''',
            'xdg-open': f'''import pathlib,sys
pathlib.Path({str(base / 'opened')!r}).write_text(sys.argv[1])
''',
        }
        for name, source in scripts.items():
            path = base / 'bin' / name
            path.write_text('#!/usr/bin/env python3\n' + source)
            path.chmod(0o755)
        test = HEADER + '''import QtQuick
import Quickshell
Scope {
 Item {
  width: 760
  QtObject {
   id: catalog
   property string saved: ""
   property bool busy: false
   function value(key) { return saved !== "false"; }
   function set(key, value) { saved = value; }
  }
  SettingsRegionPage { id: region; width: 740; catalog: catalog }
  SettingsAboutPage { id: about; width: 740 }
 }
 function find(item, name) {
  if (item.objectName === name) return item;
  for (let child of item.children || []) { const found = find(child, name); if (found) return found; }
  return null;
 }
 function check(ok, message) { if (!ok) { console.error("FAILED", message); Qt.exit(1); } }
 Timer {
  property int step: 0
  interval: 400; repeat: true; running: true
  onTriggered: {
   if (step === 0) {
    check(region.row("time.automatic").value === true, "provider read");
    check(region.options("time.zone").some(item => item.value === "Europe/Berlin"), "installed zones");
    check(region.options("locale.formats")[0].value === "", "follow language choice");
    check(region.options("locale.language")[0].label.indexOf("English") >= 0, "language name");
    find(region, "settings-24-hour-clock").valueRequested(false);
    check(catalog.saved === "false", "clock preference submitted");
    check(about.details.version === "0.5.0", "about read");
    const language = find(region, "settings-language");
    check(language && language.managed && !language.editable, "declared setting read only");
    region.apply("time.zone", "Europe/Berlin");
   } else if (step === 1) {
    check(region.message.length > 0 && region.row("time.zone").value === "UTC", "refusal visible and unchanged");
    region.start(["busy"]);
   } else if (step === 2) {
    check(region.message.indexOf("Wait a moment") >= 0 && region.row("time.zone").value === "UTC", "busy reply preserves values and plain message");
    region.start(["bad"]); about.start("bad");
   } else if (step === 3) {
    check(region.message.length > 0 && about.message.length > 0, "invalid schema visible");
    about.start("report");
   } else if (step === 4) {
    check(about.reportPath.length > 0 && about.message.indexOf("Nothing has been uploaded") >= 0, "report saved for review");
    find(about, "review-report").clicked();
   } else { console.log("PAGES_PASS"); Qt.quit(); }
   step++;
  }
 }
}'''
        if missing:
            for name in ('SettingsRegionPage.qml', 'SettingsAboutPage.qml'):
                path = base / 'shell' / name
                path.write_text(path.read_text().replace('"emaki-machine-settings"', '"emaki-fixture-missing-command"').replace('"emaki-settings-about"', '"emaki-fixture-missing-command"'))
            test = HEADER + '''import QtQuick
import Quickshell
Scope {
 Item { SettingsRegionPage { id: region; width: 740 } SettingsAboutPage { id: about; width: 740 } }
 Timer { interval: 800; running: true; onTriggered: {
  if (region.message.length > 0 && about.message.length > 0) { console.log("PAGES_PASS"); Qt.quit(); }
  else { console.error("FAILED missing command"); Qt.exit(1); }
 } }
}'''
        (base / 'shell/Test.qml').write_text(test)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(base / 'runtime'), HOME=str(base),
                   PATH=str(base / 'bin') + ':' + os.environ['PATH'],
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        env.pop('WAYLAND_DISPLAY', None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/Test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=12)
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and 'PAGES_PASS' in output and (missing or ((base / 'opened').exists() and (base / 'opened').read_text() == str(report)))
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            passed = passed and error not in output
        if not mutation:
            assert passed, output
        else:
            assert not passed, 'Surviving mutation: ' + str(mutation)


run()
run(missing=True)
run(('SettingsRegionPage.qml', 'managed: page.row(modelData.key).source === "declared"', 'managed: false'))
run(('SettingsRegionPage.qml', 'reply.schema_version !== 1', 'false'))
run(('SettingsAboutPage.qml', 'reply.schema_version !== 1', 'false'))
print('PASS: page reads, declared values, refused changes, invalid replies, report review; three mutants killed')
