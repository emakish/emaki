pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Item {
    id: policy
    property bool skipIntro: false
    property bool autoHide: Quickshell.env("EMAKI_SHELL_BAR_AUTOHIDE") === "1"
    property bool overviewWorkspaces: Quickshell.env("EMAKI_SHELL_OVERVIEW_WORKSPACES") !== "0"
    property bool overview: false
    property string presentation: "unknown"
    property bool openUI: false
    property bool pointerInside: false
    property bool edgeHovered: false
    property bool revealed: false
    property int configuredReserve: 52
    readonly property bool fullscreen: presentation === "covered"
    readonly property bool edgeEnabled: autoHide && presentation === "clear" && !overview
    readonly property bool barVisible: overview ? overviewWorkspaces : openUI || revealed || (!autoHide && !fullscreen)
    readonly property bool normalIslands: barVisible && !overview
    readonly property bool workspaceIsland: barVisible && (!overview || overviewWorkspaces)
    readonly property int effectiveReserve: autoHide ? Math.max(0, configuredReserve - 42) : configuredReserve
    // 0 → 1 while the overview opens: islands that are not the workspaces slide up and fade
    // (mockup: translate 300 ms, opacity 220 ms); back to 0 the same way when it closes.
    property real overviewShift: overview ? 1 : 0
    Behavior on overviewShift {
        enabled: !policy.skipIntro
        NumberAnimation {
            duration: 300
            easing.type: Easing.OutCubic
        }
    }
    function scheduleHide(): void {
        if (!openUI && !pointerInside && !edgeHovered)
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
    onOpenUIChanged: {
        if (openUI) {
            revealed = true;
            hideDelay.stop();
        } else if (fullscreen) {
            revealed = false;
            hideDelay.stop();
        } else
            scheduleHide();
    }
    onEdgeEnabledChanged: {
        if (!edgeEnabled)
            revealDelay.stop();
    }
    onFullscreenChanged: {
        if (fullscreen) {
            revealed = false;
            hideDelay.stop();
            revealDelay.stop();
        }
    }
    onOverviewChanged: {
        revealed = false;
        hideDelay.stop();
        revealDelay.stop();
    }
    onAutoHideChanged: {
        revealed = false;
        hideDelay.stop();
        revealDelay.stop();
    }
    Timer {
        id: revealDelay
        interval: 150
        onTriggered: {
            if (policy.edgeEnabled && policy.edgeHovered)
                policy.revealed = true;
        }
    }
    Timer {
        id: hideDelay
        interval: 500
        onTriggered: {
            if (!policy.openUI && !policy.pointerInside && !policy.edgeHovered)
                policy.revealed = false;
        }
    }
}
