// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//@ pragma UseQApplication
//@ pragma AppId emaki-update-manager
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    Component.onCompleted: Quickshell.watchFiles = false
    UpdateController { id: controller }
    function present(): bool {
        if (!windowLoader.active) windowLoader.active = true;
        const window = windowLoader.item as FloatingWindow;
        if (!window) return false;
        if (controller.phase === "idle") controller.check();
        window.visible = false;
        window.visible = true;
        window.minimized = false;
        return window.visible;
    }
    Loader {
        id: windowLoader
        sourceComponent: FloatingWindow {
            id: window
            title: "Emaki updates"
            implicitWidth: 860
            implicitHeight: 680
            minimumSize: Qt.size(640, 420)
            color: "transparent"
            onClosed: windowLoader.active = false
            onVisibleChanged: if (visible) updateView.present()
            UpdateView {
                id: updateView
                anchors.fill: parent
                controller: controller
                onHideRequested: window.visible = false
                onOpenNews: link => Qt.openUrlExternally(link)
            }
        }
    }
    IpcHandler {
        target: "updates"
        function present(): string { return root.present() ? "presented" : "not-presented"; }
    }
}
