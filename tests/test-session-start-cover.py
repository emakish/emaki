#!/usr/bin/env python3
"""Real PNG cover pixels, readiness/two-frame release, and deadline-drain/no-remap paths.

Only Wayland window plumbing and subprocess peers are replaced in an isolated
copy. The production cover state, entry and surface run in offscreen Qt.
"""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from PIL import Image, ImageChops
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
TOKEN = 'a'*32
READY = ('ready', 'slow-reply', 'observer-nonzero', 'observer-eof')
DRAIN = (*READY, 'deadline', 'observer-fails')

HELPER = '''import json, sys, time, math
from pathlib import Path
base = Path(BASE)
scenario = SCENARIO
op = sys.argv[1]
with (base / "operations").open("a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if op == "context":
    time.sleep(30 if scenario == "hung-context" else .8 if scenario == "slow-reply" else 0)
    value = CONTEXT
    value["expiresAtMs"] = time.time()*1000 + value["remainingMs"]
    if scenario == "delayed-reply": value["expiresAtMs"] -= 5000
    if scenario == "invalid-expiry": value["expiresAtMs"] = "undefined"
    if scenario == "infinite-expiry": value["expiresAtMs"] = "Infinity"
    (base / "context.json").write_text(json.dumps(value))
    print(json.dumps(value), flush=True)
if op == "observe":
    # The old fixture ignored argv[3], hiding the real helper's silent failure.
    expiry = float(sys.argv[3])
    assert math.isfinite(expiry) and expiry > time.time()*1000, sys.argv
    attempts = sum(json.loads(line)[0] == "observe" for line in (base / "operations").read_text().splitlines())
    if scenario == "observer-fails" or (scenario in ("observer-nonzero", "observer-eof") and attempts == 1):
        sys.stderr.write("fixture observer failure\\nunterminated tail")
        sys.exit(0 if scenario == "observer-eof" else 23)
    value = dict(version=1, ready=False, shellReady=False, coverMapped=True,
                 barMapped=True, dockMapped=True, wallpaperMapped=True,
                 autostartSettled=False, windowCount=2)
    print(json.dumps(value), flush=True)
    print(json.dumps(value), flush=True)  # duplicate observations must not spam
    time.sleep(.45)
    if scenario in READY_SCENARIOS:
        value.update(ready=True, shellReady=True, autostartSettled=True)
        print(json.dumps(value), flush=True)
    time.sleep(max(0, (expiry-time.time()*1000)/1000))
'''


