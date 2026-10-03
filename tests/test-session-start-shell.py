#!/usr/bin/env python3
"""Fresh-login shell readiness: real scene/surfaces, Qt frames, isolated helpers.

The copy replaces only Wayland PanelWindow plumbing with offscreen Windows and
system/helper data. The cover Loader is a signal-only stand-in (its real entry has
its own suite); capture readiness is delayed explicitly because software Qt cannot
exercise screencopy/GL. SessionStartup, shell entry, scene and surfaces stay real.
No niri socket, session bus, user files or installed session services are used.
"""
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
TOKEN = '0123456789abcdef0123456789abcdef'

MODEL = dict(schema_version=1, ipc_release='26.04', generation=1,
             connection=dict(status='connected', reason='ready'), model=dict(
                 workspaces={'1': dict(id=1, idx=1, output='fixture', is_active=True,
                                        is_focused=True, active_window_id=None)}, windows={},
                 outputs={'fixture': dict(logical=dict(x=0, y=0, width=960, height=640))},
                 focused_output='fixture', overview_open=False,
                 keyboard_layouts=dict(names=['English (US)'], current_idx=0), casts={}))

HELPER = '''import json, sys, time
from pathlib import Path
base = Path(__file__).parent.parent
op, token = sys.argv[1:3]
assert token == "TOKEN"
with (base / "startup-operations.jsonl").open("a") as out:
    out.write(json.dumps({"op": op, "dock": sys.argv[3] if op == "shell-ready" else None, "at": time.monotonic()}) + "\\n")
if op == "shell-ready":
    if FAILED_REPORT:
        raise SystemExit(1)
    (base / "ready-marker").touch()
'''

