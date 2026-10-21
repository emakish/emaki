#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture Portuguese production app views with isolated, inert controller fixtures."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LANGUAGE = 'pt_BR'
OUTPUT = ROOT / '.cache/evidence/i18n/pt_BR'
HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'

# Capture the actual view Item: the offscreen window content item has no QML engine.
CAPTURE = r'''
    function walk(item, labels, overflow) {
        if (!item || item.visible === false || item.opacity === 0) return;
        if (typeof item.text === "string" && item.text && item.width > 0 && item.height > 0) {
            labels.push(item.text);
            if (typeof item.truncated === "boolean" && item.truncated)
                overflow.push({text:item.text, width:item.width, height:item.height});
        }
        for (const child of item.children || []) walk(child, labels, overflow);
    }
    function capture(name) {
        const labels = [], overflow = [];
        walk(view, labels, overflow);
        const untranslated = labels.filter(text => ["Welcome to Emaki", "System language", "Browse", "Installed", "Updates", "Install Emaki", "Continue", "Cancel", "Review", "Install", "Update all"].includes(text));
        if ((Quickshell.env("EMAKI_LANGUAGE") !== "en" && untranslated.length) || labels.length < 5) {
            console.error("ASSERTION_FAILED translated labels " + JSON.stringify(labels));
            Qt.exit(1); return;
        }
        console.log("LABELS " + name + " " + JSON.stringify(labels));
        console.log("TRUNCATED " + name + " " + JSON.stringify(overflow));
        view.grabToImage(result => {
            if (!result.saveToFile(Quickshell.env("PT_OUTPUT") + "/" + name + ".png")) {
                console.error("ASSERTION_FAILED capture " + name); Qt.exit(1); return;
            }
            console.log("CAPTURE_OK " + name);
            next();
        });
    }
'''
PROGRAMS = r'''import QtQuick
import QtTest
import Quickshell
import Emaki.I18n
import ".." as UI
ShellRoot {
    property int stage: 0
    property var screens: ["browse", "installed", "updates", "details"]
    TestCase { id: frame; when: false }
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("TEST_WIDTH"))
        implicitHeight: Number(Quickshell.env("TEST_HEIGHT"))
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs }
    }
    Component.onCompleted: updates.data = Object.assign({}, updates.data, {warnings: JSON.parse(Quickshell.env("TEST_UPDATE_WARNINGS"))})
    function next() {
        stage++;
        if (stage >= screens.length) { console.log("INTERACTION_OK Portuguese app captures"); Qt.quit(); return; }
        if (screens[stage] === "details") view.showProgram(programs.allRows[0]);
        else view.navigate(screens[stage]);
        checkTimer.restart();
    }
    Timer {
        id: checkTimer; interval: 750; running: true
        onTriggered: {
            view.resetScroll();
            window.visible = false; frame.wait(30); window.visible = true;
            view.opacity = 0; frame.wait(30); view.opacity = 1; frame.wait(80);
            capture("programs-" + screens[stage] + "-" + view.width + "x" + view.height);
        }
    }
''' + CAPTURE + '}\n'

