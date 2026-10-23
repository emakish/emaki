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
import "Protocol.js" as Protocol
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
                    controller.session.availableLocales = test.cases.slice(0, -1).map(value => value.locale);
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
                               ["English", "Español (Latinoamérica)", "Português (Brasil)", "Deutsch", "Français", "Русский"]);
                    combo.currentIndex = test.selection === test.cases.length - 1 ? 0 : test.selection;
                    combo.activated(combo.currentIndex);
                    test.phase = 2; return;
                }
                if (test.phase === 2) {
                    test.equal(controller.language, sample.locale);
                    test.contains(sample.welcome);
                    const softwareLabel = test.find(view, "stepLabel_software");
                    const softwareButton = test.find(view, "stepButton_software");
                    if (softwareLabel.paintedWidth > softwareLabel.width + 1 ||
                        softwareLabel.paintedHeight > softwareButton.height + 1)
                        throw new Error("Software sidebar label does not fit: " + sample.locale);
                    test.equal(view.size(2000398934016), sample.size);
                    test.equal(view.number(1234.5, 2), sample.number);
                    test.equal(Protocol.number(1234.5, 2, controller.language), sample.number);
                    test.equal(Protocol.newPartitionError({size: 12.5 * Protocol.GIB}, "/", 20 * Protocol.GIB, 20 * Protocol.GIB, controller.language), sample.freeSpace);
                    test.equal(Protocol.partitionText({path: "/dev/test1", size_bytes: 1234.5 * Protocol.GIB, fs: "ext4"}, controller.language), sample.partition);
                    test.equal(Protocol.alongsideReview({size_bytes: 256 * 1048576}, 64 * 1048576, controller.language), sample.alongside);
                    test.equal(controller.layoutNames(["de"]), sample.german);
                    test.equal(controller.layoutNames(["de", "fr"]), sample.layoutPair);
                    test.equal(controller.layoutNames(["de", "fr", "cz"]), sample.layoutTriple);
                    const savedLayouts = controller.layouts.slice();
                    const savedCatalog = controller.catalog;
                    controller.catalog = {trial: false, layouts: [{layout: "cz", label: "Czech"}]};
                    test.equal(controller.layoutLabel("CZ"), sample.externalLayout);
                    controller.layouts = ["de", "fr"];
                    controller.catalog = {trial: true, layouts: []};
                    controller.step = "you";
                    controller.keyboardPending = null;
                    controller.keyboardSent = null;
                    controller.keyboardFirstPending = false;
                    controller.keyboardFirstSent = false;
                    controller.liveCodes = ["US"];
                    controller.keyboardUnavailable = true;
                    test.equal(controller.keyboardMessage, sample.keyboardWarning);
                    controller.catalog = savedCatalog;
                    controller.layouts = savedLayouts;
                    controller.keyboardUnavailable = false;
                    test.equal(view.diskLabel(), "");
                    controller.catalog = Object.assign({}, controller.catalog, {layouts: [
                        {layout: "us", variant: "", label: "English (US)"},
                        {layout: "ru", variant: "", label: "Russian"},
                        {layout: "zz", variant: "", label: "External name"},
                        {layout: "cz", variant: "", label: "Czech"}]});
                    test.equal(controller.layoutLabel("US"), sample.usLayout);
                    test.equal(controller.layoutLabel("RU"), sample.ruLayout);
                    test.equal(controller.layoutLabel("ZZ"), sample.locale === "en_US.UTF-8" ? "External name" : "zz");
                    controller.step = "keyboard";
                    test.phase = 20; return;
                }
                if (test.phase === 20) {
                    test.contains(sample.usLayout);
                    test.contains(sample.ruLayout);
                    test.find(view, "layoutSearch").text = sample.ruLayout;
                    test.phase = 21; return;
                }
                if (test.phase === 21) {
                    const list = test.find(view, "layoutList");
                    test.equal(list.count, 1);
                    test.equal(list.model[0].layout, "ru");
                    test.find(view, "layoutSearch").text = "Czech";
                    test.phase = 22; return;
                }
                if (test.phase === 22) {
                    const list = test.find(view, "layoutList");
                    test.equal(list.count, 1);
                    test.equal(list.model[0].layout, "cz");
                    controller.catalog = Object.assign({}, controller.catalog, {
                        layouts: controller.catalog.layouts.filter(layout => layout.layout !== "cz")});
                    test.find(view, "layoutSearch").text = "";
                    controller.step = "network";
                    test.phase = 3; return;
                }
                if (test.phase === 3) {
                    test.contains(sample.network);
                    test.equal(controller.fullName, "Language Test");
                    test.equal(controller.login, "languagecheck");
                    test.equal(controller.timezone, "America/Mexico_City");
                    test.equal(controller.layouts, sample.locale === "ru_RU.UTF-8" ? ["us", "ru"] : ["us"]);
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
                if (test.phase === 4) {
                    if (!test.replySeen) return;
                    test.contains(sample.message);
                    controller.session.phase = "copy_packages";
                    controller.session.phasePct = 42;
                    controller.session.indeterminate = false;
                    controller.publish();
                    controller.step = "install";
                    test.phase = 5; return;
                }
                test.equal(test.find(view, "phaseStatus").text, sample.percent);
                console.log("LANGUAGE_SWITCH_OK " + sample.locale);
                test.selection++;
                if (test.selection === test.cases.length) {
                    controller.toggleLayout("de");
                    controller.defaultLayout("de");
                    controller.language = "ru_RU.UTF-8";
                    test.equal(controller.layouts, ["de", "us"]);
                    controller.toggleLayout("ru");
                    controller.defaultLayout("ru");
                    test.equal(controller.layouts, ["de", "us", "ru"]);
                    controller.toggleLayout("us");
                    controller.toggleLayout("de");
                    test.equal(controller.layouts, ["us", "ru"]);
                    controller.toggleLayout("de");
                    controller.toggleLayout("us");
                    test.equal(controller.layouts, ["de", "ru"]);
                    controller.toggleLayout("de");
                    test.equal(controller.layouts, ["us", "ru"]);
                    console.log("KEYBOARD_LANGUAGE_OK");
                    Qt.quit(); return;
                }
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
                             ('pt_BR', 'pt_BR.UTF-8'), ('de', 'de_DE.UTF-8'),
                             ('fr', 'fr_FR.UTF-8'), ('ru', 'ru_RU.UTF-8'), ('en', 'en_US.UTF-8')]:
        translated = {}
        messages = {}
        if language != 'en':
            for context in ET.parse(ROOT / f'i18n/installer/{language}.ts').getroot().findall('context'):
                messages[context.findtext('name')] = {message.findtext('source'): message.findtext('translation')
                                                     for message in context.findall('message')}
                if context.findtext('name') == 'InstallerView':
                    translated = {message.findtext('source'): message.findtext('translation')
                                  for message in context.findall('message')}
        def tr(context, source, *args):
            text = messages.get(context, {}).get(source, source)
            for index, arg in enumerate(args, 1):
                text = text.replace('%' + str(index), arg)
            return text
        german = tr('InstallerController', 'German')
        french = tr('InstallerController', 'French')
        pair = tr('InstallerController', '%1 and %2', german, french)
        triple = tr('InstallerController', '%1, %2', german,
                    tr('InstallerController', '%1 and %2', french, 'cz'))
        decimal = '.' if language == 'en' else ','
        catalogue = gettext.translation('installer', localedir=ROOT / 'i18n/locale',
                                        languages=[language], fallback=language == 'en')
        value = dict(locale=locale, welcome=translated.get('Welcome to Emaki', 'Welcome to Emaki'),
                     network=translated.get('Get connected', 'Get connected'),
                     message=catalogue.gettext('Unknown request type.'),
                     size=translated.get('%1 GiB', '%1 GiB').replace('%1', '1863.0' if language == 'en' else '1863,0'),
                     number='1234.50' if language == 'en' else '1234,50',
                     percent='42%' if language in ('en', 'es', 'pt_BR', 'ru') else '42\u00a0%')
        value.update(
            german=german, layoutPair=pair, layoutTriple=triple,
            usLayout=tr("InstallerController", "English (US)"),
            ruLayout=tr("InstallerController", "Russian"),
            externalLayout="Czech" if language == "en" else "cz",
            keyboardWarning=tr('InstallerController', 'This session cannot switch the keyboard to %1, so passwords cannot be typed here.', pair),
            freeSpace=tr('Protocol', 'Only %1 GiB of free space is here.', '12' + decimal + '50'),
            partition='/dev/test1 · ' + tr('Protocol', '%1 GiB', '1234' + decimal + '5') + ' · ext4',
            alongside=tr('Protocol', 'Windows keeps %1 GB, Emaki gets %2 GB; back up your files first.',
                         '0' + decimal + '20', '0' + decimal + '07'))
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
        assert result.returncode == 0 and output.count('LANGUAGE_SWITCH_OK ') == 7, output
        assert 'KEYBOARD_LANGUAGE_OK' in output, output
        assert not any(word in output for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Binding loop')), output
        print('PASS installer language combo: en, es, pt_BR, de, fr, ru, en; QML, protocol, target locale and preserved inputs')


if __name__ == '__main__':
    main()
