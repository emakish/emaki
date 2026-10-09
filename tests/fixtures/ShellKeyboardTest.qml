// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "Keyboard.js" as Keyboard

ShellRoot {
    NiriService {
        id: service
        binary: ""
    }
    Window {
        id: window
        width: 1440
        height: 1000
        visible: true
        readonly property bool captured: scene.modalOpen || scene.barKeyboardActive || scene.dock.keyboardActive
        // A single offscreen window cannot implement compositor layer focus. Mirror
        // release only after production's keyboard ownership flags have all cleared.
        onCapturedChanged: if (!captured)
            underlying.forceActiveFocus()
        TextInput {
            id: underlying
            width: 300
            height: 30
            text: ""
        }
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: window.width
            testHeight: window.height
            borderMode: "soft"
            reservedSpace: 52
            skipIntro: true
        }
        TestCase {
            id: test
            name: "ShellKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SHELL_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function inside(item, root) {
                for (let parent = item; parent; parent = parent.parent)
                    if (parent === root)
                        return true;
                return false;
            }
            function close(surface) {
                const before = underlying.text;
                keyClick(Qt.Key_X);
                compare(underlying.text, before, surface + " never types into underlying editor");
                keyClick(Qt.Key_Escape);
                wait(40);
                verify(!window.captured, surface + " releases keyboard ownership on Escape");
                verify(underlying.activeFocus, surface + " returns focus in the offscreen ownership simulation");
                verify(!inside(window.activeFocusItem, scene), surface + " has no remaining item focus");
                keyClick(Qt.Key_Z);
                compare(underlying.text, before + "z", surface + " permits underlying typing after close");
            }
            function ringInk(root) {
                function find(item) {
                    if (item.shown !== undefined && item.border !== undefined && item.keyboardMode !== undefined)
                        return item;
                    for (const child of item.children || []) {
                        const found = find(child);
                        if (found)
                            return found;
                    }
                    return null;
                }
                const ring = find(root);
                verify(ring !== null && ring.visible, "keyboard ring is visible");
                const image = grabImage(ring);
                verify(image.width > 4 && image.height > 4, "ring has rendered geometry");
                verify(image.alpha(Math.floor(image.width / 2), 0) > 0, "ring has rendered edge ink");
            }
            function shot(name) {
                if (Quickshell.env("KEYBOARD_SKIP_SHOTS") === "1")
                    return;
                let done = false;
                verify(scene.grabToImage(result => {
                    verify(result.saveToFile(Quickshell.env("KEYBOARD_SHOTS") + "/" + name + ".png"));
                    done = true;
                }));
                tryVerify(() => done, 3000);
            }
            function test_surfaces() {
                let missingFocus = [];
                const workspaces = {};
                for (let i = 1; i <= 11; ++i)
                    workspaces[i] = {
                        id: i,
                        idx: i,
                        output: "Fixture",
                        is_active: i === 1
                    };
                service.connection = "connected";
                service.model = {
                    outputs: {
                        Fixture: {
                            logical: {
                                width: window.width,
                                height: window.height
                            }
                        }
                    },
                    focused_output: "Fixture",
                    workspaces: workspaces,
                    windows: {},
                    keyboard_layouts: {
                        names: ["English (US)"],
                        current_idx: 0
                    },
                    overview_open: false
                };
                window.requestActivate();
                underlying.forceActiveFocus();
                tryVerify(() => service.layerFocusSupported);
                wait(150);
                for (const surface of ["bar", "clock", "notifications", "privacy", "sound", "light", "power", "wifi", "bt", "kb", "tray", "shortcuts"]) {
                    verify(scene.openKeyboard(surface), surface + " opens");
                    wait(200);
                    verify(window.captured, surface + " owns keyboard");
                    verify(inside(window.activeFocusItem, scene), surface + " focuses a real shell control");
                    if (surface === "bar") {
                        compare(window.activeFocusItem.label, "Open launcher", "bar begins with the logo");
                        ringInk(window.activeFocusItem);
                    }
                    if (!window.activeFocusItem.activeFocusOnTab)
                        missingFocus.push(surface);
                    keyClick(Qt.Key_Tab);
                    if (surface === "bar") {
                        compare(scene.bar.workspaceStrip.entries.length, 11, "bar fixture has every workspace");
                        compare(window.activeFocusItem.label, "Workspace 1", "first Tab follows logo with workspace one");
                    }
                    if (surface === "privacy") {
                        function closeIcon(item) {
                            if (item.kind === "close")
                                return item;
                            for (const child of item.children || []) {
                                const found = closeIcon(child);
                                if (found)
                                    return found;
                            }
                            return null;
                        }
                        const icon = closeIcon(scene.privacyPopup);
                        verify(icon !== null && !!icon.paths.close, "privacy close icon has geometry");
                        verify(decodeURIComponent(icon.source.toString()).includes(icon.paths.close), "privacy close SVG contains its geometry");
                    }
                    verify(inside(window.activeFocusItem, scene), surface + " Tab remains inside shell");
                    keyClick(Qt.Key_Backtab);
                    if (["bar", "sound", "clock", "privacy", "shortcuts"].includes(surface))
                        shot(surface);
                    close(surface);
                }
                scene.openLauncher();
                wait(200);
                verify(scene.launcherOpen);
                keyClick(Qt.Key_Tab);
                keyClick(Qt.Key_Backtab);
                close("launcher");
                UpdateService.pending = 2;
                for (const keyboard of [false, true]) {
                    scene.openSystem("sound", keyboard);
                    wait(200);
                    const update = Keyboard.targets(scene.systemPanel).find(item => item.key === "cell-updates");
                    verify(update !== undefined, "update indicator participates in panel traversal");
                    update.forceActiveFocus(Qt.TabFocusReason);
                    keyClick(Qt.Key_Return);
                    wait(40);
                    verify(!window.captured, "update window entry releases the shell layer");
                    verify(underlying.activeFocus, "application can receive focus after update entry");
                }
                UpdateService.pending = 0;
                verify(scene.openKeyboard("dock"));
                wait(200);
                verify(scene.dock.keyboardActive && scene.dock.activeFocus);
                verify(scene.dockPolicy.keyboardActive, "keyboard reveals auto-hidden dock");
                verify(scene.dock.iconKeys.length > 0, "dock fixture has an actionable item");
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Left);
                ringInk(scene.dock);
                shot("dock");
                keyClick(Qt.Key_Menu);
                verify(scene.dock.popupOpen, "Menu opens focused dock item actions");
                keyClick(Qt.Key_Down);
                keyClick(Qt.Key_Escape);
                verify(!scene.dock.popupOpen && scene.dock.keyboardActive, "Escape returns from item menu to dock");
                close("dock");
                verify(!scene.dockPolicy.keyboardActive);
                compare(missingFocus.join(","), "", "Every opened surface initially focuses an actionable child");
            }
        }
    }
}
