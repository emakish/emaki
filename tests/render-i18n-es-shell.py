#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture translated shell surfaces using private, offscreen fixture profiles."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / '.cache/evidence/i18n/es'
LANGUAGE = 'es'
LOCALES = {'en': 'en_US.UTF-8', 'es': 'es_MX.UTF-8', 'pt_BR': 'pt_BR.UTF-8', 'de': 'de_DE.UTF-8',
           'fr': 'fr_FR.UTF-8', 'ru': 'ru_RU.UTF-8'}
spec = importlib.util.spec_from_file_location('renderer', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
AUDIT = '''
    function inspect(item, found) {
        if (item.visible === false || item.opacity === 0) return;
        const origin = item.mapToItem(null, 0, 0);
        if (item.key === "confirm-no" || item.key === "confirm-go")
            found.push({text: item.label, control: item.key, truncated: false,
                        x: origin.x, y: origin.y, width: item.width, height: item.height});
        if (typeof item.text === "string" && item.text.length)
            found.push({text: item.text, objectName: item.objectName, truncated: item.truncated === true,
                        x: origin.x, y: origin.y, width: item.width, height: item.height,
                        contentWidth: item.contentWidth, contentHeight: item.contentHeight,
                        pixelSize: item.font ? item.font.pixelSize : null, fontWeight: item.font ? item.font.weight : null, color: String(item.color),
                        placeholder: item.parent && item.parent.placeholderText === item.text,
                        buttonLabel: item.objectName === "settings-button-label",
                        accessibleName: item.objectName === "settings-button-label" ? item.parent.Accessible.name : "",
                        tooltip: item.objectName === "settings-button-label" ? item.parent.ToolTip.text : ""});
        for (const child of item.children || []) inspect(child, found);
    }
    property var copyAudit: []
    Timer { interval: 650; running: true; repeat: true
        onTriggered: { const found = []; inspect(parent, found); parent.copyAudit = found; }
    }
'''


def prepare(source):
    text = source.replace('import QtQuick\n', 'import QtQuick\nimport QtQuick.Controls\nimport Emaki.I18n\n', 1)
    index = text.rfind('}')
    return text[:index] + AUDIT + '\nreadonly property var renderStats: ({copy: copyAudit})\n' + text[index:]


def main():
    global OUTPUT, LANGUAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', action='append', default=[], help='capture only this named surface (repeatable)')
    parser.add_argument('--language', choices=tuple(LOCALES), default='es')
    parser.add_argument('--skip-compile', action='store_true', help='reuse catalogues already compiled by the gate')
    parser.add_argument('--settings-width', type=int, default=1240)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    LANGUAGE = args.language
    OUTPUT = args.output.resolve() if args.output else ROOT / '.cache/evidence/i18n' / LANGUAGE
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not args.skip_compile:
        subprocess.run([sys.executable, 'i18n/catalogues.py', 'compile'], cwd=ROOT, check=True)
    translated = set()
    power_sources = dict(zip(('reboot', 'poweroff', 'logout', 'suspend', 'hibernate'),
                             ('Restart now?\nSave your work first.', 'Shut down now?\nSave your work first.',
                              'Log out now?\nSave your work first.', 'Sleep now?\nThe screen locks first.',
                              'Hibernate now?\nThe screen locks first.')))
    power_questions = {}
    action_sources = dict(zip(power_sources, ('Restart', 'Shut down', 'Log out', 'Sleep', 'Hibernate')))
    action_labels = {
        message.findtext('source'): message.findtext('translation')
        for context in ET.parse(ROOT / f'i18n/shell/{LANGUAGE}.ts').findall('context')
        if context.findtext('name') == 'SystemBody'
        for message in context.findall('message') if not message.findtext('comment')
    }
    if LANGUAGE == 'fr':
        assert [action_labels[key] for key in ('Sleep', 'Log out', 'Hibernate')] == [
            'Mettre en veille', 'Fermer la session', 'Mettre en hibernation']
    capture_labels = {
        message.findtext('source'): ''.join(message.find('translation').itertext())
        for context in ET.parse(ROOT / f'i18n/shell/{LANGUAGE}.ts').findall('context')
        if context.findtext('name') == 'Shell'
        for message in context.findall('message')
        if message.findtext('source') in ('Picture', 'Recording', 'Text', 'QR code')
    }
    assert len(capture_labels) == 4, capture_labels
    for component in ('shell', 'notes'):
        for message in ET.parse(ROOT / f'i18n/{component}/{LANGUAGE}.ts').findall('.//message'):
            value = ''.join(message.find('translation').itertext())
            if value != message.findtext('source'):
                translated.add(value)
            if component == 'shell' and message.findtext('source') in power_sources.values():
                power_questions[message.findtext('source')] = value
    with tempfile.TemporaryDirectory(prefix='es-shell-') as directory:
        base = Path(directory)
        qml = base / 'shell'
        shutil.copytree(ROOT / 'shell', qml)
        subprocess.run([sys.executable, str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
        # Every external command is replaced inside the disposable source copy.
        # Keep collectors and their real UI error handling; no host backend runs.
        for path in qml.rglob('*.qml'):
            content = path.read_text()
            content = re.sub(r'(?m)^(\s*)command: (?!\[).+$', r'\1command: ["/nonexistent-emaki-fixture-command"]', content)
            content = re.sub(r'command: \[', 'command: ["/nonexistent-emaki-fixture-command", ', content)
            content = re.sub(r'(\.command = )\[', r'\1["/nonexistent-emaki-fixture-command", ', content)
            content = re.sub(r'(?m)^(\s*\w+\.command = )(?!\[).+$', r'\1["/nonexistent-emaki-fixture-command"];', content)
            path.write_text(content)
        for name in ('ClockToolsRender', 'ClockNotesRender', 'NotesWindowRender'):
            (qml / f'{name}.qml').write_text(prepare((ROOT / f'tests/fixtures/{name}.qml').read_text()))
        settings = (ROOT / 'tests/fixtures/SettingsVisualTest.qml').read_text()
        start = settings.index('    readonly property var renderStats:')
        end = settings.index('    Image {', start)
        settings = settings[:start] + settings[end:]
        settings = settings.replace('property string requestedPage:', """property bool scrollBottom: false
    property bool showResetReview: false
    function openResetReview(item) {
        if (typeof item.reviewReset === "function") item.reviewReset();
        for (const child of item.children || []) openResetReview(child);
    }
    Timer { interval: 350; running: root.showResetReview; onTriggered: root.openResetReview(root) }
    function scrollToEnd(item) {
        if (item.objectName === "settings-main-scroll")
            item.contentY = Math.max(0, item.contentHeight - item.height);
        for (const child of item.children || []) scrollToEnd(child);
    }
    Timer { interval: 500; running: root.scrollBottom; onTriggered: root.scrollToEnd(root) }
    property string requestedPage:""")
        settings_view = qml / 'SettingsView.qml'
        settings_view.write_text(settings_view.read_text().replace(
            'id: scroll\n', 'id: scroll\n            objectName: "settings-main-scroll"\n'))
        maintenance = qml / 'SettingsMaintenancePage.qml'
        maintenance.write_text(maintenance.read_text().replace(
            'property var resetter: SettingsBridge.controller?.settingsReset ?? null',
            'property var resetter: QtObject { property bool busy: false; property bool undoable: false; property string message: "" }'))
        (qml / 'SettingsEs.qml').write_text(prepare(settings).replace('({copy: copyAudit})', '({copy: copyAudit, actualPage: view.page, requestedPage: requestedPage})'))
        (qml / 'WelcomeEs.qml').write_text(prepare('''import QtQuick
Rectangle { width: 800; height: 660; color: "#fffaf8"
property int page: 0
property bool scrollBottom: false
function scrollToEnd(item) {
    if (item.objectName === "welcome-body") item.contentY = Math.max(0, item.contentHeight - item.height);
    for (const child of item.children || []) scrollToEnd(child);
}
Welcome { anchors.fill: parent; page: parent.page }
Timer { interval: 500; running: parent.scrollBottom; onTriggered: parent.scrollToEnd(parent) }
}'''))
        (qml / 'AuthLanguage.qml').write_text(prepare('''import QtQuick
Rectangle {
    id: root
    width: 1024; height: 700; color: "#fffaf8"
    property bool greeter: false
    AuthController { id: auth }
    LockEnvironment { id: environment; isolateHelper: true }
    LockSession { id: session; auth: auth; started: false; secure: false; active: false; reducedMotion: true; phase: "locked"; selectedOutput: "offscreen"; clock: 10 }
    LockSurface {
        anchors.fill: parent
        auth: auth; session: session; environment: environment
        greeter: root.greeter
        accounts: root.greeter ? [{login: "mueller", name: "Müller"}, {login: "francois", name: "François"}, {login: "alena", name: "Алёна"}] : []
        chosenUser: "alena"
    }
    Timer { interval: 100; running: true; onTriggered: { environment.layout = "US / RU"; environment.capsLock = true; } }
}'''))
        shutil.copyfile(ROOT / 'tests/fixtures/SystemFixture.qml', qml / 'SystemFixture.qml')
        (qml / 'PowerLanguage.qml').write_text(prepare('''import QtQuick
Rectangle {
    id: root
    width: 520; height: 760; color: "#ece4df"
    property string action: "reboot"
    SystemFixture { id: backend }
    SystemService { id: service; backend: backend; live: false; helpersEnabled: false; canHibernate: true }
    NiriService { id: niri; binary: "" }
    SystemPanel {
        id: panel
        service: service; niri: niri
        page: "power"; opened: true; expansion: 1
        viewportWidth: root.width; viewportHeight: root.height
    }
    Binding { target: panel.body; property: "confirmation"; value: root.action }
}''').replace('({copy: copyAudit})', '({copy: copyAudit, confirmation: panel.body.confirmation, bodyWidth: panel.body.width})'))
        (qml / 'LauncherLanguage.qml').write_text(prepare('''import QtQuick
Rectangle {
    id: root
    width: 760; height: 760; color: "#fffaf8"
    property string mode: "Commands"
    property string query: ""
    NiriService { id: niri; binary: "" }
    QtObject {
        id: system
        property QtObject night: QtObject { property bool on: false }
    }
    LauncherBody {
        id: launcher
        anchors.fill: parent; anchors.margins: 20
        expansion: 1; opened: true; niri: niri; commandSystem: system
    }
    Component.onCompleted: { launcher.setMode(mode); launcher.setQuery(query); }
}''').replace('({copy: copyAudit})', '({copy: copyAudit, mode: launcher.modes[launcher.modeIndex], results: launcher.results.length, emojiState: launcher.emoji.state, emojiLanguage: launcher.emoji.dataLanguage})'))
        (qml / 'CaptureLanguage.qml').write_text(prepare('''import QtQuick
Rectangle {
    id: root
    width: 1000; height: 720; color: "#ece4df"
    property string mode: "picture"
    CaptureController {
        id: controller
        locked: false; screens: []; helperCommand: ["/nonexistent-emaki-fixture-command"]
        mode: root.mode
        outputs: [{name: "Virtual-1", width: root.width, height: root.height}]
        outputName: "Virtual-1"
        area: Qt.rect(100, 100, 500, 240)
    }
    CapturePanel { anchors.fill: parent; controller: controller }
    Timer { interval: 100; running: true; onTriggered: { controller.opened = true; controller.ready = true; } }
}''').replace('({copy: copyAudit})', '({copy: copyAudit, mode: controller.mode, canCapture: controller.canCapture})'))
        (qml / 'BarEs.qml').write_text(prepare('''import QtQuick
Rectangle { width: 1536; height: 960; color: "#ece4df"
NiriService { id: niri; binary: "" }
ShellScene { anchors.fill: parent; niri: niri; headless: true; testWidth: 1536; testHeight: 960; borderMode: "soft"; reservedSpace: 52 }
}'''))
        # Populate Region from an explicit fixture; never invoke the machine service.
        region = qml / 'SettingsRegionPage.qml'
        text = region.read_text().replace('Component.onCompleted: start(["get", "locale.language", "locale.formats", "time.zone", "time.automatic"])', '''Component.onCompleted: {
            received = true; installedLocales = ["en_US.UTF-8", "es_MX.UTF-8", "es_AR.UTF-8", "es_CO.UTF-8"];
            timeZones = ["America/Mexico_City"];
            rows = [{key: "locale.language", value: "es_MX.UTF-8", editable: true},
                    {key: "locale.formats", value: "", editable: true},
                    {key: "time.zone", value: "America/Mexico_City", editable: true},
                    {key: "time.automatic", value: true, editable: true}];
        }''')
        region.write_text(text.replace("es_MX.UTF-8", LOCALES[LANGUAGE]))
        env = {'EMAKI_LANGUAGE': LANGUAGE, 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
               'LANG': LOCALES[LANGUAGE], 'LC_ALL': LOCALES[LANGUAGE],
               'QML_IMPORT_PATH': str(renderer.build_plugin()) + os.pathsep + str(ROOT / '.cache/i18n-native/qml'),
               'EMAKI_BIN': '/nonexistent-emaki-fixture-command', 'EMAKI_SETTINGS_PROFILE': '',
               'EMAKI_NOTES_HELPER': '/nonexistent-emaki-fixture-command',
               'EMAKI_SHELL_NOTIFICATIONS': '0', 'EMAKI_SHELL_TRAY': '0',
               'EMAKI_SHELL_CLIPBOARD_RECORDER': '0', 'EMAKI_LIVE_SESSION': '0',
               'QT_QUICK_CONTROLS_STYLE': 'Basic',
               'EMAKI_FIXTURE_WALLPAPER': str(ROOT / 'art/wallpaper/ring.png'),
               'EMAKI_FIXTURE_PICTURE': str(ROOT / 'art/wallpaper/ring.png'),
               'EMAKI_FIXTURE_ACCOUNT': json.dumps({'state': 'ready', 'name': 'Emaki'})}
        captures = [('bar', 'BarEs', {}, 1536, 960),
                    ('lock', 'AuthLanguage', {}, 1024, 700),
                    ('greeter', 'AuthLanguage', {'greeter': True}, 1024, 700)]
        power_captures = [(f'power-{action}', 'PowerLanguage', {'action': action}, 520, 760)
                          for action in power_sources]
        captures += [(f'clock-{view}', 'ClockToolsRender', {'view': view, 'integrated': view in ('shade', 'calendar')}, 560, 720)
                     for view in ('timer', 'stopwatch', 'alarms', 'world', 'shade', 'calendar')]
        captures += [('notifications', 'ClockNotesRender', {}, 560, 650), ('notes', 'NotesWindowRender', {}, 1040, 740),
                     ('notes-empty', 'NotesWindowRender', {'empty': True}, 1040, 740)]
        captures += [(f'welcome-{page + 1}', 'WelcomeEs', {'page': page}, 800, 660) for page in range(5)]
        captures += [('welcome-3-bottom', 'WelcomeEs', {'page': 2, 'scrollBottom': True}, 800, 660)]
        pages = re.findall(r'pageId: "([^"]+)"', (ROOT / 'shell/SettingsPageRegistry.qml').read_text())
        captures += [(f'settings-{page}', 'SettingsEs', {'requestedPage': page}, 1240, 810) for page in pages]
        captures += [('launcher-commands', 'LauncherLanguage', {'mode': 'Commands'}, 760, 760),
                     ('launcher-websites', 'LauncherLanguage', {'mode': 'Commands', 'query': 'website'}, 760, 760),
                     ('launcher-emoji', 'LauncherLanguage', {'mode': 'Emoji'}, 760, 760),
                     ('settings-updates-bottom', 'SettingsEs', {'requestedPage': 'updates', 'scrollBottom': True}, 1240, 810),
                     ('settings-reset-review', 'SettingsEs', {'requestedPage': 'updates', 'showResetReview': True, 'height': 1100}, 1240, 1100)]
        captures += [(f'capture-{mode}', 'CaptureLanguage', {'mode': mode}, 1000, 720)
                     for mode in ('picture', 'text', 'qr')]
        captures += power_captures
        captures = [(name, fixture, dict(props, width=args.settings_width) if fixture == 'SettingsEs' else props,
                     args.settings_width if fixture == 'SettingsEs' else width, height)
                    for name, fixture, props, width, height in captures]
        if args.only:
            known = {capture[0] for capture in captures}
            assert set(args.only) <= known, sorted(set(args.only) - known)
            captures = [capture for capture in captures if capture[0] in args.only]
        clipping_failures = []
        for name, fixture, properties, width, height in captures:
            stats = OUTPUT / f'{name}.json'
            log = renderer.render(qml / f'{fixture}.qml', png=OUTPUT / f'{name}.png', stats=stats,
                                  properties=properties, width=width, height=height, warmup=1100,
                                  milliseconds=1, extra_env=env)
            (OUTPUT / f'{name}.log').write_text(log)
            state = json.loads(stats.read_text())['fixture_end']
            data = state['copy']
            for item in data:
                if item.get('buttonLabel'):
                    assert item['contentWidth'] <= item['width'] + 1, (name, item)
                    if item['truncated']:
                        assert item['accessibleName'] == item['text'] == item['tooltip'], (name, item)
            if name.startswith('settings-'):
                assert state['actualPage'] == properties['requestedPage'], state
            if name.startswith('launcher-'):
                assert state['mode'] == properties['mode'] and state['results'] > 0, state
                if name == 'launcher-emoji':
                    assert state['emojiState'] == 'ready', state
                    assert state['emojiLanguage'] == LANGUAGE.split('_')[0], state
            if name.startswith('capture-'):
                assert state['mode'] == properties['mode'] and state['canCapture'], state
                assert set(capture_labels.values()) <= {item['text'] for item in data}, (name, data)
            if name == 'settings-updates-bottom':
                assert any(item.get('objectName') == 'settings-reset-start' for item in data), (name, data)
            if name == 'settings-reset-review':
                for control in ('settings-reset-cancel', 'settings-reset-confirm'):
                    assert any(item.get('objectName') == control for item in data), (name, control, data)
            if name.startswith('power-'):
                assert state['confirmation'] == properties['action'], state
                assert state['bodyWidth'] == 480, state
                labels = [item for item in data if item.get('objectName', '').startswith('power-label-')]
                assert len(labels) == 6, (name, labels)
                assert all(not item['truncated'] for item in labels), (name, labels)
                labels.sort(key=lambda item: item['x'])
                assert 0 <= labels[0]['x'] and labels[-1]['x'] + labels[-1]['width'] <= width, (name, labels)
                for left, right in zip(labels, labels[1:]):
                    assert left['x'] + left['width'] <= right['x'] + 0.01, (name, left, right)
                expected = power_questions[power_sources[properties['action']]]
                question_text, hint_text = expected.split('\n', 1)
                questions = [item for item in data if item['text'] == question_text]
                hints = [item for item in data if item['text'] == hint_text]
                assert len(hints) == 1, (name, expected, data)
                assert len(questions) == 1, (name, expected, data)
                question = questions[0]
                hint = hints[0]
                assert not question['truncated'] and not hint['truncated'], (name, question, hint)
                assert question['pixelSize'] == 14 and hint['pixelSize'] == 13, (name, question, hint)
                assert hint['fontWeight'] == 500 and hint['color'] != question['color'], (name, question, hint)
                assert abs(hint['y'] - question['y'] - question['height']) < 0.01, (name, question, hint)
                assert hint['width'] == question['width'], (name, question, hint)
                controls = [item for item in data if item.get('control')]
                assert {item['control'] for item in controls} == {'confirm-no', 'confirm-go'}, (name, controls)
                for control in controls:
                    if control['control'] == 'confirm-go':
                        assert control['text'] == action_labels[action_sources[properties['action']]], (name, control)
                    assert 0 <= control['x'] and control['x'] + control['width'] <= width, (name, control)
                    for label in (question, hint):
                        overlaps = (label['x'] < control['x'] + control['width']
                                    and label['x'] + label['contentWidth'] > control['x']
                                    and label['y'] < control['y'] + control['height']
                                    and label['y'] + label['height'] > control['y'])
                        assert not overlaps, (name, label, control)
            for error in ('ReferenceError:', 'TypeError:', 'Binding loop'):
                assert error not in log, (name, log)
            if LANGUAGE != 'en':
                assert any(item['text'] in translated for item in data), (name, data)
            clipped = [item['text'] for item in data if item['truncated']]
            print(f'{name}: {LANGUAGE} displayed; truncated={clipped}', flush=True)
            translated_clips = [item['text'] for item in data if item['truncated']
                                and item['text'] in translated and not item.get('buttonLabel')
                                and not item.get('placeholder')]
            if translated_clips and not name.startswith('power-'):
                clipping_failures.append((name, translated_clips))
        assert not clipping_failures, clipping_failures
    print(f'Captured {len(captures)} surfaces in {OUTPUT}')


if __name__ == '__main__':
    main()
