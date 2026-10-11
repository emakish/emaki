// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland
import "CaptureText.js" as CaptureText

Scope {
    id: overlay
    required property CaptureController controller
    PanelWindow {
        id: selectionSurface
        screen: overlay.controller.screen
        visible: overlay.controller.opened && !!screen
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "emaki-capture"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: visible ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }
        CapturePanel {
            id: panel
            anchors.fill: parent
            controller: overlay.controller
        }
        onVisibleChanged: if (visible)
            Qt.callLater(panel.forceActiveFocus)
    }
    PanelWindow {
        screen: overlay.controller.screen
        visible: !!screen && overlay.controller.mode === "recording" && (overlay.controller.recording || (overlay.controller.busy && !overlay.controller.opened))
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "emaki-recording"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        anchors.top: true
        implicitWidth: stopButton.implicitWidth + 24
        implicitHeight: stopPlate.y + stopPlate.height
        mask: Region {
            item: stopPlate
        }
        WelcomeGlass {
            id: stopPlate
            y: Metrics.reservedSpace + 8
            width: parent.width
            height: 66
            wallpaperTexture: overlay.controller.wallpaperTexture
        }
        WelcomeButton {
            id: stopButton
            anchors.centerIn: stopPlate
            text: overlay.controller.recording ? CaptureText.stop : overlay.controller.stopping ? CaptureText.finishing : CaptureText.starting
            primary: true
            enabled: !overlay.controller.stopping
            onClicked: overlay.controller.stop()
        }
    }
}
