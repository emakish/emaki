// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: content
    property string videoPath: ""
    property string stillPath: ""
    property bool playing: false
    property string fallbackPath: ""
    property bool stillFailed: false
    onStillPathChanged: stillFailed = false
    // Keep the still; the multimedia loader is destroyed on overview close.
    property bool failed: false
    readonly property bool decoderLoaded: player.item !== null
    readonly property var videoItem: player.item
    readonly property bool videoReady: videoItem?.ready ?? false
    readonly property url playerComponent: Qt.resolvedUrl("OverviewVideoPlayer.qml")
    function localUrl(path: string): string {
        return path.startsWith("/") ? "file://" + path.split("/").map(encodeURIComponent).join("/") : "";
    }
    function startPlayer(item): void {
        item.source = localUrl(videoPath);
    }
    onPlayingChanged: if (!playing)
        failed = false
    onVideoPathChanged: {
        failed = false;
        player.active = false;
        player.active = Qt.binding(() => content.playing && content.videoPath !== "" && !content.failed);
    }
    Image {
        objectName: "overview-still"
        anchors.fill: parent
        source: content.localUrl(content.stillFailed ? content.fallbackPath : content.stillPath)
        onStatusChanged: if (status === Image.Error && !content.stillFailed)
            content.stillFailed = true
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
        sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
        visible: !content.videoReady
    }
    Text {
        objectName: "overview-failure"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 32
        width: Math.max(0, parent.width - 48)
        visible: content.playing && (content.failed || content.stillFailed)
        text: content.stillFailed ? qsTr("This picture cannot be opened. Showing the default background.") : qsTr("This video cannot be played. Showing the still background.")
        color: "white"
        style: Text.Outline
        styleColor: "black"
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
    }
    Loader {
        id: player
        anchors.fill: parent
        active: content.playing && content.videoPath !== "" && !content.failed
        source: content.playerComponent
        visible: content.videoReady
        onLoaded: content.startPlayer(item)
        onStatusChanged: if (status === Loader.Error)
            content.failed = true
    }
    Connections {
        target: player.item
        function onFailed(): void {
            // Do not destroy the player from inside its own multimedia signal.
            const failedPlayer = player.item;
            Qt.callLater(() => {
                if (content.playing && player.item === failedPlayer)
                    content.failed = true;
            });
        }
    }
}
