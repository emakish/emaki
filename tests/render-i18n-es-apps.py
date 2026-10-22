#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture Spanish application pages with isolated offscreen fixture backends."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.cache/evidence/i18n/es'


def translated(domain, source):
    for message in ET.parse(ROOT / 'i18n' / domain / 'es.ts').findall('.//message'):
        if message.findtext('source') == source:
            return message.findtext('translation')
    raise AssertionError(source)


def isolated(root):
    env = os.environ.copy()
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                'QT_QPA_PLATFORMTHEME', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES'):
        env.pop(key, None)
    for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
        target = root / key
        target.mkdir(mode=0o700)
        env[key] = str(target)
    env.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
               EMAKI_LANGUAGE='es', EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
               QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               QML2_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'no-session-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(root / 'no-system-bus'))
    return env


PROGRAMS = '''import QtQuick
import QtTest
import Quickshell
import Emaki.I18n
import ".." as UI
ShellRoot {
    TestCase { id: frame; when: false }
    property int stage: 0
    property var pages: ["browse", "installed", "updates", "updates-bottom", "detail", "detail-bottom"]
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs }
    Component.onCompleted: updates.data = Object.assign({}, updates.data, {warnings: JSON.parse(Quickshell.env("TEST_UPDATE_WARNINGS"))})
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("TEST_WIDTH")); implicitHeight: Number(Quickshell.env("TEST_HEIGHT"))
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs }
    }
    function walk(item, strings) {
        if (!item || item.visible === false) return;
        if (typeof item.text === "string" && item.text) {
            strings.push(item.text);
            if (item.truncated) console.log("TRUNCATED_TEXT " + JSON.stringify({text:item.text,width:item.width,implicitWidth:item.implicitWidth,contentWidth:item.contentWidth,parentWidth:item.parent.width,grandparentWidth:item.parent.parent.width}));
        }
        for (const child of item.children || []) walk(child, strings);
    }
    function bottom(item) {
        if (!item || item.visible === false) return;
        if (item instanceof Flickable) item.contentY = Math.max(item.originY, item.originY + item.contentHeight - item.height);
        for (const child of item.children || []) bottom(child);
    }
    Timer {
        id: timer; interval: 700; running: true
        onTriggered: {
            const strings = []; walk(view, strings);
            const expected = JSON.parse(Quickshell.env("TEST_EXPECTED"))[stage];
            if (!strings.includes(expected)) { console.error("ASSERTION_FAILED " + expected + " " + JSON.stringify(strings)); Qt.exit(1); return; }
            view.resetScroll();
            if (pages[stage].endsWith("-bottom")) bottom(view);
            window.visible = false; frame.wait(30); window.visible = true;
            view.opacity = 0; frame.wait(30); view.opacity = 1; frame.wait(80);
            view.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("TEST_ES_OUTPUT") + "/programs-" + pages[stage] + ".png")) { Qt.exit(1); return; }
                console.log("INTERACTION_OK " + pages[stage] + " " + expected);
                stage++;
                if (stage === 6) { Qt.quit(); return; }
                if (stage === 4) view.showProgram(programs.allRows[0]);
                else if (!pages[stage].endsWith("-bottom")) view.navigate(pages[stage]);
                timer.restart();
            });
        }
    }
}
'''


def programs(width, height):
    with tempfile.TemporaryDirectory(prefix='emaki-es-programs-') as temporary:
        root = Path(temporary)
        env = isolated(root)
        previous = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            sys.path.insert(0, str(ROOT / 'update-manager/ui/tests'))
            import interactions
            import catalog
            entry = root / 'ProgramsEs.qml'
            entry.write_text(PROGRAMS)
            source = root / 'source'
            shutil.copytree(interactions.UI, source)
            reviews = source / 'ProgramReviews.qml'
            reviews.write_text(reviews.read_text().replace('property var command: ["emaki-programs"]',
                'property var command: ["' + sys.executable + '", "-c", "import json; print(json.dumps(dict(ok=True, reviews=[], ratings={})))"]'))
            log = interactions.run(width=width, height=height, entry=entry, source=source, extra={
                **env, 'TEST_ES_OUTPUT': str(OUTPUT), 'TEST_SHOTS': str(OUTPUT),
                'TEST_EXPECTED': json.dumps([translated('programs', text) for text in
                                            ('Popular', 'Installed', 'Update all', 'Update all', 'Install', 'Install')]),
                'TEST_UPDATE_WARNINGS': json.dumps(catalog.warnings_for([{'name': 'linux', 'aur': False}])),
            })
            (OUTPUT / 'programs.log').write_text(log)
            for line in log.splitlines():
                if 'TRUNCATED_TEXT ' in line:
                    item = json.loads(line.split('TRUNCATED_TEXT ', 1)[1])
                    # The catalogue's external application descriptions use intentional ellipsis.
                    assert item['text'] == 'Create images and edit photographs', item
        finally:
            os.environ.clear()
            os.environ.update(previous)


