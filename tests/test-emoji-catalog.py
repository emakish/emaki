#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The launcher's emoji catalogue: generated data, search in every shipped language,
copy, and recent emoji first and kept across a restart, in an isolated offscreen shell."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'shell/emoji'
passed = 0


def check(condition, message):
    global passed
    assert condition, message
    passed += 1


# Generated data: every language file has the format the shell reads; English names every emoji.
english = json.loads((DATA / 'en.json').read_text(encoding='utf-8'))
check(english['format'] == 1 and len(english['emoji']) > 1800, 'en.json')
emoji = [row[0] for row in english['emoji']]
check(len(set(emoji)) == len(emoji), 'duplicate emoji')
check(not any(chr(tone) in e for e in emoji for tone in range(0x1F3FB, 0x1F400)), 'skin-tone variants are left out')
for language in ('es', 'pt', 'de', 'fr', 'ru'):
    value = json.loads((DATA / f'{language}.json').read_text(encoding='utf-8'))
    rows = value['emoji']
    check(value['format'] == 1 and value['language'] == language and len(rows) > 1800, language)
    check(set(row[0] for row in rows) <= set(emoji), f'{language}: emoji unknown to English')
    check(all(isinstance(row[1], str) and row[1] and isinstance(row[2], str) for row in rows), f'{language}: row shape')
check(sum(len(path.read_bytes()) for path in DATA.glob('*.json')) < 1200 * 1024, 'shipped emoji data stays small')
# With the Unicode packages installed the data must match them.
if all(any((Path(base) / part).exists() for base in (os.environ.get('XDG_DATA_DIRS') or '').split(':') if base)
       for part in ('unicode/emoji/emoji-test.txt', 'unicode/cldr/common/annotations/en.xml')):
    subprocess.run(['python3', '-I', str(ROOT / 'scripts/build-emoji-data'), '--check'], check=True, timeout=120)
    passed += 1

# Real QML, offscreen, private profile.
(ROOT / '.cache').mkdir(exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='emoji-', dir=ROOT / '.cache'))
qml = root / 'q'
# Only the catalogue and its data: it needs no other shell type (and no generated Platform.qml).
shutil.copytree(DATA, qml / 'emoji')
shutil.copyfile(ROOT / 'shell/EmojiCatalog.qml', qml / 'EmojiCatalog.qml')
# The private writer the recent list is saved through.
(qml / 'helpers').mkdir()
for helper in ('private-state.py', 'clipboard_store.py'):
    shutil.copyfile(ROOT / 'shell/helpers' / helper, qml / 'helpers' / helper)
(qml / 'qmldir').write_text('EmojiCatalog 1.0 EmojiCatalog.qml\n')
(qml / 'shell.qml').write_text('''import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    EmojiCatalog { id: en; language: "en_US" }
    EmojiCatalog { id: es; language: "es_MX.UTF-8" }
    EmojiCatalog { id: pt; language: "pt_BR" }
    EmojiCatalog { id: de; language: "de_DE.UTF-8" }
    EmojiCatalog { id: fr; language: "fr_FR.UTF-8" }
    EmojiCatalog { id: ru; language: "ru_RU.UTF-8" }
    EmojiCatalog { id: other; language: "ja_JP" }
    function pick(name: string): var {
        return ({ en: en, es: es, pt: pt, de: de, fr: fr, ru: ru, other: other })[name];
    }
    IpcHandler {
        target: "emoji"
        function state(name: string): string { return root.pick(name).state + " " + root.pick(name).dataLanguage + " " + root.pick(name).revision; }
        function load(name: string): void { root.pick(name).load(); }
        function search(name: string, query: string): string { return JSON.stringify(root.pick(name).search(query)); }
        function recent(name: string): string { return JSON.stringify(root.pick(name).recentRows()); }
        function choose(name: string, emoji: string): bool { return root.pick(name).choose(emoji); }
        function clipboard(): string { return Quickshell.clipboardText; }
        function setClipboard(text: string): void { Quickshell.clipboardText = text; }
        function setLanguage(name: string, value: string): void { root.pick(name).language = value; }
    }
}
''')
for directory in ('s', 'c', 'tmp'):
    (root / directory).mkdir(mode=0o700)
