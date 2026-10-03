pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// One launcher result as drawn in liquid-glass/launcher.js drawItem(): a tile (Frequent and
// the app grid), a settings page card, or a row (everything else). Pure drawing: the same
// item lies under the glass in the list and, for the selected result, again on the glass.
// `row` is a LauncherBody result; colours come from the glass palette of the panel.
Item {
    id: item
    required property var row
    // "tile" | "page" | "row"
    required property string shape
    property color ink: LiquidPalette.inkOnDark
    property color dim: LiquidPalette.dimOnDark
    property color danger: LiquidPalette.dangerOnDark
    // The query, for highlighting a matched file name.
    property string term: ""
    readonly property string kind: row?.kind ?? ""
    readonly property string icon: row?.entry?.icon ? Quickshell.iconPath(row.entry.icon, true) : ""
    // Settings rows and kinds without an app icon carry an Adwaita symbolic icon.
    readonly property string symbol: ({
            page: row?.symbol ?? "preferences-other-symbolic",
            calculator: "accessories-calculator-symbolic",
            web: "web-browser-symbolic",
            clip: "edit-paste-symbolic",
            layout: "input-keyboard-symbolic",
            "layout-add": "list-add-symbolic",
            "layout-pick": "input-keyboard-symbolic",
            "switch-key": "preferences-desktop-keyboard-shortcuts-symbolic",
            "key-edit": "preferences-desktop-keyboard-shortcuts-symbolic",
            "wallpaper-pick": "preferences-desktop-wallpaper-symbolic",
            "default-role": "preferences-other-symbolic",
            "default-pick": "preferences-other-symbolic",
            undo: "edit-undo-symbolic",
            "dock-on": "view-app-grid-symbolic",
            "dock-auto": "view-conceal-symbolic",
            "bar-auto": "focus-top-bar-symbolic",
            "bar-ovws": "view-grid-symbolic",
            info: row?.symbol ?? "dialog-information-symbolic"
        })[kind] ?? ""
    // Right-hand word of a row: the workspace of a window, the action of a setting.
    readonly property string trailing: kind === "window" ? String(row.tag ?? "") : kind === "calculator" ? "Copy" : kind === "undo" ? "Undo" : kind === "key-edit" ? "Change" : kind === "layout-add" ? "Choose" : kind === "page" ? "" : kind === "web" ? "Open" : kind === "clip" ? "Delete" : kind === "app" ? (row.detail ?? "") : (row?.action ?? "")
    readonly property bool quietTrailing: kind === "app" || kind === "window" || kind === "clip" || ["In use", "Off", "Only one installed", "None installed"].includes(trailing)
    readonly property bool twoLines: shape === "row" && kind !== "app" && kind !== "window" && kind !== "clip" && kind !== "calculator" && (row?.detail ?? "") !== ""
    function escaped(text: string): string {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
    }
    function fileLabel(name: string): string {
        const t = term.trim();
        const index = name.toLocaleLowerCase().indexOf(t.toLocaleLowerCase());
        if (!t || index < 0)
            return escaped(name);
        return escaped(name.slice(0, index)) + '<font color="' + ShellPalette.accent + '">' + escaped(name.slice(index, index + t.length)) + '</font>' + escaped(name.slice(index + t.length));
    }
    // A file row names the folder, with the home directory as "~".
    readonly property string folder: {
        const path = row?.path ?? "";
        const home = Quickshell.env("HOME");
        const dir = path.slice(0, Math.max(0, path.lastIndexOf("/"))) || "/";
        return home && (dir === home || dir.startsWith(home + "/")) ? "~" + dir.slice(home.length) : dir;
    }

    // ---- Tile: icon 40 at the top, the name under it (tile() in launcher.js) ----
    Item {
        visible: item.shape === "tile"
        anchors.fill: parent
        Image {
            id: tileIcon
            x: (parent.width - 40) / 2
            y: 8
            width: 40
            height: 40
            sourceSize: Qt.size(80, 80)
            source: item.shape === "tile" ? item.icon : ""
            smooth: true
            mipmap: true
        }
        Text {
            anchors.centerIn: tileIcon
            visible: tileIcon.status !== Image.Ready
            text: (item.row?.label ?? "?").slice(0, 1).toLocaleUpperCase()
            font.family: ShellPalette.uiFont
            font.pixelSize: 20
            font.weight: Font.DemiBold
            color: item.dim
        }
        // The name lies inside the tile's bubble (one size on every tile, Metrics), 4 px in
        // from its sides as it was from the tile's; a longer name is elided, the bubble
        // does not grow with it (27.09).
        Text {
            width: Math.min(parent.width, Metrics.launcherTileBubble) - 8
            x: (parent.width - width) / 2
            y: 56
            height: 16
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: item.row?.label ?? ""
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: item.ink
        }
    }

    // ---- Settings page card: symbol, name, what is inside ----
    Item {
        visible: item.shape === "page"
        anchors.fill: parent
        SymbolIcon {
            x: 14
            y: 12
            name: item.shape === "page" ? item.symbol : ""
            ink: item.ink
        }
        Text {
            x: 42
            y: 12
            height: 16
            width: parent.width - 56
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: item.row?.label ?? ""
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
            color: item.ink
        }
        Text {
            x: 14
            y: 33
            height: 14
            width: parent.width - 28
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: item.row?.summary ?? ""
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: item.dim
        }
    }

    // ---- Row: icon at 10, text at 46, a quiet word on the right ----
    Item {
        visible: item.shape === "row"
        anchors.fill: parent
        readonly property real cy: height / 2
        Image {
            id: rowIcon
            x: 10
            y: parent.cy - 12
            width: 24
            height: 24
            sourceSize: Qt.size(48, 48)
            source: item.shape === "row" ? item.icon : ""
            visible: status === Image.Ready
            smooth: true
            mipmap: true
        }
        SymbolIcon {
            id: rowSymbol
            x: 13
            y: parent.cy - 9
            name: item.shape === "row" && item.icon === "" ? (item.kind === "file" ? "text-x-generic-symbolic" : item.kind === "window" || item.kind === "app" ? "application-x-executable-symbolic" : item.symbol) : ""
            ink: item.kind === "clip" ? item.dim : item.ink
        }
        // No theme at all (tests, a broken install): the first letter, as the dock does.
        Text {
            x: 10
            width: 24
            y: parent.cy - 12
            height: 24
            visible: !rowIcon.visible && !rowSymbol.found
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: item.kind === "calculator" ? "=" : item.kind === "clip" && item.row?.image ? "▣" : (item.row?.label ?? "?").slice(0, 1).toLocaleUpperCase()
            font.family: ShellPalette.uiFont
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: item.dim
        }
        Text {
            id: primary
            x: 46
            y: item.twoLines || item.kind === "file" ? parent.cy - 15 : parent.cy - 8
            height: 16
            width: parent.width - 46 - (trailingText.visible ? trailingText.implicitWidth + 28 : 14)
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: item.kind === "file" ? Text.StyledText : Text.PlainText
            // A window row names its window (launcher.js), the app is its icon.
            text: item.kind === "file" ? item.fileLabel(item.row?.label ?? "") : item.kind === "window" ? (item.row?.detail || item.row?.label || "") : (item.row?.label ?? "")
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
            color: item.ink
        }
        Text {
            x: 46
            y: parent.cy + 3
            height: 14
            width: primary.width
            visible: item.twoLines || item.kind === "file"
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: item.kind === "file" ? item.folder : item.row?.detail ?? ""
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: item.dim
        }
        Text {
            id: trailingText
            anchors.right: parent.right
            anchors.rightMargin: 14
            y: parent.cy - 8
            height: 16
            width: Math.min(implicitWidth, parent.width * .4)
            visible: item.trailing !== "" && item.kind !== "file"
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: item.trailing
            font.family: ShellPalette.uiFont
            font.pixelSize: item.kind === "window" ? 13 : item.quietTrailing ? 11 : 13
            font.weight: item.kind === "window" ? Font.DemiBold : Font.Medium
            color: item.quietTrailing ? item.dim : item.ink
        }
    }
}
