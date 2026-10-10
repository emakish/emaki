// Geometry from docs/mockups/shell/style.css and app.js; technical choices noted below.
pragma Singleton
import QtQuick

QtObject {
    readonly property int top: 8
    readonly property int side: 10
    // Left islands on glass (liquid-glass/launcher.html BAR): the launcher button at the
    // bar's 10 px edge, the workspaces 8 px right of it; the grey corner mark is gone.
    readonly property int logoX: 10
    readonly property int workspaceX: 54
    // Workspaces island: a cell of 28 per existing workspace on a pitch of 30, 8 px inset,
    // the island 6 px wider than its cells (launcher.js wsItems / surfaces()).
    readonly property int workspaceCell: 28
    readonly property int workspacePitch: 30
    readonly property int workspaceInset: 8
    readonly property int islandHeight: 36
    readonly property int windowGap: 8
    readonly property int reservedSpace: top + islandHeight + windowGap
    // Bars were chosen instead of dots; 6x2 is a technical choice. The digit sits on the
    // optical centre of its cell; the mark hangs workspaceMarkGap under the digit's ink.
    readonly property int workspaceMarkWidth: 6
    readonly property int workspaceMarkHeight: 2
    readonly property int workspaceMarkGap: 3
    readonly property int islandRadius: 12
    readonly property int panelRadius: 20
    readonly property int border: 2
    readonly property int launcherWidth: 720
    readonly property int settingsWidth: 1240
    readonly property int settingsHeight: 810
    readonly property int launcherHeader: 60
    readonly property int launcherBodyMax: 500
    readonly property int launcherFoot: 16 // was 42 with the key-hint line, since removed
    // The selection bubble of a launcher tile (Frequent and the app grid): one size on every
    // tile (27.09) — launcher.js tile()'s smallest bubble, 88 x 86, without its growth
    // with the name, which made a long name's bubble wider than the tile and put it over
    // the neighbours. The tile's name is elided inside it (LauncherItem).
    readonly property int launcherTileBubble: 88
    readonly property int morphMs: 380
    readonly property var morphCurve: [0.32, 0.72, 0, 1, 1, 1]
    readonly property color faint: "#6d5d4f"
    readonly property color dim: "#5a4c40"
    readonly property color line: "#3a2e25"
}
