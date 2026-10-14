// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Item {
    id: policy
    property bool skipIntro: false
    property bool autoHide: Quickshell.env("EMAKI_SHELL_BAR_AUTOHIDE") === "1"
    property bool autoHideFullscreen: true
    readonly property string hideMode: autoHide ? (autoHideFullscreen ? "always" : "desktop") : (autoHideFullscreen ? "fullscreen" : "never")
    property bool overviewWorkspaces: Quickshell.env("EMAKI_SHELL_OVERVIEW_WORKSPACES") !== "0"
    property bool overview: false
    property string presentation: "unknown"
    property string knownPresentation: "unknown"
    property bool openUI: false
    property bool keyboardActive: false
    property bool dragging: false
    property bool pointerInside: false
    property bool edgeHovered: false
    property bool revealed: false
    property bool pointerRevealed: false
    property int configuredReserve: 52
    readonly property int revealDelayMs: 150
    readonly property int hideDelayMs: 500
    readonly property int slideDurationMs: 280
    readonly property bool fullscreen: knownPresentation === "covered"
    readonly property bool hidesHere: fullscreen ? autoHideFullscreen : autoHide
    // Retain the last known layer and preference, but keep the edge usable during missing updates.
    readonly property bool edgeEnabled: hidesHere && !overview
    readonly property bool interactionActive: openUI || keyboardActive || dragging
    readonly property bool edgeActive: edgeEnabled && edgeHovered
    readonly property bool held: interactionActive || pointerInside || edgeActive
    readonly property bool barVisible: overview ? overviewWorkspaces : interactionActive || revealed || !hidesHere
    readonly property bool normalIslands: barVisible && !overview
    readonly property bool workspaceIsland: barVisible && (!overview || overviewWorkspaces)
    // Reserve follows the desktop preference, never a transient reveal or focus visit.
    readonly property int effectiveReserve: autoHide ? Math.max(0, configuredReserve - 42) : configuredReserve
    // A panel that stays visible over fullscreen windows asks the compositor to start them
    // below it through a separate marker surface; the reserve above stays unchanged, so no
    // other window resizes when fullscreen starts or ends. Reveals never set it.
    readonly property int fullscreenInset: fullscreen && !autoHideFullscreen ? configuredReserve : 0
    property real overviewShift: overview ? 1 : 0
    Behavior on overviewShift {
        enabled: !policy.skipIntro
        NumberAnimation {
            duration: 300
            easing.type: Easing.OutCubic
        }
    }
    function scheduleHide(): void {
        if (!held && revealed)
            hideDelay.restart();
    }
    function updateEdge(): void {
        if (edgeActive) {
            hideDelay.stop();
            if (!pointerRevealed)
                revealDelay.restart();
        } else {
            revealDelay.stop();
            scheduleHide();
        }
    }
    function resetReveal(): void {
        hideDelay.stop();
        revealDelay.stop();
        pointerRevealed = false;
        revealed = interactionActive && !overview;
        updateEdge();
    }
    onEdgeActiveChanged: updateEdge()
    onPointerInsideChanged: {
        if (pointerInside && barVisible)
            pointerRevealed = true;
    }
    onHeldChanged: {
        if (held)
            hideDelay.stop();
        else
            scheduleHide();
    }
    onInteractionActiveChanged: {
        if (interactionActive && !overview) {
            revealed = true;
            hideDelay.stop();
        } else
            scheduleHide();
    }
    // Pending window links are transient, including when another output opens a window.
    onPresentationChanged: {
        if (presentation !== "unknown")
            knownPresentation = presentation;
    }
    onFullscreenChanged: resetReveal()
    onHidesHereChanged: resetReveal()
    onOverviewChanged: resetReveal()
    onHideModeChanged: resetReveal()
    Timer {
        id: revealDelay
        interval: policy.revealDelayMs
        onTriggered: {
            if (policy.edgeActive) {
                policy.pointerRevealed = true;
                policy.revealed = true;
            }
        }
    }
    Timer {
        id: hideDelay
        interval: policy.hideDelayMs
        onTriggered: {
            if (!policy.held) {
                policy.revealed = false;
                policy.pointerRevealed = false;
            }
        }
    }
}
