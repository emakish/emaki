// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Item {
    id: root
    width: 1240
    height: 810
    property string requestedPage: "displays"
    property bool navigationChecked: false
    function findAbout(item) {
        if (item && item.details !== undefined && item.received !== undefined)
            return item;
        for (const child of item.children || []) {
            const found = findAbout(child);
            if (found)
                return found;
        }
        return null;
    }
    readonly property var aboutPage: requestedPage === "about" ? findAbout(view) : null
    readonly property bool aboutReady: requestedPage !== "about" || (aboutPage !== null && aboutPage.received && aboutPage.details.computer === "Emaki Test Computer")
    readonly property bool ready: navigationChecked && aboutReady && wallpaper.status === Image.Ready && view.children.some(item => item.objectName === "settings-glass" && item.ready)
    readonly property var renderStats: ({
            requested_page: requestedPage,
            actual_page: view.page,
            about_details: aboutPage ? aboutPage.details : null,
            available: view.availablePages.includes(requestedPage),
            keyboard_connected: niri.connected,
            graphics_api: GraphicsInfo.api
        })
    Image {
        id: wallpaper
        anchors.fill: parent
        source: Quickshell.env("EMAKI_FIXTURE_WALLPAPER")
        fillMode: Image.PreserveAspectCrop
    }
    QtObject {
        id: catalog
        readonly property bool writing: busy
        property bool busy: false
        property string state: "ready"
        property string lastStatus: ""
        property bool sessionApplied: true
        property var history: []
        property var wallpapers: [
            {
                name: "Emaki landscape",
                path: Quickshell.env("EMAKI_FIXTURE_PICTURE")
            }
        ]
        function row(key) {
            const value = key === "appearance.gaps" ? 2 : key === "appearance.wallpaper" ? null : key === "windows.default_column_width" ? "full" : !["bar.autohide", "windows.focus_follows_mouse"].includes(key);
            return {
                key: key,
                value: value,
                default: value
            };
        }
    }
    SystemBackend {
        id: backend
        networkReady: true
        wifiEnabled: true
        wifiHardwareEnabled: true
        wifiDevices: [({
                    name: "Wireless adapter"
                })]
        networks: [({
                    key: "home-network",
                    name: "Home network",
                    signal: 0.82,
                    psk: true,
                    open: false,
                    device: "wireless0",
                    connected: true
                })]
    }
    SystemService {
        id: service
        backend: backend
        account: JSON.parse(Quickshell.env("EMAKI_FIXTURE_ACCOUNT"))
        live: false
        helpersEnabled: false
        brightness: ({
                state: "ready",
                percent: 75
            })
    }
    NiriService {
        id: niri
        binary: ""
    }
    DisplaysService {
        id: displays
    }
    SettingsPageRegistry {
        id: pageRegistry
    }
    SettingsView {
        id: view
        anchors.fill: parent
        catalog: catalog
        service: service
        niri: niri
        displays: displays
        availablePages: pageRegistry.availablePages
        wallpaperTexture: wallpaper.source
    }
    Component.onCompleted: Qt.callLater(() => {
        niri.model = {
            overview_open: false,
            keyboard_layouts: {
                names: ["English (US)", "Ukrainian"],
                current_idx: 0
            },
            windows: [],
            workspaces: [],
            outputs: []
        };
        niri.connection = "connected";
        const changed = view.navigate(requestedPage, "");
        if (changed !== view.availablePages.includes(requestedPage))
            throw new Error("Unavailable section navigation changed");
        navigationChecked = true;
    })
}
