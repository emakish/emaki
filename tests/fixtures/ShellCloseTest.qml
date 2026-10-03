pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Io

// Actual Qt pointer/key dispatch in an offscreen Window, real core, fake niri.
ShellRoot {
    id: root
    property bool extraPopupOpen: false
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
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: 1536
            testHeight: 960
            borderMode: "soft"
            reservedSpace: 52
            onCloseAllRequested: {
                root.extraPopupOpen = false;
                ++root.closeCount;
            }
        }
        // Match Surfaces.overlay.visible without instantiating a Wayland surface.
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
    IpcHandler {
        target: "test"
        function click(x: real, y: real): void {
            pointer.mouseClick(scene, x, y, Qt.LeftButton);
        }
        function move(x: real, y: real): void {
            pointer.mouseMove(scene, x, y);
        }
        function mode(name: string): bool {
            return scene.input.setMode(name);
        }
        // Launch counts for Frequent, most frequent first (comma-separated desktop ids).
        function frequent(ids: string): void {
            const counts = {};
            ids.split(",").forEach((id, i) => counts[id] = 10 - i);
            scene.input.appCatalog.counts = counts;
        }
        function keyEscape(): void {
            pointer.keyClick(Qt.Key_Escape);
        }
        function open(): void {
            scene.openLauncher();
        }
        function prepare(): void {
            scene.input.setMode("Files");
            scene.input.setQuery("fixture");
            root.extraPopupOpen = true;
        }
        function race(): void {
            scene.openLauncher();
            scene.closeAll(); // Before openLauncher's queued Qt.callLater executes.
        }
        function status(): string {
            const value = JSON.parse(scene.status());
            value.extra_popup_open = root.extraPopupOpen;
            value.close_count = root.closeCount;
            value.expansion = scene.expansion;
            return JSON.stringify(value);
        }
    }
    Component.onCompleted: {
        Quickshell.watchFiles = false;
        window.requestActivate();
    }
}
