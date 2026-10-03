//@ pragma UseQApplication
//@ pragma AppId emaki-install
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    Component.onCompleted: Quickshell.watchFiles = false
    InstallerController {
        id: controller
    }
    FloatingWindow {
        id: window
        title: "Install Emaki"
        implicitWidth: 1024
        implicitHeight: 700
        minimumSize: Qt.size(960, 640)
        color: "transparent"
        onClosed: {
            controller.clearPasswords();
            visible = false;
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
    IpcHandler {
        target: "installer"
        function show(): void {
            window.visible = true;
            window.minimized = false;
        }
    }
}
