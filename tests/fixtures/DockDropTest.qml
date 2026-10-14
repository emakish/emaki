// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import DockDropProbe 1.0

ShellRoot {
    id: root
    property int stage: 0
    property double since: Date.now()
    property point target
    property var urls: ["file:///tmp/a%20b.txt", "https://example.invalid/$(touch%20BAD)?a=1&b=2"]
    function check(ok: bool, message: string): void {
        if (!ok)
            throw new Error(message);
    }
    DockDropProbe {
        id: probe
    }
    NiriService {
        id: service
        binary: ""
    }
    Window {
        visible: true
        width: 1536
        height: 960
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: 1536
            testHeight: 960
            skipIntro: true
        }
    }
    Timer {
        interval: 25
        repeat: true
        running: true
        onTriggered: {
            const dock = scene.dock;
            if (root.stage === 0 && Date.now() - root.since > 200) {
                scene.wallpaper.enabled = false;
                scene.dockStore.pinned = ["fixture-editor"];
                root.check(!scene.dockPolicy.dockVisible, "starts hidden");
                probe.enter(scene, 768, 959, root.urls);
                root.since = Date.now();
                root.stage = 1;
            } else if (root.stage === 1 && scene.dockPolicy.dockVisible && dock.visibleAmount > .99) {
                root.check(scene.dockPolicy.edgeDragging, "external edge drag reveals");
                const rect = dock.itemRect("fixture-editor");
                root.target = dock.mapToItem(scene, rect.x + rect.width / 2, rect.y + rect.height / 2);
                probe.move(scene, root.target.x, root.target.y);
                root.since = Date.now();
                root.stage = 2;
            } else if (root.stage === 2 && Date.now() - root.since > 450) {
                root.check(dock.externalDragging && scene.dockPolicy.dockVisible, "URI drag holds visible after dwell");
                root.check(probe.drop(scene, root.target.x, root.target.y), "app accepts URI drop");
                root.since = Date.now();
                root.stage = 3;
            } else if (root.stage === 3 && Date.now() - root.since > 900) {
                root.check(!dock.externalDragging, "drop releases hold");
                root.check(!scene.dockPolicy.dockVisible, "drop permits autohide");
                probe.enter(scene, 768, 959, []);
                root.since = Date.now();
                root.stage = 4;
            } else if (root.stage === 4 && Date.now() - root.since > 400) {
                root.check(!scene.dockPolicy.dockVisible && !scene.dockPolicy.edgeDragging, "plain text does not reveal");
                probe.leave(scene);
                probe.enter(scene, 768, 959, root.urls);
                root.stage = 5;
            } else if (root.stage === 5 && scene.dockPolicy.dockVisible && dock.visibleAmount > .99) {
                probe.move(scene, root.target.x, root.target.y);
                root.check(dock.externalDragging, "second drag reaches plate");
                probe.leave(scene);
                root.since = Date.now();
                root.stage = 6;
            } else if (root.stage === 6 && Date.now() - root.since > 900) {
                root.check(!dock.externalDragging && !scene.dockPolicy.dockVisible, "cancel releases hold and hides");
                console.log("DOCK_DROP_OK");
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 8000
        running: true
        onTriggered: {
            throw new Error("drag test timeout at " + root.stage);
        }
    }
}
