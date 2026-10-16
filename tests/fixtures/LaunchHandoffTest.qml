// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Io
import QtTest

ShellRoot {
    id: root
    readonly property string destination: Quickshell.env("EMAKI_LAUNCH_SHOTS")
    readonly property bool evidenceOnly: Quickshell.env("EMAKI_LAUNCH_EVIDENCE_ONLY") === "1"
    NiriService {
        id: fixtureService
        binary: ""
    }
    AppCatalog {
        id: fixtureCatalog
        property bool launcherHeldAtStart: false
        property bool dockHeldAtStart: false
        property int starts: 0
        onLaunchStateChanged: {
            if (launchState === "pending") {
                launcherHeldAtStart = fixtureLauncher.opened;
                dockHeldAtStart = fixtureDock.keyboardActive;
                starts++;
            }
        }
    }
    Process {
        id: gateWriter
    }
    DockStore {
        id: fixtureStore
    }
    WindowLabels {
        id: fixtureLabels
        niri: fixtureService
    }
    AppIdentity {
        id: fixtureIdentity
        catalog: fixtureCatalog
    }
    Window {
        id: window
        width: 960
        height: 720
        visible: true
        Item {
            id: canvas
            anchors.fill: parent
            Rectangle {
                anchors.fill: parent
                color: "#263240"
            }
            Rectangle {
                x: 140
                y: 120
                width: 700
                height: 490
                color: "#455366"
                Text {
                    anchors.centerIn: parent
                    text: "Application window fixture"
                    color: "white"
                }
            }
            LauncherBody {
                id: fixtureLauncher
                width: 700
                height: 560
                opened: false
                expansion: opened ? 1 : 0
                visible: expansion > 0
                niri: fixtureService
                sharedCatalog: fixtureCatalog
                property var failures: []
                Connections {
                    target: fixtureLauncher
                    ignoreUnknownSignals: true
                    function onOpenFailed(reason: string): void {
                        console.log("LAUNCH_OPEN_FAILURE " + reason);
                        fixtureLauncher.failures = fixtureLauncher.failures.concat([reason]);
                    }
                }
                onDismissed: opened = false
                Behavior on expansion {
                    NumberAnimation {
                        duration: Metrics.morphMs
                        easing.type: Easing.BezierSpline
                        easing.bezierCurve: Metrics.morphCurve
                    }
                }
            }
            DockPolicy {
                id: fixturePolicy
                autoHide: false
            }
            Dock {
                id: fixtureDock
                anchors.fill: parent
                store: fixtureStore
                policy: fixturePolicy
                niri: fixtureService
                labels: fixtureLabels
                catalog: fixtureCatalog
                identity: fixtureIdentity
            }
        }
        TestCase {
            id: test
            name: "LaunchHandoff"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("LAUNCH_HANDOFF_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            property string stage: "startup"
            function shot(name) {
                stage = "capture " + name;
                if (!root.destination)
                    return;
                // QtTest requests a render synchronously, including settled frames where
                // no animation remains to drive an asynchronous grabToImage callback.
                const frame = grabImage(canvas);
                compare(frame.width, canvas.width, stage + " width");
                compare(frame.height, canvas.height, stage + " height");
                frame.save(root.destination + "/" + name + ".png");
            }
            function releaseLaunch(code) {
                gateWriter.command = [Platform.python, "-c", "import pathlib,sys; target=pathlib.Path(sys.argv[1]); temporary=target.with_suffix('.tmp'); temporary.write_text(sys.argv[2]); temporary.replace(target)", Quickshell.env("EMAKI_LAUNCH_GATE"), String(code)];
                gateWriter.running = true;
                tryCompare(gateWriter, "running", false);
                tryCompare(fixtureCatalog, "busy", false);
            }
            function releaseOpen(state) {
                gateWriter.command = [Platform.python, "-c", "import pathlib,sys; target=pathlib.Path(sys.argv[1]); temporary=target.with_suffix('.tmp'); temporary.write_text(sys.argv[2]); temporary.replace(target)", Quickshell.env("EMAKI_OPEN_GATE"), state];
                gateWriter.running = true;
                tryCompare(gateWriter, "running", false);
            }
            function test_fileOpens() {
                checkOtherOpens("file");
            }
            function test_webOpens() {
                checkOtherOpens("web");
            }
            function checkOtherOpens(kind) {
                if (root.evidenceOnly)
                    return;
                try {
                    otherOpens(kind);
                } catch (error) {
                    console.error("LAUNCH_OTHER_FAILURE stage=" + stage + " error=" + error + " stack=" + error.stack);
                    throw error;
                }
            }
            function otherOpens(kind) {
                for (const state of ["requested", "no_handler"]) {
                    stage = kind + " " + state;
                    fixtureLauncher.opened = true;
                    fixtureLauncher.reset();
                    wait(Metrics.morphMs + 30);
                    fixtureLauncher.modeIndex = kind === "web" ? 0 : 3;
                    if (kind === "web") {
                        fixtureLauncher.takeFocus();
                        fixtureLauncher.keyboardControls().find(item => item.objectName === "launcher-search").text = "z";
                    }
                    tryVerify(() => fixtureLauncher.results.some(row => row.kind === kind));
                    fixtureLauncher.selectAt(fixtureLauncher.results.findIndex(row => row.kind === kind));
                    fixtureLauncher.activate();
                    const closedBeforeReply = !fixtureLauncher.opened;
                    fixtureLauncher.opened = true;
                    const failures = fixtureLauncher.failures.length;
                    releaseOpen(state);
                    wait(200);
                    if (state !== "requested")
                        tryCompare(fixtureLauncher, "failures", fixtureLauncher.failures.slice(0, failures).concat([fixtureLauncher.stateNote(kind === "web" ? "web" : "file-open", state)]));
                    wait(150);
                    verify(closedBeforeReply, kind + " releases launcher before helper reply");
                    verify(fixtureLauncher.opened, kind + " late reply preserves reopened launcher");
                    compare(fixtureLauncher.failures.length, failures + (state === "requested" ? 0 : 1));
                    fixtureLauncher.opened = false;
                }
            }
            function test_handoff() {
                try {
                    handoff();
                } catch (error) {
                    console.error("LAUNCH_HANDOFF_FAILURE stage=" + stage + " error=" + error + " stack=" + error.stack);
                    throw error;
                }
            }
            function handoff() {
                tryVerify(() => fixtureCatalog.entries.length === 1);
                tryCompare(fixtureStore, "restored", true);
                fixtureStore.pinned = [fixtureCatalog.entries[0].id];
                fixtureLauncher.opened = true;
                fixtureLauncher.modeIndex = 1;
                wait(Metrics.morphMs + 100);
                shot("launcher-00-open");
                fixtureLauncher.activate();
                compare(fixtureCatalog.starts, 1);
                shot("launcher-01-start");
                wait(80);
                shot("launcher-02-080ms");
                wait(80);
                shot("launcher-03-160ms");
                wait(Metrics.morphMs);
                shot("launcher-04-settled");
                console.log("LAUNCH_HANDOFF_OBSERVED launcher-held=" + fixtureCatalog.launcherHeldAtStart);
                if (!root.evidenceOnly) {
                    verify(!fixtureCatalog.launcherHeldAtStart, "Release launcher before starting the application");
                    console.log("LAUNCH_HANDOFF_CLOSED opened=" + fixtureLauncher.opened + " expansion=" + fixtureLauncher.expansion);
                    verify(!fixtureLauncher.opened);
                    tryCompare(fixtureLauncher, "expansion", 0);
                }
                stage = "late completion after reopening";
                // A helper reply arriving after a fresh opening must not close that opening.
                fixtureLauncher.opened = true;
                fixtureLauncher.takeFocus();
                keyClick(Qt.Key_F);
                if (!root.evidenceOnly) {
                    compare(fixtureCatalog.launchState, "pending", "Typing cannot erase an in-flight request");
                    fixtureLauncher.activate();
                    verify(fixtureLauncher.opened, "Busy launch retains the selected row");
                    compare(fixtureLauncher.noteText(), "Starting the app…", "Busy launch gives visible feedback");
                    compare(fixtureCatalog.starts, 1);
                }
                releaseLaunch(0);
                tryCompare(fixtureCatalog, "launchState", "requested");
                console.log("LAUNCH_HANDOFF_REOPEN opened=" + fixtureLauncher.opened + " pending=" + fixtureLauncher.pendingRecentApp);
                if (!root.evidenceOnly) {
                    verify(fixtureLauncher.opened, "A late launch reply must not dismiss a new visit");
                    compare(fixtureLauncher.pendingRecentApp, "");
                }
                stage = "dock preparation";
                fixtureLauncher.opened = false;
                wait(Metrics.morphMs + 50);
                fixtureDock.takeFocus();
                wait(150);
                shot("dock-00-keyboard");
                stage = "dock after a desktop rescan";
                // The fixture's ID is found twice (XDG_DATA_HOME and XDG_DATA_DIRS are one
                // directory), like a copy in ~/.local/share/applications over a system entry.
                // Each rescan then replaces its DesktopEntry and deletes the old object, so a
                // pinned icon must never act on an object from before the last scan. The packaged
                // watcher (quickshell-emaki 0007) rescans on a change inside an applications
                // directory, not on one in its parents: write the mimeinfo.cache that
                // update-desktop-database writes after each package transaction.
                const held = DesktopEntries.byId(fixtureCatalog.entries[0].id);
                gateWriter.command = [Platform.python, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('[MIME Cache]\\n')", Quickshell.env("XDG_DATA_HOME") + "/applications/mimeinfo.cache"];
                gateWriter.running = true;
                tryCompare(gateWriter, "running", false);
                tryVerify(() => DesktopEntries.byId(fixtureCatalog.entries[0].id) !== held, 5000, "A rescan replaces a doubly listed entry");
                wait(50);
                stage = "dock launch";
                fixtureDock.activate(fixtureCatalog.entries[0].id);
                console.log("LAUNCH_HANDOFF_DOCK starts=" + fixtureCatalog.starts + " pinned=" + fixtureStore.pinned + " entries=" + fixtureDock.entries.length);
                compare(fixtureCatalog.starts, 2);
                shot("dock-01-start");
                wait(80);
                shot("dock-02-080ms");
                wait(160);
                shot("dock-03-240ms");
                console.log("LAUNCH_HANDOFF_OBSERVED dock-held=" + fixtureCatalog.dockHeldAtStart);
                if (!root.evidenceOnly) {
                    verify(!fixtureCatalog.dockHeldAtStart, "Release dock before starting the application");
                    verify(!fixtureDock.keyboardActive);
                }
                releaseLaunch(0);
                tryCompare(fixtureCatalog, "launchState", "requested");
                stage = "failed launch cleanup";
                fixtureLauncher.opened = true;
                fixtureLauncher.activate();
                compare(fixtureCatalog.starts, 3);
                releaseLaunch(1);
                tryCompare(fixtureCatalog, "launchState", "failed");
                if (!root.evidenceOnly)
                    compare(fixtureLauncher.pendingRecentApp, "");
            }
        }
    }
}