INSTALLER = r'''import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import Emaki.I18n
import "." as UI
ShellRoot {
    property int stage: 0
    property bool bottomCapture: false
    property var screens: ["welcome", "keyboard", "network", "timezone", "disk", "manual", "filesystem", "encryption", "you", "software", "review", "install", "error", "done"]
    property var fixture: JSON.parse(Quickshell.env("PT_INSTALLER_FIXTURE"))
    TestCase { id: frame; when: false }
    UI.InstallerController {
        id: backend
        mockTransport: true
        helpersEnabled: false
        catalog: ({layouts:[{layout:"us",variant:"",label:"English (US)"},{layout:"br",variant:"",label:"Português (Brasil)"}], zones:["UTC","America/Sao_Paulo"], trial:false})
        network: ({wired:true,networks:[]})
        // No worker, bus, keyboard helper or destructive action is reachable.
        onOutbound: message => {
            console.log("FIXTURE_REQUEST " + message.type);
            if (message.type === "set_timezone") Qt.callLater(() => backend.receive({type:"reply",for_id:message.id,ok:true,
                timezone:message.timezone,offset_seconds:-10800,abbreviation:"-03",unix_ms:Date.now()}));
        }
    }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("TEST_WIDTH"))
        implicitHeight: Number(Quickshell.env("TEST_HEIGHT"))
        UI.InstallerView { id: view; anchors.fill: parent; controller: backend }
    }
    Component.onCompleted: {
        backend.receive({type:"hello",proto:1,emaki_version:"0.11.0",available_locales:["en_US.UTF-8","pt_BR.UTF-8"]});
        const probe = Object.keys(backend.session.pending).find(key => backend.session.pending[key] === "probe");
        backend.receive(Object.assign({type:"inventory",for_id:probe}, fixture.inventory));
        if (!backend.session.inventory || !backend.session.inventory.disks.length) { console.error("ASSERTION_FAILED fixture inventory"); Qt.exit(1); }
        backend.language = "pt_BR.UTF-8";
        backend.diskId = "/dev/vda";
        backend.mode = "erase";
        backend.fullName = "Pessoa de teste";
        backend.login = "demo";
        backend.timezone = "America/Sao_Paulo";
        backend.timezoneEdited = true;
        backend.timezoneInfo = {timezone:"America/Sao_Paulo",offset_seconds:-10800,abbreviation:"-03",unix_ms:Date.now(),receivedMs:Date.now()};
        backend.encryption = "none";
    }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) { const found = find(child, name); if (found) return found; }
        return null;
    }
    function next() {
        const body = find(view, "installerBody");
        const flick = body.contentItem;
        if (!bottomCapture && flick.contentHeight > flick.height + 1) {
            bottomCapture = true;
            flick.contentY = flick.contentHeight - flick.height;
            shot.restart(); return;
        }
        bottomCapture = false;
        stage++;
        if (stage >= screens.length) { console.log("INSTALLER_PT_OK"); Qt.quit(); return; }
        const screen = screens[stage];
        backend.mode = screen === "manual" ? "manual" : "erase";
        backend.manualAssignments = screen === "manual";
        backend.session.running = screen === "install";
        backend.session.plan = fixture.plan;
        backend.session.deadline = Date.now() + 600000;
        backend.session.phase = "copy_packages";
        backend.session.started = Date.now() - 90000;
        backend.session.seconds = 187;
        backend.session.phasePct = 42;
        backend.session.totalPct = 32;
        backend.session.error = screen === "error" ? fixture.error : null;
        backend.session.outcome = screen === "done" ? "done" : "";
        backend.publish();
        backend.step = screen === "manual" ? "disk" : screen;
        shot.restart();
    }
    Timer {
        id: shot; interval: 600; running: true
        onTriggered: {
            frame.waitForPolish(view.Window.window, 5000);
            const body = find(view, "installerBody");
            if (!bottomCapture) { body.contentItem.contentY = 0; frame.wait(30); }
            const footer = find(view, "installerFooter");
            const top = footer.mapToItem(view, 0, 0);
            if (body.height <= 0 || body.mapToItem(view,0,body.height).y > top.y + 1 || top.y + footer.height > view.height + 1) {
                console.error("ASSERTION_FAILED installer bounds " + screens[stage] + " " + JSON.stringify({bodyHeight:body.height,bodyBottom:body.mapToItem(view,0,body.height).y,footerTop:top.y,footerHeight:footer.height,viewHeight:view.height}));
                view.grabToImage(result => { result.saveToFile(Quickshell.env("PT_OUTPUT") + "/installer-bounds-failure.png"); Qt.exit(1); }); return;
            }
            console.log("LAYOUT_OK " + screens[stage]);
            view.opacity = 0; frame.wait(30); view.opacity = 1; frame.wait(80);
            capture("installer-" + screens[stage] + (bottomCapture ? "-bottom" : "") + "-" + view.width + "x" + view.height);
        }
    }
''' + CAPTURE + '}\n'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment(root):
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
               EMAKI_LANGUAGE=LANGUAGE, EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
               QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               QML2_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'), PT_OUTPUT=str(OUTPUT))
    for name, variable in [('home', 'HOME'), ('runtime', 'XDG_RUNTIME_DIR'), ('config', 'XDG_CONFIG_HOME'),
                           ('cache', 'XDG_CACHE_HOME'), ('state', 'XDG_STATE_HOME'), ('data', 'XDG_DATA_HOME')]:
        target = root / name
        target.mkdir(mode=0o700)
        env[variable] = str(target)
    for variable in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'QT_QPA_PLATFORMTHEME']:
        env.pop(variable, None)
    return env


