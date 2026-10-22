#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the production language combo and real protocol replies offscreen."""
import gettext
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / 'installer/ui'
WORKER = '''import json
import sys
from emaki_installer.protocol import Controller
controller = Controller(None, None)
for line in sys.stdin:
    request = json.loads(line)
    if request['type'] != 'language_test':
        continue
    for response in controller.handle(request):
        print(json.dumps(response), flush=True)
'''
QML = '''import QtQuick
import Quickshell
import Quickshell.Io
import Emaki.I18n
import "." as UI
ShellRoot {
    id: test
    property var cases: CASES
    property int selection: 0
    property int phase: 0
    property bool replySeen: false
    property var planConfig: null
    function equal(actual, expected) {
        if (JSON.stringify(actual) !== JSON.stringify(expected))
            throw new Error(JSON.stringify(actual) + " != " + JSON.stringify(expected));
    }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const result = find(child, name); if (result) return result;
        }
        return null;
    }
    function texts(item, result) {
        if (!item || item.visible === false) return;
        if (typeof item.text === "string") result.push(item.text);
        for (const child of item.children || []) texts(child, result);
    }
    function contains(text) {
        const labels = []; texts(view, labels);
        if (!labels.includes(text)) throw new Error("Missing visible label " + text + ": " + JSON.stringify(labels));
    }
    UI.InstallerController {
        id: controller
        mockTransport: true
        helpersEnabled: false
        onOutbound: message => {
            if (message.type === "plan") test.planConfig = message.config;
            else if (message.type === "language_test") {
                worker.write(JSON.stringify(message) + "\\n");
            } else if (message.type === "probe") {
                Qt.callLater(() => controller.receive({type: "inventory", id: message.id,
                    disks: [], memory_bytes: 0}));
            }
        }
    }
    FloatingWindow {
        implicitWidth: 1024; implicitHeight: 700
        UI.InstallerView { id: view; anchors.fill: parent; controller: controller }
    }
    Process {
        id: worker
        command: ["python3", "-B", Qt.resolvedUrl("language-worker.py").toString().replace("file://", "")]
        running: true
        stdinEnabled: true
        stdout: SplitParser {
            onRead: data => {
                try {
                    const reply = JSON.parse(data);
                    test.equal(reply.msg, test.cases[test.selection].message);
                    controller.receive(reply);
                    test.equal(controller.session.notice, reply.msg);
                    test.replySeen = true;
                } catch (error) { console.error("ASSERTION_FAILED " + error); Qt.exit(1); }
            }
        }
    }
    Timer {
        interval: 200; running: true; repeat: true
        onTriggered: {
            try {
                const sample = test.cases[test.selection];
                if (test.phase === 0) {
                    controller.session.ready = true;
                    controller.session.availableLocales = test.cases.slice(0, 3).map(value => value.locale);
                    controller.publish();
                    controller.fullName = "Language Test";
                    controller.login = "languagecheck";
                    controller.hostname = "languagecheck";
                    controller.timezone = "America/Mexico_City";
                    controller.encryption = "none";
                    controller.mode = "erase";
                    controller.diskId = "/dev/language-fixture";
                    controller.mounts = [{partition_id: "kept", mountpoint: "/", format: false}];
                    controller.partitionEdits = {delete: [], create: [], new_table: false};
                    test.phase = 1; return;
                }
                if (test.phase === 1) {
                    const combo = test.find(view, "languageChoice");
                    test.equal(controller.languageChoices.map(choice => choice.label),
                               ["English", "Español (Latinoamérica)", "Português (Brasil)"]);
                    combo.currentIndex = test.selection === 3 ? 0 : test.selection;
                    combo.activated(combo.currentIndex);
                    test.phase = 2; return;
                }
                if (test.phase === 2) {
                    test.equal(controller.language, sample.locale);
                    test.contains(sample.welcome);
                    controller.step = "network";
                    test.phase = 3; return;
                }
                if (test.phase === 3) {
                    test.contains(sample.network);
                    test.equal(controller.fullName, "Language Test");
                    test.equal(controller.login, "languagecheck");
                    test.equal(controller.timezone, "America/Mexico_City");
                    test.equal(controller.layouts, ["us"]);
                    test.equal(controller.diskId, "/dev/language-fixture");
                    test.equal(controller.mounts, [{partition_id: "kept", mountpoint: "/", format: false}]);
                    test.equal(controller.partitionEdits, {delete: [], create: [], new_table: false});
                    test.planConfig = null;
                    controller.plan("testpassword", "testpassword");
                    test.equal(test.planConfig.language, sample.locale);
                    controller.session.planning = false;
                    controller.session.pending = {};
                    controller.publish();
                    test.replySeen = false;
                    controller.send("language_test", {});
                    test.phase = 4; return;
                }
                if (!test.replySeen) return;
                test.contains(sample.message);
                console.log("LANGUAGE_SWITCH_OK " + sample.locale);
                test.selection++;
                if (test.selection === test.cases.length) { Qt.quit(); return; }
                controller.step = "welcome";
                test.phase = 1;
            } catch (error) { console.error("ASSERTION_FAILED phase=" + test.phase + " " + error); Qt.exit(1); }
        }
    }
    Timer { interval: 15000; running: true; onTriggered: { console.error("ASSERTION_FAILED timeout"); Qt.exit(1); } }
}
'''


