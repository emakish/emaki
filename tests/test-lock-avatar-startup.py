#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded first-picture waiting and account changes on the production lock surface."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
QML = r'''
import QtQuick
import QtQuick.Window
import Quickshell
ShellRoot {
    id: root
    property int phase: 0
    property real since: Date.now()
    property int blankSamples: 0
    function find(item) {
        if (item.objectName === "lock-avatar") return item;
        for (const child of item.children) {
            const found = find(child);
            if (found) return found;
        }
        return null;
    }
    function fail(message) { console.log("STARTUP_FAILED " + message); Qt.exit(1); }
    function next(user) { phase++; since = Date.now(); picture.user = user; }
    // The silhouette is drawn in the shell's faint ink, which follows the theme (translucent
    // dark on light, opaque on dark): match the ink itself, not one theme's alpha.
    function silhouette(canvas, px) {
        const ink = LiquidPalette.faint, near = (a, b) => Math.abs(a - b * 255) < 8;
        return !canvas.showsPicture && near(px[0], ink.r) && near(px[1], ink.g) && near(px[2], ink.b) && near(px[3], ink.a);
    }
    // The helper's red face.png, not the silhouette.
    function face(canvas, px) { return canvas.showsPicture && px[3] > 240 && px[0] > 200 && px[1] < 60 && px[2] < 60; }
    LockAuth { id: auth }
    LockEnvironment { id: environment }
    LockSession { id: session; auth: auth; active: false; reducedMotion: true }
    AccountPicture { id: picture; user: "delayed" }
    Window {
        visible: true; width: 1000; height: 700
        LockSurface {
            id: surface; anchors.fill: parent
            session: session; auth: auth; environment: environment
            avatarUrl: picture.url; avatarPending: picture.pending
        }
    }
    Timer {
        interval: 25; running: true; repeat: true
        onTriggered: {
            const canvas = root.find(surface), age = Date.now() - root.since;
            if (!canvas || !canvas.available || canvas.width < 90) return;
            const px = canvas.getContext("2d").getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data, alpha = px[3];
            if (age > 5000) return root.fail("phase timed out " + root.phase);
            if (root.phase === 0) {
                if (picture.pending && age > 100) {
                    if (alpha !== 0) return root.fail("initial silhouette flash");
                    root.blankSamples++;
                }
                if (root.face(canvas, px)) {
                    if (root.blankSamples < 2) return root.fail("delayed response was not observed");
                    root.next("timeout");
                }
            } else if (root.phase === 1) {
                if (age > 100 && age < 800 && (!picture.pending || alpha !== 0))
                    return root.fail("account switch retained old picture or showed silhouette");
                if (age > 1100 && !picture.pending && root.silhouette(canvas, px))
                    root.phase = 2;
            } else if (root.phase === 2) {
                if (root.face(canvas, px)) root.next("absent");
            } else if (root.phase === 3) {
                if (age > 50 && picture.pending && alpha !== 0)
                    return root.fail("absent account switch flashed");
                if (!picture.pending && root.silhouette(canvas, px) && age < 950) {
                    console.log("STARTUP_COMPLETE delayed timeout late-picture absent account-switch");
                    Qt.quit();
                }
            }
        }
    }
}
'''
HELPER = r'''
import json
from pathlib import Path
import sys
import time
user = sys.argv[-1]
# A stale response must not settle the newly chosen account.
print(json.dumps(dict(version=1, user="previous", path="")), flush=True)
time.sleep({"delayed": .5, "timeout": 1.8, "absent": .3}[user])
path = str(Path(__file__).with_name("face.png")) if user != "absent" else ""
print(json.dumps(dict(version=1, user=user, path=path)), flush=True)
time.sleep(20)
'''
with tempfile.TemporaryDirectory(prefix='avatar-startup-') as folder:
    work = Path(folder)
    shutil.copytree(ROOT / 'shell', work / 'qml')
    shutil.copy(ROOT / 'tests/fixtures/lock/LockPam.qml', work / 'qml/LockPam.qml')
    (work / 'qml/check.qml').write_text(QML)
    (work / 'qml/helpers/account-picture.py').write_text(HELPER)
    (work / 'qml/helpers/lock-environment.py').write_text('')
    Image.new('RGB', (64, 64), 'red').save(work / 'qml/helpers/face.png')
    for name in ('LockVisual.qml', 'LockWordmark.qml'):
        (work / 'qml' / name).write_text('deliberately unavailable optional visual\n')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(work)),
               XDG_CACHE_HOME=str(work / 'cache'), XDG_CONFIG_HOME=str(work / 'config'),
               XDG_STATE_HOME=str(work / 'state'), XDG_DATA_HOME=str(work / 'data'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(work / 'none'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(work / 'none'), NIRI_SOCKET='')
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(work / 'qml/check.qml'), '--no-color'],
                            env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0 and 'STARTUP_COMPLETE' in result.stdout, result.stdout
    for name in ('lock.qml', 'greeter.qml'):
        assert 'avatarPending: accountPicture.pending' in (ROOT / 'shell' / name).read_text()
    print('PASS: first response waits without a silhouette; timeout, late update, absent account and account switch')
