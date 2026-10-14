// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

// Pointer pass on the real scene: hovering and clicking the bar's islands, the panels they open
// and the dock never draws a keyboard focus ring, and Escape closes a pointer-opened panel
// without handing the keyboard to the bar. Copied into a temporary shell/ as test.qml by
// tests/test-keyboard-gaps.py. Offscreen focus is a policy simulation, not compositor proof.
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
        onCapturedChanged: if (!captured)
            underlying.forceActiveFocus()
        TextInput {
            id: underlying
            width: 300
            height: 30
        }
        // What Surfaces.qml's overlay does with a press outside every control (DockTest.qml).
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onPressed: mouse => scene.outsidePressButton(mouse.x, mouse.y, mouse.button)
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
            name: "KeyboardGapsScene"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("KEYBOARD_GAPS_SCENE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function check(ok, message) {
                if (!ok)
                    console.log("KEYBOARD_GAPS_FAIL " + message + " focus=" + window.activeFocusItem + " bar=" + scene.barKeyboardActive + " modal=" + scene.modalOpen + " rings=" + visibleRings().map(describe).join("|"));
                verify(ok, message);
            }
            function inside(item, root) {
                for (let parent = item; parent; parent = parent.parent)
                    if (parent === root)
                        return true;
                return false;
            }
            function describe(ring) {
                const owner = ring.parent;
                return String(owner) + (owner && owner.key ? ":" + owner.key : "") + (owner && owner.label ? ":" + owner.label : "");
            }
            // Every FocusRing in the scene that would be drawn now.
            function visibleRings() {
                const found = [];
                function shownOnScreen(item) {
                    for (let parent = item; parent; parent = parent.parent)
                        if (parent.opacity <= .01)
                            return false;
                    return item.visible && item.width > 0 && item.height > 0;
                }
                function visit(item) {
                    if (item.shown !== undefined && item.strong !== undefined && item.keyboardMode !== undefined && item.boundary !== undefined && shownOnScreen(item))
                        found.push(item);
                    for (const child of item.children || [])
                        visit(child);
                }
                visit(scene);
                return found;
            }
            function noRings(message) {
                const rings = visibleRings();
                check(rings.length === 0, message + ": no keyboard focus ring after pointer use");
            }
            function noKeyboard(message) {
                check(!scene.barKeyboardActive, message + ": bar does not take the keyboard");
                check(!scene.dock.keyboardActive, message + ": dock does not take the keyboard");
                check(scene.keyboardSurface === "", message + ": no keyboard surface");
            }
            function find(item, key) {
                if (item.key === key && item.activeFocusOnTab)
                    return item;
                for (const child of item.children || []) {
                    const found = find(child, key);
                    if (found)
                        return found;
                }
                return null;
            }
            function firstTarget(root) {
                if (root !== null && root.key !== undefined && root.activeFocusOnTab && root.visible && root.width > 0)
                    return root;
                for (const child of root.children || []) {
                    const found = firstTarget(child);
                    if (found)
                        return found;
                }
                return null;
            }
            function center(item) {
                mouseMove(item, item.width / 2, item.height / 2);
            }
            function escapeCloses(open, message) {
                for (let i = 0; i < 2 && open(); ++i) {
                    keyClick(Qt.Key_Escape);
                    wait(60);
                }
                check(!open(), message + ": Escape closes");
                wait(100);
                noKeyboard(message + " after Escape");
                check(!window.captured, message + ": keyboard released after Escape");
                check(underlying.activeFocus, message + ": application keeps typing after Escape");
                noRings(message + " after Escape");
            }
            function test_pointer_pass() {
                const workspaces = {};
                for (let i = 1; i <= 3; ++i)
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
                tryVerify(() => scene.dock.iconKeys.length > 0, 3000, "dock has an item");
                wait(150);
                const cell = scene.bar.systemGlass.row.cell("sound");
                const dockKey = scene.dock.iconKeys[0];
                const dockRect = scene.dockScreenRect(dockKey);
                check(cell !== null && dockRect !== null, "sound cell and dock item exist");

                // Hover alone: nothing takes the keyboard, nothing shows a ring.
                for (const entry of [[scene.bar.logo, "logo"], [scene.bar.clock, "clock island"], [cell, "sound cell"], [scene.privacy, "privacy pill"]]) {
                    center(entry[0]);
                    wait(60);
                    noRings("hover " + entry[1]);
                    noKeyboard("hover " + entry[1]);
                }
                mouseMove(scene, dockRect.x + dockRect.width / 2, dockRect.y + dockRect.height / 2);
                wait(60);
                noRings("hover dock item");
                noKeyboard("hover dock item");

                // Clock island click: the drawer opens with the keyboard on its glass, no ring.
                mouseClick(scene.bar.clock, scene.bar.clock.width / 2, scene.bar.clock.height / 2);
                tryVerify(() => scene.drawerOpen, 1000, "clock click opens the drawer");
                wait(400);
                check(inside(window.activeFocusItem, scene), "pointer-opened drawer has the keys (Escape reaches it)");
                noRings("clock drawer opened by pointer");
                noKeyboard("clock drawer opened by pointer");
                const clockTarget = firstTarget(scene.clockPanel);
                check(clockTarget !== null, "drawer has a pressable target");
                center(clockTarget);
                wait(60);
                noRings("hover in the clock drawer");
                escapeCloses(() => scene.drawerOpen, "clock drawer");

                // System cell click, then a tab click inside the panel.
                mouseClick(cell, cell.width / 2, cell.height / 2);
                tryVerify(() => scene.systemOpen && scene.systemPage === "sound", 1000, "sound cell click opens the panel");
                wait(400);
                check(inside(window.activeFocusItem, scene), "pointer-opened system panel has the keys");
                check(!scene.systemPanel.keyboardMode, "pointer-opened system panel is not in keyboard mode");
                noRings("system panel opened by pointer");
                noKeyboard("system panel opened by pointer");
                const lightTab = find(scene.systemPanel, "cell-light");
                check(lightTab !== null, "panel has the light tab");
                mouseClick(lightTab, lightTab.width / 2, lightTab.height / 2);
                tryVerify(() => scene.systemPage === "light", 1000, "tab click switches the page");
                wait(300);
                check(!scene.systemPanel.keyboardMode, "tab click keeps pointer mode");
                noRings("system tab clicked");
                noKeyboard("system tab clicked");
                escapeCloses(() => scene.systemOpen, "system panel");

                // Privacy pill click.
                mouseClick(scene.privacy, scene.privacy.width / 2, scene.privacy.height / 2);
                tryVerify(() => scene.privacyOpen, 1000, "pill click opens privacy");
                wait(400);
                check(inside(window.activeFocusItem, scene), "pointer-opened privacy panel has the keys");
                noRings("privacy opened by pointer");
                noKeyboard("privacy opened by pointer");
                escapeCloses(() => scene.privacyOpen, "privacy panel");

                // Logo click: the launcher.
                mouseClick(scene.bar.logo, scene.bar.logo.width / 2, scene.bar.logo.height / 2);
                tryVerify(() => scene.launcherOpen, 1000, "logo click opens the launcher");
                wait(400);
                check(inside(window.activeFocusItem, scene), "pointer-opened launcher has the keys");
                noRings("launcher opened by pointer");
                noKeyboard("launcher opened by pointer");
                escapeCloses(() => scene.launcherOpen, "launcher");

                // Dock: right click opens the item menu without keyboard ownership or a ring.
                mouseClick(scene, dockRect.x + dockRect.width / 2, dockRect.y + dockRect.height / 2, Qt.RightButton);
                tryVerify(() => scene.dock.popupOpen, 1000, "right click opens the dock menu");
                wait(300);
                noRings("dock menu opened by pointer");
                noKeyboard("dock menu opened by pointer");
                keyClick(Qt.Key_Escape);
                wait(100);
                console.log("KEYBOARD_GAPS_DOCK_MENU_AFTER_ESCAPE open=" + scene.dock.popupOpen + " focus=" + window.activeFocusItem);
                noRings("dock menu after Escape");
                noKeyboard("dock menu after Escape");
                mouseClick(scene, 700, 400);
                tryVerify(() => !scene.dock.popupOpen, 1000, "outside click closes the dock menu");
                noRings("dock menu closed by pointer");
                noKeyboard("dock menu closed by pointer");

                // Control: the same detector sees the ring once the keyboard opens a panel.
                check(scene.openKeyboard("sound"), "keyboard opens sound");
                wait(400);
                check(visibleRings().length > 0, "ring detector sees the keyboard ring");
                keyClick(Qt.Key_Escape);
                wait(100);
                check(!scene.systemOpen, "keyboard panel closes");
                check(scene.openKeyboard("bar"), "keyboard opens the bar");
                tryVerify(() => scene.bar.keyboardItem !== null, 1000);
                check(visibleRings().length > 0, "ring detector sees the bar ring");
                keyClick(Qt.Key_Escape);
                wait(100);
                check(!window.captured, "bar releases the keyboard");
            }
        }
    }
}
