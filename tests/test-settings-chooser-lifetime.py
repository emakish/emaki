#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Settings pages unload during selection; session services apply and retain replies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
base = Path(os.environ['CHOOSER_FIXTURE'])
state = json.loads((base / 'state.json').read_text())
if Path(sys.argv[0]).name == 'emaki-settings-users':
    args = sys.argv[1:]
    if args[0] == 'picture':
        assert args[:2] == ['picture', '--set']
        state['picture'] = True
        (base / 'state.json').write_text(json.dumps(state))
        with (base / 'calls.log').open('a') as log:
            log.write(json.dumps(args) + '\n')
        time.sleep(.3)
    print(json.dumps({'schema_version': 1, 'status': 'read' if args[0] == 'status' else 'applied',
        'users': [{'login': 'ada', 'name': 'Ada', 'uid': 1000, 'administrator': True,
          'locked': False, 'current': True, 'signed_in': True, 'home': '/home/ada',
          'picture': state['picture']}], 'administrators': 1}))
else:
    request = json.loads(sys.stdin.readline())
    op = request['op']
    if op.endswith('-choose'):
        index = state.get(op, 0)
        state[op] = index + 1
        (base / 'state.json').write_text(json.dumps(state))
        time.sleep(1.2)
        if index == 4:
            print('invalid reply')
        else:
            print(json.dumps({'schema_version': 1,
                'state': ['ready', 'portal_failed', 'cancelled', 'invalid_overview_background', '', 'ready', 'ready', 'ready'][index],
                'kind': 'picture', 'path': str(base / 'chosen picture.png')}))
    elif op == 'settings':
        if request['action'] == 'set':
            assert request['args'][0] in ('appearance.wallpaper', 'appearance.overview_background')
            chooser_op = 'wallpaper-choose' if request['args'][0] == 'appearance.wallpaper' else 'overview-choose'
            if state[chooser_op] == 6:
                print(json.dumps({'schema_version': 1, 'state': 'invalid_wallpaper', 'status': 'rejected'}))
                sys.exit(0)
            if state[chooser_op] == 8:
                print(json.dumps({'schema_version': 1, 'state': 'ready'}))
                sys.exit(0)
            if state[chooser_op] == 7:
                print('invalid reply')
                sys.exit(0)
            if request['args'][0] == 'appearance.wallpaper':
                state['wallpaper'] = request['args'][1]
            else:
                state['overview'] = json.loads(request['args'][1])['still']
            (base / 'state.json').write_text(json.dumps(state))
            with (base / 'calls.log').open('a') as log:
                log.write(json.dumps(['wallpaper', *request['args']]) + '\n')
            time.sleep(.3)
        print(json.dumps({'schema_version': 1, 'state': 'ready',
            'status': 'committed' if request['action'] == 'set' else 'read',
            'settings': [{'key': 'appearance.wallpaper', 'value': state['wallpaper'], 'default': ''},
                         {'key': 'appearance.overview_still', 'value': state.get('overview', ''), 'default': ''},
                         {'key': 'appearance.overview_video', 'value': '', 'default': ''}]}))
    else:
        print(json.dumps({'schema_version': 1, 'state': 'ready', 'entries': []}))
