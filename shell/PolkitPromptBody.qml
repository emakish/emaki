// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Clock-to-prompt content shared by the protected surface and offscreen fixtures.
Item {
    id: surface
    required property PolkitPromptController controller
    required property ShellScene scene
    property bool presented: false
    property DockBackdrop backdrop: null
    readonly property alias card: card
    readonly property bool warningVisible: !controller.opened && card.expansion === 0 && SudoKeySupport.warningNeeded
    readonly property bool present: !!scene && (controller.opened || card.expansion > 0 || warningVisible) && !controller.locked
    readonly property bool clockCovered: present && presented && card.morph > .5
    width: scene?.viewportWidth ?? 0
    height: scene?.viewportHeight ?? 0
    visible: present
    function takeFocus(): void {
        if (!visible)
            return;
        if (warningVisible)
            warning.forceActiveFocus();
        else
            card.focusPassword();
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
        expansion: surface.controller.opened && surface.presented ? 1 : 0
        viewportWidth: surface.width
        viewportHeight: surface.height
        islandX: surface.scene ? surface.scene.bar.x + surface.scene.bar.clockIsland.islandRect.x : (viewportWidth - islandWidth) / 2
        islandWidth: surface.scene?.bar.clockIsland.islandWidth ?? 148
        wallpaperTexture: LiquidPalette.sharedCanvas ? surface.scene?.wallpaper.texture ?? "" : ""
        dpr: surface.scene?.outputScale ?? 1
        time: surface.scene?.bar.clockTime ?? ""
        date: surface.scene?.bar.clockDate ?? ""
        dnd: surface.scene?.notes.effectiveDnd ?? false
        notificationCount: surface.scene?.notes.count ?? 0
        backdrop: surface.backdrop
        islandBubble: surface.scene?.bar.clockIsland.bubble ?? null
        reducedMotion: Quickshell.env("EMAKI_REDUCED_MOTION") === "1" || Quickshell.env("EMAKI_LOCK_REDUCED_MOTION") === "1"
    }
    SudoKeyWarning {
        id: warning
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
        Qt.callLater(takeFocus)
    onSceneChanged: Qt.callLater(takeFocus)
}