def main():
    global LANGUAGE, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['pt_BR', 'en'], default='pt_BR')
    LANGUAGE = parser.parse_args().language
    OUTPUT = ROOT / '.cache/evidence/i18n' / ('pt_BR' if LANGUAGE == 'pt_BR' else 'pt_BR-english-baseline')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.environ.update(EMAKI_LANGUAGE=LANGUAGE, EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'))
    os.environ.pop('QT_QPA_PLATFORMTHEME', None)
    interactions = load('pt_program_interactions', ROOT / 'update-manager/ui/tests/interactions.py')
    import catalog
    with tempfile.TemporaryDirectory(prefix='pt-app-captures-') as directory:
        root = Path(directory)
        entry = root / 'PortuguesePrograms.qml'
        entry.write_text(HEADER + PROGRAMS)
        # Reviews keep their production presentation and use an inert local reply.
        source = root / 'programs-ui'
        shutil.copytree(interactions.UI, source, ignore=shutil.ignore_patterns('__pycache__','artifacts'))
        reviews = source / 'ProgramReviews.qml'
        reply = root / 'reviews.py'
        reply.write_text('# Copyright (C) 2026 Artur Yakymenko\n# SPDX-License-Identifier: GPL-3.0-or-later\n'
                         'import json\nprint(json.dumps({"ok": True, "reviews": [], "appId": "org.kde.krita"}))\n')
        before = 'property var command: ["emaki-programs"]'
        assert before in reviews.read_text()
        reviews.write_text(reviews.read_text().replace(before, 'property var command: ' + json.dumps([sys.executable,str(reply)])))
        for width, height in [(1180, 780), (960, 640)]:
            log = interactions.run(width, height, entry=entry, source=source, extra={
                'EMAKI_LANGUAGE': LANGUAGE, 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
                'QML_IMPORT_PATH': str(ROOT / '.cache/i18n-native/qml'),
                'QML2_IMPORT_PATH': str(ROOT / '.cache/i18n-native/qml'),
                'PT_OUTPUT': str(OUTPUT),
                'TEST_UPDATE_WARNINGS': json.dumps(catalog.warnings_for([{'name':'linux','aur':False}]))})
            (OUTPUT / f'programs-{width}x{height}.log').write_text(log.replace(str(ROOT), '<checkout>'))
        editor = load('pt_editor_fixture', ROOT / 'installer/ui/tests/editor_fixture.py')
        inv = editor.inventory()
        config = dict(mode='erase',disk_id='/dev/vda',fs='btrfs',encryption='none',hibernation=False,
                      layouts=['us'],user=dict(name='Pessoa de teste',login='demo',password='fixture-password'),
                      timezone='America/Sao_Paulo',language='pt_BR.UTF-8',software='rich')
        plan = editor.plan_ack(config, inv)
        assert not plan['errors'], plan
        fixture = dict(inventory=editor.public(inv),plan=plan,
                       error=dict(code='command_failed',phase='copy_packages',retryable=True,
                                  message='A operação de pacotes falhou.',log_path='/tmp/fixture-install.log'))
        ui = root / 'ui'
        shutil.copytree(ROOT / 'installer/ui', ui, ignore=shutil.ignore_patterns('__pycache__','artifacts'))
        for path in ui.glob('*.qml'):
            path.write_text(path.read_text().replace('file://' + '/usr/share/emaki/shell', 'file://' + str(ROOT / 'shell')))
        entry = ui / 'PortugueseInstaller.qml'
        entry.write_text(HEADER + INSTALLER)
        env = environment(root)
        env['PT_INSTALLER_FIXTURE'] = json.dumps(fixture)
        for width, height in [(1024, 700), (960, 640)]:
            env.update(TEST_WIDTH=str(width),TEST_HEIGHT=str(height))
            result = subprocess.run(['qs','-p',str(entry),'--no-color'],env=env,text=True,capture_output=True,timeout=40)
            log = result.stdout + result.stderr
            (OUTPUT / f'installer-{width}x{height}.log').write_text(log.replace(str(ROOT), '<checkout>'))
            assert result.returncode == 0 and 'INSTALLER_PT_OK' in log, log
            assert not any(word in log for word in ['ASSERTION_FAILED','ReferenceError','TypeError','Unable to assign','Binding loop']), log
            assert log.count('CAPTURE_OK') >= 14, log
            for screen in ['welcome','keyboard','network','timezone','disk','manual','filesystem','encryption','you','software','review','install','error','done']:
                assert f'CAPTURE_OK installer-{screen}-{width}x{height}' in log, screen
            print(f'PASS {LANGUAGE} installer: 14 screens at {width}x{height}')
    print(OUTPUT)


if __name__ == '__main__':
    main()
