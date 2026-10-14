// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// The system panel opens only over its live glass source: hidden while the capture warms up,
// shown once it is live or after the cap, never after it was closed meanwhile.
ShellRoot {
    id: root
    property int phase: 0
    property double openedAt: 0
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        visible: true
        width: 1280
        height: 800
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: niri
            borderMode: "soft"
            reservedSpace: 52
            headless: true
            testWidth: 1280
            testHeight: 800
            skipIntro: true
        }
    }
    function check(ok: bool, message: string): void {
        if (!ok)
            throw new Error(message);
    }
    function hidden(): bool {
        return scene.systemOpen && scene.systemPresent && scene.systemWarming && !scene.systemPanel.visible;
    }
    function shown(): bool {
        return scene.systemOpen && !scene.systemWarming && scene.systemPanel.visible;
    }
    function open(page: string): void {
        scene.systemGlassLive = false;
        check(scene.openSystem(page), "openSystem refused " + page);
        openedAt = Date.now();
    }
    Timer {
        interval: 30
        repeat: true
        running: true
        onTriggered: {
            try {
                switch (root.phase) {
                case 0:
                    root.check(scene.openSystem("power") && root.shown(), "a live source did not open the panel at once");
                    scene.closeAll();
                    break;
                case 1:
                    root.open("power");
                    root.check(root.hidden(), "the panel showed before its glass source was live");
                    break;
                case 2:
                    root.check(root.hidden(), "the panel showed before its glass source was live (next frame)");
                    scene.systemGlassLive = true;
                    root.check(root.shown(), "the panel stayed hidden after its glass source went live");
                    scene.closeAll();
                    break;
                case 3:
                    root.open("sound");
                    break;
                case 4:
                    if (Date.now() - root.openedAt < scene.systemWarmupMs + 100)
                        return;
                    root.check(root.shown(), "the panel never opened without a live glass source");
                    scene.closeAll();
                    break;
                case 5:
                    root.open("light");
                    scene.closeSystem();
                    root.check(!scene.systemWarming && !scene.systemOpen, "closing during warm-up left it pending");
                    break;
                case 6:
                    scene.systemGlassLive = true;
                    root.check(!scene.systemOpen && !scene.systemWarming, "a closed panel opened when its source went live");
                    break;
                case 7:
                    if (Date.now() - root.openedAt < scene.systemWarmupMs + 600)
                        return;
                    root.check(!scene.systemOpen && scene.systemExpansion === 0, "a closed panel opened after the cap");
                    console.log("SYSTEM WARMUP PASS");
                    Qt.quit();
                    return;
                }
                root.phase++;
            } catch (error) {
                console.error("SYSTEM WARMUP FAIL: " + error);
                Qt.quit();
            }
        }
    }
}