'''
QML = '''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
Scope {
 id: root
 readonly property bool translationsReady: Translation.initialize()
 QtObject {
  id: notices
  property var entries: []
  function local(app, summary, body) { entries = entries.concat([{app: app, summary: summary, body: body}]); }
 }
 SettingsCatalog { id: settingsCatalog; active: false; notificationStore: notices }
 Item {
  Loader { id: users; active: true; sourceComponent: Component { SettingsUsersPage { width: 740 } } }
  Loader { id: wallpaper; active: false; sourceComponent: Component { SettingsWallpaperPage { width: 740; catalog: settingsCatalog } } }
 }
 property int phase: 0
 property int tick: 0
 property int closedAt: 0
 property int scenario: 0
 property int noticesBefore: 0
 readonly property bool overview: scenario >= 10
 readonly property int choiceIndex: scenario < 2 ? scenario : overview ? scenario - 10 : scenario - 2
 readonly property bool refused: scenario >= 2 && [1, 3, 4, 5, 6, 7].includes(choiceIndex)
 property bool usersSetWhileClosed: false
 property bool wallpaperSetWhileClosed: false
 readonly property var loader: scenario < 2 ? users : wallpaper
 Connections {
  target: SettingsUsersService
  function onPendingChanged() {
   if (SettingsUsersService.pending && SettingsUsersService.operation === "picture")
    root.usersSetWhileClosed = !users.active && users.item === null;
  }
 }
 Connections {
  target: settingsCatalog
  function onCurrentActionChanged() {
   if (settingsCatalog.currentAction === "set")
    root.wallpaperSetWhileClosed = !wallpaper.active && wallpaper.item === null;
  }
 }
 function check(ok, message) {
  if (!ok) { console.error("FAILED", scenario, phase, message); Qt.exit(1); }
 }
 Timer {
  interval: 100; running: true; repeat: true
  onTriggered: {
   root.tick++;
   root.check(root.tick < 600, "lifetime timeout");
   if (root.phase === 0) {
    if (SettingsUsersService.pending || !SettingsUsersService.received || settingsCatalog.busy) return;
    root.loader.active = true;
    root.phase++;
   } else if (root.phase === 1) {
    if (!root.loader.item) return;
    root.noticesBefore = notices.entries.length;
    if (root.overview) settingsCatalog.chooseOverview();
    else root.loader.item.choosePicture();
    root.phase++;
   } else if (root.phase === 2) {
    const chooser = root.scenario < 2 ? root.loader.item.pictureChooser : root.overview ? settingsCatalog.overviewChooser : root.loader.item.chooser;
    root.check(chooser.busy && chooser.state === "pending", "chooser remains pending before destruction");
    root.loader.active = false;
    root.closedAt = root.tick;
    root.phase++;
   } else if (root.phase === 3) {
    root.check(root.loader.item === null, "page destroyed");
    if (root.tick - root.closedAt < 20 || SettingsUsersService.pending || settingsCatalog.busy) return;
    if (root.scenario === 0) root.check(root.usersSetWhileClosed, "picture setter ran after page destruction");
    if (root.scenario === 2) root.check(root.wallpaperSetWhileClosed, "wallpaper setter ran after page destruction");
    if (root.scenario >= 2) {
     root.check(notices.entries.length === root.noticesBefore + (root.refused ? 1 : 0), "one notice for refusal while page is destroyed; none for success/cancel");
     if (root.refused) {
      const error = root.overview ? settingsCatalog.overviewChooserError : settingsCatalog.wallpaperChooserError;
      const notice = notices.entries[notices.entries.length - 1];
      if (root.choiceIndex >= 6)
       root.check(error === "The change could not be confirmed. Check the current value before trying again.", "missing apply confirmation does not claim nothing changed");
      root.check(error.length > 0 && notice.app === "Emaki" && notice.summary === error && notice.body === "", "notice carries retained page error");
     }
    }
    root.loader.active = true;
    root.phase++;
   } else if (root.phase === 4) {
    if (!root.loader.item) return;
    const page = root.loader.item;
    if (root.scenario === 0) root.check(page.me.picture === true && !page.problem && !page.pictureError, "picture result retained on reopening");
    if (root.scenario === 1) root.check(page.me.picture === true && page.pictureError.indexOf("Could not choose") === 0, "picture error retained on reopening");
    if (root.scenario >= 2) {
     const error = root.overview ? page.catalog.overviewChooserError : page.chooserError;
     root.check(!!error === root.refused, "error retained on reopening, cancellation silent");
     if (root.overview) root.check(page.currentOverviewName === "chosen picture.png", "own overview displays filename only");
     else root.check(page.currentPath.endsWith("/chosen picture.png"), "previous wallpaper retained");
    }
    if (root.scenario === 17) { console.log("CHOOSER_LIFETIME_PASS"); Qt.quit(); }
    root.loader.active = false;
    root.scenario++;
    root.phase = 0;
   }
  }
 }
}
'''


def run(baseline=False, wallpaper_only=False):
    with tempfile.TemporaryDirectory(prefix='settings-chooser-lifetime-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        if baseline:
            for name in ('SettingsUsersPage.qml', 'SettingsUsersService.qml', 'SettingsWallpaperPage.qml', 'SettingsCatalog.qml'):
                original = subprocess.check_output(['git', 'show', '96aba30:shell/' + name], cwd=ROOT)
                (base / 'shell' / name).write_bytes(original)
        (base / 'shell/helpers/launcher-tools.py').write_text(FAKE)
        (base / 'shell/Test.qml').write_text(QML.replace('property int scenario: 0', 'property int scenario: 2') if wallpaper_only else QML)
        (base / 'bin').mkdir()
        fake = base / 'bin/emaki-settings-users'
        fake.write_text(FAKE)
        fake.chmod(0o700)
        (base / 'runtime').mkdir(mode=0o700)
        (base / 'state.json').write_text(json.dumps({'picture': False, 'wallpaper': ''}))
        env = dict(os.environ, CHOOSER_FIXTURE=str(base), HOME=str(base),
                   XDG_RUNTIME_DIR=str(base / 'runtime'), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   PATH=str(base / 'bin') + ':' + os.environ['PATH'],
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'))
        for name in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY', 'QT_QPA_PLATFORMTHEME'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/Test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=65)
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and 'CHOOSER_LIFETIME_PASS' in output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            passed = passed and error not in output
        calls = [json.loads(line) for line in (base / 'calls.log').read_text().splitlines()] \
            if (base / 'calls.log').exists() else []
        if passed:
            expected = [['picture', '--set', str(base / 'chosen picture.png'), '--json'],
                        ['wallpaper', 'appearance.wallpaper', str(base / 'chosen picture.png')],
                        ['wallpaper', 'appearance.overview_background',
                         json.dumps({'video': '', 'still': str(base / 'chosen picture.png')}, separators=(',', ':'))]]
            assert calls == (expected[1:] if wallpaper_only else expected), calls
        return passed, output


if __name__ == '__main__':
    import sys
    passed, output = run(baseline='--baseline' in sys.argv, wallpaper_only='--wallpaper-only' in sys.argv)
    assert passed, output
    print('PASS: Users and Wallpaper chooser jobs and setters survive page destruction; '
          'refusals notify once while closed; success/cancel are silent; errors and filenames survive reopening')
