pragma ComponentBehavior: Bound
import QtQuick

// Resource bounds only: expansion is immediate, reclamation waits for all motion
// and an idle interval. In particular drag=null precedes the return spring setup.
QtObject {
    id: bounds
    required property Dock dock
    required property real screenWidth
    required property real screenHeight
    required property real windowTop
    property real dpr: 1
    property real clearSigma: 9 * screenWidth / 1280
    property real regularSigma: 30
    property int idleMilliseconds: 1000
    readonly property real idleTop: screenHeight - dock.band - 120
    // Same outer shadow padding as Dock's tooltip glass.
    readonly property real tooltipPad: Math.ceil(28 * 1.8 + 5 + 12 + 6)
    readonly property bool needsHeadroom: {
        dock.tick;
        return dock.popupOpen || dock.popupProgress > 0 || dock.drag !== null || dock.holdBubble() || dock.bubble.following || (dock.tipOpacity > .001 && windowTop + dock.tipRect.y - tooltipPad < idleTop);
    }
    readonly property bool moving: dock.animating
    property bool expanded: false
    // Both separable kernels can reach the next paired tap, plus downsample/filter
    // support. Quarter-resolution Regular is the larger bound at DPR 1.
    readonly property real blurReach: Math.max(3 * clearSigma, 3 * regularSigma) + 26 + 12 / dpr
    readonly property real pixelHeight: screenHeight * dpr
    readonly property real regionTop: Math.abs(pixelHeight - Math.round(pixelHeight)) < .0001 && Math.round(pixelHeight) % 4 === 0 ? Math.max(0, Math.floor(((expanded ? windowTop : idleTop) - blurReach) * dpr / 4) * 4 / dpr) : 0
    readonly property rect region: Qt.rect(0, regionTop, screenWidth, screenHeight - regionTop)
    function update(): void {
        if (needsHeadroom) {
            reclaim.stop();
            expanded = true;
        } else if (moving) {
            reclaim.stop();
        } else if (expanded && !reclaim.running) {
            reclaim.start();
        }
    }
    onNeedsHeadroomChanged: update()
    onMovingChanged: update()
    Component.onCompleted: update()
    property Timer reclaim: Timer {
        interval: bounds.idleMilliseconds
        onTriggered: {
            if (!bounds.needsHeadroom && !bounds.moving)
                bounds.expanded = false;
            else
                bounds.update();
        }
    }
}
