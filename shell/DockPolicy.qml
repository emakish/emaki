// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

// Visibility, reserve and edge dwell timers of the dock.
Item {
    id: policy
    property bool on: true
    property bool autoHide: true
    property bool overview: false
    property string presentation: "unknown"
    property string knownPresentation: "unknown"
    property bool keyboardActive: false
    property bool popupOpen: false
    property bool dragging: false
    property bool pointerInside: false
    property bool edgeHovered: false
    property bool edgeDragging: false
    property bool revealed: false
    readonly property int revealDelayMs: 100
    readonly property int hideDelayMs: 100
    readonly property int slideDurationMs: 320
    // The plate and its bottom margin retain the existing 77 px reserve.
    readonly property int thickness: 77
    readonly property bool fullscreen: knownPresentation === "covered"
    readonly property bool edgeEnabled: on && (autoHide || fullscreen) && !overview
    readonly property bool interactionActive: keyboardActive || popupOpen || dragging
    readonly property bool edgeActive: edgeEnabled && (edgeHovered || edgeDragging)
    readonly property bool held: interactionActive || pointerInside || edgeActive
    readonly property bool dockVisible: on && (!overview || keyboardActive) && (interactionActive || (!autoHide && !fullscreen) || revealed)
    readonly property int reserve: on && !autoHide ? thickness : 0
    function scheduleHide(): void {
        if (!held && revealed)
            hideDelay.restart();
    }
    function updateEdge(): void {
        if (edgeActive) {
            hideDelay.stop();
            if (!revealed)
                revealDelay.restart();
        } else {
            revealDelay.stop();
            scheduleHide();
        }
    }
    function resetReveal(): void {
        hideDelay.stop();
        revealDelay.stop();
        revealed = on && interactionActive && (!overview || keyboardActive);
        updateEdge();
    }
    onEdgeActiveChanged: updateEdge()
    onHeldChanged: {
        if (held)
            hideDelay.stop();
        else
            scheduleHide();
    }
    onInteractionActiveChanged: {
        if (on && interactionActive && (!overview || keyboardActive)) {
            revealed = true;
            hideDelay.stop();
        } else
            scheduleHide();
    }
    // An incomplete window snapshot is not a transition out of fullscreen.
    onPresentationChanged: {
        if (presentation !== "unknown")
            knownPresentation = presentation;
    }
    onFullscreenChanged: resetReveal()
    onOverviewChanged: resetReveal()
    onAutoHideChanged: resetReveal()
    onOnChanged: resetReveal()
    Timer {
        id: revealDelay
        interval: policy.revealDelayMs
        onTriggered: {
            if (policy.edgeActive)
                policy.revealed = true;
        }
    }
    Timer {
        id: hideDelay
        interval: policy.hideDelayMs
        onTriggered: {
            if (!policy.held)
                policy.revealed = false;
        }
    }
}