def run(scenario):
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='cover-r2-') as tmp:
        base = Path(tmp)
        qml = base / 'qml'; shutil.copytree(ROOT / 'shell', qml)
        for directory in ('runtime', 'config', 'state', 'cache', 'data', 'bin'):
            (base / directory).mkdir(mode=0o700)
        image = Image.new('RGB', (320, 240))
        image.putdata([((x*3+y)%256, (x+y*2)%256, (x//3+y//2)%256) for y in range(240) for x in range(320)])
        image.save(base / 'frame.png'); image.save(base / 'frame-plate.png')
        if scenario == 'missing-plate':
            (base / 'frame-plate.png').unlink()
        elif scenario == 'corrupt-plate':
            (base / 'frame-plate.png').write_bytes(b'broken png')
        shutil.copytree(ROOT / '.cache/shell-shaders', qml / 'shaders', dirs_exist_ok=True)
        context = dict(version=1, active=scenario != 'late', remainingMs=1600 if scenario == 'deadline' else 2500,
                       outputs=['absent'] if scenario == 'no-output' else ['offscreen'], image=str(base / 'frame.png'),
                       visual=dict(clock=4, introStart=1, idleMode='lake'))
        (qml / 'helpers/session-start.py').write_text(HELPER.replace('BASE', repr(str(base)))
            .replace('SCENARIO\n', repr(scenario) + '\n').replace('CONTEXT', repr(context))
            .replace('READY_SCENARIOS', repr((*READY, 'hung-release'))))
        stub = base / 'bin/niri-emaki'
        stub.write_text('#!/usr/bin/python3\nimport os,sys,time\nfrom pathlib import Path\n'
                        'assert sys.argv[1:]==["msg","action","emaki-startup-cover-release"]\n'
                        'Path(os.environ["RELEASE_FILE"]).write_text("released")\n'
                        f'time.sleep({30 if scenario == "hung-release" else 0})\n')
        stub.chmod(0o700)
        entry = (qml / 'session-cover.qml').read_text()
        entry = entry.replace('import QtQuick\n', 'import QtQuick\nimport QtQuick.Window\n')
        entry = re.sub(r'model: root.visibleCover[^\n]*', 'model: root.visibleCover && root.context.outputs.includes("offscreen") ? [null] : []', entry)
        entry = entry.replace('PanelWindow {', 'Window {\n            width: 320; height: 240; visible: true')
        entry = entry.replace('            screen: modelData\n', '')
        entry = entry.replace('screen: panel.screen', 'screen: panel.modelData')
        entry = re.sub(r'            anchors \{.*?\n            }\n', '', entry, flags=re.S)
        entry = re.sub(r'^            (?:exclusionMode|WlrLayershell\.[A-Za-z]+):[^\n]*\n', '', entry, flags=re.M)
        entry = entry.replace('            mask: Region {}\n', '')
        entry = entry.replace('            SessionCoverSurface {', '''            Component.onCompleted: root.fixtureWindow = panel
            SessionCoverSurface {''')
        driver = '''
    SessionStartup { id: startup }
    onCompleted: startup.finish()
    property var fixtureWindow: null
    property bool tookShot: false
    property int drains: 0
    property int releases: 0
    property real fixtureCreated: Date.now()
    Connections {
        target: transition
        function onReleaseCover(): void {
            ++root.releases;
            if (!transition.presented || !transition.visualsReady || (!transition.desktopReady && !transition.deadlineReached))
                Qt.exit(3);
        }
        function onDrainStarted(reason: string): void {
            ++root.drains;
            if (reason !== EXPECT_REASON) Qt.exit(4);
        }
    }
    Timer {
        interval: 20; repeat: true; running: true
        onTriggered: {
            if (root.fixtureWindow && transition.presented && !root.tookShot) {
                root.tookShot = true;
                root.fixtureWindow.contentItem.grabToImage(result => result.saveToFile(SHOT));
            }
            if (root.closed) {
                if (root.context.active && Date.now() > root.context.expiresAtMs + 350)
                    Qt.exit(7);
                const ok = !startup.skipIntro && !startup.coverActive && (EXPECT_READY ? root.drains === 1 && root.releases === 1 && root.tookShot
                                        : root.drains === 0 && root.releases === EXPECT_RELEASES);
                console.log("COVER_DONE " + ok);
                console.log("COVER_CREATED " + root.fixtureCreated);
                Qt.exit(ok ? 0 : 5);
            }
        }
    }
    Timer { interval: 4000; running: true; onTriggered: Qt.exit(6) }
'''.replace('EXPECT_RELEASES', '1' if scenario == 'hung-release' else '0').replace('SHOT', json.dumps(str(base / 'shot.png'))).replace('EXPECT_READY', str(scenario in DRAIN).lower()).replace('EXPECT_REASON', json.dumps('deadline' if scenario in ('deadline', 'observer-fails') else 'ready'))
        entry = entry[:entry.rfind('}')] + driver + '\n}\n'
        (qml / 'check.qml').write_text(entry)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
                   QML_DISABLE_DISK_CACHE='1', EMAKI_SESSION_START=TOKEN, EMAKI_SESSION_SKIP_INTRO='1',
                   PATH=str(base / 'bin') + ':' + os.environ['PATH'], RELEASE_FILE=str(base/'release'),
                   XDG_RUNTIME_DIR=str(base/'runtime'), XDG_CONFIG_HOME=str(base/'config'),
                   XDG_STATE_HOME=str(base/'state'), XDG_CACHE_HOME=str(base/'cache'), XDG_DATA_HOME=str(base/'data'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path='+str(base/'no-bus'))
        for name in ('DISPLAY','WAYLAND_DISPLAY','NIRI_SOCKET','GREETD_SOCK'):
            env.pop(name,None)
        done = subprocess.run(['qs','-p',str(qml/'check.qml'),'--no-color'], env=env,
                              text=True,capture_output=True,timeout=6)
        log = done.stdout+done.stderr
        operations = [json.loads(line) for line in (base/'operations').read_text().splitlines()]
        (ROOT / '.cache' / f'r5-cover-{scenario}.log').write_text(log + repr(operations) + '\n')
        assert done.returncode == 0 and 'COVER_DONE true' in log, log + repr(operations)
        assert not any(word in log for word in ('TypeError','ReferenceError','Binding loop')), log
        events = [json.loads(line.split('EMAKI_SESSION_COVER ', 1)[1]) for line in log.splitlines() if 'EMAKI_SESSION_COVER ' in line]
        observers = [op for op in operations if op[0] == 'observe']
        exits = [event for event in events if event['phase'] == 'observer-exited']
        if scenario in ('late', 'delayed-reply', 'invalid-expiry', 'infinite-expiry', 'hung-context'):
            assert not observers, operations
        else:
            expiry = json.loads((base / 'context.json').read_text())['expiresAtMs']
            assert all(math.isfinite(float(op[2])) and float(op[2]) == expiry for op in observers), operations
            assert len(observers) == (3 if scenario == 'observer-fails' else 2 if scenario in ('observer-nonzero', 'observer-eof') else 1), operations
        if scenario in ('observer-nonzero', 'observer-eof', 'observer-fails'):
            assert len(exits) == (3 if scenario == 'observer-fails' else 1), exits
            for i, event in enumerate(exits):
                assert event['code'] == (0 if scenario == 'observer-eof' else 23), event
                assert event['status'] == 0 and event['attempt'] == i+1, event
                assert event['retry'] == (i < 2), event
                assert 'fixture observer failure\nunterminated tail' in event['stderr'], event
        if scenario == 'no-output':
            assert len(exits) == 1 and not exits[0]['retry'], exits
            assert exits[0]['atMs'] >= expiry, exits  # expiry also bounds retries
        if scenario in READY:
            loaded = next(event for event in events if event['phase'] == 'loaded')
            drain = next(event for event in events if event['phase'] == 'drain-ready')
            assert drain['atMs'] < expiry - 1600, events  # >350 ms before the drain deadline
            assert [op[2] for op in operations if op[0] == 'drain'] == ['ready'], operations
            observations = [event['observation'] for event in events if event['phase'] == 'observation']
            assert len(observations) == 2 and not observations[0]['ready'] and observations[1]['ready'], events
            assert observations[1] == dict(ready=True, shellReady=True, coverMapped=True,
                barMapped=True, dockMapped=True, wallpaperMapped=True, autostartSettled=True, windowCount=2), events
            if scenario == 'slow-reply':
                created = float(re.search(r'COVER_CREATED (\d+)', log)[1])
                assert loaded['atMs'] - created >= 750, events
        if scenario in ('deadline', 'observer-fails'):
            assert [op[2] for op in operations if op[0] == 'drain'] == ['deadline'], operations
        if scenario in (*DRAIN, 'hung-release'):
            with Image.open(base/'shot.png') as shot:
                assert ImageChops.difference(image,shot.convert('RGB')).getbbox() is None, 'PNG handoff changed pixels'
        else:
            assert not (base/'shot.png').exists(), 'late compositor cover mapped a second sheet'
        assert (base/'release').exists() == (scenario in (*DRAIN, 'hung-release'))
        print('PASS actual cover:',scenario)

for scenario in sys.argv[1:] or ('ready','deadline','slow-reply','observer-nonzero','observer-eof','observer-fails',
                 'invalid-expiry','infinite-expiry','late','delayed-reply',
                 'missing-plate','corrupt-plate','no-output','hung-context','hung-release'):
    run(scenario)
