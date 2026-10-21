#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture Portuguese production shell items without a compositor or personal session.

Every process uses a temporary home and XDG directories, inaccessible session/system
buses, offscreen Qt and inert executable helpers. PNGs and visible text geometry are
written to .cache/evidence/i18n/pt_BR. Hardware pages include unavailable states;
these pictures do not claim hardware, PAM or compositor integration coverage.
"""
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

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.cache/evidence/i18n/pt_BR'
HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'
INSPECT = '''
    property var renderStats: ({texts: []})
    Timer { interval: 100; running: true; repeat: true; onTriggered: { const rows = []; root.inspect(root, rows); root.renderStats = {texts: rows, catalogue: qsTranslate("Shell", "Settings")}; } }
    function inspect(item, rows) {
        if (!item || !item.visible || item.opacity <= 0) return;
        if (typeof item.text === "string" && item.text) {
            const point = item.mapToItem(root, 0, 0);
            let clipped = false;
            for (let ancestor = item.parent; ancestor; ancestor = ancestor.parent) {
                if (!ancestor.clip) continue;
                const p = item.mapToItem(ancestor, 0, 0);
                if (p.y + item.height <= 0 || p.y >= ancestor.height || p.x + item.width <= 0 || p.x >= ancestor.width) return;
                if (p.y < -1 || p.x < -1 || p.y + item.height > ancestor.height + 1 || p.x + item.width > ancestor.width + 1) clipped = true;
            }
            rows.push({text: item.text, x: point.x, y: point.y, width: item.width,
                       height: item.height, contentWidth: item.contentWidth || 0,
                       contentHeight: item.contentHeight || 0, truncated: !!item.truncated,
                       clipped: clipped, object: item.objectName || ""});
        }
        for (const child of item.children || []) inspect(child, rows);
    }
'''
BAR = '''import QtQuick
Rectangle {
    id: root
    width: 1366; height: 200; color: "#e5d8d1"
    NiriService { id: niri; binary: "" }
    SystemBackend { id: backend }
    SystemService { id: service; backend: backend; helpersEnabled: false; live: false }
    HoverTip { id: tip }
    BarPolicy { id: policy; skipIntro: true; presentation: "desktop" }
    StaticBar {
        width: parent.width; height: 52
        policy: policy; tip: tip; borderMode: "soft"; launcherOpen: false
        launcherPresent: false; niri: niri; outputName: "fixture"; services: service
        skipIntro: true; dateTime: new Date(2026, 9, 21, 14, 30)
        notificationCount: 2
    }
    Component.onCompleted: {
        NotesService.active = false;
        niri.connection = "connected";
        niri.model = {windows: {}, outputs: [], workspaces: [{id: 1, idx: 1, output: "fixture", is_active: true}], keyboard_layouts: {names: ["Português (Brasil)"], current_idx: 0}};
    }
}
'''
WELCOME = '''import QtQuick
Rectangle {
    id: root
    width: 900; height: 760; color: "#fffaf8"
    property int requestedPage: 0
    Welcome { anchors.fill: parent; page: root.requestedPage }
    Component.onCompleted: NotesService.active = false
}
'''


def instrument(text):
    text = text.replace('import QtQuick\n', 'import QtQuick\nimport Emaki.I18n\n', 1)
    # Existing fixture statistics are replaced with text/geometry diagnostics.
    if 'readonly property var renderStats:' in text:
        start = text.index('    readonly property var renderStats:')
        end = text.index('    Image {', start)
        text = text[:start] + text[end:]
    return HEADER + text.replace('id: root\n', 'id: root\n' + INSPECT, 1)


def stage(base):
    shell = base / 'shell'
    shutil.copytree(ROOT / 'shell', shell)
    subprocess.run([sys.executable, str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True,
                   stdout=subprocess.DEVNULL)
    # Prevent every Python helper in the copied profile from inspecting the host.
    for path in (shell / 'helpers').glob('*.py'):
        path.write_text('import json\nprint(json.dumps({"schema_version":1,"state":"unavailable","status":"unavailable"}))\n')
    settings = (ROOT / 'tests/fixtures/SettingsVisualTest.qml').read_text()
    settings = settings.replace('navigationChecked && aboutReady && wallpaper.status === Image.Ready && view.children.some(item => item.objectName === "settings-glass" && item.ready)', 'navigationChecked')
    settings = settings.replace('id: catalog\n', '''id: catalog
        property var values: []
        property var defaults: []
        property var xkbLayouts: []
        property string inheritedWallpaper: ""
        function set(key, value) {}
        function reset(key) {}
''')
    settings = settings.replace('function row(key) {', '''function row(key) {
            const values = {"keyboard.layouts": "br", "keyboard.switch_key": "grp:win_space_toggle",
                "keyboard.repeat_delay": 500, "keyboard.repeat_rate": 30, "mouse.speed": 0,
                "touchpad.speed": 0, "notifications.until": "0", "notifications.schedule": "{}",
                "notifications.rules": "{}", "notifications.dnd": false,
                "defaults.browser": "", "defaults.files": "", "defaults.editor": "",
                "defaults.mail": "", "defaults.terminal": "", "keybindings.toggle_window_floating": "Super+V"};
            if (key in values) return {key: key, value: values[key], default: values[key], editable: true};''')
    settings = settings.replace('id: displays\n', 'id: displays\n        active: false\n')
    settings = settings.replace('property string requestedPage: "displays"', '''property string requestedPage: "displays"
    Timer {
        interval: 400; running: root.requestedPage === "users"
        onTriggered: {
            SettingsUsersService.accounts = [
                {login: "emaki", name: "Emaki", current: true, administrator: true, signed_in: true, locked: false, available: true},
                {login: "visitante", name: "Visitante", current: false, administrator: false, signed_in: false, locked: false, available: true}];
            SettingsUsersService.administrators = 1;
            SettingsUsersService.received = true;
            SettingsUsersService.message = "";
            SettingsUsersService.problem = false;
        }
    }''')
    settings = settings.replace('name: "Emaki landscape"', 'name: "Paisagem Emaki"')
    settings = settings.replace('name: "Home network"', 'name: "Rede de casa"')
    settings = settings.replace('"English (US)", "Ukrainian"', '"Português (Brasil)", "English (US)"')
    settings = settings.replace('Component.onCompleted: Qt.callLater(() => {', 'Component.onCompleted: Qt.callLater(() => {\n        NotesService.active = false;')
    settings = settings.replace('property string requestedPage:', 'property bool scrollBottom: false\n    Timer { interval: 400; running: root.scrollBottom; onTriggered: view.scroller.contentY = Math.max(0, view.scroller.contentHeight - view.scroller.height) }\n    property string requestedPage:')
    clocks = (ROOT / 'tests/fixtures/ClockToolsRender.qml').read_text()
    clocks = clocks.replace('"Morning walk"', '"Caminhada matinal"').replace('"Wednesday, 21 October"', '"quarta-feira, 21 de outubro"')
    clocks = clocks.replace('root.view !== "calendar"', '!["calendar", "notifications"].includes(root.view)')
    clocks = clocks.replace('Component.onCompleted: {', '''Timer {
        interval: 500; running: root.view === "notifications"
        onTriggered: {
            notifications.local("Programs & Updates", "Atualizações disponíveis", "Há novas atualizações para seus aplicativos.");
            notifications.local("Notas", "Lembrete", "Confira suas notas antes de sair.");
        }
    }
    Component.onCompleted: {''')
    notes = (ROOT / 'tests/fixtures/NotesWindowRender.qml').read_text()
    for old, new in [('A little room to think', 'Um espaço para pensar'), ('A few things for a quieter afternoon.', 'Algumas ideias para uma tarde tranquila.'), ('Sketch the next idea','Esboçar a próxima ideia'), ('Take a walk before sunset','Caminhar antes do pôr do sol'), ('Clear a space on the desk','Organizar um espaço na mesa'), ('Visit the bookshop','Visitar a livraria'), ('For the weekend','Para o fim de semana'), ('The small bookshop by the park, a long walk, and time to read.','A pequena livraria perto do parque, uma longa caminhada e tempo para ler.'), ('A thought to keep','Uma ideia para guardar'), ('Make the everyday things feel a little more considered.','Dar mais atenção às pequenas coisas do dia a dia.')]:
        notes = notes.replace(old, new)
    for name, text in [('Settings', settings), ('Clocks', clocks), ('Notes', notes), ('Welcome', WELCOME), ('Bar', BAR)]:
        (shell / ('Portuguese' + name + '.qml')).write_text(instrument(text))
    return shell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', help='capture names containing this substring')
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/build-shell-shaders")], check=True, stdout=subprocess.DEVNULL)
    spec = importlib.util.spec_from_file_location('portuguese_render', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    native = ROOT / '.cache/i18n-native/qml'
    assert (ROOT / 'i18n/locale/shell_pt_BR.qm').exists(), 'Compile Portuguese catalogues first'
    # The renderer clears display variables itself; remove its empty NIRI_SOCKET as well.
    original_run = subprocess.run
    def isolated_run(*arguments, **options):
        if 'env' in options:
            options['env'] = dict(options['env'])
            for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
                options['env'].pop(name, None)
        return original_run(*arguments, **options)
    subprocess.run = isolated_run
    with tempfile.TemporaryDirectory(prefix='emaki-portuguese-shell-') as temporary:
        base = Path(temporary)
        for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'bin'):
            (base / name).mkdir(mode=0o700)
        shell = stage(base)
        names = set(re.findall(r'\bemaki-[a-z][a-z0-9-]+', '\n'.join(p.read_text() for p in shell.rglob('*.qml'))))
        for name in names | {'niri', 'nmcli', 'pkexec', 'wlsunset', 'emaki-shell-health'}:
            path = base / 'bin' / name
            path.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"schema_version":1,"state":"unavailable","status":"unavailable"}\'\n')
            path.chmod(0o755)
        machine = base / 'bin/emaki-machine-settings'
        machine_reply = {'schema_version': 1, 'status': 'read',
                         'locales': ['en_US.UTF-8', 'pt_BR.UTF-8'],
                         'time_zones': ['America/Sao_Paulo', 'UTC'],
                         'settings': [{'key': key, 'value': value, 'source': 'system', 'editable': True}
                                      for key, value in [('locale.language', 'pt_BR.UTF-8'), ('locale.formats', 'pt_BR.UTF-8'),
                                                         ('time.zone', 'America/Sao_Paulo'), ('time.automatic', True)]]}
        machine.write_text('#!' + sys.executable + '\nprint(' + repr(json.dumps(machine_reply)) + ')\n')
        machine.chmod(0o755)
        imports = renderer.build_plugin()
        env = dict(HOME=str(base / 'home'), XDG_CONFIG_DIRS=str(base / 'config'),
                   PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
                   EMAKI_LANGUAGE='pt_BR', LC_ALL='pt_BR.UTF-8', EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   QML_IMPORT_PATH=str(native) + os.pathsep + str(imports),
                   EMAKI_BIN=str(base / 'bin'), EMAKI_LIVE_SESSION='0', EMAKI_SETTINGS_PROFILE='',
                   EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', EMAKI_SHELL_CLIPBOARD_RECORDER='0',
                   EMAKI_NOTES_HELPER=str(base / 'shell/helpers/notes.py'),
                   EMAKI_FIXTURE_WALLPAPER=(ROOT / 'art/wallpaper/fallback.png').as_uri(),
                   EMAKI_FIXTURE_PICTURE=str(ROOT / 'art/wallpaper/fallback.png'),
                   EMAKI_FIXTURE_ACCOUNT=json.dumps({'state':'ready','name':'Emaki','username':'fixture'}),
                   QT_QUICK_CONTROLS_STYLE='Basic')
        pages = re.findall(r'pageId: "([^"]+)"', (ROOT / 'shell/SettingsPageRegistry.qml').read_text())
        shots = [('bar', 'Bar', {}, 1366, 200)]
        shots += [('settings-' + page, 'Settings', {'requestedPage': page}, 1240, 810) for page in pages]
        shots += [('settings-' + page + '-bottom', 'Settings', {'requestedPage': page, 'scrollBottom': True}, 1240, 810) for page in pages]
        shots += [('welcome-' + str(page), 'Welcome', {'requestedPage': page}, 900, 760) for page in range(5)]
        shots += [('clocks-' + page, 'Clocks', {'view': page, 'integrated': page in ('shade', 'calendar', 'notifications')}, 560, 720) for page in ('timer','stopwatch','alarms','world','shade','calendar','notifications')]
        shots += [('notes', 'Notes', {}, 1040, 740), ('notes-empty', 'Notes', {'empty': True}, 1040, 740)]
        summary = []
        for name, component, properties, width, height in shots:
            if args.only and args.only not in name:
                continue
            log = renderer.render(shell / ('Portuguese' + component + '.qml'),
                                  png=OUTPUT / (name + '.png'), stats=OUTPUT / (name + '.json'),
                                  properties=properties, width=width, height=height,
                                  warmup=900, milliseconds=100, shader_dir=ROOT / '.cache/shell-shaders', extra_env=env)
            (OUTPUT / (name + '.log')).write_text(log)
            assert (OUTPUT / (name + '.png')).stat().st_size > 1000, name
            errors = [line for line in log.splitlines() if any(error in line for error in ('ReferenceError:', 'TypeError:', 'Binding loop', 'Unable to assign', 'Cannot assign', 'Required property', 'Failed to deserialize', 'Failed to find shader'))]
            stats = json.loads((OUTPUT / (name + '.json')).read_text())
            assert stats['fixture_end'].get('catalogue') == 'Configurações', name + ': Portuguese catalogue not loaded'
            texts = stats['fixture_end']['texts']
            summary.append({'name': name, 'texts': len(texts), 'truncated': [row for row in texts if row['truncated']], 'clipped': [row for row in texts if row['clipped']], 'errors': errors})
            print(name + ': ' + str(len(texts)) + ' visible labels; ' + str(len(errors)) + ' QML errors', flush=True)
        (OUTPUT / ('shell-summary' + ('-' + args.only if args.only else '') + '.json')).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        assert not any(row['errors'] for row in summary), 'See shell-summary JSON for QML errors'
    print(OUTPUT)


if __name__ == '__main__':
    main()
