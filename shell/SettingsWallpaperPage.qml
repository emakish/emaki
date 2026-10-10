// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var catalog
    readonly property var row: catalog.row("appearance.wallpaper")
    readonly property string currentPath: (row?.value || catalog.inheritedWallpaper || "")
    readonly property string currentName: (catalog.wallpapers || []).find(picture => picture.path === currentPath)?.name || currentPath || "Inherited wallpaper"
    readonly property alias chooser: chooser
    property string chooserError: ""
    readonly property bool busy: catalog.writing || chooser.busy
    spacing: 20
    function pictureUrl(path): string {
        return path ? "file://" + path.split("/").map(encodeURIComponent).join("/") : "";
    }
    function choosePicture(): void {
        if (busy || !row)
            return;
        chooserError = "";
        chooser.start({
            op: "wallpaper-choose"
        });
    }
    function selectPicture(path): void {
        if (!busy && row) {
            chooserError = "";
            catalog.set("appearance.wallpaper", path);
        }
    }
    PrivateJob {
        id: chooser
        timeoutMs: 130000
        onCompleted: result => {
            if (result.state === "ready" && typeof result.path === "string" && result.path.startsWith("/")) {
                page.chooserError = "";
                page.catalog.set("appearance.wallpaper", result.path);
            } else if (result.state !== "cancelled") {
                page.chooserError = "Could not choose a picture. Your wallpaper has not changed.";
            }
        }
        onStateChanged: {
            if (!["idle", "pending", "ready", "cancelled"].includes(state))
                page.chooserError = "Could not choose a picture. Your wallpaper has not changed.";
        }
    }
    SettingsCard {
        width: parent.width
        title: "Wallpaper"
        SettingsRow {
            objectName: "setting-appearance.wallpaper"
            width: parent.width
            title: "Your own picture"
            explanation: "Choose a picture for every display. Reset restores your inherited wallpaper."
            value: page.row?.value ?? null
            defaultValue: page.row?.default ?? null
            unavailable: !page.row
            busy: page.busy
            onResetRequested: {
                page.chooserError = "";
                page.catalog.reset("appearance.wallpaper");
            }
            control: Component {
                SettingsButton {
                    objectName: "wallpaper-choose"
                    text: page.chooser.busy ? "Choosing…" : "Choose picture…"
                    onClicked: page.choosePicture()
                }
            }
        }
        Column {
            width: parent.width
            topPadding: 10
            bottomPadding: 16
            spacing: 12
            Text {
                width: parent.width
                text: page.currentName
                textFormat: Text.PlainText
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.WrapAnywhere
            }
            Image {
                width: parent.width
                height: visible ? Math.min(220, width * 0.45) : 0
                visible: page.currentPath !== ""
                source: page.pictureUrl(page.currentPath)
                sourceSize.width: 1000
                sourceSize.height: 440
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                clip: true
            }
            Text {
                objectName: "wallpaper-error"
                width: parent.width
                visible: page.chooserError !== ""
                text: page.chooserError
                textFormat: Text.PlainText
                color: "#a01b45"
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Emaki wallpapers"
        Text {
            width: parent.width
            text: "Choose a wallpaper from the installed collection."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        Flow {
            width: parent.width
            spacing: 12
            Repeater {
                model: page.catalog.wallpapers || []
                delegate: SettingsButton {
                    id: picture
                    required property var modelData
                    required property int index
                    objectName: "wallpaper-choice-" + index
                    width: Math.max(140, (parent.width - 24) / 3)
                    height: 128
                    padding: 7
                    enabled: !page.busy && !!page.row
                    text: modelData.name
                    Accessible.name: modelData.name
                    Accessible.role: Accessible.RadioButton
                    Accessible.checked: page.currentPath === modelData.path
                    onClicked: page.selectPicture(modelData.path)
                    contentItem: Column {
                        spacing: 6
                        Image {
                            width: parent.width
                            height: 86
                            source: page.pictureUrl(picture.modelData.path)
                            sourceSize.width: 360
                            sourceSize.height: 172
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            clip: true
                        }
                        Text {
                            width: parent.width
                            text: (page.currentPath === picture.modelData.path ? "✓ " : "") + picture.modelData.name
                            textFormat: Text.PlainText
                            color: SettingsTheme.ink
                            font.family: SettingsTheme.fontFamily
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
        Text {
            width: parent.width
            visible: !page.catalog.wallpapers?.length
            text: "No wallpapers were found in the installed collection."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
    }
}
