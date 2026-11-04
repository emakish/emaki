// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import ".." as UI
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: test
    readonly property bool translationsReady: Shell.Translation.initialize()
    property double started: Number(Quickshell.env("TEST_START_MS"))
    property bool captured: false
    property bool grabbing: false
    property var recorded: ({})
    function mark(event, detail) {
        if (recorded[event]) return;
        recorded[event] = true;
        console.log("STARTUP_EVENT " + JSON.stringify({event: event, ms: Date.now() - started, detail: detail || ""}));
    }
    function card(item) {
        // Both start screens use cards with a real catalog program. Ignore placeholders,
        // invisible ancestors and items outside the initial viewport.
        if (!item.visible || item.opacity === 0) return null;
        if (item.program && item.program.key && item.width > 0 && item.height > 0) {
            const point = item.mapToItem(view, 0, 0);
            if (point.x >= 0 && point.x < view.width && point.y >= 0 && point.y < view.height)
                return item;
        }
        for (const child of item.children || []) {
            const found = card(child);
            if (found) return found;
        }
        return null;
    }
    Component.onCompleted: {
        Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}];
        if ("discoveryDay" in view) view.discoveryDay = "2026-11-01";
        mark("qml-completed");
    }
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs }
    Connections {
        target: programs
        function onCatalogReceivedChanged() { if (programs.catalogReceived) test.mark("catalog-received"); }
        function onInstalledReceivedChanged() { if (programs.installedReceived) test.mark("inventory-received"); }
        function onAurKnownChanged() { if (programs.aurKnown) test.mark("aur-received"); }
        function onAllRowsChanged() { if (programs.allRows.length) test.mark("rows-published", String(programs.allRows.length)); }
    }
    FloatingWindow {
        id: window
        implicitWidth: 1180
        implicitHeight: 780
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs }
    }
    Timer {
        interval: 10; repeat: true; running: true
        onTriggered: {
            if (Date.now() - test.started > Number(Quickshell.env("TEST_TIMEOUT"))) {
                console.error("ASSERTION_FAILED startup timed out");
                Qt.quit();
                return;
            }
            if (!test.captured && !test.grabbing) {
                const first = test.card(view);
                if (first) {
                    test.mark("card-laid-out", first.program.key);
                    test.grabbing = true;
                    // The callback follows a software-rendered frame; a populated list or
                    // a merely instantiated delegate is insufficient for this measurement.
                    if (!view.grabToImage(function(result) {
                        if (!result.saveToFile(Quickshell.env("TEST_FRAME"))) {
                            console.error("ASSERTION_FAILED could not save startup frame");
                            Qt.quit();
                            return;
                        }
                        test.mark("first-content-frame", first.program.key);
                        test.captured = true;
                    })) {
                        console.error("ASSERTION_FAILED startup frame capture refused");
                        Qt.quit();
                    }
                }
            }
            if (test.captured && programs.catalogReceived && programs.installedReceived && programs.aurKnown && !programs.busy) {
                test.mark("startup-settled");
                console.log("STARTUP_OK");
                Qt.quit();
            }
        }
    }
}
