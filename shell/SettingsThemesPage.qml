// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var catalog
    readonly property var styles: catalog.styles || []
    readonly property bool busy: catalog.writing
    spacing: 20

    function pictureUrl(path: string): string {
        return path ? "file://" + path.split("/").map(encodeURIComponent).join("/") : "";
    }
    function styleName(style): string {
        const names = {
            "emaki": "Emaki",
            "01-rose-light": qsTr("Rose Light"),
            "02-orange-light": qsTr("Orange Light"),
            "03-coral-light": qsTr("Coral Light"),
            "04-amber-light": qsTr("Amber Light"),
            "05-green-light": qsTr("Green Light"),
            "06-teal-light": qsTr("Teal Light"),
            "07-blue-light": qsTr("Blue Light"),
            "08-violet-light": qsTr("Violet Light"),
            "09-rose-dark": qsTr("Rose Dark"),
            "10-orange-dark": qsTr("Orange Dark"),
            "11-coral-dark": qsTr("Coral Dark"),
            "12-amber-dark": qsTr("Amber Dark"),
            "13-green-dark": qsTr("Green Dark"),
            "14-teal-dark": qsTr("Teal Dark"),
            "15-blue-dark": qsTr("Blue Dark"),
            "16-violet-dark": qsTr("Violet Dark"),
            "17-silver": qsTr("Silver"),
            "18-graphite": qsTr("Graphite")
        };
        return names[style.id] || style.name;
    }
    function isSelected(style): bool {
        // Compare the live values too: a manual accent change immediately removes the mark.
        return catalog.value("appearance.style") === style.id && catalog.value("appearance.accent") === style.accent && catalog.value("appearance.wallpaper") === style.wallpaper;
    }
    function selectStyle(style): void {
        if (!busy && style.available && catalog.row("appearance.style"))
            catalog.set("appearance.style", style.id);
    }

    SettingsCard {
        objectName: "setting-appearance.style"
        width: parent.width
        title: qsTr("Themes")
        Column {
            width: parent.width
            spacing: 16
            Text {
                width: parent.width
                text: qsTr("Choose a wallpaper, overview background and accent colour together.")
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
            Flow {
                id: grid
                width: parent.width
                spacing: 12
                readonly property int columns: width >= 600 ? 3 : width >= 360 ? 2 : 1
                Repeater {
                    // Replies replace the array even when only the current theme changed.
                    // Keep the cards (and their focus/geometry) alive across those replies.
                    model: page.styles.length
                    delegate: SettingsButton {
                        id: choice
                        required property int index
                        readonly property var modelData: page.styles[index]
                        objectName: "theme-choice-" + modelData.id
                        width: (grid.width - (grid.columns - 1) * grid.spacing) / grid.columns
                        height: 174
                        padding: 8
                        readonly property bool selected: page.isSelected(modelData)
                        readonly property color accentColor: ShellPalette.accents.find(entry => entry.id === modelData.accent)?.base || ShellPalette.accent
                        text: page.styleName(modelData)
                        enabled: !page.busy && modelData.available && !!page.catalog.row("appearance.style")
                        Accessible.name: text + (selected ? ", " + qsTr("Current theme") : "")
                        Accessible.role: Accessible.RadioButton
                        Accessible.checked: selected
                        onClicked: page.selectStyle(modelData)
                        background: Rectangle {
                            radius: 12
                            color: choice.hovered || choice.down ? SettingsTheme.dropHover : SettingsTheme.drop
                            border.width: choice.activeFocus || choice.selected ? 2 : 1
                            border.color: choice.activeFocus || choice.selected ? SettingsTheme.accent : SettingsTheme.rim
                        }
                        contentItem: Column {
                            spacing: 7
                            Rectangle {
                                width: parent.width
                                height: 106
                                color: SettingsTheme.field
                                clip: true
                                Image {
                                    anchors.fill: parent
                                    source: page.pictureUrl(choice.modelData.thumbnail || "")
                                    sourceSize: Qt.size(480, 270)
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                }
                                Rectangle {
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 7
                                    width: 24
                                    height: 24
                                    radius: 12
                                    color: choice.accentColor
                                    border.color: SettingsTheme.rim
                                }
                            }
                            Text {
                                width: parent.width
                                text: choice.text
                                textFormat: Text.PlainText
                                color: SettingsTheme.ink
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                            }
                            Text {
                                objectName: "theme-status-" + choice.modelData.id
                                width: parent.width
                                text: choice.selected ? qsTr("Current theme") : !choice.modelData.available ? qsTr("Not installed") : choice.modelData.video_available === false ? qsTr("Still overview background") : ""
                                color: choice.selected ? SettingsTheme.accentInk : SettingsTheme.dim
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 11
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
            Text {
                visible: !page.styles.length
                width: parent.width
                text: qsTr("No themes were found in the installed collection.")
                wrapMode: Text.WordWrap
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
        }
    }
}
