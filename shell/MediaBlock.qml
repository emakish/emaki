pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Services.Mpris

// MPRIS on the clock panel (docs/mockups/liquid-glass/clock.js, media): the player's own app
// icon as the cover (44 px), the title and "artist · player", three symbolic buttons on the
// right; under it, when more than one player plays, their names in a row, the chosen one a
// resting drop. No boxes: everything lies on the plate, under the glass (GlassTarget).
// Frame coordinates of the panel: the block starts at the panel's side margin.
Item {
    id: media
    property var players: Mpris.players.values
    property string chosen: ""
    // The clock panel (colours, drops); null in tests of the block alone.
    property var glass: null
    readonly property var player: players.find(p => p.dbusName === chosen) || players[0] || null
    readonly property int playerCount: players.length
    readonly property bool playing: player?.isPlaying ?? false
    property string actionState: "idle"
    readonly property color ink: glass ? glass.ink : LiquidPalette.inkOnDark
    readonly property color dim: glass ? glass.dim : LiquidPalette.dimOnDark
    // The chosen player's name: a resting drop of the panel.
    readonly property GlassTarget playerTarget: playerCount > 1 && playerRow.count === playerCount ? playerRow.itemAt(Math.max(0, players.indexOf(player))) as GlassTarget : null
    // 56 for the block, 4 + 26 for the row of players when there are several.
    implicitHeight: player ? (playerCount > 1 ? 86 : 56) : 0
    function perform(action: string): bool {
        const p = player;
        if (!p) {
            actionState = "player_missing";
            return false;
        }
        if (action === "toggle" && p.canTogglePlaying)
            p.togglePlaying();
        else if (action === "next" && p.canGoNext)
            p.next();
        else if (action === "previous" && p.canGoPrevious)
            p.previous();
        else {
            actionState = "unsupported";
            return false;
        }
        actionState = "requested";
        return true;
    }
    // The player's app: its desktop entry when it names one, else a guess by identity.
    function appIcon(p: var): string {
        if (!p)
            return "";
        const entry = (p.desktopEntry ? DesktopEntries.byId(p.desktopEntry) : null) || (p.identity ? DesktopEntries.heuristicLookup(p.identity) : null);
        return entry?.icon ? Quickshell.iconPath(entry.icon, true) : "";
    }
    Item {
        width: parent.width
        height: 56
        visible: media.player !== null
        Image {
            id: cover
            x: 4
            y: 6
            width: 44
            height: 44
            sourceSize: Qt.size(88, 88)
            source: media.appIcon(media.player)
            smooth: true
            mipmap: true
        }
        SymbolIcon {
            x: 4 + 10
            y: 6 + 10
            width: 24
            height: 24
            name: cover.status === Image.Ready ? "" : "audio-x-generic-symbolic"
            ink: media.dim
        }
        Text {
            x: 62
            y: 28 - 8 - 10
            width: Math.max(0, parent.width - 200)
            height: 20
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: media.player?.trackTitle || "Unknown track"
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
            color: media.ink
        }
        Text {
            x: 62
            y: 28 + 10 - 10
            width: Math.max(0, parent.width - 200)
            height: 20
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
            text: [media.player?.trackArtist, media.player?.identity].filter(Boolean).join(" · ")
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: media.dim
        }
        // clock.js: three 32 px buttons, 38 apart, the last 14 px from the right edge.
        Repeater {
            model: [
                {
                    action: "previous",
                    icon: "media-skip-backward-symbolic",
                    label: "Previous track"
                },
                {
                    action: "toggle",
                    icon: media.playing ? "media-playback-pause-symbolic" : "media-playback-start-symbolic",
                    label: media.playing ? "Pause" : "Play"
                },
                {
                    action: "next",
                    icon: "media-skip-forward-symbolic",
                    label: "Next track"
                }
            ]
            GlassTarget {
                id: control
                required property var modelData
                required property int index
                glass: media.glass
                key: "media-" + modelData.action
                label: modelData.label
                x: media.width - 14 - 32 - (2 - index) * 38
                y: 12
                width: 32
                height: 32
                bubblePad: 0
                bubbleRadius: 14
                enabled: modelData.action === "toggle" ? (media.player?.canTogglePlaying ?? false) : modelData.action === "next" ? (media.player?.canGoNext ?? false) : (media.player?.canGoPrevious ?? false)
                onClicked: media.perform(modelData.action)
                SymbolIcon {
                    id: symbol
                    x: 7
                    y: 7
                    width: 18
                    height: 18
                    name: control.modelData.icon
                    ink: media.ink
                    opacity: control.enabled ? 1 : .4
                }
                // Without the icon theme: the old glyphs.
                Text {
                    anchors.fill: parent
                    visible: !symbol.found
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: ({
                            previous: "⏮",
                            toggle: media.playing ? "Ⅱ" : "▶",
                            next: "⏭"
                        })[control.modelData.action]
                    textFormat: Text.PlainText
                    font.pixelSize: 14
                    color: media.ink
                    opacity: control.enabled ? 1 : .4
                }
            }
        }
    }
    // Several players: their names; the chosen one in ink on its drop, the others dim.
    Row {
        y: 60
        height: 26
        spacing: 2
        visible: media.playerCount > 1
        Repeater {
            id: playerRow
            model: media.playerCount > 1 ? media.players : []
            GlassTarget {
                id: playerButton
                required property var modelData
                readonly property bool active: modelData === media.player
                glass: media.glass
                key: "player-" + (modelData.dbusName || modelData.identity)
                label: modelData.identity || "Player"
                width: playerLabel.implicitWidth + 20
                height: 26
                onClicked: media.chosen = modelData.dbusName
                Text {
                    id: playerLabel
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: .5
                    text: playerButton.label
                    textFormat: Text.PlainText
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    color: playerButton.active ? media.ink : media.dim
                }
            }
        }
    }
}