INSTALLER_CAPTURE = '''    Timer {
        id: shot; interval: 700
        onTriggered: {
            const layoutOk = test.checkLayout();
            if (!layoutOk) console.error("ASSERTION_FAILED layout");
            if (test.screenName === "welcome" && test.languageStage < 2) {
                const picker = test.findItem(content, "languageChoice");
                const index = controller.languageChoices.findIndex(choice => choice.locale === "es_MX.UTF-8");
                if (!picker || index < 0) { console.error("ASSERTION_FAILED language unavailable"); Qt.exit(1); return; }
                if (test.languageStage === 0) {
                    picker.currentIndex = index; picker.activated(index);
                    controller.step = "keyboard"; test.languageStage = 1;
                    Qt.callLater(() => { controller.step = "welcome"; shot.restart(); }); return;
                }
                if (controller.language !== "es_MX.UTF-8" || picker.currentIndex !== index || picker.currentText !== Quickshell.env("TEST_ES_LANGUAGE")) {
                    console.error("ASSERTION_FAILED language persistence " + picker.currentText); Qt.exit(1); return;
                }
                test.languageStage = 2;
                console.log("LANGUAGE_PERSISTENCE_OK " + picker.currentText);
            }
            test.contentEvidence(test.screenName);
            const expected = Quickshell.env("TEST_ES_EXPECTED");
            const texts = [];
            function visit(item) {
                if (!item || item.visible === false) return;
                if (typeof item.text === "string") texts.push(item.text);
                for (const child of item.children || []) visit(child);
            }
            visit(content);
            if (!texts.includes(expected)) { console.error("ASSERTION_FAILED translated heading " + expected + " " + JSON.stringify(texts)); Qt.exit(1); return; }
            content.grabToImage(result => {
                if (!result.saveToFile(test.screenshot)) { Qt.exit(1); return; }
                console.log("SCREENSHOT_OK " + test.screenName); Qt.exit(layoutOk ? 0 : 1);
            });
        }
    }
'''


