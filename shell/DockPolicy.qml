pragma ComponentBehavior: Bound
import QtQuick

// Visibility, reserve and edge dwell timers of the dock.
Item {
    id: policy
    property bool on: true
    property bool autoHide: true
    property bool overview: false
    property string presentation: "unknown"
    property bool popupOpen: false
    property bool dragging: false
    property bool pointerInside: false
    property bool edgeHovered: false
    property bool revealed: false
    // 11 px margin + 66 px plate: the 84 px bubble (bottom 75 px below the plate top) ends
    // 2 px above the screen edge (2026-09-25; mockup 74).
    readonly property int thickness: 77
    readonly property bool fullscreen: presentation === "covered"
    // The edge remains reachable over fullscreen windows, including a pinned dock.
    readonly property bool edgeEnabled: on && (autoHide || fullscreen) && !overview
    // Overview and fullscreen slide the dock away in the mockup regardless of auto-hide.
    readonly property bool dockVisible: on && !overview && ((!autoHide && !fullscreen) || revealed || popupOpen || dragging)
    readonly property int reserve: on && !autoHide ? thickness : 0
    function scheduleHide(): void {
        if (!popupOpen && !dragging && !pointerInside && !edgeHovered)
            hideDelay.restart();
    }
    onEdgeHoveredChanged: {
        if (edgeHovered && edgeEnabled) {
            hideDelay.stop();
            revealDelay.restart();
        } else {
            revealDelay.stop();
            scheduleHide();
        }
    }
    onPointerInsideChanged: {
        if (pointerInside)
            hideDelay.stop();
        else
            scheduleHide();
    }
    onPopupOpenChanged: {
        if (popupOpen)
            hideDelay.stop();
        else
            scheduleHide();
    }
    onDraggingChanged: {
        if (dragging)
            hideDelay.stop();
        else
            scheduleHide();
    }
    onEdgeEnabledChanged: {
        if (edgeEnabled && edgeHovered)
            revealDelay.restart();
        else
            revealDelay.stop();
    }
    function resetReveal(): void {
        revealed = false;
        hideDelay.stop();
        revealDelay.stop();
        if (edgeEnabled && edgeHovered)
            revealDelay.restart();
    }
    onFullscreenChanged: {
        if (fullscreen)
            resetReveal();
    }
    onOverviewChanged: resetReveal()
    onAutoHideChanged: resetReveal()
    onOnChanged: resetReveal()
    Timer {
        id: revealDelay
        interval: 100
        onTriggered: {
            if (policy.edgeEnabled && policy.edgeHovered)
                policy.revealed = true;
        }
    }
    Timer {
        id: hideDelay
        interval: 100
        onTriggered: {
            if (!policy.popupOpen && !policy.dragging && !policy.pointerInside && !policy.edgeHovered)
                policy.revealed = false;
        }
    }
}
