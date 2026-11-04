// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    NiriService {
        id: niri
    }
    OverviewVideo {
        niri: niri
    }
    Window {
        id: window
        visible: true
        width: 320
        height: 180
        Loader {
            id: content
            anchors.fill: parent
        }
        TestCase {
            name: "OverviewVideo"
            when: window.visible
            function test_lifecycle() {
                SettingsBridge.values = [
                    {
                        key: "appearance.overview_still",
                        value: "/tmp/still.png"
                    }
                ];
                tryCompare(SettingsBridge, "surfaceCount", Quickshell.screens.length);
                compare(SettingsBridge.playingCount, 0);
                niri.overviewOpen = true;
                tryCompare(SettingsBridge, "playingCount", Quickshell.screens.length);
                niri.overviewOpen = false;
                tryCompare(SettingsBridge, "playingCount", 0);
                compare(SettingsBridge.surfaceCount, Quickshell.screens.length);
                niri.overviewOpen = true;
                tryCompare(SettingsBridge, "surfaceCount", Quickshell.screens.length);
                SettingsBridge.values = [];
                tryCompare(SettingsBridge, "surfaceCount", 0);
            }
            function test_picture_failure() {
                content.active = true;
                content.setSource("OverviewVideoContent.qml");
                tryCompare(content, "status", Loader.Ready);
                content.item.fallbackPath = Quickshell.env("EMAKI_TEST_STILL");
                content.item.stillPath = "/missing-picture.png";
                content.item.playing = true;
                tryCompare(content.item, "stillFailed", true);
                const still = findChild(content.item, "overview-still");
                tryCompare(still, "status", Image.Ready);
                const notice = findChild(content.item, "overview-failure");
                verify(notice.visible && notice.text.length > 0);
                compare(content.item.decoderLoaded, false);
                content.item.stillPath = Quickshell.env("EMAKI_TEST_STILL");
                tryCompare(content.item, "stillFailed", false);
                tryCompare(still, "status", Image.Ready);
                verify(!notice.visible);
                content.active = false;
            }
            function test_playback_and_failure() {
                content.active = true;
                content.setSource("OverviewVideoContent.qml");
                tryCompare(content, "status", Loader.Ready);
                compare(content.item.decoderLoaded, false);
                compare(content.item.localUrl("/tmp/a #?.mp4"), "file:///tmp/a%20%23%3F.mp4");
                compare(content.item.localUrl("https://example.com/x"), "");
                content.item.videoPath = Quickshell.env("EMAKI_TEST_VIDEO");
                compare(content.item.decoderLoaded, false);
                content.item.playing = true;
                tryCompare(content.item, "videoReady", true, 8000);
                compare(content.item.failed, false);
                content.item.playing = false;
                tryCompare(content.item, "decoderLoaded", false);
                compare(content.item.videoReady, false);
                content.item.playing = true;
                tryCompare(content.item, "videoReady", true, 8000);
                content.item.videoPath = "/missing-video.mp4";
                tryCompare(content.item, "failed", true, 8000);
                compare(content.item.decoderLoaded, false);
                content.item.videoPath = Quickshell.env("EMAKI_TEST_VIDEO");
                tryCompare(content.item, "videoReady", true, 8000);
                compare(content.item.failed, false);
                // A transient failure must retry the same video after overview close.
                content.item.videoItem.failed();
                tryCompare(content.item, "failed", true);
                compare(content.item.decoderLoaded, false);
                content.item.playing = false;
                compare(content.item.failed, false);
                compare(content.item.decoderLoaded, false);
                content.item.playing = true;
                tryCompare(content.item, "videoReady", true, 8000);
                // A queued failure from a player being closed cannot poison the next open.
                content.item.videoItem.failed();
                content.item.playing = false;
                wait(50);
                compare(content.item.failed, false);
                content.item.playing = true;
                tryCompare(content.item, "videoReady", true, 8000);
                content.active = false;
                tryCompare(content, "item", null);
            }
            function cleanupTestCase() {
                console.log("OVERVIEW_VIDEO_RESULT", qtest_results.failCount);
                Qt.callLater(Qt.quit);
            }
        }
    }
}
