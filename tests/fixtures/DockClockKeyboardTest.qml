// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: root
    property int mediaPrevious: 0
    property int mediaToggle: 0
    property int mediaNext: 0
    property int defaultActions: 0
    property int secondaryActions: 0
    Component {
        id: fixtureMedia
        MediaBlock {
            players: [
                {
                    dbusName: "fixture.first",
                    identity: "First player",
                    trackTitle: "First track",
                    isPlaying: false,
                    canTogglePlaying: true,
                    canGoPrevious: true,
                    canGoNext: true,
                    previous: () => ++root.mediaPrevious,
                    togglePlaying: () => ++root.mediaToggle,
                    next: () => ++root.mediaNext
                },
                {
                    dbusName: "fixture.second",
                    identity: "Second player",
                    trackTitle: "Second track",
                    isPlaying: true,
                    canTogglePlaying: true,
                    canGoPrevious: true,
                    canGoNext: true,
                    previous: () => ++root.mediaPrevious,
                    togglePlaying: () => ++root.mediaToggle,
                    next: () => ++root.mediaNext
                }
            ]
        }
    }
    function tabTo(key: string): void {
        let count = 0;
        while (keyOf(window.activeFocusItem) !== key && count++ < 160)
            keys.keyClick(Qt.Key_Tab);
        check(keyOf(window.activeFocusItem) === key, "Tab reaches " + key);
    }
    function keyOf(item: var): string {
        return item?.key ?? "";
    }
    function ringOf(item: Item): Item {
        for (const child of item.children) {
            if (child.shown !== undefined && child.border !== undefined && child.keyboardMode !== undefined)
                return child;
        }
        return null;
    }
    function check(ok: bool, label: string): void {
        if (!ok)
            throw new Error(label);
    }
    NiriService {
        id: service
        binary: ""
    }
    Window {
        id: window
        width: 1536
        height: 960
        visible: true
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: 1536
            testHeight: 960
        }
        TestCase {
            id: keys
            when: false
        }
    }
    Timer {
        interval: 300
        running: true
        onTriggered: {
            try {
                scene.dockStore.pinned = ["fixture-one", "fixture-two"];
                scene.dockStore.autoHide = true;
                scene.dock.takeFocus();
                keys.wait(350);
                root.check(scene.dock.keyboardActive && scene.dock.policy.dockVisible, "hidden dock reveals on focus");
                root.check(scene.dock.keyboardKey === "fixture-one", "first dock item");
                keys.keyClick(Qt.Key_Right);
                root.check(scene.dock.keyboardKey === "fixture-two", "dock right arrow");
                keys.keyClick(Qt.Key_Tab);
                root.check(scene.dock.keyboardKey === "fixture-one", "dock wraps");
                keys.keyClick(Qt.Key_F10, Qt.ShiftModifier);
                root.check(scene.dock.popupOpen && scene.dock.keyboardRow >= 0, "dock item menu");
                keys.keyClick(Qt.Key_Return);
                root.check(!scene.dockStore.pinned.includes("fixture-one"), "keyboard unpin");
                keys.keyClick(Qt.Key_Escape);
                root.check(!scene.dock.keyboardActive, "dock escape releases focus");
                for (let i = 0; i < 12; ++i)
                    scene.notifications.local("Keyboard", "Notice " + i, "Body");
                scene.openDrawer();
                keys.wait(400);
                root.check(!scene.clockBody.keyboardMode, "pointer-opened clock starts without keyboard mode");
                keys.keyClick(Qt.Key_Tab);
                const firstClockTarget = window.activeFocusItem;
                const firstClockRing = root.ringOf(firstClockTarget);
                root.check(scene.clockBody.keyboardMode, "first Tab in pointer-opened clock enables keyboard mode");
                root.check(firstClockRing !== null && firstClockRing.visible, "first Tab in pointer-opened clock shows ring");
                keys.mouseClick(firstClockTarget, firstClockTarget.width / 2, firstClockTarget.height / 2);
                root.check(!scene.clockBody.keyboardMode && !firstClockRing.visible, "clock pointer press hides ring");
                keys.keyClick(Qt.Key_Tab);
                const resumedClockRing = root.ringOf(window.activeFocusItem);
                root.check(resumedClockRing !== null && resumedClockRing.visible, "first Tab after clock pointer press restores ring");
                scene.clockPanel.focusNewest();
                keys.wait(100);
                root.check(scene.clockBody.keyboardMode, "keyboard history enabled");
                root.check(scene.clockBody.plan.items.filter(r => r.kind === "note").length === 12, "all notifications reachable");
                root.check(root.keyOf(window.activeFocusItem).startsWith("note-"), "newest notification focused");
                for (let i = 0; i < 22; ++i)
                    keys.keyClick(Qt.Key_Tab);
                root.check(scene.clockBody.scroller.contentY > 0, "history scroll follows keyboard focus");
                scene.clockPanel.focusNewest();
                keys.wait(100);
                keys.keyClick(Qt.Key_Tab);
                root.check(root.keyOf(window.activeFocusItem).endsWith(":x"), "dismiss reachable after note");
                keys.keyClick(Qt.Key_Delete);
                keys.wait(100);
                root.check(scene.notifications.count === 11, "delete dismisses focused note");
                keys.keyClick(Qt.Key_Delete, Qt.ControlModifier);
                keys.wait(100);
                root.check(scene.notifications.count === 0, "control delete clears history");
                keys.keyClick(Qt.Key_Escape);
                root.check(!scene.drawerOpen, "drawer escape closes");
                scene.clockBody.mediaSource = fixtureMedia;
                scene.clockBody.mediaEnabled = true;
                scene.openDrawer(true);
                keys.wait(400);
                root.check(scene.clockBody.player.playerCount === 2, "fake media source loaded");
                root.tabTo("media-previous");
                keys.keyClick(Qt.Key_Return);
                root.tabTo("media-toggle");
                keys.keyClick(Qt.Key_Space);
                root.tabTo("media-next");
                keys.keyClick(Qt.Key_Return);
                root.check(root.mediaPrevious === 1 && root.mediaToggle === 1 && root.mediaNext === 1, "media transport callbacks");
                root.tabTo("player-fixture.second");
                keys.keyClick(Qt.Key_Space);
                root.check(scene.clockBody.player.chosen === "fixture.second", "second player selected");
                root.tabTo("player-fixture.first");
                keys.keyClick(Qt.Key_Return);
                root.check(scene.clockBody.player.chosen === "fixture.first", "first player selected");
                const calendar = scene.clockBody.calendar;
                const month = calendar.month;
                root.tabTo("month-next");
                keys.keyClick(Qt.Key_Return);
                root.check(calendar.month === (month + 1) % 12, "next month activation");
                root.tabTo("month-previous");
                keys.keyClick(Qt.Key_Space);
                root.check(calendar.month === month, "previous month activation");
                root.tabTo("month-next");
                keys.keyClick(Qt.Key_Space);
                root.tabTo("month-today");
                keys.keyClick(Qt.Key_Return);
                root.check(calendar.current, "Today restores current month");
                root.tabTo("day-" + calendar.year + "-" + calendar.month + "-" + calendar.start);
                keys.keyClick(Qt.Key_Space);
                root.check(calendar.picked === 1, "day activation");
                scene.notifications.entries = [
                    {
                        id: 77,
                        app: "Actions",
                        summary: "Action notice",
                        body: "Body",
                        time: Date.now(),
                        actions: [
                            {
                                id: "default",
                                text: "Open"
                            },
                            {
                                id: "secondary",
                                text: "Mark read"
                            }
                        ],
                        object: {
                            actions: [
                                {
                                    identifier: "default",
                                    invoke: () => ++root.defaultActions
                                },
                                {
                                    identifier: "secondary",
                                    invoke: () => ++root.secondaryActions
                                }
                            ]
                        },
                        deadline: 0
                    }
                ];
                keys.wait(100);
                root.tabTo("note-77");
                keys.keyClick(Qt.Key_Return);
                root.check(root.defaultActions === 1 && !scene.drawerOpen, "default notification callback and close");
                scene.openDrawer(true);
                keys.wait(400);
                root.tabTo("note-77:a0");
                keys.keyClick(Qt.Key_Space);
                root.check(root.secondaryActions === 1 && !scene.drawerOpen, "secondary notification callback and close");
                console.info("DOCK_CLOCK_KEYBOARD_PASS");
            } catch (error) {
                console.error("DOCK_CLOCK_KEYBOARD_FAIL " + error);
            }
            Qt.quit();
        }
    }
}
