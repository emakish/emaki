// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

// The login screen's row of people when the computer has more than one: picture and name,
// chosen with the mouse or Left/Right in an empty field. Loaded by URL: a failure here
// leaves the password field of the chosen person working.
Item {
    id: row
    property var accounts: []
    property string chosen: ""
    property real life: 1
    signal picked(string login)
    readonly property real tileWidth: 96
    readonly property real drop: 60
    onChosenChanged: Qt.callLater(strip.revealChoice)
    Flickable {
        id: strip
        anchors.horizontalCenter: parent.horizontalCenter
        y: row.height - height - Math.max(28, row.height * .08)
        width: Math.min(tiles.width, Math.max(0, row.width - 32))
        height: tiles.height
        contentWidth: tiles.width
        contentHeight: height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.HorizontalFlick
        interactive: contentWidth > width
        opacity: row.life
        function revealChoice(): void {
            const index = row.accounts.findIndex(person => person.login === row.chosen);
            if (index < 0)
                return;
            const left = index * (row.tileWidth + tiles.spacing);
            const right = left + row.tileWidth;
            contentX = Math.max(0, Math.min(Math.max(0, contentWidth - width), left < contentX ? left : right > contentX + width ? right - width : contentX));
        }
        onWidthChanged: Qt.callLater(revealChoice)
        onContentWidthChanged: Qt.callLater(revealChoice)
        WheelHandler {
            enabled: strip.interactive
            onWheel: event => {
                const delta = event.angleDelta.x || event.angleDelta.y;
                strip.contentX = Math.max(0, Math.min(strip.contentWidth - strip.width, strip.contentX - delta));
                event.accepted = true;
            }
        }
        Row {
            id: tiles
            objectName: "greeter-users"
            spacing: 12
            Repeater {
                model: row.accounts
                Item {
                    id: tile
                    required property var modelData
                    required property int index
                    readonly property bool selected: modelData.login === row.chosen
                    objectName: "greeter-user-" + modelData.login
                    width: row.tileWidth
                    height: row.drop + 8 + name.height
                    opacity: selected ? 1 : hover.containsMouse ? .9 : .66
                    Rectangle {
                        x: (tile.width - width) / 2
                        width: row.drop
                        height: width
                        radius: width / 2
                        color: LiquidPalette.flatDrop
                        border.color: tile.selected ? LiquidPalette.accent : LiquidPalette.flatDropRim
                        border.width: tile.selected ? 2 : 1
                    }
                    AccountPicture {
                        id: picture
                        user: tile.modelData.login
                        isolateHelper: true
                    }
                    // The person's picture, or a silhouette without one, inside the drop's rim.
                    Canvas {
                        id: face
                        x: (tile.width - width) / 2
                        width: row.drop
                        height: width
                        property string loaded: ""
                        readonly property color ink: LiquidPalette.faint
                        onInkChanged: requestPaint()
                        function follow(): void {
                            if (picture.url === loaded)
                                return;
                            if (loaded)
                                unloadImage(loaded);
                            loaded = picture.url;
                            if (loaded)
                                loadImage(loaded);
                            requestPaint();
                        }
                        Component.onCompleted: follow()
                        Connections {
                            target: picture
                            function onUrlChanged(): void {
                                face.follow();
                            }
                            function onPendingChanged(): void {
                                face.requestPaint();
                            }
                        }
                        onImageLoaded: requestPaint()
                        onAvailableChanged: requestPaint()
                        onPaint: {
                            const context = getContext("2d"), r = width / 2 - 4, cx = width / 2, cy = height / 2;
                            context.reset();
                            context.fillStyle = face.ink;
                            context.beginPath();
                            context.arc(cx, cy, r, 0, 2 * Math.PI);
                            context.clip();
                            if (loaded && isImageLoaded(loaded)) {
                                context.drawImage(loaded, cx - r, cy - r, 2 * r, 2 * r);
                                return;
                            }
                            if (picture.pending || (loaded && isImageLoading(loaded)))
                                return;
                            context.beginPath();
                            context.arc(cx, cy - r * .2, r * .3, 0, 2 * Math.PI);
                            context.fill();
                            context.beginPath();
                            context.ellipse(cx - r * .62, cy + r * .28, r * 1.24, r);
                            context.fill();
                        }
                    }
                    Text {
                        id: name
                        y: row.drop + 8
                        width: tile.width
                        text: tile.modelData.name
                        color: tile.selected ? LiquidPalette.ink : LiquidPalette.plateDim
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 13
                        font.weight: tile.selected ? Font.DemiBold : Font.Medium
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    MouseArea {
                        id: hover
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: row.picked(tile.modelData.login)
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: tile.modelData.name
                    Accessible.onPressAction: row.picked(tile.modelData.login)
                }
            }
        }
    }
}
