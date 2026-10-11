// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: surface
    required property ShellScene scene
    required property bool blocked
    screen: scene?.output ?? null
    visible: !!scene && !blocked && SudoKeySupport.warningNeeded
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "emaki-auth-prompt"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: visible ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    anchors {
        top: true
        bottom: true
        left: true
        right: true
    }
    mask: Region {
        item: warning
    }
    SudoKeyWarning {
        id: warning
        anchors.centerIn: parent
        width: Math.min(820, surface.width - 32)
        height: Math.min(implicitHeight, surface.height - 40)
        onDismissed: {
            SudoKeySupport.warningDismissed = true;
            SudoKeySupport.warningRequested = false;
        }
        onAccepted: SudoKeySupport.warningRequested = false
    }
}
