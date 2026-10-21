#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture Spanish shell surfaces using private, offscreen fixture profiles."""
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
spec = importlib.util.spec_from_file_location('renderer', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
AUDIT = '''
    function inspect(item, found) {
        if (item.visible === false || item.opacity === 0) return;
        if (typeof item.text === "string" && item.text.length)
            found.push({text: item.text, truncated: item.truncated === true});
        for (const child of item.children || []) inspect(child, found);
    }
    property var copyAudit: []
    Timer { interval: 650; running: true; repeat: true
        onTriggered: { const found = []; inspect(parent, found); parent.copyAudit = found; }
    }
'''


def prepare(source):
    text = source.replace('import QtQuick\n', 'import QtQuick\nimport Emaki.I18n\n', 1)
    index = text.rfind('}')
    return text[:index] + AUDIT + '\nreadonly property var renderStats: ({copy: copyAudit})\n' + text[index:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', action='append', default=[], help='capture only this named surface (repeatable)')
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, 'i18n/catalogues.py', 'compile'], cwd=ROOT, check=True)
    translated = set()
    for component in ('shell', 'notes'):
        for message in ET.parse(ROOT / f'i18n/{component}/es.ts').findall('.//message'):
            value = ''.join(message.find('translation').itertext())
            if value != message.findtext('source'):
                translated.add(value)
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
        (qml / 'SettingsEs.qml').write_text(prepare(settings).replace('({copy: copyAudit})', '({copy: copyAudit, actualPage: view.page, requestedPage: requestedPage})'))
        (qml / 'WelcomeEs.qml').write_text(prepare('''import QtQuick
Rectangle { width: 800; height: 660; color: "#fffaf8"
property int page: 0
Welcome { anchors.fill: parent; page: parent.page }
}'''))
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
        region.write_text(text)
        env = {'EMAKI_LANGUAGE': 'es', 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
               'QML_IMPORT_PATH': str(renderer.build_plugin()) + os.pathsep + str(ROOT / '.cache/i18n-native/qml'),
               'EMAKI_BIN': '/nonexistent-emaki-fixture-command', 'EMAKI_SETTINGS_PROFILE': '',
               'EMAKI_NOTES_HELPER': '/nonexistent-emaki-fixture-command',
               'EMAKI_SHELL_NOTIFICATIONS': '0', 'EMAKI_SHELL_TRAY': '0',
               'EMAKI_SHELL_CLIPBOARD_RECORDER': '0', 'EMAKI_LIVE_SESSION': '0',
               'QT_QUICK_CONTROLS_STYLE': 'Basic',
               'EMAKI_FIXTURE_WALLPAPER': str(ROOT / 'art/wallpaper/ring.png'),
               'EMAKI_FIXTURE_PICTURE': str(ROOT / 'art/wallpaper/ring.png'),
               'EMAKI_FIXTURE_ACCOUNT': json.dumps({'state': 'ready', 'name': 'Emaki'})}
        captures = [('bar', 'BarEs', {}, 1536, 960)]
        captures += [(f'clock-{view}', 'ClockToolsRender', {'view': view, 'integrated': view in ('shade', 'calendar')}, 560, 720)
                     for view in ('timer', 'stopwatch', 'alarms', 'world', 'shade', 'calendar')]
        captures += [('notifications', 'ClockNotesRender', {}, 560, 650), ('notes', 'NotesWindowRender', {}, 1040, 740),
                     ('notes-empty', 'NotesWindowRender', {'empty': True}, 1040, 740)]
        captures += [(f'welcome-{page + 1}', 'WelcomeEs', {'page': page}, 800, 660) for page in range(5)]
        pages = re.findall(r'pageId: "([^"]+)"', (ROOT / 'shell/SettingsPageRegistry.qml').read_text())
        captures += [(f'settings-{page}', 'SettingsEs', {'requestedPage': page}, 1240, 810) for page in pages]
        if args.only:
            known = {capture[0] for capture in captures}
            assert set(args.only) <= known, sorted(set(args.only) - known)
            captures = [capture for capture in captures if capture[0] in args.only]
        for name, fixture, properties, width, height in captures:
            stats = OUTPUT / f'{name}.json'
            log = renderer.render(qml / f'{fixture}.qml', png=OUTPUT / f'{name}.png', stats=stats,
                                  properties=properties, width=width, height=height, warmup=1100,
                                  milliseconds=1, extra_env=env)
            (OUTPUT / f'{name}.log').write_text(log)
            state = json.loads(stats.read_text())['fixture_end']
            data = state['copy']
            if name.startswith('settings-'):
                assert state['actualPage'] == properties['requestedPage'], state
            for error in ('ReferenceError:', 'TypeError:', 'Binding loop'):
                assert error not in log, (name, log)
            assert any(item['text'] in translated for item in data), (name, data)
            if name == 'settings-region':
                assert any('español' in item['text'].lower() for item in data), data
            clipped = [item['text'] for item in data if item['truncated']]
            print(f'{name}: Spanish displayed; truncated={clipped}', flush=True)
            assert not [text for text in clipped if text in translated], (name, clipped)
    print(f'Captured {len(captures)} surfaces in {OUTPUT}')


if __name__ == '__main__':
    main()