def cases():
    result = []
    for language, locale in [('en', 'en_US.UTF-8'), ('es', 'es_MX.UTF-8'),
                             ('pt_BR', 'pt_BR.UTF-8'), ('en', 'en_US.UTF-8')]:
        translated = {}
        if language != 'en':
            for context in ET.parse(ROOT / f'i18n/installer/{language}.ts').getroot().findall('context'):
                if context.findtext('name') == 'InstallerView':
                    translated = {message.findtext('source'): message.findtext('translation')
                                  for message in context.findall('message')}
        catalogue = gettext.translation('installer', localedir=ROOT / 'i18n/locale',
                                        languages=[language], fallback=language == 'en')
        value = dict(locale=locale, welcome=translated.get('Welcome to Emaki', 'Welcome to Emaki'),
                     network=translated.get('Get connected', 'Get connected'),
                     message=catalogue.gettext('Unknown request type.'))
        if language != 'en':
            assert value['welcome'] != 'Welcome to Emaki' and value['network'] != 'Get connected'
            assert value['message'] != 'Unknown request type.'
        result.append(value)
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='installer-language-') as temporary, \
            tempfile.TemporaryDirectory(prefix='il-', dir='/tmp') as runtime:
        root = Path(temporary)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        for path in (root / 'ui').glob('*.qml'):
            path.write_text(path.read_text().replace('file:///usr/share/emaki/shell', (ROOT / 'shell').as_uri()))
        entry = root / 'ui/LanguageTest.qml'
        entry.write_text(QML.replace('CASES', json.dumps(cases(), ensure_ascii=False)))
        (root / 'ui/language-worker.py').write_text(WORKER)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   EMAKI_LANGUAGE='en', LANG='en_US.UTF-8', EMAKI_INSTALLER_UI='1',
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   QML2_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'), PYTHONPATH=str(ROOT / 'installer'))
        for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME'):
            directory = root / key
            directory.mkdir(mode=0o700)
            env[key] = str(directory)
        env['XDG_RUNTIME_DIR'] = runtime
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                    'LC_ALL', 'LC_MESSAGES', 'LANGUAGE'):
            env.pop(key, None)
        env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(root / 'no-session-bus')
        env['DBUS_SYSTEM_BUS_ADDRESS'] = 'unix:path=' + str(root / 'no-system-bus')
        result = subprocess.run(['qs', '-p', str(entry), '--no-color'], env=env,
                                capture_output=True, text=True, timeout=25)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and output.count('LANGUAGE_SWITCH_OK ') == 4, output
        assert not any(word in output for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Binding loop')), output
        print('PASS installer language combo: en, es, pt_BR, en; QML, protocol, target locale and preserved inputs')


if __name__ == '__main__':
    main()
