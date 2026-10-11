// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: surface
    required property PolkitPromptController controller
    required property ShellScene scene
    readonly property bool warningVisible: !controller.opened && card.expansion === 0 && SudoKeySupport.warningNeeded
    screen: scene?.output ?? null
    visible: !!scene && (controller.opened || card.expansion > 0 || warningVisible) && !controller.locked
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
    // The entire output belongs to the prompt while it is visible. The card
    // handles its controls; empty space consumes pointer and wheel events.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        onWheel: event => event.accepted = true
    }
    PolkitPromptCard {
        id: card
        controller: surface.controller
        viewportWidth: surface.width
        viewportHeight: surface.height
        islandX: surface.scene ? surface.scene.bar.x + surface.scene.bar.clockIsland.islandRect.x : (viewportWidth - islandWidth) / 2
        islandWidth: surface.scene?.bar.clockIsland.islandWidth ?? 148
        reducedMotion: Quickshell.env("EMAKI_REDUCED_MOTION") === "1" || Quickshell.env("EMAKI_LOCK_REDUCED_MOTION") === "1"
    }
    SudoKeyWarning {
        anchors.centerIn: parent
        visible: surface.warningVisible
        onVisibleChanged: if (visible)
            forceActiveFocus()
        width: Math.min(820, surface.width - 32)
        height: Math.min(implicitHeight, surface.height - 40)
        onDismissed: {
            SudoKeySupport.warningDismissed = true;
            SudoKeySupport.warningRequested = false;
        }
        onAccepted: SudoKeySupport.warningRequested = false
    }
    onVisibleChanged: if (visible)
        Qt.callLater(card.focusPassword)
}
