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
            function check(ok, message) {
                if (!ok)
                    console.log("SHELL_KEYBOARD_FAIL " + message + " focus=" + window.activeFocusItem + " bar=" + scene.barKeyboardActive + " modal=" + scene.modalOpen);
                verify(ok, message);
            }
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
                if (surface === "launcher") {
                    check(scene.launcherOpen, "first Escape clears the launcher search");
                    keyClick(Qt.Key_Escape);
                }
                wait(40);
                check(!window.captured, surface + " releases keyboard ownership on Escape");
                check(underlying.activeFocus, surface + " returns focus in the offscreen ownership simulation");
                check(!inside(window.activeFocusItem, scene), surface + " has no remaining item focus");
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
                check(ring !== null && ring.visible, "keyboard ring is visible");
                const image = grabImage(ring);
                check(image.width > 4 && image.height > 4, "ring has rendered geometry");
                check(image.alpha(Math.floor(image.width / 2), 0) > 0, "ring has rendered edge ink");
            }
            function shot(name) {
                if (Quickshell.env("KEYBOARD_SKIP_SHOTS") === "1")
                    return;
                let done = false;
                check(scene.grabToImage(result => {
                    check(result.saveToFile(Quickshell.env("KEYBOARD_SHOTS") + "/" + name + ".png"));
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
                    casts: {
                        fixture: {
                            target: "output",
                            output: "Fixture",
                            is_active: true,
                            by_parent: false
                        }
                    },
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
                    check(scene.openKeyboard(surface), surface + " opens");
                    wait(200);
                    check(window.captured, surface + " owns keyboard");
                    check(inside(window.activeFocusItem, scene), surface + " focuses a real shell control");
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
                        check(icon !== null && !!icon.paths.close, "privacy close icon has geometry");
                        check(decodeURIComponent(icon.source.toString()).includes(icon.paths.close), "privacy close SVG contains its geometry");
                    }
                    check(inside(window.activeFocusItem, scene), surface + " Tab remains inside shell");
                    keyClick(Qt.Key_Backtab);
                    if (["bar", "sound", "clock", "privacy", "shortcuts"].includes(surface))
                        shot(surface);
                    close(surface);
                }
                scene.openLauncher();
                wait(200);
                check(scene.launcherOpen);
                keyClick(Qt.Key_Tab);
                keyClick(Qt.Key_Backtab);
                close("launcher");
                UpdateService.pending = 2;
                for (const keyboard of [false, true]) {
                    scene.openSystem("sound", keyboard);
                    wait(200);
                    const update = Keyboard.targets(scene.systemPanel).find(item => item.key === "cell-updates");
                    check(update !== undefined, "update indicator participates in panel traversal");
                    update.forceActiveFocus(Qt.TabFocusReason);
                    keyClick(Qt.Key_Return);
                    tryVerify(() => !window.captured, 1000, "update window entry releases the shell layer");
                    check(underlying.activeFocus, "application can receive focus after update entry");
                }
                UpdateService.pending = 0;
                check(scene.openKeyboard("dock"));
                wait(200);
                check(scene.dock.keyboardActive && scene.dock.activeFocus);
                check(scene.dockPolicy.keyboardActive, "keyboard reveals auto-hidden dock");
                check(scene.dock.iconKeys.length > 0, "dock fixture has an actionable item");
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Left);
                ringInk(scene.dock);
                shot("dock");
                keyClick(Qt.Key_Menu);
                check(scene.dock.popupOpen, "Menu opens focused dock item actions");
                keyClick(Qt.Key_Down);
                keyClick(Qt.Key_Escape);
                check(!scene.dock.popupOpen && scene.dock.keyboardActive, "Escape returns from item menu to dock");
                close("dock");
                check(!scene.dockPolicy.keyboardActive);
                // Returning from an island's panel is one keyboard level, not
                // a release back to the application behind the shell.
                for (const label of ["Calendar and notifications", "Open launcher", "cell-sound", "Privacy"]) {
                    check(scene.openKeyboard("bar"));
                    wait(200);
                    const origin = Keyboard.targets(scene.bar).find(item => item.label === label || item.key === label);
                    check(origin !== undefined);
                    origin.forceActiveFocus(Qt.TabFocusReason);
                    keyClick(Qt.Key_Return);
                    wait(200);
                    check(scene.modalOpen);
                    keyClick(Qt.Key_Escape);
                    wait(80);
                    check(scene.barKeyboardActive, "panel Escape returns to the bar");
                    for (let step = 0; step < 30 && !origin.activeFocus; ++step)
                        wait(50);
                    check(origin.activeFocus, "panel Escape restores " + label + " visible=" + origin.visible + " pending=" + scene.bar.returnItem + " actual=" + window.activeFocusItem?.label);
                    keyClick(Qt.Key_Escape);
                    wait(40);
                    check(!window.captured);
                }
                check(scene.openKeyboard("bar"));
                tryVerify(() => scene.barKeyboardActive);
                const launcherOrigin = Keyboard.targets(scene.bar).find(item => item.label === "Open launcher");
                launcherOrigin.forceActiveFocus(Qt.TabFocusReason);
                keyClick(Qt.Key_Return);
                tryVerify(() => scene.launcherOpen, 1000, "Bar opens launcher");
                scene.input.takeFocus(true);
                check(scene.input.inputFocused, "Bar launcher starts with search focus");
                scene.input.setMode("All");
                scene.input.takeFocus(true);
                scene.input.setQuery("2+2");
                tryVerify(() => scene.input.results.some(row => row.kind === "calculator"), 1000, "Calculator result appears");
                scene.input.selectAt(scene.input.results.findIndex(row => row.kind === "calculator"));
                keyClick(Qt.Key_Return);
                tryVerify(() => !scene.launcherOpen, 1000);
                check(!window.captured, "Result activation releases a bar-opened launcher");
                check(underlying.activeFocus, "Result activation permits application keyboard focus");
                check(scene.openKeyboard("bar"));
                tryVerify(() => scene.bar.keyboardItem !== null);
                tryVerify(() => !scene.launcherPresent && !scene.privacyPresent, 2000, "Panels finish closing before geometry checks");
                const privacyTarget = Keyboard.targets(scene.bar).find(item => item.label === "Privacy");
                const pill = scene.bar.privacyItem;
                function privacyAligned() {
                    const actual = privacyTarget.mapToItem(scene, 0, 0);
                    const expected = pill.mapToItem(scene, 0, 0);
                    return Math.abs(actual.x - expected.x) < 0.1 && Math.abs(actual.y - expected.y) < 0.1 && privacyTarget.width === pill.width && privacyTarget.height === pill.height;
                }
                check(privacyTarget !== undefined && pill !== null, "Privacy pill and keyboard target exist");
                if (!privacyAligned())
                    console.log("PRIVACY_GEOMETRY", privacyTarget.mapToItem(scene, 0, 0), pill.mapToItem(scene, 0, 0), privacyTarget.width, pill.width, privacyTarget.height, pill.height);
                check(privacyAligned(), "Privacy target follows the pill before resize");
                privacyTarget.forceActiveFocus(Qt.TabFocusReason);
                // The pill's keyboard focus is its own hover drop (pad 3, the pill's radius + 3),
                // not a square ring under the round pill.
                const privacyRing = scene.bar.children.find(item => item.targetRect !== undefined);
                check(privacyRing !== undefined && privacyRing.shown === false && !privacyRing.visible, "The bar's square ring stays off the round privacy pill");
                function ownRing(item) {
                    return item.children.some(child => child.keyboardMode !== undefined && child.visible);
                }
                check(!ownRing(privacyTarget), "The privacy cell draws no square ring of its own");
                function hoverDrop() {
                    const drop = pill.drops[0];
                    if (pill.glassStatus().alpha <= .95 || drop === undefined)
                        return false;
                    const placed = Math.abs(drop.rect.x + 3) < .5 && Math.abs(drop.rect.y + 3) < .5;
                    const sized = Math.abs(drop.rect.z - pill.width - 6) < .5 && Math.abs(drop.rect.w - pill.height - 6) < .5;
                    return placed && sized && Math.abs(drop.params.x - Math.min(pill.radius + 3, (pill.height + 6) / 2)) < .5;
                }
                tryVerify(hoverDrop, 2000, "Keyboard focus raises the pill's hover drop");
                shot("privacy-focus");
                const originalPillX = pill.x;
                window.width = 1100;
                tryVerify(() => pill.x !== originalPillX);
                tryVerify(privacyAligned, 1000, "Privacy target follows the pill after resize");
                check(privacyTarget.activeFocus && hoverDrop(), "The pill's drop stays on the pill after resize");
                keyClick(Qt.Key_Left);
                check(!privacyTarget.activeFocus, "Left leaves the pill");
                tryVerify(() => pill.glassStatus().alpha < .02, 2000, "The drop leaves with the keyboard focus");
                window.width = 1440;
                scene.closeAll();
                compare(missingFocus.join(","), "", "Every opened surface initially focuses an actionable child");
            }
        }
    }
}