def installer(width, height, selected=None):
    ui = ROOT / 'installer/ui'
    screens = ('welcome', 'keyboard', 'network', 'timezone', 'disk', 'filesystem', 'encryption',
               'you', 'software', 'review', 'install', 'done')
    headings = ('Welcome', 'Keyboard', 'Network', 'Time zone', 'Disk', 'File system', 'Encryption',
                'You', 'Software', 'Review', 'Install', 'Done')
    with tempfile.TemporaryDirectory(prefix='emaki-es-installer-') as temporary:
        root = Path(temporary)
        shutil.copytree(ui, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (ui / 'tests/TestWindow.qml').read_text()
        entry = entry.replace('import Quickshell\n', 'import Quickshell\nimport Emaki.I18n\n')
        entry = entry.replace('".." as UI', '"." as UI').replace('"mock-worker.py"', '"tests/mock-worker.py"').replace('"FakeNiri.js"', '"tests/FakeNiri.js"').replace('"real-worker.py"', '"tests/real-worker.py"')
        entry = entry.replace('if (!flick || flick.contentHeight <= flick.height)', 'if (!flick)').replace('flick.contentY = flick.contentHeight - flick.height;', 'flick.contentY = Math.max(0, flick.contentHeight - flick.height);')
        entry = entry.replace('stdinEnabled: true', 'stdinEnabled: true\n        stderr: SplitParser { onRead: data => console.error("FIXTURE_WORKER " + data) }')
        entry = entry.replace('property bool captured: false', 'property bool captured: false\n    property int languageStage: 0')
        entry = entry.replace('controller.receive(message);', 'if (message.type === "hello") message.available_locales = ["en_US.UTF-8", "es_MX.UTF-8"];\n                controller.receive(message);')
        start = entry.index('    Timer {\n        id: shot')
        end = entry.index('    Timer {\n        interval: 10000', start)
        entry = entry[:start] + INSTALLER_CAPTURE + entry[end:]
        (root / 'ui/InstallerEs.qml').write_text(entry)
        for path in (root / 'ui').glob('*.qml'):
            path.write_text(path.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"').replace('@EMAKI_DATADIR@', str(ROOT)))
        worker = root / 'ui/tests/mock-worker.py'
        worker_text = worker.read_text().replace("        if kind == 'plan':\n            config = request['config']", "        if kind == 'plan':\n            es_config = copy.deepcopy(request['config'])\n            config = request['config']")
        worker_text = worker_text.replace('        result = []\n        for response in responses:', """        if kind == 'plan':
            sys.path.insert(0, str(Path(__file__).parent))
            import editor_fixture
            fixture = editor_fixture.inventory(empty=True)
            fixture['disks'][0].update(model='VirtIO System Disk', size_bytes=40 * editor_fixture.GIB)
            plan = editor_fixture.plan_ack(es_config, fixture)
            assert not plan['errors'], plan['errors']
            for response in responses:
                if response['type'] == 'plan_ack':
                    response.update(summary=plan['summary'], warnings=plan['warnings'])
        result = []
        for response in responses:""")
        worker.write_text(worker_text)
        env = isolated(root)
        env.update(EMAKI_INSTALLER_WIDTH=str(width), EMAKI_INSTALLER_HEIGHT=str(height),
                   EMAKI_INSTALLER_CHECKOUT=str(ROOT), TMPDIR=str(root),
                   TEST_ES_LANGUAGE='Español (Latinoamérica)')
        for screen, heading in zip(screens, headings):
            if selected and screen != selected:
                continue
            for bottom in (False, True) if screen in ('welcome', 'keyboard', 'disk', 'you', 'software', 'review', 'install') else (False,):
                name = 'installer-' + screen + ('-bottom' if bottom else '')
                env.update(EMAKI_INSTALLER_SCREEN=screen, EMAKI_INSTALLER_SCREENSHOT=str(OUTPUT / (name + '.png')),
                           EMAKI_INSTALLER_SCROLL_BOTTOM='1' if bottom else '0', TEST_ES_EXPECTED=translated('installer', heading))
                result = subprocess.run(['qs', '-p', str(root / 'ui/InstallerEs.qml'), '--no-color'],
                                        env=env, text=True, capture_output=True, timeout=20)
                log = result.stdout + result.stderr
                (OUTPUT / (name + '.log')).write_text(log)
                assert result.returncode == 0 and 'SCREENSHOT_OK ' + screen in log, log
                assert not any(error in log for error in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign')), log
                for line in log.splitlines():
                    if 'CONTENT_FRAME ' in line:
                        frame = json.loads(line[line.index('{'):])
                        assert not [item for item in frame['texts'] if item['truncated'] or (item['clipped'] and not item['body'])], frame
                if screen == 'welcome':
                    assert 'LANGUAGE_PERSISTENCE_OK ' + env['TEST_ES_LANGUAGE'] in log, log
                print('PASS', name)


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--component', choices=('programs', 'installer', 'all'), default='all')
    parser.add_argument('--screen', choices=('welcome', 'keyboard', 'network', 'timezone', 'disk', 'filesystem', 'encryption', 'you', 'software', 'review', 'install', 'done'))
    parser.add_argument('--width', type=int, default=1180)
    parser.add_argument('--height', type=int, default=780)
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.component in ('programs', 'all'):
        programs(args.width, args.height)
    if args.component in ('installer', 'all'):
        installer(args.width, args.height, args.screen)
    print(OUTPUT)


if __name__ == '__main__':
    main()
