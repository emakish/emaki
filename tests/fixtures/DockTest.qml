pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Io

// Dock in an offscreen Window: real core, fake niri, actual Qt pointer dispatch.
// Headless ShellScene places the dock items at dockOrigin, where Surfaces.qml's dock surface would be.
ShellRoot {
    id: root
    property int closeCount: 0
    NiriService {
        id: service
        binary: Quickshell.env("EMAKI_BIN")
    }
    Window {
        id: window
        width: 1536
        height: 960
        visible: true
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
        Binding {
            target: scene.panel
            property: "visible"
            value: scene.launcherPresent
        }
        TestCase {
            id: pointer
            when: false
        }
    }
    DockIpc {
        scene: scene
    }
    DockPolicy {
        id: timedPolicy
        on: false
    }
    IpcHandler {
        target: "test"
        function timing(): string {
            function check(value) {
                if (!value)
                    throw new Error("Dock timing/state regression");
            }
            timedPolicy.on = true;
            timedPolicy.edgeHovered = true;
            pointer.wait(50);
            check(!timedPolicy.revealed);
            timedPolicy.edgeHovered = false;
            pointer.wait(130);
            check(!timedPolicy.revealed);
            timedPolicy.edgeHovered = true;
            pointer.wait(140);
            check(timedPolicy.dockVisible);
            timedPolicy.edgeHovered = false;
            pointer.wait(50);
            check(timedPolicy.dockVisible);
            pointer.wait(90);
            check(!timedPolicy.dockVisible);
            timedPolicy.edgeHovered = true;
            pointer.wait(140);
            timedPolicy.popupOpen = true;
            timedPolicy.edgeHovered = false;
            pointer.wait(140);
            check(timedPolicy.dockVisible);
            timedPolicy.dragging = true;
            timedPolicy.popupOpen = false;
            pointer.wait(140);
            check(timedPolicy.dockVisible);
            timedPolicy.dragging = false;
            pointer.wait(140);
            check(!timedPolicy.dockVisible);
            timedPolicy.autoHide = false;
            timedPolicy.presentation = "clear";
            check(timedPolicy.dockVisible);
            timedPolicy.presentation = "covered";
            check(!timedPolicy.dockVisible && timedPolicy.edgeEnabled);
            timedPolicy.edgeHovered = true;
            pointer.wait(140);
            check(timedPolicy.dockVisible);
            timedPolicy.pointerInside = true;
            timedPolicy.edgeHovered = false;
            pointer.wait(140);
            check(timedPolicy.dockVisible);
            timedPolicy.pointerInside = false;
            pointer.wait(140);
            check(!timedPolicy.dockVisible);
            timedPolicy.presentation = "clear";
            check(timedPolicy.dockVisible);
            timedPolicy.on = false;
            return "ok";
        }
        function click(x: real, y: real): void {
            pointer.mouseClick(scene, x, y, Qt.LeftButton);
        }
        function rightClick(x: real, y: real): void {
            pointer.mouseClick(scene, x, y, Qt.RightButton);
        }
        function hover(x: real, y: real): void {
            pointer.mouseMove(scene, x, y);
        }
        function drag(x0: real, y0: real, x1: real, y1: real): void {
            pointer.mousePress(scene, x0, y0, Qt.LeftButton);
            for (let step = 1; step <= 8; ++step)
                pointer.mouseMove(scene, x0 + (x1 - x0) * step / 8, y0 + (y1 - y0) * step / 8, 10);
            pointer.mouseRelease(scene, x1, y1, Qt.LeftButton);
        }
        function open(): void {
            scene.openLauncher();
        }
        function close(): void {
            scene.closeAll();
        }
        function select(index: int): void {
            scene.input.selectAt(index);
        }
        function activate(): void {
            scene.input.activate();
        }
        function query(text: string): void {
            scene.input.setQuery(text);
        }
        function status(): string {
            const value = JSON.parse(scene.status());
            // Test-only detail: keys of every dock slot and the popup rows.
            value.dock_keys = scene.dock.keys;
            value.dock_windows = scene.dock.entries.map(e => e.windows.map(w => w.id));
            value.dock_rows = scene.dockPopup.rows.map(r => r.kind);
            // The dock draws its own tooltip glass above the icon (dock.js tooltipRect).
            const dock = scene.dock;
            value.dock_tip = dock.tipOpacity > 0 ? dock.tipLabel : "";
            value.tip_rect = {
                x: dock.tipRect.x + dock.screenOrigin.x,
                y: dock.tipRect.y + dock.screenOrigin.y,
                width: dock.tipRect.width,
                height: dock.tipRect.height
            };
            value.close_count = root.closeCount;
            value.item_rects = scene.dock.keys.filter(k => k !== "sep").map(k => scene.dockScreenRect(k));
            return JSON.stringify(value);
        }
    }
    Component.onCompleted: {
        Quickshell.watchFiles = false;
        window.requestActivate();
    }
}
