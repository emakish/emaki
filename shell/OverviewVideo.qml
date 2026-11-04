// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// The compositor event stream is the only playback clock; no polling or hidden decoder.
Scope {
    id: overview
    required property NiriService niri
    property bool enabled: true
    function path(key: string): string {
        const value = SettingsBridge.values.find(row => row.key === key)?.value;
        return typeof value === "string" && value.startsWith("/") ? value : "";
    }
    readonly property string videoPath: path("appearance.overview_video")
    readonly property string stillPath: path("appearance.overview_still")
    function configure(surface, screen): void {
        surface.screen = screen;
        surface.videoPath = Qt.binding(() => overview.videoPath);
        surface.stillPath = Qt.binding(() => overview.stillPath);
        surface.playing = Qt.binding(() => overview.niri.overviewOpen);
    }
    Variants {
        // Keep the still through closing animations and between workspaces.
        model: overview.enabled && overview.stillPath !== "" ? Quickshell.screens : []
        Scope {
            id: instance
            required property var modelData
            Loader {
                source: Qt.resolvedUrl("OverviewVideoSurface.qml")
                onLoaded: overview.configure(item, instance.modelData)
            }
        }
    }
}