DRIVER = '''
    Timer { id: completeCover; interval: 350; onTriggered: root.coverController.completed(); }
    Timer {
        id: fixture
        interval: 20
        repeat: true
        running: true
        property int stage: 0
        property real began: Date.now()
        property real movedAt: 0
        property bool sawMiddle: false
        property int nativeStage: 0
        property real nativeChangedAt: 0
        function check(ok: bool, label: string): void {
            if (!ok) throw new Error(label);
        }
        onTriggered: {
            try {
                if (stage === 0) {
                    check(startup.enabled === FRESH, "startup enabled without fresh token");
                    check(scene.skipIntro === FRESH, "skip flag unavailable to initial scene");
                    check(!startup.ready && !startup.reported, "component construction claimed rendered readiness");
                    check(scene.services.startupReady, "nonlive fixture service awaited native hardware");
                    scene.services.osdRequested("sound");
                    check(scene.osd.shown === !FRESH, "startup sound OSD suppression changed ordinary behavior");
                    scene.osd.dismiss();
                    scene.services.osdRequested("light");
                    check(scene.osd.shown === !FRESH, "startup brightness OSD suppression changed ordinary behavior");
                    scene.osd.dismiss();
                    if (NATIVE_TEST) {
                        scene.services.helpersEnabled = false;
                        scene.services.live = true;
                    }
                    if (FRESH) {
                        scene.barPolicy.autoHide = true;
                        scene.dockStore.autoHide = true;
                        check(scene.bar.y === -60 && scene.dock.visibleAmount === 0,
                              "late initial hide policy animated under the cover");
                        scene.barPolicy.autoHide = false;
                        scene.dockStore.autoHide = false;
                        check(scene.bar.y === 0 && scene.dock.visibleAmount === (DOCK_ON ? 1 : 0),
                              "late initial show policy did not snap under the cover");
                    }
                    stage = 1;
                }
                if (NATIVE_FAILED && stage === 1) {
                    check(!scene.services.startupReady && !scene.startupModelsReady,
                          "failed native Loader was treated as initial readiness");
                    check(!startup.ready && !startup._reportStarted && !startup.reported,
                          "failed native Loader acknowledged readiness");
                    if (startup.coverActive) return;
                    check(Date.now() - began >= 4750, "failed native Loader bypassed cover completion");
                    stage = 3;
                }
                if (NATIVE_TEST && !NATIVE_FAILED && stage === 1 && nativeStage < 3) {
                    if (!scene.services.backend) return;
                    if (nativeStage === 0) {
                        check(!scene.services.startupReady && !scene.startupModelsReady && !startup._reportStarted,
                              "native initial-data gate did not hold shell readiness");
                        if (Date.now() - began < 700) return;
                        check(scene.niri.connected && scene.dockStore.restored, "native gate test never loaded other models");
                        scene.services.backend.startupReady = true;
                        check(scene.services.startupReady, "native initial-data gate never opened");
                        nativeStage = SYSTEM_ICONS ? 1 : 3;
                        nativeChangedAt = Date.now();
                        if (SYSTEM_ICONS) return;
                    } else if (Date.now() - nativeChangedAt >= 40) {
                        check(!startup._reportStarted, "icon change arrived after the quiet-period acknowledgement");
                        const before = scene.startupRevision;
                        const width = scene.bar.systemGlass.islandWidth;
                        if (nativeStage === 1) {
                            scene.services.backend.networkReady = true;
                            scene.services.backend.wifiEnabled = true;
                            scene.services.backend.wifiHardwareEnabled = true;
                            scene.services.backend.wifiDevices = [{}];
                            scene.services.backend.connectivity = "full";
                            scene.services.backend.networks = [{name: "fixture-private-ssid", connected: true, signal: .5}];
                        } else {
                            scene.services.backend.adapter = {enabled: true};
                        }
                        check(scene.bar.systemGlass.islandWidth === width, "icon-only change altered width");
                        check(scene.startupRevision !== before, "constant-width system icon change escaped revision");
                        check(!scene.startupRevision.includes("fixture-private-ssid"), "raw network name entered startup revision");
                        check(!startup.settled && startup.frameCounts.bar === 0 && !startup._reportStarted,
                              "system icon change did not request fresh quiet-period frames");
                        nativeChangedAt = Date.now();
                        ++nativeStage;
                        return;
                    } else return;
                }
                if (!surfacesLoader.item || (stage <= 1 && !scene.startupModelsReady))
                    return;
                if (!FRESH && stage === 1) {
                    check(!startup.coverActive && !startup.skipIntro && !startup.reported,
                          "ordinary restart started handshake or skipped animation");
                    stage = 3;
                }
                if (FRESH && stage === 1) {
                    if (MATERIAL_DELAYED && Date.now() - began < 650) {
                        check(!startup._reportStarted, "unready capture acknowledged shell frames");
                        return;
                    }
                    if (MATERIAL_CAP) {
                        if (Date.now() - began < 5750) return;
                        check(!startup.coverActive && !startup.reported, "unready material bypassed cap");
                        check(surfacesLoader.item.startupMaterial === "flat", "cap did not seal flat fallback");
                        check(surfacesLoader.item.liveMaterialsReady, "late capture never completed in fixture");
                        check(scene.bar.backdrop === null && scene.dock.backdrop === null,
                              "late capture switched visible material after cap");
                        stage = 3;
                    }
                    if (NO_OVERLAY_FRAMES) {
                        if (Date.now() - began < 800) return;
                        check(startup.settled && startup.barMapped && startup.dockMapped && startup.overlayMapped,
                              "negative control did not map/settle surfaces");
                        check(startup.frameCounts.bar >= 2 && startup.frameCounts.dock >= 2
                              && startup.frameCounts.overlay === 0, "negative control counted a missing overlay frame");
                        check(!startup.ready && !startup.reported, "mapping without overlay frames claimed readiness");
                        console.log("SESSION_START_SHELL_COMPLETE");
                        stop();
                        Qt.exit(0);
                        return;
                    }
                    if (FAILED_REPORT) {
                        if (Date.now() - began < 800) return;
                        check(startup.ready && startup._reportStarted && !startup.reported,
                              "failed report was treated as a successful acknowledgement");
                        stage = 2;
                    }
                    if (stage === 1) {
                    if (!startup.reported) return;
                    check(startup.ready, "ack preceded readiness");
                    check(startup.frameCounts.bar >= 2 && startup.frameCounts.overlay >= 2,
                          "bar/overlay ack preceded two frames");
                    check(!DOCK_ON || startup.frameCounts.dock >= 2, "dock ack preceded two frames");
                    check(DOCK_ON || (!startup.dockMapped && startup.frameCounts.dock === 0),
                          "disabled dock was required or mapped");
                    check(startup.skipIntro && scene.skipIntro, "intro released before cover completion");
                    check(surfacesLoader.item.backdropMode === "capture", "capture was not prepared under the startup cover");
                    check(surfacesLoader.item.materialsReady, "ack preceded final glass materials");
                    coverController.revealing();
                    completeCover.start();
                    check(surfacesLoader.item.startupMaterial === "live",
                          "cover transfer/drain changed the selected material");
                    check(scene.bar.y === 0, "initial bar was not at rest");
                    check(scene.bar.leftIslands.workspaceBubble.alpha.x === 1, "workspace drop still entering");
                    check(scene.dock.visibleAmount === (DOCK_ON ? 1 : 0), "initial dock visibility unsettled");
                    check(scene.dock.plateWidth.x === scene.dock.length, "initial dock width unsettled");
                    console.log("SESSION_START_RENDERED " + JSON.stringify(startup.frameCounts));
                    stage = 2;
                    }
                }
                if (stage === 2) {
                    if (startup.coverActive) return;
                    check(!LONG_COVER || Date.now() - began >= 5500, "independent shell timer cut the longer cover budget");
                    check(!scene.skipIntro && !startup.skipIntro, "cover completion did not restore animation");
                    check(surfacesLoader.item.backdropMode === "capture", "capture did not resume after cover");
                    check(!(FAILED_WATCH || FAILED_REPORT) || Date.now() - began >= 4750, "failed helper bypassed cover completion");
                    stage = 3;
                }
                if (stage === 3) {
                    scene.services.osdRequested("sound");
                    check(scene.osd.shown && scene.osd.kind === "sound", "sound OSD did not resume after the cover");
                    scene.osd.dismiss();
                    scene.services.osdRequested("light");
                    check(scene.osd.shown && scene.osd.kind === "light", "brightness OSD did not resume after the cover");
                    scene.osd.dismiss();
                    scene.barPolicy.autoHide = true;
                    scene.dockStore.autoHide = true;
                    check(scene.bar.y > -60, "normal bar close snapped instead of animating");
                    if (DOCK_ON)
                        check(scene.dock.visibleAmount > 0, "normal dock close snapped instead of animating");
                    movedAt = Date.now();
                    stage = 4;
                } else if (stage === 4) {
                    if (scene.bar.y < 0 && scene.bar.y > -60)
                        sawMiddle = true;
                    if (Date.now() - movedAt < 650) return;
                    check(sawMiddle && scene.bar.y === -60, "normal bar animation did not finish: "
                          + JSON.stringify({y: scene.bar.y, middle: sawMiddle, visible: scene.barPolicy.barVisible,
                                            revealed: scene.barPolicy.revealed, autoHide: scene.barPolicy.autoHide}));
                    check(!DOCK_ON || scene.dock.visibleAmount === 0, "normal dock animation did not finish");
                    console.log("SESSION_START_SHELL_COMPLETE");
                    stop();
                    Qt.exit(0);
                }
            } catch (error) {
                console.error("SESSION_START_SHELL_FAILED " + error.message);
                stop();
                Qt.exit(2);
            }
        }
    }
'''


