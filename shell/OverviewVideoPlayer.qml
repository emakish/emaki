// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtMultimedia

// Loaded separately: missing codecs or Qt Multimedia leave the still frame usable.
Item {
    id: video
    property alias source: player.source
    property bool ready: false
    signal failed
    MediaPlayer {
        id: player
        loops: MediaPlayer.Infinite
        videoOutput: output
        // No AudioOutput: themes never acquire an audio stream or disturb music.
        onSourceChanged: if (source.toString() !== "")
            play()
        onErrorOccurred: video.failed()
    }
    VideoOutput {
        id: output
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectCrop
    }
    Connections {
        target: output.videoSink
        function onVideoFrameChanged(): void {
            video.ready = true;
        }
    }
    Timer {
        interval: 5000
        running: !video.ready
        onTriggered: video.failed()
    }
    Component.onDestruction: player.stop()
}
