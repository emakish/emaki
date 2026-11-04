// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var catalog
    readonly property var row: catalog.row("appearance.wallpaper")
    readonly property string currentPath: row?.value || ""
    readonly property var currentPicture: (catalog.wallpapers || []).find(picture => picture.path === currentPath)
    readonly property string currentName: currentPicture?.name || currentPath || qsTranslate("SettingsWallpaperPage", "Inherited wallpaper")
    readonly property var chooser: catalog.wallpaperChooser
    readonly property string chooserError: catalog.wallpaperChooserError
    readonly property bool busy: catalog.writing || chooser.busy
    readonly property var overviewTheme: (catalog.styles || []).find(style => style.id === (catalog.value("appearance.style") || "emaki"))
    readonly property string currentOverviewName: currentOverview ? qsTranslate("SettingsThemesPage", currentOverview.name) : (catalog.value("appearance.overview_video") || catalog.value("appearance.overview_still") || "").split("/").pop()
    readonly property var currentOverview: (catalog.styles || []).find(style => overviewMatches(style))
    function overviewMatches(style): bool {
        return !!style && catalog.value("appearance.overview_still") === style.overview_still && (catalog.value("appearance.overview_video") === style.overview_video || (!style.video_available && catalog.value("appearance.overview_video") === ""));
    }
    spacing: 20
    function pictureUrl(path): string {
        return path ? "file://" + path.split("/").map(encodeURIComponent).join("/") : "";
    }
    function choosePicture(): void {
        if (busy || !row)
            return;
        catalog.chooseWallpaper();
    }
    function selectPicture(path): void {
        if (!busy && row) {
            catalog.wallpaperChooserError = "";
            catalog.set("appearance.wallpaper", path);
        }
    }
    SettingsCard {
        width: parent.width
        title: qsTr("Overview background")
        SettingsRow {
            objectName: "setting-appearance.overview_background"
            width: parent.width
            controlWidth: Math.min(230, Math.max(150, width * 0.45))
            title: qsTr("Your own video or picture")
            formatValue: () => qsTr("Theme background")
            explanation: qsTr("Videos play only while the overview is open. A picture stays still. Changing the theme replaces this background.")
            value: page.catalog.value("appearance.overview_video") + "|" + page.catalog.value("appearance.overview_still")
            defaultValue: (page.overviewTheme?.overview_video || "") + "|" + (page.overviewTheme?.overview_still || "")
            busy: page.busy || (page.catalog.overviewChooser?.busy ?? false)
            onResetRequested: page.catalog.resetOverview()
            control: Component {
                SettingsButton {
                    objectName: "overview-choose"
                    text: qsTr("Choose video or picture…")
                    onClicked: page.catalog.chooseOverview()
                }
            }
        }
        Column {
            width: parent.width
            topPadding: 10
            bottomPadding: 16
            spacing: 8
            SettingsButton {
                objectName: "overview-theme"
                checked: page.overviewMatches(page.overviewTheme)
                Accessible.role: Accessible.RadioButton
                Accessible.checked: checked
                text: (checked ? "✓ " : "") + qsTr("Theme background")
                enabled: !page.busy && !(page.catalog.overviewChooser?.busy ?? false)
                onClicked: page.catalog.resetOverview()
            }
            Text {
                width: parent.width
                text: page.currentOverviewName
                textFormat: Text.PlainText
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.WrapAnywhere
            }
            Flow {
                id: overviewGrid
                width: parent.width
                spacing: 8
                Repeater {
                    model: (page.catalog.styles || []).length
                    delegate: SettingsButton {
                        required property int index
                        readonly property var style: page.catalog.styles[index]
                        objectName: "overview-choice-" + index
                        checked: page.overviewMatches(style)
                        text: (checked ? "✓ " : "") + qsTranslate("SettingsThemesPage", style.name)
                        enabled: style.available && !page.busy && !(page.catalog.overviewChooser?.busy ?? false)
                        Accessible.role: Accessible.RadioButton
                        Accessible.checked: checked
                        onClicked: page.catalog.selectOverview(style)
                    }
                }
            }
            Text {
                objectName: "overview-error"
                width: parent.width
                visible: (page.catalog.overviewChooserError || "") !== ""
                text: page.catalog.overviewChooserError || ""
                textFormat: Text.PlainText
                color: SettingsTheme.error
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: qsTranslate("SettingsWallpaperPage", "Wallpaper")
        SettingsRow {
            objectName: "setting-appearance.wallpaper"
            width: parent.width
            title: qsTranslate("SettingsWallpaperPage", "Your own picture")
            explanation: qsTranslate("SettingsWallpaperPage", "Choose a picture for every display.")
            value: page.row?.value ?? null
            defaultValue: page.row?.default ?? null
            unavailable: !page.row
            busy: page.busy
            onResetRequested: {
                page.catalog.wallpaperChooserError = "";
                page.catalog.reset("appearance.wallpaper");
            }
            control: Component {
                SettingsButton {
                    objectName: "wallpaper-choose"
                    text: page.chooser.busy ? qsTranslate("SettingsWallpaperPage", "Choosing…") : qsTranslate("SettingsWallpaperPage", "Choose picture…")
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
                color: SettingsTheme.error
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: qsTranslate("SettingsWallpaperPage", "Emaki wallpapers")
        Text {
            width: parent.width
            text: qsTranslate("SettingsWallpaperPage", "Choose a wallpaper from the installed collection.")
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        Flow {
            id: grid
            width: parent.width
            spacing: 12
            readonly property int columns: width >= 600 ? 3 : width >= 360 ? 2 : 1
            Repeater {
                // Keep focused cards alive when the settings reply refreshes the catalog.
                model: (page.catalog.wallpapers || []).length
                delegate: SettingsButton {
                    id: picture
                    required property int index
                    readonly property var modelData: page.catalog.wallpapers[index]
                    objectName: "wallpaper-choice-" + index
                    width: (grid.width - (grid.columns - 1) * grid.spacing) / grid.columns
                    height: 128
                    padding: 7
                    enabled: !page.busy && !!page.row && modelData.available !== false
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
                            objectName: "wallpaper-thumbnail-" + picture.index
                            source: page.pictureUrl(picture.modelData.thumbnail || picture.modelData.path)
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
            text: qsTranslate("SettingsWallpaperPage", "No wallpapers were found in the installed collection.")
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
    }
}