def remove_block(source, marker):
    while marker in source:
        begin = source.index(marker)
        opening = source.index('{', begin)
        depth = 1
        end = opening + 1
        while depth:
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        source = source[:begin] + source[end:]
    return source


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(scenario):
    fresh = scenario not in ('ordinary-restart', 'invalid-token')
    dock_on = scenario != 'disabled-dock'
    failed_watch = scenario == 'failed-watch'
    failed_report = scenario == 'failed-report'
    no_overlay_frames = scenario == 'no-overlay-frames'
    native_test = scenario in ('native-delayed', 'system-icon-settle', 'native-failed')
    native_failed = scenario == 'native-failed'
    with tempfile.TemporaryDirectory(prefix='ss-', dir=CACHE) as temp:
        profile = Path(temp)
        for directory in ('r', 'config', 'state', 'cache', 'data', 'tmp'):
            (profile / directory).mkdir(mode=0o700)
        qml = profile / 'qml'
        shutil.copytree(ROOT / 'shell', qml)
        (profile / 'state/emaki').mkdir()
        (profile / 'state/emaki/dock.json').write_text(json.dumps(dict(
            version=1, on=dock_on, auto_hide=False, pinned=['fixture-one', 'fixture-two'])))
        (qml / 'helpers/session-start.py').write_text(HELPER.replace('TOKEN', TOKEN).replace(
            'FAILED_WATCH', str(failed_watch)).replace('FAILED_REPORT', str(failed_report)).replace(
            'DECLINED', str(scenario == 'cover-declined')).replace(
            'EARLY_DRAIN', str(scenario == 'early-drain')))
        (qml / 'helpers/wallpaper.py').write_text('import json\nprint(json.dumps(dict(state="unavailable", texture="")))\n')
        (qml / 'helpers/launcher-tools.py').write_text('import json,sys\njson.loads(sys.stdin.readline())\nprint(json.dumps(dict(schema_version=1,state="unavailable")))\n')
        if native_failed:
            (qml / 'SystemNative.qml').unlink()
        else:
            (qml / 'SystemNative.qml').write_text('import QtQuick\nSystemBackend { startupReady: ' +
                                                 str(not native_test).lower() + ' }\n')
        core = profile / 'core'
        core.write_text('#!/usr/bin/python3\nimport json,time\ntime.sleep(.25)\nprint(' + repr(json.dumps(MODEL)) + ', flush=True)\ntime.sleep(12)\n')
        core.chmod(0o700)
        surfaces = (qml / 'Surfaces.qml').read_text()
        surfaces = remove_block(surfaces, '        mask: Region {')
        surfaces = remove_block(surfaces, '        anchors {')
        surfaces = re.sub(r'^        (?:screen|exclusiveZone|exclusionMode|WlrLayershell\.[A-Za-z]+|BackgroundEffect.blurRegion|mask):[^\n]*\n', '', surfaces, flags=re.MULTILINE)
        surfaces = re.sub(r'^        implicit(?:Width|Height):[^\n]*\n', '', surfaces, flags=re.MULTILINE)
        surfaces = surfaces.replace('PanelWindow {', 'Window {\n        width: surfaces.controller.viewportWidth\n        height: surfaces.controller.viewportHeight')
        surfaces = surfaces.replace('surfaces.controller.output !== null', 'true')
        if no_overlay_frames:
            assert surfaces.count('surfaces.startup?.painted("overlay");') == 1
            surfaces = surfaces.replace('surfaces.startup?.painted("overlay");', '')
        (qml / 'Surfaces.qml').write_text(surfaces)
        # Preserve the real GPU pipeline file but replace its readiness input:
        # software Qt has no screencopy, and must not invent successful materials.
        backdrop = (qml / 'DockBackdrop.qml').read_text()
        backdrop = backdrop.replace('readonly property bool ready:', 'readonly property bool sourceReady:')
        backdrop = backdrop.replace('    id: backdrop', '    id: backdrop\n'
            '    property bool fixtureReady: false\n'
            '    readonly property bool ready: mode === "capture" ? fixtureReady : sourceReady\n'
            '    Timer { interval: ' + str(5500 if scenario == 'material-cap' else 800 if scenario == 'material-delayed' else 200) +
            '; running: backdrop.mode === "capture"; onTriggered: backdrop.fixtureReady = true }')
        (qml / 'DockBackdrop.qml').write_text(backdrop)
        # Emulate completion from the cover's reported budget, not SessionStartup.
        (qml / 'session-cover.qml').write_text('import QtQuick\nimport Quickshell\nScope { id: cover; '
            'signal revealing(); signal completed(); function complete(): void { completed(); } '
            'Timer { interval: 5000; running: true; onTriggered: { cover.revealing(); cover.completed(); } } }\n')
        entry = (qml / 'shell.qml').read_text().replace('headless: root.headless', 'headless: true')
        driver = DRIVER.replace('FRESH', str(fresh).lower()).replace('DOCK_ON', str(dock_on).lower()).replace(
            'FAILED_WATCH', str(failed_watch).lower()).replace('FAILED_REPORT', str(failed_report).lower()).replace(
            'NO_OVERLAY_FRAMES', str(no_overlay_frames).lower()).replace(
            'NATIVE_TEST', str(native_test).lower()).replace('NATIVE_FAILED', str(native_failed).lower()).replace(
            'SYSTEM_ICONS', str(scenario == 'system-icon-settle').lower()).replace(
            'MATERIAL_DELAYED', str(scenario == 'material-delayed').lower()).replace(
            'MATERIAL_CAP', str(scenario == 'material-cap').lower()).replace(
            'DECLINED', str(scenario == 'cover-declined').lower()).replace(
            'EARLY_DRAIN', str(scenario == 'early-drain').lower())
        driver = driver.replace('LONG_COVER', str(scenario == 'long-cover').lower())
        if scenario == 'long-cover':
            driver = driver.replace('id: completeCover; interval: 350', 'id: completeCover; interval: 5500')
            stub = (qml / 'session-cover.qml').read_text().replace('interval: 5000', 'interval: 7000')
            (qml / 'session-cover.qml').write_text(stub)
        entry = entry[:entry.rfind('}')] + driver + '\n}\n'
        (qml / 'check.qml').write_text(entry)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', EMAKI_BIN=str(core), NIRI_SOCKET='',
                   EMAKI_SHELL_BORDER='soft', EMAKI_SHELL_HEADLESS='0',
                   EMAKI_SHELL_TEST_WIDTH='960', EMAKI_SHELL_TEST_HEIGHT='640',
                   EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', EMAKI_TEST_MPRIS='0',
                   EMAKI_SHELL_BAR_AUTOHIDE='0', EMAKI_SETTINGS_PROFILE='',
                   EMAKI_SHELL_DOCK_BACKDROP='capture', EMAKI_PYTHON='/usr/bin/python3',
                   EMAKI_SESSION_START=TOKEN if fresh else '', EMAKI_SESSION_SKIP_INTRO='1' if fresh else '',
                   XDG_RUNTIME_DIR=str(profile / 'r'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_CACHE_HOME=str(profile / 'cache'),
                   XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
                   HOME=str(profile), TMPDIR=str(profile / 'tmp'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'GREETD_SOCK'):
            env.pop(key, None)
        if scenario == 'invalid-token':
            env.update(EMAKI_SESSION_START='invalid', EMAKI_SESSION_SKIP_INTRO='1')
        result = subprocess.run(['qs', '-p', str(qml / 'check.qml'), '--no-color'], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                timeout=12, preexec_fn=no_core)
        assert result.returncode == 0 and 'SESSION_START_SHELL_COMPLETE' in result.stdout, result.stdout
        assert not any(error in result.stdout for error in ('TypeError', 'ReferenceError', 'Binding loop', 'SESSION_START_SHELL_FAILED')), result.stdout
        operations = qml / 'startup-operations.jsonl'
        if fresh:
            records = [json.loads(line) for line in operations.read_text().splitlines()] if operations.exists() else []
            assert sorted(record['op'] for record in records) == (
                [] if no_overlay_frames or native_failed or scenario == 'material-cap' else ['shell-ready']), records
            if not no_overlay_frames and not native_failed and scenario != 'material-cap':
                assert next(record['dock'] for record in records if record['op'] == 'shell-ready') == (
                    'dock' if dock_on else 'no-dock'), records
            if not no_overlay_frames and not failed_report and not native_failed and scenario != 'material-cap':
                assert 'SESSION_START_RENDERED' in result.stdout
        else:
            assert not operations.exists(), 'ordinary shell restart invoked startup helper'
        print('PASS: real shell startup:', scenario)


for scenario in ('fresh-login', 'long-cover', 'disabled-dock', 'ordinary-restart', 'invalid-token',
                 'no-overlay-frames', 'failed-report',
                 'native-delayed', 'system-icon-settle', 'native-failed', 'material-delayed', 'material-cap'):
    run(scenario)
