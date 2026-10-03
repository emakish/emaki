pragma ComponentBehavior: Bound
import QtQuick

// The digits of the workspaces island, drawn under its glass (liquid-glass/launcher.html):
// only the workspaces niri has on this output (it keeps one empty at the end), a cell of
// 28 on a pitch of 30. The active one is the Clear drop LeftIslands flows between cells;
// here it only takes the ink. Occupied: the 6×2 mark under the digit (23.09, bars
// instead of dots). Clicks focus a workspace; the wheel lives on the whole island.
Item {
    id: strip
    required property NiriService niri
    required property string outputName
    property color ink: LiquidPalette.inkOnDark
    property color dim: LiquidPalette.dimOnDark
    property color faint: LiquidPalette.faintOnDark
    readonly property var entries: niri.workspaces.filter(w => w.output === outputName).sort((a, b) => a.idx - b.idx)
    readonly property var active: entries.find(w => w.is_active) ?? null
    readonly property int activeIndex: active?.idx ?? 0
    // Position of the active workspace in `entries` (-1: none on this output).
    readonly property int activePosition: entries.findIndex(w => w.is_active)
    readonly property int pitch: Metrics.workspacePitch
    readonly property int cell: Metrics.workspaceCell
    property double lastWheel: 0
    // Device-pixel snapping: at scale 1.25 logical integers land on half pixels, so cells,
    // digits and marks are placed on whole device pixels. LeftIslands puts the strip's own
    // origin on a whole device pixel; everything below is relative to it.
    readonly property real dpr: Screen.devicePixelRatio || 1
    function snap(value: real): real {
        return Math.round(value * dpr) / dpr;
    }
    // For sizes: a hair under the whole device pixel, so scale × size never rounds up to an
    // extra aliased row (2.4 × 1.25 is 3.0000000000000004 in floating point).
    function snapSize(value: real): real {
        return (Math.round(value * dpr) - 0.01) / dpr;
    }
    // Left edge of cell `position` (index in entries), in strip coordinates.
    function cellX(position: int): real {
        return snap(position * pitch);
    }
    implicitWidth: niri.connected ? Math.max(cell, entries.length * pitch - (pitch - cell)) : offline.implicitWidth + 12
    implicitHeight: Metrics.islandHeight
    function choose(position: int): bool {
        const entry = entries.find(w => w.idx === position);
        return entry ? niri.focusWorkspace(entry.id) : false;
    }
    // Next/previous existing workspace (launcher.js wheel), at most one step per 250 ms.
    function wheel(delta: real): void {
        if (!delta || Date.now() - lastWheel < 250 || !entries.length)
            return;
        lastWheel = Date.now();
        const i = Math.max(0, activePosition);
        const next = entries[Math.max(0, Math.min(entries.length - 1, i + (delta < 0 ? 1 : -1)))];
        if (next && next.idx !== activeIndex)
            choose(next.idx);
    }
    Text {
        id: offline
        anchors.centerIn: parent
        visible: !strip.niri.connected
        text: "niri · " + strip.niri.connection
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 12
        color: strip.dim
    }
    Repeater {
        model: strip.niri.connected ? strip.entries : []
        Item {
            id: cell
            required property int index
            required property var modelData
            readonly property int position: modelData.idx
            readonly property bool current: modelData.is_active
            readonly property bool occupied: strip.niri.windows.some(w => w.workspace_id === modelData.id)
            x: strip.cellX(index)
            width: strip.cell
            height: strip.height
            // Optical centre: the glyph's ink box (TextMetrics.tightBoundingRect), not the
            // advance box with its side bearings, sits on the cell centre; the mark hangs a
            // fixed gap under the ink, centred on the same axis. All on device pixels.
            TextMetrics {
                id: inkBox
                font: glyph.font
                text: glyph.text
            }
            // Vertical box of all digits at once: "3" overshoots the baseline where "1" does
            // not, and the mark must sit on one row under every digit.
            TextMetrics {
                id: digitInk
                font: glyph.font
                text: "0123456789"
            }
            readonly property real inkCenterX: inkBox.tightBoundingRect.x + inkBox.tightBoundingRect.width / 2
            readonly property real inkTop: glyph.baselineOffset + digitInk.tightBoundingRect.y
            readonly property real inkBottom: inkTop + digitInk.tightBoundingRect.height
            readonly property real glyphX: strip.snap(cell.x + cell.width / 2 - inkCenterX) - cell.x
            readonly property real glyphY: strip.snap((cell.height - (inkTop + inkBottom)) / 2)
            Text {
                id: glyph
                x: cell.glyphX
                y: cell.glyphY
                text: cell.position
                textFormat: Text.PlainText
                font.family: ShellPalette.uiFont
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: cell.current || cell.occupied ? strip.ink : strip.faint
            }
            Rectangle {
                id: mark
                width: strip.snapSize(Metrics.workspaceMarkWidth)
                height: strip.snapSize(Metrics.workspaceMarkHeight)
                x: strip.snap(cell.x + cell.glyphX + cell.inkCenterX - width / 2) - cell.x
                y: strip.snap(cell.glyphY + cell.inkBottom + Metrics.workspaceMarkGap)
                antialiasing: false
                visible: cell.occupied
                color: cell.current ? strip.ink : strip.dim
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: strip.choose(cell.position)
            }
        }
    }
}
