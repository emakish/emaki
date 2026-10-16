#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The login screen's people and their look (0.9): the production greeter.qml offscreen.

Two people: the row of pictures and names, the remembered person chosen, Right/Left in an
empty field and a click choose another person, each person's published look (theme, accent)
shows before the password and switches with the person; typed input keeps the person. One
person: no row, the published look still shows. Pictures go to .cache/evidence (and to
G9_SHOTS when set). Only compositor plumbing and the metadata helpers are replaced, as in
tests/test-greeter-entry.py.
"""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)

STATE_STUB = '''import json, sys
from pathlib import Path
request = json.loads(sys.stdin.readline())
with (Path(__file__).parent.parent / "operations.jsonl").open("a") as record:
    record.write(json.dumps({"op": request["op"], "user": request.get("user")}) + "\\n")
people = PEOPLE
looks = {"alice": {"theme": "dark", "accent": "rose"}, "bob": {"theme": "light", "accent": "teal"}}
user = request.get("user") or "alice"
print(json.dumps(dict(version=1, ok=True, user=user, sessionId="niri-emaki.desktop",
    command=["niri-emaki-session"], wallpaperRoot=str(Path(__file__).parent.parent.parent / "published" / user),
    multipleUsers=len(people) > 1, message="", accounts=people if len(people) > 1 else [],
    look=looks[user])), flush=True)
'''

DRIVER = '''
    property var testKeyboard: null
    property var testSurface: null
    function findItem(item, name) {
        if (!item)
            return null;
        if (item.objectName === name)
            return item;
        for (let index = 0; index < item.children.length; ++index) {
            const found = findItem(item.children[index], name);
            if (found)
                return found;
        }
        return null;
    }
    Timer {
        id: driver
        interval: 50
        repeat: true
        running: true
        property int stage: 0
        property bool grabbing: false
        property real settledAt: 0
        function check(ok, label) {
            if (!ok)
                throw new Error(label);
        }
        function shot(name) {
            grabbing = true;
            root.testSurface.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("G9_OUTPUT") + "/" + name + ".png"))
                    Qt.exit(3);
                grabbing = false;
                ++stage;
                settledAt = 0;
            });
        }
        function settled(ready) {
            // Two seconds after the condition holds: the pour, the picture helpers and the
            // wallpaper decode have finished before a picture is taken.
            if (!ready) {
                settledAt = 0;
                return false;
            }
            if (!settledAt)
                settledAt = Date.now();
            return Date.now() - settledAt > 2000;
        }
        function idle() {
            return memory.ready && controller.fieldReady && controller.selectionReady && lockSession.phase === "locked";
        }
        function lookIs(theme, accent) {
            return Appearance.theme === theme && Appearance.accent === accent && ShellPalette.dark === (theme === "dark");
        }
        onTriggered: {
            if (grabbing || !root.testSurface || !root.testKeyboard)
                return;
            try {
                const row = findItem(root.testSurface, "greeter-users");
                if (stage === 0) {
                    if (!settled(idle() && root.testSurface.visualItem && root.testSurface.visualItem.readyForPour && lockSession.elapsed > .5))
                        return;
                    check(memory.user === "alice" && controller.user === "alice" && !controller.usernameMode, "the remembered person is not chosen");
                    check(lookIs("dark", "rose"), "the first person's look is not shown: " + Appearance.theme + " " + Appearance.accent);
                    if (!MULTIPLE) {
                        check(row === null, "one person shows a row of people");
                        stage = 99;  // the picture's callback moves on to 100
                        shot("one-person-dark");
                        return;
                    }
                    check(row !== null && row.visible, "two people show no row");
                    check(findItem(row, "greeter-user-alice") && findItem(row, "greeter-user-bob"), "a person is missing from the row");
                    shot("two-people-dark");
                } else if (stage === 1) {
                    root.testKeyboard.keyClick(Qt.Key_Right);
                    ++stage;
                } else if (stage === 2) {
                    if (!settled(idle() && memory.user === "bob"))
                        return;
                    check(controller.user === "bob" && memory.wallpaperRoot.endsWith("/published/bob"), "Right did not choose the next person");
                    check(lookIs("light", "teal"), "the next person's look is not shown: " + Appearance.theme + " " + Appearance.accent);
                    shot("two-people-light");
                } else if (stage === 3) {
                    root.testKeyboard.keyClick(Qt.Key_X);
                    check(controller.buffer.length === 1, "the field did not take input");
                    root.testKeyboard.keyClick(Qt.Key_Left);
                    check(memory.user === "bob" && memory.ready && controller.buffer.length === 1, "Left with typed input changed the person");
                    root.testKeyboard.keyClick(Qt.Key_Escape);
                    check(controller.buffer.length === 0, "Escape left input");
                    root.testKeyboard.keyClick(Qt.Key_Left);
                    ++stage;
                } else if (stage === 4) {
                    // The refreshed account list replaces the tiles: wait for layout
                    // before delivering a pointer event to the newly created item.
                    if (!settled(idle() && memory.user === "alice"))
                        return;
                    check(lookIs("dark", "rose"), "Left did not bring back the first person's look");
                    root.testKeyboard.keyClick(Qt.Key_Left);  // already the first: stays
                    check(memory.ready && memory.user === "alice", "Left beyond the first person changed something");
                    const tile = findItem(row, "greeter-user-bob");
                    root.testKeyboard.mouseClick(tile, tile.width / 2, tile.height / 2);
                    ++stage;
                } else if (stage === 5) {
                    if (!idle() || memory.user !== "bob")
                        return;
                    check(lookIs("light", "teal"), "a click did not choose the person and look");
                    stage = 100;
                } else if (stage === 100) {
                    console.log("GREETER_USERS_PASS");
                    stop();
                    Qt.quit();
                }
            } catch (error) {
                console.error("GREETER_USERS_FAILED " + error.message);
                stop();
                Qt.exit(2);
            }
        }
    }
    Timer {
        interval: 40000
        running: true
        onTriggered: {
            console.error("GREETER_USERS_TIMEOUT stage " + driver.stage + " user " + memory.user + " phase " + lockSession.phase);
            Qt.exit(4);
        }
    }
'''


def build(profile, multiple):
    qml = profile / 'qml'
    shutil.copytree(ROOT / 'shell', qml)
    people = [dict(login='alice', name='Alice Liddell'), dict(login='bob', name='Bob')] if multiple else [dict(login='alice', name='Alice Liddell')]
    (qml / 'helpers/greeter-state.py').write_text(STATE_STUB.replace('PEOPLE', repr(people)))
    (qml / 'helpers/lock-environment.py').write_text(
        'import json, sys, time\nprint(json.dumps(dict(layout="EN", outputs_active=True, caps=False)), flush=True)\ntime.sleep(60)\n')
    # Each person's published wallpaper: a different picture for each.
    for user, picture in (('alice', 'art/wallpaper/fallback.png'), ('bob', 'art/wallpaper/ring.png')):
        (profile / 'published' / user).mkdir(parents=True)
        shutil.copy(ROOT / picture, profile / 'published' / user / 'wallpaper.png')
    (qml / 'helpers/wallpaper.py').write_text(
        'import json, os, pathlib\nroot = pathlib.Path(os.environ["EMAKI_GREETER_WALLPAPER_ROOT"])\n'
        'print(json.dumps(dict(state="ready", texture=(root / "wallpaper.png").as_uri())))\n')
    entry = (ROOT / 'shell/greeter.qml').read_text()
    assert entry.count('PanelWindow {') == 1
    entry = entry.replace('import QtQuick\n', 'import QtQuick\nimport QtQuick.Window\nimport QtTest\n', 1)
    entry = entry.replace('PanelWindow {', 'Window {')
    entry = entry.replace('model: Quickshell.screens', 'model: [null]')
    entry = entry.replace('required property ShellScreen modelData',
                          'width: 1280\n            height: 800\n            visible: true\n            required property ShellScreen modelData')
    entry = entry.replace('            screen: modelData\n', '')
    entry = entry.replace('panel.screen === Quickshell.screens[0]', 'true')
    entry = entry.replace('screen: panel.screen', 'screen: panel.modelData')
    entry = re.sub(r'            anchors \{\n(?:                [^\n]*\n)+            }\n', '', entry)
    entry = re.sub(r'            (?:exclusiveZone|exclusionMode|WlrLayershell\.[A-Za-z]+):[^\n]*\n', '', entry)
    entry = entry.replace('                LockSurface {\n', '''            TestCase {
                id: keyboard
                when: false
            }
            Component.onCompleted: {
                root.testKeyboard = keyboard;
                root.testSurface = lockSurface;
                panel.requestActivate();
            }
            LockSurface {
''')
    marker = entry.rfind('}')
    entry = entry[:marker] + DRIVER.replace('MULTIPLE', 'true' if multiple else 'false') + '}\n'
    target = qml / 'check-users.qml'
    target.write_text(entry)
    return target


def run(multiple):
    with tempfile.TemporaryDirectory(prefix='gu-', dir=EVIDENCE) as work:
        profile = Path(work)
        for name in ('r', 'cache', 'config', 'data', 'state', 'tmp', 'out'):
            (profile / name).mkdir(mode=0o700)
        target = build(profile, multiple)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   HOME=str(profile), TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='', G9_OUTPUT=str(profile / 'out'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'), QS_DISABLE_CRASH_HANDLER='1')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'GREETD_SOCK', 'EMAKI_PYTHON'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(target), '--no-color'], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
                                preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
        output = result.stdout
        assert result.returncode == 0 and 'GREETER_USERS_PASS' in output, output[-4000:]
        assert not any(text in output for text in ('ReferenceError', 'TypeError', 'Binding loop')), output[-4000:]
        operations = [json.loads(line) for line in (profile / 'qml/operations.jsonl').read_text().splitlines()]
        expected = [('load', None)] + ([('select', 'bob'), ('select', 'alice'), ('select', 'bob')] if multiple else [])
        assert [(item['op'], item['user']) for item in operations] == expected, operations
        names = ['two-people-dark', 'two-people-light'] if multiple else ['one-person-dark']
        destinations = [EVIDENCE] + ([Path(os.environ['G9_SHOTS'])] if os.environ.get('G9_SHOTS') else [])
        for name in names:
            with Image.open(profile / 'out' / (name + '.png')) as image:
                image = image.convert('RGB')
                # The plate at the top left corner: the dark or the light glass.
                corner = image.getpixel((40, 40))
                assert (sum(corner) < 300) == name.endswith('dark'), (name, corner)
                if multiple:
                    # The row of people sits under the field: drops and names are drawn there.
                    strip = image.crop((440, 600, 840, 760))
                    assert len(strip.getcolors(maxcolors=1 << 20)) > 40, name
            for destination in destinations:
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(profile / 'out' / (name + '.png'), destination / ('g9-greeter-' + name + '.png'))
        print('PASS: greeter people and look;', 'two people' if multiple else 'one person')


def run_many():
    """All twelve tiles remain reachable on narrow outputs, including by mouse."""
    with tempfile.TemporaryDirectory(prefix='gu-many-', dir=EVIDENCE) as work:
        profile = Path(work)
        qml = profile / 'qml'
        shutil.copytree(ROOT / 'shell', qml)
        # This geometry test needs no system account lookup.
        (qml / 'helpers/account-picture.py').write_text('import json\nprint(json.dumps({"path": ""}))\n')
        target = qml / 'check-many.qml'
        target.write_text('''import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
Window {
    id: window
    width: 960
    height: 800
    visible: true
    property string clicked: ""
    GreeterUsers {
        id: people
        anchors.fill: parent
        accounts: Array.from({length: 12}, (_, i) => ({login: "person" + i, name: "Person " + i}))
        chosen: "person0"
        onPicked: login => window.clicked = login
    }
    TestCase { id: mouse; when: false }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children) {
            const found = find(child, name);
            if (found) return found;
        }
        return null;
    }
    Timer {
        interval: 80
        repeat: true
        running: true
        property int index: 0
        property bool resized: false
        onTriggered: {
            const login = "person" + index;
            const tile = window.find(people, "greeter-user-" + login);
            const point = tile.mapToItem(window.contentItem, 0, 0);
            if (point.x < 0 || point.x + tile.width > window.width) {
                console.error("GREETER_MANY_HIDDEN", login, point.x, tile.width, window.width);
                Qt.exit(2);
                return;
            }
            window.clicked = "";
            mouse.mouseClick(tile, tile.width / 2, tile.height / 2);
            if (window.clicked !== login) {
                console.error("GREETER_MANY_CLICK", login, window.clicked);
                Qt.exit(3);
                return;
            }
            if (++index === 12) {
                if (resized) {
                    // With the last tile visible, a wheel scroll reaches the first
                    // without changing the chosen account or requiring keyboard input.
                    mouse.mouseWheel(tile, tile.width / 2, tile.height / 2, 0, 2400);
                    const first = window.find(people, "greeter-user-person0");
                    mouse.mouseClick(first, first.width / 2, first.height / 2);
                    if (window.clicked !== "person0") {
                        console.error("GREETER_MANY_WHEEL", window.clicked);
                        Qt.exit(5);
                        return;
                    }
                    console.log("GREETER_MANY_PASS");
                    Qt.quit();
                    return;
                }
                window.width = 640;
                resized = true;
                index = 0;
            }
            people.chosen = "person" + index;
        }
    }
    Timer { interval: 10000; running: true; onTriggered: Qt.exit(4) }
}
''')
        for name in ('cache', 'config', 'data', 'state'):
            (profile / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   HOME=str(profile), QS_DISABLE_CRASH_HANDLER='1',
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        result = subprocess.run(['qs', '-p', str(target), '--no-color'], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        assert result.returncode == 0 and 'GREETER_MANY_PASS' in result.stdout, result.stdout[-4000:]
        assert not any(text in result.stdout for text in ('ReferenceError', 'TypeError', 'Binding loop')), result.stdout[-4000:]
        print('PASS: twelve people visible and clickable at 960 and 640 pixels')


if __name__ == '__main__':
    run_many()
    run(True)
    run(False)
