// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: harness
    readonly property var controller: scene.context.settingsController
    readonly property alias scene: scene
    readonly property alias window: window
    readonly property alias catalog: catalog
    readonly property alias service: service
    readonly property alias niri: niri
    QtObject {
        id: catalog
        readonly property bool writing: busy
        property bool busy: false
        property bool rejectNext: false
        property var wallpapers: []
        property string inheritedWallpaper: ""
        property string state: "ready"
        property string lastAction: ""
        property string lastStatus: ""
        property bool sessionApplied: true
        property var calls: []
        property var history: []
        property var values: [
            {
                key: "bar.autohide",
                value: false,
                default: false
            },
            {
                key: "bar.overview_workspaces",
                value: true,
                default: true
            },
            {
                key: "dock.on",
                value: true,
                default: true
            },
            {
                key: "dock.auto_hide",
                value: true,
                default: true
            },
            {
                key: "windows.default_column_width",
                value: "full",
                default: "full"
            },
            {
                key: "windows.focus_follows_mouse",
                value: false,
                default: false
            },
            {
                key: "appearance.wallpaper",
                value: null,
                default: null
            },
            {
                key: "appearance.gaps",
                value: 2,
                default: 2
            }
        ]
        function row(key) {
            return values.find(row => row.key === key) || null;
        }
        function set(key, value) {
            calls = calls.concat([
                {
                    operation: "set",
                    key: key,
                    value: value
                }
            ]);
            if (rejectNext) {
                rejectNext = false;
                lastStatus = "rejected";
                return;
            }
            values = values.map(row => Object.assign({}, row, {
                    value: row.key === key ? (typeof row.default === "boolean" ? value === "true" : typeof row.default === "number" ? Number(value) : value) : row.value
                }));
            lastStatus = "committed";
        }
        function reset(key) {
            calls = calls.concat([
                {
                    operation: "reset",
                    key: key
                }
            ]);
            values = values.map(row => Object.assign({}, row, {
                    value: row.key === key ? row.default : row.value
                }));
        }
        function undo(id) {
            calls = calls.concat([
                {
                    operation: "undo",
                    id: id
                }
            ]);
        }
    }
    SystemService {
        id: service
        live: false
        helpersEnabled: false
    }
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        width: 1536
        height: 960
        visible: true
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: harness.niri
            headless: true
            testWidth: window.width
            testHeight: window.height
            borderMode: "soft"
            reservedSpace: 52
            skipIntro: true
        }
        Binding {
            target: scene.panel
            property: "visible"
            value: scene.launcherPresent
        }
    }
    Component.onCompleted: {
        controller.catalog = catalog;
        controller.targetHost = scene;
    }
}
