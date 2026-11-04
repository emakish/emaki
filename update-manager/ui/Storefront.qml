// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import "file:@EMAKI_DATADIR@/shell" as Shell

ColumnLayout {
    id: store
    required property var editorial
    required property var recommendations
    required property bool catalogAvailable
    signal openProgram(var program)
    signal openList(string id)
    spacing: 28
    readonly property bool empty: !recommendations.daily && !recommendations.collections.length
        && !recommendations.because
    function localArtwork(program) {
        for (const icon of program.icons || []) {
            if (icon.kind !== "cached" && icon.kind !== "local") continue;
            const value = icon.url || icon.path || icon.value || "";
            if (value.startsWith("/") && !value.startsWith("//")) return "file://" + value;
            if (value.startsWith("file:///") || /^file:\/(?!\/)/.test(value)) return value;
        }
        return "";
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 6
        ManagerLabel { objectName: "storefront-heading"; text: store.editorial.title; font.pixelSize: 30; font.weight: Font.Bold }
        ManagerLabel { text: store.editorial.subtitle; color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
    }
    C.AbstractButton {
        id: daily
        objectName: "daily-card"
        readonly property var program: store.recommendations.daily || ({})
        readonly property bool wide: width >= 640
        visible: !!store.recommendations.daily
        Layout.fillWidth: true
        implicitHeight: dailyContent.implicitHeight + 40
        padding: 20
        focusPolicy: Qt.StrongFocus
        Accessible.name: store.editorial.dailyTitle + ": " + (program.name || "")
            + (program.installed ? ": " + qsTranslate("ProgramCard", "Installed") : "")
        Keys.onReturnPressed: clicked()
        Keys.onEnterPressed: clicked()
        onClicked: store.openProgram(program)
        background: Rectangle {
            radius: 20
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Qt.alpha(Shell.ShellPalette.accent, .24) }
                GradientStop { position: 1; color: Qt.alpha(Shell.ShellPalette.managerCream, .74) }
            }
            border.width: daily.activeFocus ? 2 : 1
            border.color: daily.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .12)
        }
        contentItem: GridLayout {
            id: dailyContent
            columns: daily.wide ? 2 : 1
            columnSpacing: 28
            rowSpacing: 20
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                spacing: 12
                ManagerLabel { text: store.editorial.dailyTitle; font.pixelSize: 12; font.weight: Font.Bold; color: Shell.ShellPalette.accentDeepForeground }
                ProgramIcon { program: daily.program; allowRemote: false; Layout.preferredWidth: 64; Layout.preferredHeight: 64 }
                ManagerLabel { text: daily.program.name || ""; font.pixelSize: daily.wide ? 28 : 23; font.weight: Font.Bold }
                ManagerLabel { text: daily.program.summary || ""; font.pixelSize: 14; color: Shell.ShellPalette.managerDim }
                ManagerLabel { objectName: "daily-installed"; visible: !!daily.program.installed; text: qsTranslate("ProgramCard", "Installed"); font.pixelSize: 13; font.weight: Font.DemiBold }
                ManagerLabel { text: store.editorial.detailsLabel; font.pixelSize: 13; font.weight: Font.DemiBold; color: Shell.ShellPalette.accentDeepForeground; Layout.topMargin: 4 }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                Layout.preferredHeight: daily.wide ? 260 : Math.max(120, Math.min(220, width * .6))
                radius: 12
                color: Qt.alpha(Shell.ShellPalette.managerInk, .06)
                Image {
                    id: artwork
                    objectName: "daily-artwork"
                    anchors.fill: parent
                    anchors.margins: 8
                    // Browse never fetches artwork; remote screenshots belong to the opened program page.
                    source: store.visible ? store.localArtwork(daily.program) : ""
                    sourceSize.width: Math.ceil(width * 2)
                    sourceSize.height: Math.ceil(height * 2)
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    visible: status === Image.Ready
                }
                ColumnLayout {
                    objectName: "daily-artwork-fallback"
                    visible: artwork.status !== Image.Ready
                    anchors.centerIn: parent
                    width: parent.width - 24
                    spacing: 12
                    ProgramIcon { program: daily.program; allowRemote: false; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 64; Layout.preferredHeight: 64 }
                    ManagerLabel { text: store.editorial.unavailableArtwork; font.pixelSize: 12; color: Shell.ShellPalette.managerDim; horizontalAlignment: Text.AlignHCenter }
                }
            }
        }
    }
    ColumnLayout {
        objectName: "storefront-empty"
        visible: store.catalogAvailable && store.empty
        Layout.fillWidth: true
        spacing: 8
        ManagerLabel { text: store.editorial.emptyTitle; font.pixelSize: 21; font.weight: Font.DemiBold }
        ManagerLabel { text: store.editorial.emptyNote; color: Shell.ShellPalette.managerDim }
    }
    ColumnLayout {
        visible: store.recommendations.collections.length > 0
        Layout.fillWidth: true
        spacing: 14
        ManagerLabel { text: store.editorial.collectionsTitle; font.pixelSize: 21; font.weight: Font.Bold }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 700 ? 2 : 1
            columnSpacing: 16
            rowSpacing: 16
            Repeater {
                model: store.recommendations.collections
                Rectangle {
                    id: collection
                    required property var modelData
                    objectName: "collection-" + modelData.id
                    Accessible.role: Accessible.Grouping
                    Accessible.name: modelData.title
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    implicitHeight: collectionContent.implicitHeight + 32
                    radius: 16
                    color: Qt.alpha(Shell.ShellPalette.managerCream, .5)
                    border.color: Qt.alpha(Shell.ShellPalette.managerInk, .10)
                    ColumnLayout {
                        id: collectionContent
                        anchors { top: parent.top; left: parent.left; right: parent.right; margins: 16 }
                        spacing: 10
                        ManagerLabel { text: collection.modelData.title; font.pixelSize: 21; font.weight: Font.Bold }
                        ManagerLabel { text: collection.modelData.description; color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
                        Repeater {
                            model: collection.modelData.rows.slice(0, 3)
                            ProgramCard {
                                required property var modelData
                                program: modelData
                                Layout.fillWidth: true
                                onClicked: store.openProgram(program)
                            }
                        }
                        ManagerButton {
                            objectName: "see-all-" + collection.modelData.id
                            text: store.editorial.seeAllLabel
                            Accessible.name: text + ": " + collection.modelData.title
                            quiet: true
                            Layout.maximumWidth: collectionContent.width
                            squeezed: collectionContent.width < implicitWidth
                            onClicked: store.openList(collection.modelData.id)
                        }
                    }
                }
            }
        }
    }
    AppShelf {
        objectName: "shelf-because"
        visible: !!store.recommendations.because
        title: store.editorial.becauseTitle.arg(store.recommendations.because?.source.name || "")
        note: store.editorial.becauseNote
        rows: store.recommendations.because?.rows || []
        listId: "because"
    }
    component AppShelf: ColumnLayout {
        id: shelf
        required property string title
        required property string note
        required property var rows
        required property string listId
        Layout.fillWidth: true
        spacing: 12
        ManagerLabel { text: shelf.title; font.pixelSize: 21; font.weight: Font.Bold }
        ManagerLabel { text: shelf.note; font.pixelSize: 13; color: Shell.ShellPalette.managerDim }
        GridLayout {
            Layout.fillWidth: true
            columns: width >= 840 ? 3 : width >= 480 ? 2 : 1
            columnSpacing: 12
            rowSpacing: 12
            Repeater {
                model: shelf.rows.slice(0, 6)
                ProgramCard {
                    required property var modelData
                    program: modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    onClicked: store.openProgram(program)
                }
            }
        }
        ManagerButton {
            objectName: "see-all-" + shelf.listId
            text: store.editorial.seeAllLabel
            Accessible.name: text + ": " + shelf.title
            quiet: true
            Layout.maximumWidth: shelf.width
            squeezed: shelf.width < implicitWidth
            onClicked: store.openList(shelf.listId)
        }
    }
}