env = dict(os.environ, HOME=str(root), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
           QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1', XDG_STATE_HOME=str(root / 's'),
           XDG_CONFIG_HOME=str(root / 'c'), XDG_RUNTIME_DIR=str(runtime_path(root)), TMPDIR=str(root / 'tmp'))
for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'QT_LOGGING_RULES'):
    env.pop(key, None)


def ipc(*args):
    return subprocess.run(['qs', '-p', str(qml), 'ipc', 'call', 'emoji', *args], env=env, check=True,
                          capture_output=True, timeout=10).stdout.decode().strip()


def wait_for(probe, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if probe():
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(.05)
    raise AssertionError('wait timeout: ' + (root / 'qs.log').read_text(errors='replace')[-3000:])


def start():
    log = (root / 'qs.log').open('a')
    # umask 022, as many logins have: the recent list must still be private.
    proc = subprocess.Popen(['qs', '-p', str(qml), '--no-color'], env=env, stdout=log, stderr=subprocess.STDOUT,
                            umask=0o022)
    wait_for(lambda: ipc('state', 'en').startswith(('idle', 'loading', 'ready')))
    return proc


def stop(proc):
    proc.terminate()
    proc.wait(timeout=10)


def names(rows):
    return [row['name'] for row in rows]


proc = start()
try:
    # Nothing is read before the mode opens; the language picks the file.
    check(ipc('state', 'en').split()[:2] == ['idle', 'en'], ipc('state', 'en'))
    check(ipc('state', 'es').split()[1] == 'es' and ipc('state', 'pt').split()[1] == 'pt', 'language codes')
    check(ipc('state', 'other').split()[1] == 'en', 'unknown language falls back to English')
    check(json.loads(ipc('recent', 'en')) == [], 'no rows before loading')
    for name in ('en', 'es', 'pt', 'de', 'fr', 'ru', 'other'):
        ipc('load', name)
        wait_for(lambda name=name: ipc('state', name).startswith('ready'))
    # English: whole names and word prefixes first; every row has the documented shape.
    rows = json.loads(ipc('search', 'en', 'grinning face'))
    check(rows and rows[0]['emoji'] == '😀' and rows[0]['name'] == 'grinning face', rows[:3])
    check(set(rows[0]) == {'emoji', 'name', 'keywords', 'detail'} and isinstance(rows[0]['keywords'], list), rows[0])
    rows = json.loads(ipc('search', 'en', 'heart'))
    check('❤️' in [row['emoji'] for row in rows[:12]] and len(rows) <= 60, names(rows[:12]))
    check(json.loads(ipc('search', 'en', 'France'))[0]['emoji'] == '🇫🇷', 'flag search')
    check(json.loads(ipc('search', 'en', 'zzzqqq')) == [], 'no match')
    check('😍' in [row['emoji'] for row in json.loads(ipc('search', 'en', 'heart-eyes smiling'))[:3]], 'hyphen is a word break')
    # Spanish (Latin American) and Brazilian Portuguese names, accents optional; English still finds.
    rows = json.loads(ipc('search', 'es', 'cara sonriendo'))
    check(rows[0]['emoji'] == '😀' and rows[0]['name'] == 'cara sonriendo', rows[:3])
    check('❤️' in [row['emoji'] for row in json.loads(ipc('search', 'es', 'corazon rojo'))[:3]], 'accent folding')
    check(json.loads(ipc('search', 'es', 'grinning face'))[0]['emoji'] == '😀', 'English in a Spanish session')
    rows = json.loads(ipc('search', 'pt', 'coração vermelho'))
    check(rows[0]['emoji'] == '❤️' and rows[0]['name'] == 'coração vermelho', rows[:3])
    check(json.loads(ipc('search', 'pt', 'bandeira frança'))[0]['emoji'] == '🇫🇷', 'pt flag')
    for language, query in (('de', 'rotes herz'), ('fr', 'cœur rouge'), ('ru', 'красное сердце')):
        check(ipc('state', language).split()[1] == language, f'{language}: regional locale')
        rows = json.loads(ipc('search', language, query))
        check(rows and rows[0]['emoji'] == '❤️', (language, query, rows[:3]))
        check('😀' in [row['emoji'] for row in json.loads(ipc('search', language, 'grinning face'))],
              f'{language}: English fallback search')
    # A language change after loading reloads the names.
    ipc('setLanguage', 'other', 'de_DE')
    wait_for(lambda: ipc('state', 'other').split()[:2] == ['ready', 'de'] and json.loads(ipc('search', 'other', 'herz'))[:1] != [])
    check(json.loads(ipc('search', 'other', 'rotes herz'))[0]['emoji'] == '❤️', 'de after a language change')
    # Choose copies the emoji and puts it first in the recent list, also for an empty query.
    ipc('setClipboard', 'before')
    check(ipc('choose', 'en', '🇫🇷') == 'true', 'choose flag')
    check(ipc('choose', 'en', '😀') == 'true', 'choose face')
    check(ipc('clipboard') == '😀', ipc('clipboard'))
    check(ipc('choose', 'en', 'not an emoji') == 'false' and ipc('clipboard') == '😀', 'unknown text is refused')
    check([row['emoji'] for row in json.loads(ipc('recent', 'en'))] == ['😀', '🇫🇷'], ipc('recent', 'en'))
    check([row['emoji'] for row in json.loads(ipc('search', 'en', '  '))] == ['😀', '🇫🇷'], 'empty query is recent')
    stored = root / 's/emaki/emoji-recent.json'
    wait_for(stored.exists)
    check(json.loads(stored.read_text()) == {'version': 1, 'recent': ['😀', '🇫🇷']}, stored.read_text())
    check(stored.stat().st_mode & 0o777 == 0o600 and stored.parent.stat().st_mode & 0o777 == 0o700,
          'recent emoji private under umask 022: %o %o' % (stored.stat().st_mode, stored.parent.stat().st_mode))
finally:
    stop(proc)
# A list saved before by a readable writer is made private again when it is read.
os.chmod(stored, 0o644)
os.chmod(stored.parent, 0o755)

# Recent survives a restart and shows localized names; a newer format is left alone.
proc = start()
try:
    ipc('load', 'es')
    wait_for(lambda: ipc('state', 'es').startswith('ready'))
    rows = json.loads(ipc('recent', 'es'))
    check([row['emoji'] for row in rows] == ['😀', '🇫🇷'] and rows[0]['name'] == 'cara sonriendo', rows)
    wait_for(lambda: stored.stat().st_mode & 0o777 == 0o600 and stored.parent.stat().st_mode & 0o777 == 0o700)
    check(True, 'a readable recent list is repaired to 0600 in a 0700 folder')
finally:
    stop(proc)
stored.write_text(json.dumps({'version': 2, 'recent': ['😀'], 'more': True}))
proc = start()
try:
    ipc('load', 'en')
    wait_for(lambda: ipc('state', 'en').startswith('ready'))
    check(json.loads(ipc('recent', 'en')) == [], 'newer format not read')
    check(ipc('choose', 'en', '🎉') == 'true' and ipc('clipboard') == '🎉', 'copy still works')
    time.sleep(.3)
    check(json.loads(stored.read_text())['version'] == 2, 'newer format not overwritten')
finally:
    stop(proc)
# Damaged state: a huge list is not parsed (it took 20 s on the UI thread); a small malformed
# one keeps only distinct short strings, at most 24, and only catalogued ones show.
stored.write_text(json.dumps({'version': 1, 'recent': ['x%d' % n for n in range(100000)]}))
proc = start()
try:
    began = time.monotonic()
    ipc('load', 'en')
    wait_for(lambda: ipc('state', 'en').startswith('ready'), timeout=5)
    check(time.monotonic() - began < 5 and json.loads(ipc('recent', 'en')) == [], 'oversized recent list ignored')
finally:
    stop(proc)
stored.write_text(json.dumps({'version': 1, 'recent': [1, None, '', 'x' * 40, '🔥', '🔥', 'nope', '🎉'] + ['😀'] * 50}))
proc = start()
try:
    ipc('load', 'en')
    wait_for(lambda: ipc('state', 'en').startswith('ready'))
    check([row['emoji'] for row in json.loads(ipc('recent', 'en'))] == ['🔥', '🎉', '😀'], ipc('recent', 'en'))
finally:
    stop(proc)
log = (root / 'qs.log').read_text(errors='replace')
check('EmojiCatalog' not in log or 'error' not in log.lower(), log[-2000:])
shutil.rmtree(root)
print(f'emoji catalogue: {passed} checks passed')
