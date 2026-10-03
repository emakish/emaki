pragma ComponentBehavior: Bound
import QtQuick

// The clock island's line (docs/mockups/liquid-glass/clock.js paint()): the time, then the
// short date a step dimmer, no separator dot; at the right end one accent dot while there are
// notifications, or the moon while Do not disturb is on. Drawn by the bar island and by the
// panel (whose closed state is the island, pixel for pixel), under and on the glass.
// `naturalWidth` is the island's one width all year: the widest weekday/day/month line.
Item {
    id: line
    required property string time
    required property string date
    property bool dnd: false
    property int notificationCount: 0
    property color ink: LiquidPalette.inkOnDark
    property color dim: LiquidPalette.dimOnDark
    property color accent: LiquidPalette.accentOnDark
    readonly property alias dateLabel: dateLabel
    readonly property var weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    readonly property var months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    // Reading .font makes the bindings follow the font (a method call alone is not tracked,
    // and the first evaluation may see the default font).
    readonly property real timeAdvance: {
        timeMetrics.font;
        return timeMetrics.advanceWidth("00:00");
    }
    readonly property real widestDate: {
        dateMetrics.font;
        let widest = 0;
        for (const d of weekdays)
            for (const m of months)
                widest = Math.max(widest, dateMetrics.advanceWidth(d + " 30 " + m));
        return widest;
    }
    // clock.js measureIsland(): 18 + time + 12 + widest date + 12 + indicator 12 + 18.
    readonly property int naturalWidth: Math.ceil(18 + timeAdvance + 12 + widestDate + 12 + 12 + 18)
    height: Metrics.islandHeight
    FontMetrics {
        id: timeMetrics
        font: timeLabel.font
    }
    FontMetrics {
        id: dateMetrics
        font: dateLabel.font
    }
    Text {
        id: timeLabel
        x: 18
        y: .5
        height: parent.height
        verticalAlignment: Text.AlignVCenter
        text: line.time
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        font.weight: Font.DemiBold
        color: line.ink
    }
    Text {
        id: dateLabel
        x: 18 + line.timeAdvance + 12
        y: .5
        height: parent.height
        verticalAlignment: Text.AlignVCenter
        text: line.date
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 12
        font.weight: Font.Medium
        color: line.dim
    }
    SymbolIcon {
        id: moon
        x: line.width - 30
        y: 11
        width: 13
        height: 13
        name: "weather-clear-night-symbolic"
        ink: line.dim
        visible: line.dnd && found
    }
    // Without the icon theme (tests, a bare system): the moon glyph of the old island.
    Text {
        x: line.width - 30
        width: 13
        height: parent.height
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        visible: line.dnd && !moon.found
        text: "☾"
        textFormat: Text.PlainText
        font.pixelSize: 13
        color: line.dim
    }
    // clock.js: a dot of radius 2.8 at (width − 24, 18); whole pixels keep it round.
    Rectangle {
        x: line.width - 27
        y: 15
        width: 6
        height: 6
        radius: 3
        antialiasing: true
        color: line.accent
        visible: !line.dnd && line.notificationCount > 0
    }
}
