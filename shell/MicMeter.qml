pragma ComponentBehavior: Bound
import QtQuick

// Publish every meaningful peak immediately. Qt Quick presents the latest geometry
// at the window's normal vsync cadence; an extra timer would lose short audio peaks.
// Keep the original two-pixel hairline and skip repeated sub-raster endpoint buckets.
Item {
    id: meter
    property real level: -1
    property color color: "transparent"
    readonly property real targetWidth: width * Math.max(0, Math.min(1, level))
    // Suppress sub-raster noise without snapping the published geometry. A bucket
    // spans 1/1024 of a physical pixel; meaningful samples keep their exact width.
    readonly property real endpointScale: (Window.window?.devicePixelRatio || 1) * 1024
    property real shownWidth: 0
    height: 2
    visible: level >= 0

    function update(): void {
        if (level < 0) {
            shownWidth = 0;
        } else if (Math.round(shownWidth * endpointScale) !== Math.round(targetWidth * endpointScale)) {
            shownWidth = targetWidth;
        }
    }
    onTargetWidthChanged: update()
    onLevelChanged: {
        if (level < 0)
            update();
    }
    Component.onCompleted: update()
    Rectangle {
        width: meter.shownWidth
        height: 2
        radius: 1
        color: meter.color
        opacity: .55
    }
}
