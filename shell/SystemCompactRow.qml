pragma ComponentBehavior: Bound
import QtQuick
import "SystemIcons.js" as SystemIcons

// The system island's cells (docs/mockups/liquid-glass/system.js cells()): one per page —
// background apps, keyboard layout (only with more than one), Wi-Fi, Bluetooth, sound,
// brightness, battery. Cells are 26 px high with 2 px between them and no separators; icons
// are Adwaita symbolic 16 px in the ink of the glass, the layout is its code, the battery the
// only figure. Drawn by the bar island and, at the same pixels, by the panel's head, so the
// panel closing onto the island ends on exactly what the island then shows. Every cell is a
// GlassTarget of `glass` (the SystemIsland or the SystemPanel); the page the panel shows is a
// resting drop (`restKey`), the tab. Scrolling over sound or brightness steps it by 5.
Item {
    id: row
    required property NiriService niri
    required property SystemService services
    property var glass: null
    property HoverTip tip: null
    // The page the panel shows (its cell is the tab); "" in the bar.
    property string activePage: ""
    property color ink: LiquidPalette.inkOnDark
    signal clicked(string page)
    readonly property var backend: services.backend
    readonly property int layoutCount: niri.layouts?.names.length ?? 0
    readonly property var pages: ["tray"].concat(layoutCount > 1 ? ["kb"] : []).concat(["wifi", "bt", "sound", "light", "power"])
    readonly property real cellsWidth: cellRow.implicitWidth
    readonly property alias cells: cells
    function cell(page: string): Item {
        const i = pages.indexOf(page);
        return i >= 0 ? cells.itemAt(i) : null;
    }
    implicitWidth: cellsWidth
    implicitHeight: 26
    width: cellsWidth
    height: 26

    // ---- What each cell shows ----
    // A connected cable wins over Wi-Fi: NetworkManager routes over it by default.
    readonly property string wifiIcon: {
        const b = backend;
        if (b?.wiredConnected)
            return ["portal", "limited", "none"].includes(b.connectivity) ? "network-wired-no-route-symbolic" : "network-wired-symbolic";
        if (!b?.networkReady || !b.wifiDevices.length)
            return "network-wireless-disabled-symbolic";
        if (!b.wifiHardwareEnabled)
            return "network-wireless-hardware-disabled-symbolic";
        if (!b.wifiEnabled)
            return "network-wireless-disabled-symbolic";
        const n = b.networks.find(n => n.connected);
        if (!n)
            return b.networks.some(n => n.busy) ? "network-wireless-acquiring-symbolic" : "network-wireless-offline-symbolic";
        if (["portal", "limited", "none"].includes(b.connectivity))
            return "network-wireless-no-route-symbolic";
        return SystemIcons.signal(n.signal);
    }
    readonly property bool btOn: backend?.adapter?.enabled ?? false
    readonly property bool soundReady: !!(backend?.audioReady && backend.sink?.audio)
    readonly property string batteryText: services.batteryPercent >= 0 ? services.batteryPercent + "%" : ""
    function iconOf(page: string): string {
        switch (page) {
        case "tray":
            return "pan-up-symbolic";
        case "wifi":
            return wifiIcon;
        case "bt":
            return btOn ? "bluetooth-active-symbolic" : "bluetooth-disabled-symbolic";
        case "sound":
            return SystemIcons.sound(soundReady, services.sinkVolume, services.sinkMuted);
        case "light":
            return "display-brightness-symbolic";
        case "power":
            return SystemIcons.battery(services.batteryPercent, backend?.charging ?? false);
        }
        return "";
    }
    // Without the icon theme (tests, a bare system): Icon.qml's shapes.
    function fallbackOf(page: string): string {
        if (page === "sound")
            return soundReady && !services.sinkMuted ? "sound" : "muted";
        return ({
                tray: "tray",
                wifi: backend?.wiredConnected ? "wired" : "wifi",
                bt: "bt",
                light: "light",
                power: "battery"
            })[page] ?? "";
    }
    readonly property var tips: ({
            tray: "Background apps",
            kb: "Keyboard layout",
            wifi: "Wi-Fi",
            bt: "Bluetooth",
            sound: "Sound · scroll to change",
            light: "Brightness · scroll to change",
            power: "Battery and power"
        })
    readonly property var shortcuts: ({
            kb: "Super+Space"
        })

    Row {
        id: cellRow
        height: 26
        spacing: 2
        Repeater {
            id: cells
            model: row.pages
            GlassTarget {
                id: cell
                required property string modelData
                readonly property string page: modelData
                readonly property string icon: row.iconOf(page)
                readonly property string text: page === "kb" ? row.niri.layoutLabel : page === "power" ? row.batteryText : ""
                glass: row.glass
                key: "cell-" + page
                label: row.tips[page] ?? ""
                // system.js cells(): icon 16 + 6 + text + 14; whole pixels keep the next
                // cell's text on the pixel grid.
                width: Math.ceil(content.implicitWidth + 14)
                height: 26
                bubblePad: 3
                bubbleRadius: 13
                // The tab: the panel's page rests under a drop (pad 3, radius 13).
                readonly property string restKey: row.activePage === page ? key : ""
                readonly property rect restRect: Qt.rect(-3, -3, width + 6, height + 6)
                readonly property real restRadius: 13
                readonly property string restKind: "target"
                onRestKeyChanged: {
                    if (row.glass && row.glass.wake)
                        row.glass.wake();
                }
                onClicked: row.clicked(page)
                // Under the cell's MouseArea: its TapHandler would otherwise take the press
                // (instance children lie above GlassTarget's own MouseArea). A press opens or
                // switches the panel, which hides the tip itself.
                TipTarget {
                    z: -1
                    tip: row.tip
                    enabled: row.activePage !== cell.page
                    label: cell.label
                    shortcut: row.shortcuts[cell.page] ?? ""
                }
                WheelHandler {
                    enabled: cell.page === "sound" || cell.page === "light"
                    onWheel: event => row.services.step(cell.page, (event.angleDelta.y || event.angleDelta.x) > 0 ? 5 : -5)
                }
                Row {
                    id: content
                    x: 7
                    height: 26
                    spacing: 6
                    Item {
                        y: 5
                        width: 16
                        height: 16
                        visible: cell.icon !== ""
                        SymbolIcon {
                            id: symbol
                            width: 16
                            height: 16
                            name: cell.icon
                            ink: row.ink
                        }
                        Icon {
                            visible: !symbol.found && kind !== ""
                            width: 16
                            height: 16
                            kind: row.fallbackOf(cell.page)
                            charge: Math.max(0, row.services.batteryPercent) / 100
                            ink: row.ink
                        }
                    }
                    Text {
                        y: .5
                        height: 26
                        visible: cell.text !== ""
                        verticalAlignment: Text.AlignVCenter
                        text: cell.text
                        textFormat: Text.PlainText
                        font.family: ShellPalette.uiFont
                        // system.js FONT.code for the layout, FONT.ui for the battery.
                        font.pixelSize: cell.page === "kb" ? 12 : 13
                        font.weight: cell.page === "kb" ? Font.DemiBold : Font.Medium
                        // Figures of one width: the island keeps its width from 10 % to 99 %.
                        font.features: ({
                                tnum: 1
                            })
                        color: row.ink
                    }
                }
            }
        }
    }
}
