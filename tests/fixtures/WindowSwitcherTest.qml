// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Io

// The window switcher in an offscreen Window: real core, fake niri (tests/test-window-switcher.py),
// actual Qt keys and clicks. The "desktop" draws the fake windows where niri puts them, and the
// switcher cuts its live pictures out of it, as it does out of the output's capture on Wayland.
// The keyboard's arrival and the held modifiers are the test's to say (focusArrived(), a fake
// probe), or the real Quickshell.queryKeyboardModifiers() fed by Qt key events.
ShellRoot {
    id: root
    property int fakeModifiers: 0
    property bool realProbe: false
    property int shownCount: 0
    property int finishedCount: 0
    property int closeCount: 0
    property var shots: []
    NiriService {
        id: service
        binary: Quickshell.env("EMAKI_BIN")
    }
    Window {
        id: window
        width: 1536
        height: 960
        visible: true
        color: "#3a2f2a"
        Item {
            id: desktop
            anchors.fill: parent
            readonly property var active: service.workspaces.find(w => w.output === "A" && w.is_active)
            readonly property var shown: service.windows.filter(w => desktop.active && w.workspace_id === desktop.active.id && desktop.at(w))
            function at(w: var): var {
                return w.layout?.tile_pos_in_workspace_view ?? scene.dockLabels.positions[w.id] ?? null;
            }
            Repeater {
                model: desktop.shown
                delegate: Rectangle {
                    id: fake
                    required property var modelData
                    readonly property var label: scene.dockLabels.values[modelData.id] ?? ({})
                    readonly property var palette: ({
                            "fixture-browser": ["#f3efe9", "#e2733f"],
                            "fixture-editor": ["#20242c", "#6aa1ff"],
                            "fixture-files": ["#f7f7f4", "#4a9b6e"]
                        })[label.appId ?? ""] ?? ["#e9e1f2", "#8a5cc7"]
                    readonly property var at: desktop.at(modelData) ?? [0, 0]
                    x: at[0] + modelData.layout.window_offset_in_tile[0]
                    y: at[1] + modelData.layout.window_offset_in_tile[1]
                    width: modelData.layout.window_size[0]
                    height: modelData.layout.window_size[1]
                    radius: 10
                    color: palette[0]
                    Rectangle {
                        width: parent.width
                        height: 44
                        radius: 10
                        color: fake.palette[1]
                        Text {
                            x: 18
                            anchors.verticalCenter: parent.verticalCenter
                            text: fake.label.title ?? ""
                            color: "white"
                            font.pixelSize: 18
                            font.weight: Font.DemiBold
                        }
                    }
                    Column {
                        x: 28
                        y: 76
                        spacing: 14
                        Repeater {
                            model: 9
                            Rectangle {
                                required property int index
                                width: (fake.width - 56) * (index % 3 === 2 ? .55 : .9)
                                height: 14
                                radius: 7
                                color: Qt.rgba(.5, .5, .5, .25)
                            }
                        }
                    }
                }
            }
        }
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
            testWidth: 1536
            testHeight: 960
            borderMode: "soft"
            reservedSpace: 52
            onCloseAllRequested: ++root.closeCount
        }
        TestCase {
            id: keys
            when: false
        }
    }
    Connections {
        target: scene.switcher
        function onShownChanged(): void {
            if (scene.switcher.shown)
                ++root.shownCount;
        }
        function onFinished(): void {
            ++root.finishedCount;
        }
    }
    Component.onCompleted: {
        scene.outputName = "A";
        scene.switcher.modifierProbe = () => root.realProbe ? (typeof Quickshell.queryKeyboardModifiers === "function" ? Quickshell.queryKeyboardModifiers() : -1) : root.fakeModifiers;
        scene.switcher.pictureSource = desktop;
        scene.switcher.pictureSourceReady = true;
    }
    function keyCode(name: string): int {
        return ({
                escape: Qt.Key_Escape,
                left: Qt.Key_Left,
                right: Qt.Key_Right,
                up: Qt.Key_Up,
                down: Qt.Key_Down,
                enter: Qt.Key_Return,
                alt: Qt.Key_Alt
            })[name] ?? 0;
    }
    IpcHandler {
        target: "test"
        function status(): string {
            const cards = [];
            const grid = scene.switcher.cards;
            for (let i = 0; grid && i < grid.count; ++i) {
                const item = grid.itemAtIndex(i);
                if (item)
                    cards.push({
                        id: item.row.id,
                        title: item.row.title,
                        live: item.liveShown,
                        kept: item.kept !== null,
                        icon: item.iconSource !== "",
                        rect: item.mapToItem(null, 0, 0, item.width, item.height)
                    });
            }
            return JSON.stringify({
                niri: service.connection,
                immediate_ready: service.immediateFocusReady,
                action_state: service.actionState,
                labels: scene.dockLabels.state,
                times: scene.dockLabels.focusTimes,
                switcher: JSON.parse(scene.switcherStatus()),
                opacity: scene.switcher.panel.opacity,
                presenting: scene.switcher.presenting,
                shown_count: root.shownCount,
                finished_count: root.finishedCount,
                close_count: root.closeCount,
                modal: scene.modalOpen,
                keyboard_surface: scene.keyboardSurface,
                bar_open_ui: scene.barPolicy.openUI,
                focus: window.activeFocusItem?.objectName ?? "",
                cards: cards,
                shots: root.shots,
                dark: Appearance.dark,
                accent: Appearance.accent
            });
        }
        function queuedLayout(index: int): bool {
            return service.switchLayout(index);
        }
        function immediateFocus(id: int): int {
            return service.focusWindowImmediate(id);
        }
        function step(delta: int, sameApp: bool, sent: string): bool {
            return scene.switchWindows(delta, sameApp, Number(sent));
        }
        function now(): string {
            return String(Date.now());
        }
        function lost(): void {
            scene.switcherKeyboardLost();
        }
        function arrive(): void {
            window.requestActivate();
            scene.switcher.focusArrived();
        }
        function modifiers(value: int): void {
            root.fakeModifiers = value;
        }
        function realProbe(on: bool): void {
            root.realProbe = on;
        }
        function key(name: string): void {
            window.requestActivate();
            keys.keyClick(root.keyCode(name));
        }
        function press(name: string): void {
            window.requestActivate();
            keys.keyPress(root.keyCode(name), name === "alt" ? Qt.AltModifier : Qt.NoModifier);
        }
        function release(name: string): void {
            window.requestActivate();
            keys.keyRelease(root.keyCode(name), Qt.NoModifier);
        }
        function click(x: real, y: real): void {
            keys.mouseClick(window.contentItem, x, y);
        }
        function move(x: real, y: real): void {
            keys.mouseMove(window.contentItem, x, y);
        }
        function closeAll(): void {
            scene.closeAll();
        }
        function timeouts(reveal: int, keyboard: int, handoff: int): void {
            scene.switcher.revealDelay = reveal;
            scene.switcher.keyboardTimeout = keyboard;
            scene.switcher.handoffTimeout = handoff;
        }
        function appearance(theme: string, accent: string): void {
            SettingsBridge.values = [
                {
                    key: "appearance.theme",
                    value: theme
                },
                {
                    key: "appearance.accent",
                    value: accent
                }
            ];
        }
        function shot(path: string): bool {
            return window.contentItem.grabToImage(result => {
                result.saveToFile(path);
                root.shots = root.shots.concat([path]);
            });
        }
    }
}
