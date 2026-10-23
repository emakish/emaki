#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The command menu: every action resolves to something Emaki has, search works in every
shipped language, and the mode works with the keyboard and the mouse in an
isolated offscreen launcher."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
passed = 0


def ok(message):
    global passed
    passed += 1
    print('PASS:', message)


# Commands.js evaluated by node with the translation calls of QML; `table` is a catalogue.
EVAL = r'''
const fs = require('fs'), vm = require('vm');
const [source, tablePath, queries] = process.argv.slice(1);
const table = tablePath ? JSON.parse(fs.readFileSync(tablePath, 'utf8')) : {};
const tr = (context, text) => table[context + '\u0004' + text] || text;
const context = vm.createContext({qsTranslate: tr, QT_TRANSLATE_NOOP: (c, t) => t});
const code = fs.readFileSync(source, 'utf8').replace(/^\.pragma library$/m, '');
vm.runInContext(code + '\nthis.actions = actions; this.search = search;', context);
const out = {actions: context.actions, found: {}};
for (const q of JSON.parse(queries)) out.found[q] = context.search(context.actions, q).map(f => f.entry.id);
console.log(JSON.stringify(out));
'''


def commands(table=None, queries=()):
    with tempfile.TemporaryDirectory() as directory:
        path = ''
        if table is not None:
            path = str(Path(directory) / 'table.json')
            Path(path).write_text(json.dumps(table))
        result = subprocess.run(['node', '-e', EVAL, str(ROOT / 'shell/Commands.js'), path, json.dumps(list(queries))],
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)


def catalogue(language):
    table = {}
    for context in ET.parse(ROOT / f'i18n/shell/{language}.ts').getroot().iter('context'):
        name = context.findtext('name')
        for message in context.iter('message'):
            if message.findtext('comment'):
                continue
            translation = message.find('translation')
            if translation is not None and translation.text and translation.get('type') not in ('unfinished', 'obsolete', 'vanished'):
                table[name + '\u0004' + message.findtext('source')] = translation.text
    return table


def ipc_functions():
    """target → functions of every IpcHandler in the shell."""
    found = {}
    for path in (ROOT / 'shell').glob('*.qml'):
        text = path.read_text()
        for match in re.finditer(r'IpcHandler\s*\{', text):
            depth, end = 1, match.end()
            while depth and end < len(text):
                depth += {'{': 1, '}': -1}.get(text[end], 0)
                end += 1
            block = text[match.end():end]
            target = re.search(r'target:\s*"([^"]+)"', block)
            if target:
                found.setdefault(target.group(1), set()).update(re.findall(r'function\s+(\w+)\s*\(', block))
    return found


def check_resolution():
    data = commands()
    actions = data['actions']
    ids = [a['id'] for a in actions]
    assert len(ids) == len(set(ids)), ids
    registry = (ROOT / 'shell/SettingsPageRegistry.qml').read_text()
    pages = set(re.findall(r'pageId:\s*"([^"]+)"', registry))
    keys = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', registry))
    body = (ROOT / 'shell/LauncherBody.qml').read_text()
    modes = json.loads(re.search(r'readonly property var modes: (\[[^\]]*\])', body).group(1))
    makefile = (ROOT / 'Makefile').read_text()
    installed = set(re.findall(r'scripts/(emaki-[\w-]+)', re.search(r'^SCRIPTS = (.*)$', makefile, re.M).group(1)))
    installed |= set(re.findall(r'\$\(BIN\)/([\w-]+)', makefile))
    desktop = (ROOT / 'packaging/emaki-desktop/PKGBUILD').read_text()
    depends = set(re.findall(r"'([a-z0-9-]+)(?:[<>=][^']*)?'", re.search(r'depends=\(([\s\S]*?)\)', desktop).group(1)))
    ipc = ipc_functions()
    system_tools = (ROOT / 'shell/helpers/system-tools.py').read_text()
    night = (ROOT / 'shell/NightLight.qml').read_text()
    service = (ROOT / 'shell/SystemService.qml').read_text()
    kinds = set()
    required = {'lock', 'logout', 'reboot', 'poweroff', 'theme-dark', 'theme-light', 'night-on', 'night-off',
                'updates', 'install', 'screenshot', 'record', 'clipboard', 'emoji', 'shortcuts', 'notes',
                'report', 'settings-wifi', 'settings-bluetooth', 'capture-text', 'capture-qr',
                'development', 'websites'}
    assert required <= set(ids), required - set(ids)
    assert {'settings-' + page for page in pages} <= set(ids), 'a Settings page has no command'
    for action in actions:
        call = action['call']
        assert action['title'] and action['keywords'] and action['icon'].endswith('-symbolic'), action
        if 'settings' in call:
            assert call['settings'] in pages, action
            kinds.add('settings')
        elif 'mode' in call:
            assert call['mode'] in modes, action
            kinds.add('mode')
        elif 'setting' in call:
            assert call['setting'][0] in keys and call['setting'][1] in ('light', 'dark'), action
            kinds.add('setting')
        elif 'night' in call:
            assert 'function setOn(value: bool)' in night and 'NightLight' in service, action
            kinds.add('night')
        elif 'session' in call:
            assert f"'{call['session']}'" in system_tools and 'confirm' not in action, action
            assert 'function session(value: string)' in (ROOT / 'shell/SystemBody.qml').read_text(), action
            assert 'onSessionRequested: action => scene.confirmSession(' in (ROOT / 'shell/ShellScene.qml').read_text(), action
            assert 'function act(' in service, action
            kinds.add('session')
        elif 'run' in call:
            program = call['run'][0]
            assert program in installed or program in depends, action
            if program == 'emaki-shell':
                target, function = call['run'][2], call['run'][3]
                assert call['run'][1] == 'call' and function in ipc.get(target, set()), (action, ipc.get(target))
            if program == 'niri':
                assert call['run'][1:3] == ['msg', 'action'], action
            if 'app' in call:
                assert list(ROOT.glob(f"*/{call['app']}.desktop")), action
            kinds.add('run')
        else:
            raise AssertionError(('unknown call', action))
        print(f"  {action['id']:24} → {json.dumps(call)}")
    assert kinds == {'settings', 'mode', 'setting', 'night', 'session', 'run'}, kinds
    ok(f'{len(actions)} actions, each resolves to an existing page, mode, setting, service call, program or IPC function')
    return actions


