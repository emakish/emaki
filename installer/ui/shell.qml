//@ pragma UseQApplication
//@ pragma AppId emaki-install
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    Component.onCompleted: Quickshell.watchFiles = false
    // Brings the window back. The controller, and with it the plan, outlives both ways out:
    // Hide keeps this window object; a compositor close discards it and a new one is made here.
    function present(): bool {
        let window = windowLoader.item as FloatingWindow;
        if (!windowLoader.active) {
            // A new FloatingWindow starts visible; Quickshell maps it on the next event loop turn.
            windowLoader.active = true;
            window = windowLoader.item as FloatingWindow;
        } else if (window && !window.backingWindowVisible) {
            // Quickshell 0.3.1 keeps a closed FloatingWindow "wanting" to be visible, so a plain
            // `visible = true` can be a no-op; false first always makes it a real change.
            window.visible = false;
            window.visible = true;
        }
        if (!window)
            return false;
        window.minimized = false;
        return window.visible;
    }
    InstallerController {
        id: controller
    }
    // GParted opens as a tiled window, under this floating one. While it runs the window steps
    // aside as for "Try Emaki first", and comes back by itself with the refreshed disk list.
    Connections {
        target: controller
        function onPartitioningChanged(): void {
            const window = windowLoader.item as FloatingWindow;
            if (controller.partitioning) {
                controller.clearPasswords();
                if (window)
                    window.visible = false;
            } else
                root.present();
        }
    }
    Loader {
        id: windowLoader
        sourceComponent: FloatingWindow {
            id: window
            title: "Install Emaki"
            implicitWidth: 1024
            implicitHeight: 700
            minimumSize: Qt.size(960, 640)
            color: "transparent"
            onClosed: {
                controller.clearPasswords();
                // Qt has already destroyed the surface. Drop the window; present() builds a new
                // one, exactly as at startup, instead of reviving this one.
                windowLoader.active = false;
            }
            InstallerView {
                anchors.fill: parent
                controller: controller
                onHideRequested: {
                    controller.clearPasswords();
                    window.visible = false;
                }
            }
        }
    }
    IpcHandler {
        target: "installer"
        // Never name a function after a `qs ipc` subcommand (show, call, wait, listen, prop):
        // `qs ipc call installer show` runs `qs ipc show`, prints metadata, exits 0 and calls
        // nothing. emaki-install checks for this exact reply.
        function present(): string {
            return root.present() ? "presented" : "not-presented";
        }
    }
}