def check_languages(actions):
    english = commands(queries=['shut down', 'wifi', 'emoji', 'settings'])['found']
    assert english['shut down'][0] == 'poweroff' and english['wifi'][0] == 'settings-wifi', english
    assert english['emoji'][0] == 'emoji', english
    assert set(english['settings']) >= {a['id'] for a in actions if a['id'].startswith('settings-')}, english
    for language in ('es', 'pt_BR', 'de', 'fr', 'ru'):
        table = catalogue(language)
        missing = [a['id'] for a in actions if a['keywords'] not in [k.split('\u0004', 1)[1] for k in table if k.startswith('Commands\u0004')]]
        assert not missing, (language, 'untranslated keywords', missing)
        shut = table['SystemBody\u0004Shut down'].lower()
        restart = table['SystemBody\u0004Restart'].lower()
        found = commands(table, [shut, restart, 'shut down', 'restart', 'bluetooth'])['found']
        assert found[shut][0] == 'poweroff', (language, shut, found)
        assert found[restart][0] == 'reboot', (language, restart, found)
        assert found['shut down'][0] == 'poweroff' and found['restart'][0] == 'reboot', (language, found)
        assert found['bluetooth'][0] == 'settings-bluetooth', (language, found)
        accent_titles = {a['id']: table.get(a['context'] + '\u0004' + a['title'], a['title'])
                         for a in actions if a['id'] in ('settings-battery', 'settings-region', 'settings-about', 'capture-qr')}
        for action_id, title in accent_titles.items():
            plain = ''.join(char for char in unicodedata.normalize('NFD', title)
                            if not unicodedata.combining(char))
            queries = [title, plain, plain.upper(), unicodedata.normalize('NFD', title)]
            matches = commands(table, queries)['found']
            assert all(action_id in matches[query] for query in queries), (language, action_id, matches)
        feature_titles = {a['id']: table[a['context'] + '\u0004' + a['title']]
                          for a in actions if a['id'] in ('capture-text', 'capture-qr', 'development', 'websites')}
        feature_results = commands(table, feature_titles.values())['found']
        for action_id, title in feature_titles.items():
            assert feature_results[title][0] == action_id, (language, title, feature_results[title])
        ok(f'{language}: “{shut}” and “{restart}” find their actions, the English words still do')


def check_launcher():
    with tempfile.TemporaryDirectory(prefix='emaki-commands-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        (base / 'shell/commands-test.qml').write_text((ROOT / 'tests/fixtures/LauncherCommandsTest.qml').read_text())
        for name in ('home', 'config', 'data', 'cache', 'state', 'bin'):
            (base / name).mkdir(mode=0o700)
        log = base / 'ran.log'
        # The programs a row may start, as fakes that only write down their arguments.
        for program in ('emaki-lock', 'emaki-shell', 'niri', 'emaki-notes', 'emaki-update-manager'):
            fake = base / 'bin' / program
            fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "{program} $*" >> "{log}"\n')
            fake.chmod(0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
                   XDG_RUNTIME_DIR=str(runtime_path(base)), PATH=str(base / 'bin') + ':' + os.environ['PATH'],
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_LANGUAGE='en', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/commands-test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and 'LAUNCHER_COMMANDS_RESULT 8 0' in output, output
        ok('Commands tab with the mouse, search, type + arrows + Enter, Esc, session rows only ask the power panel (Enter, double Enter, double click), clicks, modes with an empty search; Emoji search, copy by Enter and click, recent first')
        for _ in range(50):
            if log.exists() and len(log.read_text().splitlines()) >= 3:
                break
            subprocess.run(['sleep', '0.1'])
        ran = sorted(log.read_text().splitlines()) if log.exists() else []
        assert ran == ['emaki-lock ', 'emaki-shell call capture record', 'niri msg action screenshot'], ran
        ok('rows started exactly: ' + ', '.join(line.strip() for line in ran))


actions = check_resolution()
check_launcher()
check_languages(actions)
print(f'PASS: {passed} checks')
